import re
from datetime import datetime, timedelta
from .conftest import phone_login

FUT = (datetime.now() + timedelta(days=5)).replace(microsecond=0, second=0).isoformat()


def test_phone_login_creates_verified_user(client):
    h = phone_login(client, "98765 43210")
    me = client.get("/me", headers=h).json()
    assert me["phone"] == "+919876543210" and me["phone_verified"] and not me["needs_phone"]


def test_wrong_otp_and_lockout(client):
    client.post("/auth/phone/request-otp", json={"phone": "9876543210"})
    for _ in range(5):
        assert client.post("/auth/phone/verify", json={"phone": "9876543210", "code": "000000"}).status_code == 400
    r = client.post("/auth/phone/verify", json={"phone": "9876543210", "code": "000000"})
    assert r.status_code == 429 and r.json()["detail"]["ta"]


def test_otp_single_use(client):
    code = client.post("/auth/phone/request-otp", json={"phone": "9876543210"}).json()["dev_otp"]
    assert client.post("/auth/phone/verify", json={"phone": "9876543210", "code": code}).status_code == 200
    assert client.post("/auth/phone/verify", json={"phone": "9876543210", "code": code}).status_code == 400


def test_invalid_phone(client):
    assert client.post("/auth/phone/request-otp", json={"phone": "12345"}).status_code == 422


def test_email_register_login_and_phone_verification(client):
    r = client.post("/auth/email/register", json={"email": "A@Test.com", "password": "hunter2hunter2"})
    assert r.status_code == 200 and r.json()["user"]["needs_phone"] is True
    h = {"Authorization": "Bearer " + r.json()["access_token"]}
    assert client.post("/auth/email/register", json={"email": "a@test.com", "password": "hunter2hunter2"}).status_code == 409
    assert client.post("/auth/email/login", json={"email": "a@test.com", "password": "wrong-password"}).status_code == 401
    assert client.post("/auth/email/login", json={"email": "a@test.com", "password": "hunter2hunter2"}).status_code == 200
    code = client.post("/me/phone/request-otp", json={"phone": "9123456780"}, headers=h).json()["dev_otp"]
    assert client.post("/me/phone/verify", json={"phone": "9123456780", "code": "111111"}, headers=h).status_code == 400
    r = client.post("/me/phone/verify", json={"phone": "9123456780", "code": code}, headers=h)
    assert r.status_code == 200 and r.json()["phone_verified"] and r.json()["phone"] == "+919123456780"


def test_phone_cannot_be_claimed_twice(client):
    phone_login(client, "9876543210")
    r = client.post("/auth/email/register", json={"email": "b@test.com", "password": "hunter2hunter2"})
    h = {"Authorization": "Bearer " + r.json()["access_token"]}
    assert client.post("/me/phone/request-otp", json={"phone": "9876543210"}, headers=h).status_code == 409


def test_magic_link_and_preregistration_takeover_protection(client, caplog):
    client.post("/auth/email/register", json={"email": "v@test.com", "password": "attacker-pass1"})  # squatter
    with caplog.at_level("WARNING", logger="nira"):
        client.post("/auth/email/magic/request", json={"email": "v@test.com"})
    token = re.search(r"\?magic=([\w\-]+)", caplog.text).group(1)
    assert client.post("/auth/email/magic/verify", json={"token": token}).status_code == 200
    assert client.post("/auth/email/magic/verify", json={"token": token}).status_code == 400  # single use
    assert client.post("/auth/email/login", json={"email": "v@test.com", "password": "attacker-pass1"}).status_code == 401


def test_auth_required(client):
    assert client.get("/deadlines").status_code == 401
    assert client.get("/deadlines", headers={"Authorization": "Bearer junk"}).status_code == 401


def test_deadline_crud(client, auth):
    r = client.post("/deadlines", headers=auth, json={"title": "  Car EMI ", "category": "emi", "due_at": FUT, "notes": "n"})
    assert r.status_code == 201, r.text
    d = r.json()
    assert d["title"] == "Car EMI" and d["due_at"].endswith("+05:30")
    p = client.patch(f"/deadlines/{d['id']}", headers=auth, json={"title": "Bike EMI", "category": "bill"}).json()
    assert p["title"] == "Bike EMI" and p["category"] == "bill"
    assert client.post(f"/deadlines/{d['id']}/complete", headers=auth).json()["done"] is True
    assert client.get("/deadlines", headers=auth).json() == []
    assert len(client.get("/deadlines?status=done", headers=auth).json()) == 1
    assert client.post(f"/deadlines/{d['id']}/undo", headers=auth).json()["done"] is False
    assert client.delete(f"/deadlines/{d['id']}", headers=auth).status_code == 204
    assert client.get(f"/deadlines/{d['id']}", headers=auth).status_code == 404


def test_deadline_validation(client, auth):
    bad = [{"title": "", "due_at": FUT}, {"title": "x", "due_at": FUT, "category": "nope"},
           {"title": "x", "due_at": "garbage"}, {"title": "x", "due_at": FUT, "repeat": "daily"},
           {"title": "x", "due_at": "2999-01-01T10:00"}]
    for b in bad:
        assert client.post("/deadlines", headers=auth, json=b).status_code == 422, b


def test_users_are_isolated(client):
    a, b = phone_login(client, "9876543210"), phone_login(client, "9123456789")
    did = client.post("/deadlines", headers=a, json={"title": "secret", "due_at": FUT}).json()["id"]
    assert client.get("/deadlines", headers=b).json() == []
    assert client.get(f"/deadlines/{did}", headers=b).status_code == 404
    assert client.patch(f"/deadlines/{did}", headers=b, json={"title": "x"}).status_code == 404
    assert client.post(f"/deadlines/{did}/complete", headers=b).status_code == 404
    assert client.delete(f"/deadlines/{did}", headers=b).status_code == 404
    assert client.get(f"/deadlines/{did}", headers=a).json()["title"] == "secret"


def test_repeating_deadline_rolls_forward(client, auth):
    d = client.post("/deadlines", headers=auth, json={"title": "Rent", "due_at": FUT, "repeat": "monthly"}).json()
    r = client.post(f"/deadlines/{d['id']}/complete", headers=auth).json()
    assert r["rolled"] and not r["done"] and r["due_at"] > d["due_at"]


def test_settings_consent_rules(client):
    r = client.post("/auth/email/register", json={"email": "c@test.com", "password": "hunter2hunter2"})
    h = {"Authorization": "Bearer " + r.json()["access_token"]}
    assert client.put("/settings", headers=h, json={"consent_to_call": True, "calls_enabled": True}).status_code == 400  # no verified phone
    h2 = phone_login(client, "9876543210")
    assert client.put("/settings", headers=h2, json={"calls_enabled": True}).status_code == 400  # no consent
    s = client.put("/settings", headers=h2, json={"consent_to_call": True, "calls_enabled": True, "lead_days": 2,
                                                  "call_time": "08:30", "language": "en"}).json()
    assert s["calls_enabled"] and s["lead_days"] == 2 and s["call_time"] == "08:30"
    s = client.put("/settings", headers=h2, json={"consent_to_call": False}).json()
    assert s["calls_enabled"] is False
    assert client.put("/settings", headers=h2, json={"call_time": "25:99"}).status_code == 422
    assert client.put("/settings", headers=h2, json={"timezone": "Mars/Base"}).status_code == 422


def test_logout_all_and_delete_account(client, auth):
    assert client.post("/auth/logout-all", headers=auth).status_code == 200
    assert client.get("/me", headers=auth).status_code == 401
    h = phone_login(client)
    client.post("/deadlines", headers=h, json={"title": "x", "due_at": FUT})
    assert client.delete("/me", headers=h).status_code == 200
    assert client.get("/me", headers=h).status_code == 401


def test_errors_are_bilingual_and_do_not_echo_input(client):
    r = client.post("/auth/email/register", json={"email": "d@test.com", "password": "short"})
    body = r.text
    assert r.status_code == 422 and "ta" in r.json()["detail"] and "short" not in body.replace("shorter", "")


def test_frontend_is_served(client):
    r = client.get("/")
    assert r.status_code == 200 and "NIRA" in r.text
    assert client.get("/health").json() == {"ok": True}
