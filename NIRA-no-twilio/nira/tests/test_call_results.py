"""4B: Twilio call results, retries and call history. Twilio is ALWAYS the fake from test_calls.py (and the autouse
guard in conftest.py blocks the real client), so nothing here can place a real call."""
import base64
import hashlib
import hmac
from datetime import timedelta
from urllib.parse import urlencode

import httpx
import pytest
from sqlalchemy import select

from app import call_results, calls, scheduler
from app.calls import TwilioCallError
from app.config import get_settings
from app.database import SessionLocal
from app.models import CallLog, Deadline
from .conftest import phone_login
from .test_calls import (DUE, OWNER, REMIND, TWILIO_NUMBER, FakeTwilio, add_deadline, configured, fake,  # noqa: F401
                         owner_setup, state, tick)

CB_URL = "https://nira.example.com/twilio/status"
T0 = REMIND + timedelta(minutes=1)          # first call time used by most tests
MIN = timedelta(minutes=1)


@pytest.fixture
def public(monkeypatch):
    """A public https APP_BASE_URL, so calls ask Twilio for status callbacks."""
    monkeypatch.setattr(get_settings(), "app_base_url", "https://nira.example.com")


def logs(did=None):
    with SessionLocal() as db:
        q = select(CallLog).order_by(CallLog.id)
        if did is not None:
            q = q.where(CallLog.deadline_id == did)
        return [(r.id, r.attempt, r.status, r.provider_call_id, r.duration_sec, r.error) for r in db.scalars(q)]


def dl(did):
    with SessionLocal() as db:
        d = db.get(Deadline, did)
        return d.call_attempts, d.last_call_at, d.next_retry_at


def result(log_id, status, now, **kw):
    with SessionLocal() as db:
        return call_results.apply_result(db, log_id, status, now=now, **kw)


def sign(params, url=CB_URL, token="secret-token-xyz"):
    data = url + "".join(k + v for k, v in sorted(params.items()))
    return base64.b64encode(hmac.new(token.encode(), data.encode(), hashlib.sha1).digest()).decode()


def post_status(client, params, signature=None, url_ok=True):
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    sig = sign(params) if signature is None else signature
    if sig:
        headers["X-Twilio-Signature"] = sig
    return client.post("/twilio/status", content=urlencode(params), headers=headers)


# ---------------------------------------------------------------- success comes only from Twilio's result
def test_reminder_counts_as_called_only_when_twilio_says_answered(client, configured, fake):
    h = owner_setup(client)
    did = add_deadline()
    assert tick(T0) == [did]
    (log_id, attempt, status, sid, *_), = logs(did)
    assert (attempt, status, sid) == (1, "queued", "CAfake1")
    assert dl(did)[:2] == (1, None)
    assert client.get(f"/deadlines/{did}", headers=h).json()["called"] is False
    assert result(log_id, "ringing", T0 + MIN) and dl(did)[1] is None          # ringing is not success
    assert result(log_id, "in-progress", T0 + 2 * MIN)                           # answered
    attempts, last_call_at, next_retry = dl(did)
    assert attempts == 1 and last_call_at == T0 + 2 * MIN and next_retry is None
    assert client.get(f"/deadlines/{did}", headers=h).json()["called"] is True
    assert result(log_id, "completed", T0 + 3 * MIN, duration=12)
    assert logs(did)[0][2:5] == ("completed", "CAfake1", 12)
    assert tick(T0 + 30 * MIN) == [] and len(fake.calls) == 1                    # answered: never retried


@pytest.mark.parametrize("status", ["busy", "no-answer", "failed"])
def test_unsuccessful_results_are_retried_after_the_delay(client, configured, fake, status):
    owner_setup(client)
    did = add_deadline()
    assert tick(T0) == [did]
    log_id = logs(did)[0][0]
    assert result(log_id, status, T0 + MIN)
    attempts, last_call_at, next_retry = dl(did)
    assert (attempts, last_call_at) == (1, None) and next_retry == T0 + MIN + 10 * MIN
    assert tick(T0 + 5 * MIN) == []                                              # too early
    assert tick(T0 + 12 * MIN) == [did]                                          # retry
    assert len(fake.calls) == 2
    rows = logs(did)
    assert [(r[1], r[2]) for r in rows] == [(1, status), (2, "queued")]
    assert dl(did)[0] == 2 and dl(did)[2] is None                                # retry claimed: next_retry cleared
    assert tick(T0 + 13 * MIN) == [] and len(fake.calls) == 2                    # no duplicate retry


def test_retry_limit(client, configured, fake):
    owner_setup(client)
    did = add_deadline()
    max_retries = get_settings().call_max_retries
    t = T0
    for n in range(1, max_retries + 2):                                          # first call + every allowed retry
        assert tick(t) == [did], f"attempt {n}"
        result(logs(did)[-1][0], "no-answer", t + MIN)
        t += 15 * MIN
    assert dl(did)[0] == max_retries + 1 and dl(did)[2] is None                  # exhausted: nothing scheduled
    assert tick(t + 60 * MIN) == [] and tick(t + 600 * MIN) == []
    assert len(fake.calls) == max_retries + 1
    assert [r[1] for r in logs(did)] == list(range(1, max_retries + 2))


def test_canceled_is_final_not_retried(client, configured, fake):
    owner_setup(client)
    did = add_deadline()
    tick(T0)
    result(logs(did)[0][0], "canceled", T0 + MIN)
    assert dl(did)[2] is None and tick(T0 + 60 * MIN) == [] and len(fake.calls) == 1


def test_failed_twilio_request_is_logged_and_retried(client, configured, monkeypatch):
    f = FakeTwilio(fail=True)
    monkeypatch.setattr(calls, "get_twilio_client", lambda: f)
    owner_setup(client)
    did = add_deadline()
    assert tick(T0) == [did]
    (_, attempt, status, sid, _, error), = logs(did)
    assert (attempt, status, sid) == (1, "failed", None) and "HTTP 400" in error
    assert dl(did) == (1, None, T0 + 10 * MIN)                                   # retryable, not "called"
    f.fail = False                                                               # Twilio recovers
    assert tick(T0 + 11 * MIN) == [did] and len(f.calls) == 1
    assert [(r[1], r[2]) for r in logs(did)] == [(1, "failed"), (2, "queued")]
    result(logs(did)[-1][0], "completed", T0 + 12 * MIN, duration=8)
    assert dl(did)[1] == T0 + 12 * MIN


# ---------------------------------------------------------------- duplicates / ordering
def test_duplicate_and_late_results_change_nothing(client, configured, fake):
    owner_setup(client)
    did = add_deadline()
    tick(T0)
    log_id = logs(did)[0][0]
    assert result(log_id, "no-answer", T0 + MIN) is True
    first = dl(did)
    assert result(log_id, "no-answer", T0 + 2 * MIN) is False                    # duplicate callback
    assert result(log_id, "ringing", T0 + 3 * MIN) is False                      # older event arriving late
    assert result(log_id, "completed", T0 + 3 * MIN) is False                    # a finished call is final
    assert result(log_id, "banana", T0 + 3 * MIN) is False                       # unknown status ignored
    assert dl(did) == first


def test_out_of_order_ringing_after_answer_is_ignored(client, configured, fake):
    owner_setup(client)
    did = add_deadline()
    tick(T0)
    log_id = logs(did)[0][0]
    assert result(log_id, "in-progress", T0 + MIN)
    assert result(log_id, "ringing", T0 + 2 * MIN) is False
    assert logs(did)[0][2] == "in-progress"


def test_late_failure_after_answer_does_not_trigger_retry(client, configured, fake):
    owner_setup(client)
    did = add_deadline()
    tick(T0)
    log_id = logs(did)[0][0]
    result(log_id, "in-progress", T0 + MIN)
    result(log_id, "failed", T0 + 2 * MIN)
    assert dl(did)[1] is not None and dl(did)[2] is None
    assert tick(T0 + 60 * MIN) == [] and len(fake.calls) == 1


def test_concurrent_retry_claims_only_one_call(client, configured, fake):
    owner_setup(client)
    did = add_deadline()
    tick(T0)
    result(logs(did)[0][0], "busy", T0 + MIN)
    now = T0 + 12 * MIN
    with SessionLocal() as a, SessionLocal() as b:
        da, db_ = a.get(Deadline, did), b.get(Deadline, did)
        assert scheduler._attempt(a, da, "Asia/Kolkata", now) is True
        assert scheduler._attempt(b, db_, "Asia/Kolkata", now) is False
    assert len(fake.calls) == 2 and len(logs(did)) == 2


def test_finished_or_past_due_reminders_are_not_retried(client, configured, fake):
    h = owner_setup(client)
    did = add_deadline()
    tick(T0)
    result(logs(did)[0][0], "no-answer", T0 + MIN)
    assert client.post(f"/deadlines/{did}/complete", headers=h).status_code == 200   # user did it meanwhile
    assert tick(T0 + 12 * MIN) == [] and len(fake.calls) == 1
    did2 = add_deadline("Late", due=T0 + 5 * MIN)                                    # falls due before the retry
    with SessionLocal() as db:
        db.execute(Deadline.__table__.update().where(Deadline.id == did2).values(call_attempts=1, next_retry_at=T0 + 2 * MIN))
        db.commit()
    assert tick(T0 + 12 * MIN) == []


def test_moving_the_date_ignores_results_of_the_old_call(client, configured, fake):
    h = owner_setup(client)
    did = add_deadline()
    tick(T0)
    old_log = logs(did)[0][0]
    assert client.patch(f"/deadlines/{did}", headers=h, json={"due_at": "2030-07-15T18:00"}).status_code == 200
    assert dl(did) == (0, None, None)
    from datetime import datetime
    from app.timeutil import to_utc_naive
    t2 = to_utc_naive(datetime(2030, 7, 14, 9, 1), "Asia/Kolkata")
    assert tick(t2) == [did]                                                     # new cycle, new call
    assert result(old_log, "completed", t2 + MIN)                                # old call's result arrives late
    assert dl(did)[:2] == (1, None)                                              # ...and must not mark the new cycle called


# ---------------------------------------------------------------- polling when no callback arrives
def test_stale_call_is_checked_with_twilio(client, configured, fake):
    owner_setup(client)
    did = add_deadline()
    tick(T0)
    assert tick(T0 + 5 * MIN) == [] and dl(did)[:2] == (1, None)                 # not stale yet: nothing asked
    fake.fetch_result = ("completed", 31)
    assert tick(T0 + 11 * MIN) == []
    assert logs(did)[0][2] == "completed" and logs(did)[0][4] == 31
    assert dl(did)[1] == T0 + 11 * MIN and len(fake.calls) == 1


def test_stale_no_answer_found_by_polling_is_retried(client, configured, fake):
    owner_setup(client)
    did = add_deadline()
    tick(T0)
    fake.fetch_result = ("no-answer", 0)
    assert tick(T0 + 11 * MIN) == []                                             # result found; retry not due yet
    assert dl(did)[2] == T0 + 21 * MIN
    assert tick(T0 + 22 * MIN) == [did] and len(fake.calls) == 2


def test_unreachable_twilio_never_causes_a_guess_or_duplicate(client, configured, fake):
    owner_setup(client)
    did = add_deadline()
    tick(T0)
    fake.fetch_error = True
    for m in (11, 12, 90):
        assert tick(T0 + m * MIN) == []
    assert logs(did)[0][2] == "queued" and len(fake.calls) == 1


def test_call_never_sent_to_twilio_is_failed_then_retried(client, configured, fake):
    owner_setup(client)
    did = add_deadline()
    with SessionLocal() as db:                                                   # a row left behind by a crash: claimed, no Twilio id
        db.execute(Deadline.__table__.update().where(Deadline.id == did).values(call_attempts=1))
        db.add(CallLog(user_id=db.get(Deadline, did).user_id, deadline_id=did, title="Car EMI", status="queued",
                       attempt=1, started_at=T0, created_at=T0))
        db.commit()
    assert tick(T0 + 11 * MIN) == []
    assert logs(did)[0][2] == "failed" and dl(did)[2] == T0 + 21 * MIN
    assert tick(T0 + 22 * MIN) == [did]


# ---------------------------------------------------------------- Twilio webhook
def test_status_callback_url_rules(monkeypatch):
    s = get_settings()
    assert calls.status_callback_url() is None                                   # tests run on http://localhost
    monkeypatch.setattr(s, "app_base_url", "https://nira.example.com/")
    assert calls.status_callback_url() == CB_URL
    monkeypatch.setattr(s, "app_base_url", "http://nira.example.com")
    assert calls.status_callback_url() is None                                   # not https
    monkeypatch.setattr(s, "twilio_status_callback_url", "https://abc.ngrok.app/twilio/status")
    assert calls.status_callback_url() == "https://abc.ngrok.app/twilio/status"


def test_calls_ask_for_status_callbacks_only_with_a_public_url(client, configured, fake, public, monkeypatch):
    owner_setup(client)
    add_deadline()
    tick(T0)
    assert fake.calls[0]["kw"] == {"status_callback": CB_URL}
    monkeypatch.setattr(get_settings(), "app_base_url", "http://localhost:8000")
    add_deadline("Other", due=DUE + timedelta(seconds=1))
    tick(T0)
    assert fake.calls[1]["kw"] == {}


def test_webhook_updates_the_call_and_reminder(client, configured, fake, public):
    h = owner_setup(client)
    did = add_deadline()
    tick(T0)
    r = post_status(client, {"CallSid": "CAfake1", "CallStatus": "in-progress", "AccountSid": "ACtest"})
    assert r.status_code == 204
    assert client.get(f"/deadlines/{did}", headers=h).json()["called"] is True
    r = post_status(client, {"CallSid": "CAfake1", "CallStatus": "completed", "CallDuration": "27"})
    assert r.status_code == 204 and logs(did)[0][2] == "completed" and logs(did)[0][4] == 27


def test_webhook_busy_schedules_one_retry(client, configured, fake, public):
    owner_setup(client)
    did = add_deadline()
    tick(T0)
    for _ in range(2):                                                           # Twilio may deliver twice
        assert post_status(client, {"CallSid": "CAfake1", "CallStatus": "busy"}).status_code == 204
    assert logs(did)[0][2] == "busy" and dl(did)[2] is not None
    assert dl(did)[1] is None


def test_webhook_rejects_bad_or_missing_signature(client, configured, fake, public):
    owner_setup(client)
    did = add_deadline()
    tick(T0)
    p = {"CallSid": "CAfake1", "CallStatus": "completed"}
    assert post_status(client, p, signature="bm90LXR3aWxpbw==").status_code == 403
    assert post_status(client, p, signature="").status_code == 403
    assert post_status(client, p, signature=sign(p, token="wrong-token")).status_code == 403
    assert post_status(client, p, signature=sign(p, url="https://evil.example.com/twilio/status")).status_code == 403
    tampered = sign(p)
    assert post_status(client, {"CallSid": "CAfake1", "CallStatus": "failed"}, signature=tampered).status_code == 403
    assert logs(did)[0][2] == "queued" and dl(did)[:2] == (1, None)              # nothing was applied


def test_webhook_refused_without_a_public_url_or_token(client, configured, monkeypatch):
    p = {"CallSid": "CAfake1", "CallStatus": "completed"}
    assert post_status(client, p, signature=sign(p)).status_code == 403          # app_base_url is localhost -> no valid URL
    monkeypatch.setattr(get_settings(), "app_base_url", "https://nira.example.com")
    monkeypatch.setattr(get_settings(), "twilio_auth_token", "")
    assert post_status(client, p, signature=sign(p, token="")).status_code == 403


def test_webhook_ignores_unknown_calls_and_statuses(client, configured, fake, public):
    owner_setup(client)
    did = add_deadline()
    tick(T0)
    assert post_status(client, {"CallSid": "CAsomeoneelse", "CallStatus": "completed"}).status_code == 204
    assert post_status(client, {"CallSid": "CAfake1", "CallStatus": "initiated"}).status_code == 204
    assert post_status(client, {"CallStatus": "completed"}).status_code == 204
    assert logs(did)[0][2] == "queued"
    big = {"CallSid": "CAfake1", "CallStatus": "completed", "Junk": "x" * 25000}
    assert post_status(client, big).status_code == 413


def test_webhook_survives_odd_duration(client, configured, fake, public):
    owner_setup(client)
    did = add_deadline()
    tick(T0)
    r = post_status(client, {"CallSid": "CAfake1", "CallStatus": "completed", "CallDuration": "\u00b2"})   # "²".isdigit() is True
    assert r.status_code == 204 and logs(did)[0][2] == "completed" and logs(did)[0][4] == 0


def test_non_ascii_signature_is_refused_not_a_crash(monkeypatch):
    monkeypatch.setattr(get_settings(), "twilio_auth_token", "12345")
    assert calls.valid_signature(CB_URL, [("CallSid", "CA1")], "\u00e9\u00e9") is False
    assert calls.valid_signature(CB_URL, [("CallSid", "CA1")], "") is False
    assert calls.valid_signature(None, [("CallSid", "CA1")], "abc") is False


# ---------------------------------------------------------------- history API
def test_history_shows_title_masked_phone_time_status_and_retries(client, configured, fake, public):
    h = owner_setup(client)
    did = add_deadline()
    tick(T0)
    post_status(client, {"CallSid": "CAfake1", "CallStatus": "no-answer"})
    tick(T0 + 12 * MIN)
    post_status(client, {"CallSid": "CAfake2", "CallStatus": "completed", "CallDuration": "9"})
    r = client.get("/calls/history", headers=h)
    assert r.status_code == 200
    rows = r.json()
    assert [(x["title"], x["status"], x["retry_count"]) for x in rows] == [("Car EMI", "completed", 1), ("Car EMI", "no-answer", 0)]
    assert rows[0]["phone"].endswith("3210") and rows[0]["phone"] != OWNER and "98765" not in rows[0]["phone"]
    assert rows[0]["call_time"].startswith("2030-06-14T09:") and rows[0]["duration_sec"] == 9
    for secret in ("CAfake", "secret-token-xyz", "ACtest", OWNER, TWILIO_NUMBER):
        assert secret not in r.text
    assert did and set(rows[0]) == {"id", "title", "phone", "call_time", "status", "retry_count", "duration_sec"}


def test_history_is_private_and_needs_login(client, configured, fake):
    h = owner_setup(client)
    add_deadline()
    tick(T0)
    assert client.get("/calls/history").status_code == 401
    assert len(client.get("/calls/history", headers=h).json()) == 1
    other = phone_login(client, "9123456780")
    assert client.get("/calls/history", headers=other).json() == []


def test_test_call_appears_in_history_and_follows_callbacks(client, configured, fake, public):
    h = owner_setup(client)
    assert client.post("/calls/test", headers=h).status_code == 200
    assert fake.calls[0]["kw"] == {"status_callback": CB_URL}
    row = client.get("/calls/history", headers=h).json()[0]
    assert (row["title"], row["status"], row["retry_count"]) == ("Test call", "queued", 0)
    assert post_status(client, {"CallSid": "CAfake1", "CallStatus": "completed", "CallDuration": "5"}).status_code == 204
    assert client.get("/calls/history", headers=h).json()[0]["status"] == "completed"
    assert tick(T0) == [] and len(fake.calls) == 1                                # a test call never schedules retries


def test_failed_test_call_is_recorded_but_not_retried(client, configured, monkeypatch):
    monkeypatch.setattr(calls, "get_twilio_client", lambda: FakeTwilio(fail=True))
    h = owner_setup(client)
    assert client.post("/calls/test", headers=h).status_code == 502
    assert client.get("/calls/history", headers=h).json()[0]["status"] == "failed"
    assert tick(T0 + 60 * MIN) == []


# ---------------------------------------------------------------- HTTP layer (httpx replaced: still no network)
def test_http_client_sends_callback_options_and_reads_status(monkeypatch):
    seen = {}

    class Resp:
        status_code = 201

        def __init__(self, body):
            self._b = body

        def json(self):
            return self._b

    def fake_post(url, auth=None, data=None, timeout=None):
        seen["data"] = data
        return Resp({"sid": "CA9"})

    def fake_get(url, auth=None, timeout=None):
        seen["get"] = url
        return Resp({"status": "no-answer", "duration": "0"})
    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr(httpx, "get", fake_get)
    c = calls.TwilioClient("ACx", "tok")
    assert c.create_call("+911", "+141", "<Response/>", status_callback=CB_URL) == "CA9"
    assert seen["data"]["StatusCallback"] == CB_URL and seen["data"]["StatusCallbackMethod"] == "POST"
    assert seen["data"]["StatusCallbackEvent"] == ["initiated", "ringing", "answered", "completed"]
    assert c.fetch_call("CA9") == ("no-answer", 0) and seen["get"].endswith("/Accounts/ACx/Calls/CA9.json")
    Resp.status_code = 404
    with pytest.raises(TwilioCallError) as e:
        c.fetch_call("CA9")
    assert "tok" not in str(e.value)


def test_signature_check_matches_twilios_algorithm(monkeypatch):
    monkeypatch.setattr(get_settings(), "twilio_auth_token", "12345")
    # Example from Twilio's request-validation docs.
    url = "https://mycompany.com/myapp.php?foo=1&bar=2"
    params = [("Digits", "1234"), ("To", "+18005551212"), ("From", "+14158675310"), ("Caller", "+14158675310"),
              ("CallSid", "CA1234567890ABCDE")]
    assert calls.valid_signature(url, params, "GvWf1cFY/Q7PnoempGyD5oXAezc=")
    assert not calls.valid_signature(url, params, "GvWf1cFY/Q7PnoempGyD5oXAezd=")
