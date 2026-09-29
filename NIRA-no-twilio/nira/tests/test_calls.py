"""Scheduler + Twilio call tests. The Twilio client is ALWAYS a fake (see the autouse `no_real_twilio` fixture in
conftest.py); nothing here touches the network, so no test can place a real call."""
from datetime import datetime, timedelta

import httpx
import pytest

from app import calls, scheduler
from app.calls import TwilioCallError, is_e164
from app.config import get_settings
from app.database import SessionLocal
from app.models import Deadline, User
from app.timeutil import to_utc_naive
from .conftest import phone_login

OWNER = "+919876543210"
TWILIO_NUMBER = "+14155550100"
# Deterministic clock: deadline 2030-06-15 18:00 IST, lead 1 day, call time 09:00 -> reminder due 2030-06-14 09:00 IST.
DUE = to_utc_naive(datetime(2030, 6, 15, 18, 0), "Asia/Kolkata")
REMIND = to_utc_naive(datetime(2030, 6, 14, 9, 0), "Asia/Kolkata")


class FakeTwilio:
    """Stands in for TwilioClient. Never touches the network."""

    def __init__(self, fail=False):
        self.calls, self.fail = [], fail
        self.fetch_result = ("queued", None)     # what "ask Twilio for this call" answers (see call-results tests)
        self.fetch_error = False

    def create_call(self, to, from_, twiml, **kw):
        if self.fail:
            raise TwilioCallError("Twilio returned HTTP 400")
        self.calls.append({"to": to, "from": from_, "twiml": twiml, "kw": kw})
        return f"CAfake{len(self.calls)}"

    def fetch_call(self, sid):
        if self.fetch_error:
            raise TwilioCallError("network error (ConnectError)")
        return self.fetch_result


@pytest.fixture
def fake(monkeypatch):
    f = FakeTwilio()
    monkeypatch.setattr(calls, "get_twilio_client", lambda: f)
    return f


@pytest.fixture
def configured(monkeypatch):
    s = get_settings()
    for k, v in dict(twilio_account_sid="ACtest", twilio_auth_token="secret-token-xyz", twilio_phone_number=TWILIO_NUMBER,
                     nira_user_phone_number=OWNER).items():
        monkeypatch.setattr(s, k, v)


def owner_setup(client, lead_days=1, call_time="09:00", enable=True):
    h = phone_login(client, "9876543210")
    if enable:
        r = client.put("/settings", headers=h, json={"consent_to_call": True, "calls_enabled": True,
                                                     "lead_days": lead_days, "call_time": call_time})
        assert r.status_code == 200, r.text
    return h


def add_deadline(title="Car EMI", due=DUE, user_phone=OWNER, **kw):
    with SessionLocal() as db:
        uid = db.query(User).filter(User.phone == user_phone).one().id
        d = Deadline(user_id=uid, title=title, category="emi", due_at=due, **kw)
        db.add(d)
        db.commit()
        return d.id


def tick(now):
    return scheduler.run_due_reminders(now=now)


def state(did):
    with SessionLocal() as db:
        d = db.get(Deadline, did)
        return d.call_attempts, d.last_call_at


# ---------------------------------------------------------------- E.164 + config
@pytest.mark.parametrize("n", ["+919876543210", "+14155550100", "+442071838750"])
def test_e164_valid(n):
    assert is_e164(n)


@pytest.mark.parametrize("n", ["9876543210", "919876543210", "+91 98765 43210", "+91-9876543210", "+0919876543210",
                               "+91", "+9198765", "+9198765432109876", "", None, "+91987654321a"])
def test_e164_invalid(n):
    assert not is_e164(n)


def test_config_problems_never_reveal_values(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "twilio_account_sid", "ACsecretsid")
    monkeypatch.setattr(s, "twilio_auth_token", "")
    monkeypatch.setattr(s, "twilio_phone_number", "4155550100")   # not E.164
    monkeypatch.setattr(s, "nira_user_phone_number", OWNER)
    p = calls.call_config_problems()
    assert any("TWILIO_AUTH_TOKEN" in x for x in p) and any("TWILIO_PHONE_NUMBER" in x for x in p)
    assert "ACsecretsid" not in " ".join(p) and OWNER not in " ".join(p)
    assert not calls.calls_configured()


def test_configured_ok(configured):
    assert calls.calls_configured()


def test_same_from_and_to_rejected(configured, monkeypatch):
    monkeypatch.setattr(get_settings(), "twilio_phone_number", OWNER)
    assert not calls.calls_configured()


# ---------------------------------------------------------------- real client is blocked in tests
def test_real_client_is_blocked_in_tests():
    with pytest.raises(AssertionError):
        calls.get_twilio_client()


def test_twilio_http_client_payload_and_errors(monkeypatch):
    """The HTTP layer itself, with httpx.post replaced: still no network."""
    seen = {}

    class Resp:
        status_code = 201

        def json(self):
            return {"sid": "CA123"}

    def fake_post(url, auth=None, data=None, timeout=None):
        seen.update(url=url, auth=auth, data=data)
        return Resp()
    monkeypatch.setattr(httpx, "post", fake_post)
    sid = calls.TwilioClient("ACx", "tok").create_call("+911", "+141", "<Response/>")
    assert sid == "CA123" and seen["url"].endswith("/Accounts/ACx/Calls.json") and seen["auth"] == ("ACx", "tok")
    assert seen["data"] == {"To": "+911", "From": "+141", "Twiml": "<Response/>"}

    Resp.status_code = 401
    with pytest.raises(TwilioCallError) as e:
        calls.TwilioClient("ACx", "tok").create_call("+911", "+141", "<Response/>")
    assert "tok" not in str(e.value) and "ACx" not in str(e.value)

    def boom(*a, **k):
        raise httpx.ConnectError("no route to api.twilio.com for ACx")
    monkeypatch.setattr(httpx, "post", boom)
    with pytest.raises(TwilioCallError) as e:
        calls.TwilioClient("ACx", "tok").create_call("+911", "+141", "<Response/>")
    assert "ACx" not in str(e.value)


def test_twiml_escapes_titles():
    x = calls.reminder_twiml("Rent <b> & \"tax\"", "Saturday 15 June at 6:00 PM")
    assert "<b>" not in x and "&lt;b&gt;" in x and "&amp;" in x


# ---------------------------------------------------------------- POST /calls/test
def test_test_call_requires_login(client, configured, fake):
    assert client.post("/calls/test").status_code == 401
    assert fake.calls == []


def test_test_call_not_configured(client, fake):
    h = owner_setup(client)
    r = client.post("/calls/test", headers=h)
    assert r.status_code == 503 and r.json()["detail"]["code"] == "twilio_not_configured"
    assert fake.calls == []


def test_test_call_places_exactly_one_call_to_configured_number(client, configured, fake):
    h = owner_setup(client)
    r = client.post("/calls/test", headers=h, json={"to": "+15555550123"})   # a body value must be ignored
    assert r.status_code == 200 and r.json()["ok"] is True
    assert len(fake.calls) == 1
    c = fake.calls[0]
    assert c["to"] == OWNER and c["from"] == TWILIO_NUMBER and "test call" in c["twiml"]
    body = r.text
    assert "secret-token-xyz" not in body and "ACtest" not in body and OWNER not in body   # masked, no credentials


def test_test_call_forbidden_for_other_accounts(client, configured, fake):
    h = phone_login(client, "9123456780")            # verified, but not the NIRA_USER_PHONE_NUMBER
    r = client.post("/calls/test", headers=h)
    assert r.status_code == 403 and r.json()["detail"]["code"] == "call_forbidden"
    r = client.post("/auth/email/register", json={"email": "e@test.com", "password": "hunter2hunter2"})
    h2 = {"Authorization": "Bearer " + r.json()["access_token"]}   # no verified phone at all
    assert client.post("/calls/test", headers=h2).status_code == 403
    assert fake.calls == []


def test_test_call_twilio_failure_is_bilingual_502(client, configured, monkeypatch):
    monkeypatch.setattr(calls, "get_twilio_client", lambda: FakeTwilio(fail=True))
    h = owner_setup(client)
    r = client.post("/calls/test", headers=h)
    d = r.json()["detail"]
    assert r.status_code == 502 and d["code"] == "call_failed" and d["en"] and d["ta"]
    assert "secret-token-xyz" not in r.text


def test_test_call_rate_limited(client, configured, fake, monkeypatch):
    from app.ratelimit import limiter
    monkeypatch.setattr(get_settings(), "test_call_rate_limit", "1/minute")
    limiter.reset()
    h = owner_setup(client)
    assert client.post("/calls/test", headers=h).status_code == 200
    assert client.post("/calls/test", headers=h).status_code == 429
    assert len(fake.calls) == 1
    monkeypatch.setattr(get_settings(), "test_call_rate_limit", "1000/minute")
    limiter.reset()


def test_credentials_not_in_frontend_or_settings_api(client, configured):
    h = owner_setup(client)
    blob = client.get("/").text + client.get("/settings", headers=h).text + client.get("/me", headers=h).text
    for secret in ("secret-token-xyz", "ACtest", TWILIO_NUMBER):
        assert secret not in blob
    assert "/calls/test" in client.get("/").text     # the button calls the protected endpoint only


# ---------------------------------------------------------------- scheduler
def test_reminder_time_uses_lead_days_call_time_and_timezone():
    assert scheduler.reminder_time(DUE, 1, "09:00", "Asia/Kolkata") == REMIND
    assert scheduler.reminder_time(DUE, 0, "09:00", "Asia/Kolkata") == REMIND + timedelta(days=1)
    assert scheduler.reminder_time(DUE, 2, "18:30", "Asia/Kolkata") == to_utc_naive(datetime(2030, 6, 13, 18, 30), "Asia/Kolkata")
    assert scheduler.reminder_time(DUE, 1, "nonsense", "Asia/Kolkata") is None


def test_due_reminder_calls_once_and_never_duplicates(client, configured, fake):
    owner_setup(client)
    did = add_deadline()
    assert tick(REMIND - timedelta(minutes=1)) == []                # not due yet
    assert fake.calls == []
    assert tick(REMIND + timedelta(minutes=1)) == [did]             # due -> one call
    assert len(fake.calls) == 1
    c = fake.calls[0]
    assert c["to"] == OWNER and c["from"] == TWILIO_NUMBER and "Car EMI" in c["twiml"]
    assert state(did) == (1, None)                                   # one ATTEMPT; "called" only once Twilio says answered
    for m in (2, 3, 60):                                             # later ticks: no duplicate
        assert tick(REMIND + timedelta(minutes=m)) == []
    assert len(fake.calls) == 1


def test_claim_is_atomic_only_one_winner(client, configured, fake):
    owner_setup(client)
    did = add_deadline()
    now = REMIND + timedelta(minutes=1)
    with SessionLocal() as a, SessionLocal() as b:
        da, db_ = a.get(Deadline, did), b.get(Deadline, did)     # two workers both saw the reminder as never called
        assert scheduler._attempt(a, da, "Asia/Kolkata", now) is True
        assert scheduler._attempt(b, db_, "Asia/Kolkata", now) is False
    assert len(fake.calls) == 1


def test_failed_request_is_not_marked_called_and_stays_retryable(client, configured, monkeypatch):
    f = FakeTwilio(fail=True)
    monkeypatch.setattr(calls, "get_twilio_client", lambda: f)
    h = owner_setup(client)
    did = add_deadline()
    assert tick(REMIND + timedelta(minutes=1)) == [did]              # attempted once
    attempts, last_call_at = state(did)
    assert attempts == 1 and last_call_at is None                    # an attempt, NOT a successful call
    assert client.get(f"/deadlines/{did}", headers=h).json()["called"] is False
    assert tick(REMIND + timedelta(minutes=2)) == []                 # waits for the retry delay (see test_call_results.py)
    assert f.calls == []


def test_skips_when_not_eligible(client, configured, fake):
    h = owner_setup(client)
    done_id = add_deadline("Done one", done=True)
    past_id = add_deadline("Overdue", due=REMIND - timedelta(days=30))
    now = REMIND + timedelta(minutes=1)
    assert tick(now) == []
    client.put("/settings", headers=h, json={"calls_enabled": False})   # calls switched off
    add_deadline("Off")
    assert tick(now) == []
    client.put("/settings", headers=h, json={"consent_to_call": True, "calls_enabled": True})
    client.put("/settings", headers=h, json={"consent_to_call": False})  # consent withdrawn
    assert tick(now) == [] and fake.calls == []
    assert state(done_id)[0] == 0 and state(past_id)[0] == 0


def test_only_owner_number_is_called(client, configured, fake):
    h = phone_login(client, "9123456780")                # a different verified user with calls on
    client.put("/settings", headers=h, json={"consent_to_call": True, "calls_enabled": True, "lead_days": 1, "call_time": "09:00"})
    add_deadline("Other user's", user_phone="+919123456780")
    assert tick(REMIND + timedelta(minutes=1)) == [] and fake.calls == []


def test_missed_window_is_skipped_not_called_late(client, configured, fake):
    owner_setup(client)
    did = add_deadline()
    assert tick(REMIND + scheduler.CATCH_UP_WINDOW + timedelta(minutes=1)) == []
    assert fake.calls == [] and state(did)[0] == 0


def test_not_configured_scheduler_does_nothing(client, fake):
    owner_setup(client)
    add_deadline()
    assert tick(REMIND + timedelta(minutes=1)) == [] and fake.calls == []


def test_per_run_cap(client, configured, fake):
    owner_setup(client)
    ids = [add_deadline(f"D{i}", due=DUE + timedelta(seconds=i)) for i in range(scheduler.MAX_CALLS_PER_RUN + 2)]
    now = REMIND + timedelta(minutes=1)
    assert len(tick(now)) == scheduler.MAX_CALLS_PER_RUN
    assert len(tick(now)) == 2                            # the rest go out on the next tick, each exactly once
    assert len(fake.calls) == len(ids)
    assert tick(now) == []


def test_moving_the_date_allows_a_new_call(client, configured, fake):
    """Existing behaviour (PATCH due_at resets the markers) keeps working with the scheduler."""
    h = owner_setup(client)
    did = add_deadline()
    assert tick(REMIND + timedelta(minutes=1)) == [did]
    r = client.patch(f"/deadlines/{did}", headers=h, json={"due_at": "2030-07-15T18:00"})
    assert r.status_code == 200 and state(did) == (0, None)
    assert tick(to_utc_naive(datetime(2030, 7, 14, 9, 1), "Asia/Kolkata")) == [did]
    assert len(fake.calls) == 2


def test_start_scheduler_respects_flags(monkeypatch):
    monkeypatch.setattr(scheduler, "_scheduler", None)
    assert scheduler.start_scheduler() is None            # SCHEDULER_ENABLED=false in tests
    s = get_settings()
    monkeypatch.setattr(s, "scheduler_enabled", True)
    assert scheduler.start_scheduler() is None            # enabled, but Twilio settings missing -> stays off
    assert scheduler._scheduler is None
