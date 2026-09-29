"""Part 3A: per-user AI key settings. Every provider call is mocked: no network, no real keys anywhere."""
import json, re
import httpx
import pytest
from cryptography.fernet import Fernet
from sqlalchemy import func, select, text
from app import ai_providers
from app.ai_keys import resolve_ai
from app.ai_providers import DEFAULT_MODELS
from app.config import get_settings
from app.database import SessionLocal
from app.models import User, UserAISettings
from app.security import decrypt
from .conftest import phone_login

KEY_A = "test-key-A-not-real-abcd"     # obviously fake; last four characters: abcd
KEY_B = "test-key-B-not-real-wxyz"
SERVER_KEY = "server-key-not-real-1111"
GEMINI_HOST = "generativelanguage.googleapis.com"


def resp(status, body=None, raw=None):
    req = httpx.Request("POST", "https://provider.invalid/")
    return httpx.Response(status, json=body, request=req) if body is not None else httpx.Response(status, text=raw or "", request=req)


OK_BODY = {"candidates": [{"content": {"parts": [{"text": "ok"}]}}],                                   # Gemini shape
           "output": [{"type": "message", "content": [{"type": "output_text", "text": "ok"}]}]}         # OpenAI shape


class FakeProvider:
    """Replaces app.ai_providers._http_post; records every call, replies with `.reply` (a Response or an exception)."""
    def __init__(self):
        self.calls, self.reply = [], resp(200, OK_BODY)

    def __call__(self, url, *, headers, json):
        self.calls.append({"url": url, "headers": dict(headers), "json": json})
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


@pytest.fixture
def fake_ai(monkeypatch):
    f = FakeProvider()
    monkeypatch.setattr(ai_providers, "_http_post", f)
    return f


def put(client, h, provider="gemini", model="", key=KEY_A):
    return client.put("/settings/ai", headers=h, json={"provider": provider, "model": model, "key": key})


def code(r):
    return r.json()["detail"]["code"]


def rows():
    with SessionLocal() as db:
        return db.scalar(select(func.count()).select_from(UserAISettings))


def test_key_is_encrypted_at_rest_and_never_in_any_response_or_log(client, auth, fake_ai, caplog):
    r = put(client, auth, key="  " + KEY_A + "  ")          # spaces around the key are trimmed
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["key_last4"] == "abcd" and body["source"] == "user" and body["configured"] is True
    assert body["model"] == DEFAULT_MODELS["gemini"]        # empty model -> provider default
    with SessionLocal() as db:
        row = db.scalar(select(UserAISettings))
        assert row.encrypted_key != KEY_A and KEY_A not in row.encrypted_key
        assert decrypt(row.encrypted_key) == KEY_A          # trimmed, and round-trips
        assert row.key_last4 == "abcd"
        raw_dump = str(db.execute(text("SELECT * FROM user_ai_settings")).all())
    assert KEY_A not in raw_dump                            # not stored in clear anywhere in the table
    with caplog.at_level("DEBUG"):
        t = client.post("/settings/ai/test", headers=auth)
    assert t.status_code == 200 and t.json()["ok"] is True
    for label, blob in [("PUT", r.text), ("GET", client.get("/settings/ai", headers=auth).text), ("TEST", t.text), ("logs", caplog.text)]:
        assert KEY_A not in blob, label
    call = fake_ai.calls[0]
    assert call["headers"]["x-goog-api-key"] == KEY_A       # the key goes to the provider in a header ...
    assert KEY_A not in call["url"]                         # ... never in the URL (which httpx would log)


def test_get_when_nothing_is_configured(client, auth):
    g = client.get("/settings/ai", headers=auth).json()
    assert g["source"] == "none" and g["configured"] is False and g["key_last4"] is None
    assert g["provider"] == "gemini" and g["model"] == DEFAULT_MODELS["gemini"] and g["default_models"] == DEFAULT_MODELS
    assert "key" not in g and "encrypted_key" not in g


def test_empty_key_keeps_old_key_and_never_moves_a_key_to_another_provider(client, auth):
    assert code(client.put("/settings/ai", headers=auth, json={"provider": "gemini", "key": ""})) == "key_required"   # nothing to keep
    put(client, auth, model="my-model-1")
    r = client.put("/settings/ai", headers=auth, json={"provider": "gemini", "model": "my-model-2", "key": "   "})
    assert r.status_code == 200 and r.json()["model"] == "my-model-2" and r.json()["key_last4"] == "abcd"
    with SessionLocal() as db:
        assert decrypt(db.scalar(select(UserAISettings)).encrypted_key) == KEY_A     # old key still there
    r = client.put("/settings/ai", headers=auth, json={"provider": "openai", "key": ""})
    assert r.status_code == 422 and code(r) == "key_required"                        # would send a Gemini key to OpenAI
    assert client.get("/settings/ai", headers=auth).json()["provider"] == "gemini"   # unchanged
    r = put(client, auth, provider="openai", key=KEY_B)                              # a new key switches provider fine
    assert r.json()["provider"] == "openai" and r.json()["key_last4"] == "wxyz"


def test_invalid_input_is_rejected_and_never_echoed(client, auth):
    bad = [{"provider": "claude", "key": KEY_A}, {"provider": "", "key": KEY_A}, {"key": KEY_A},
           {"provider": "gemini", "model": "a/b", "key": KEY_A}, {"provider": "gemini", "model": "m?x=1", "key": KEY_A},
           {"provider": "gemini", "key": "short"}, {"provider": "gemini", "key": "has a space inside-1234"},
           {"provider": "gemini", "key": "x" * 600}]
    for b in bad:
        r = client.put("/settings/ai", headers=auth, json=b)
        assert r.status_code == 422 and code(r) == "validation_error" and r.json()["detail"]["ta"], b
        assert KEY_A not in r.text and "has a space" not in r.text
    assert rows() == 0


def test_fallback_order_user_key_then_server_default_then_error(client, auth, fake_ai, monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "default_ai_provider", "openai")
    monkeypatch.setattr(s, "default_ai_api_key", SERVER_KEY)
    g = client.get("/settings/ai", headers=auth).json()                      # 2) server default
    assert g["source"] == "server" and g["configured"] and g["provider"] == "openai" and g["key_last4"] is None
    assert SERVER_KEY not in json.dumps(g)
    t = client.post("/settings/ai/test", headers=auth).json()
    assert t["source"] == "server" and t["provider"] == "openai"
    call = fake_ai.calls[-1]
    assert call["url"] == "https://api.openai.com/v1/responses" and call["headers"]["Authorization"] == "Bearer " + SERVER_KEY
    put(client, auth, provider="gemini", key=KEY_A)                          # 1) the user's own key wins
    assert client.get("/settings/ai", headers=auth).json()["source"] == "user"
    assert client.post("/settings/ai/test", headers=auth).json()["source"] == "user"
    call = fake_ai.calls[-1]
    assert GEMINI_HOST in call["url"] and call["headers"]["x-goog-api-key"] == KEY_A
    with SessionLocal() as db:                                               # the reusable helper itself
        creds = resolve_ai(db, db.scalar(select(User)))
        assert (creds.source, creds.provider, creds.api_key) == ("user", "gemini", KEY_A)
        assert KEY_A not in repr(creds)                                      # a stray log of the object can't leak the key
    d = client.delete("/settings/ai", headers=auth).json()                   # removing the user's key -> server default again
    assert d["source"] == "server" and d["provider"] == "openai" and rows() == 0
    monkeypatch.setattr(s, "default_ai_api_key", "")                         # 3) nothing left
    assert client.get("/settings/ai", headers=auth).json()["source"] == "none"
    r = client.post("/settings/ai/test", headers=auth)
    assert r.status_code == 400 and code(r) == "ai_not_configured"
    assert r.json()["detail"]["en"] and r.json()["detail"]["ta"]


def test_server_default_with_unknown_provider_is_ignored(client, auth, monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "default_ai_provider", "skynet")
    monkeypatch.setattr(s, "default_ai_api_key", SERVER_KEY)
    assert client.get("/settings/ai", headers=auth).json()["source"] == "none"
    assert code(client.post("/settings/ai/test", headers=auth)) == "ai_not_configured"


def test_users_are_isolated(client, fake_ai):
    a, b = phone_login(client, "9876543210"), phone_login(client, "9123456789")
    put(client, a, key=KEY_A)
    gb = client.get("/settings/ai", headers=b).json()
    assert gb["source"] == "none" and gb["key_last4"] is None
    assert code(client.post("/settings/ai/test", headers=b)) == "ai_not_configured"       # B cannot use A's key
    assert fake_ai.calls == []
    put(client, b, provider="openai", key=KEY_B)
    assert client.get("/settings/ai", headers=a).json()["key_last4"] == "abcd"
    assert client.get("/settings/ai", headers=b).json()["key_last4"] == "wxyz"
    client.post("/settings/ai/test", headers=a)
    client.post("/settings/ai/test", headers=b)
    assert fake_ai.calls[0]["headers"]["x-goog-api-key"] == KEY_A                          # each call used its owner's key
    assert fake_ai.calls[1]["headers"]["Authorization"] == "Bearer " + KEY_B
    client.delete("/settings/ai", headers=b)                                               # B's delete leaves A alone
    assert client.get("/settings/ai", headers=a).json()["source"] == "user"
    assert rows() == 1


def test_success_request_shapes(client, auth, fake_ai):
    put(client, auth, provider="gemini", model="gemini-test-model", key=KEY_A)
    t = client.post("/settings/ai/test", headers=auth)
    assert t.json() == {"ok": True, "provider": "gemini", "model": "gemini-test-model", "source": "user"}
    c = fake_ai.calls[-1]
    assert c["url"] == f"https://{GEMINI_HOST}/v1beta/models/gemini-test-model:generateContent"
    assert c["json"]["contents"][0]["parts"][0]["text"] and c["json"]["generationConfig"]["maxOutputTokens"] > 0
    put(client, auth, provider="openai", model="oa-test-model", key=KEY_B)
    client.post("/settings/ai/test", headers=auth)
    c = fake_ai.calls[-1]
    assert c["url"] == "https://api.openai.com/v1/responses" and c["headers"]["Authorization"] == "Bearer " + KEY_B
    assert c["json"]["model"] == "oa-test-model" and c["json"]["store"] is False and c["json"]["input"]


def test_provider_text_parsing():
    g = ai_providers.make_provider("gemini", "m", KEY_A)
    o = ai_providers.make_provider("openai", "m", KEY_A)
    assert g._text(OK_BODY) == "ok" and o._text(OK_BODY) == "ok"
    assert g._text({}) == "" and o._text({}) == "" and g._text({"candidates": [{}]}) == ""     # no visible text is not an error
    assert KEY_A not in repr(g) and KEY_A not in repr(o)


GENERIC = [(401, "invalid_key", 400), (403, "invalid_key", 400), (429, "quota_exceeded", 429), (402, "quota_exceeded", 429),
           (404, "model_not_found", 400), (500, "provider_error", 502), (503, "provider_error", 502), (400, "provider_error", 502)]


@pytest.mark.parametrize("provider", ["gemini", "openai"])
def test_provider_errors_map_to_the_right_codes(client, auth, fake_ai, provider):
    put(client, auth, provider=provider, key=KEY_A)
    for status, expected, http in GENERIC:
        # the provider's error text may echo (part of) the key: it must never reach our response
        fake_ai.reply = resp(status, {"error": {"message": f"Incorrect API key provided: {KEY_A}", "code": "x"}})
        r = client.post("/settings/ai/test", headers=auth)
        assert (r.status_code, code(r)) == (http, expected), (provider, status)
        assert r.json()["detail"]["en"] and r.json()["detail"]["ta"] and KEY_A not in r.text
    fake_ai.reply = resp(200, raw="<html>not json</html>")
    assert code(client.post("/settings/ai/test", headers=auth)) == "provider_error"
    fake_ai.reply = httpx.ReadTimeout("slow")
    r = client.post("/settings/ai/test", headers=auth)
    assert (r.status_code, code(r)) == (504, "timeout")
    for exc in (httpx.ConnectError("boom"), httpx.ConnectTimeout("slow connect")):
        fake_ai.reply = exc
        r = client.post("/settings/ai/test", headers=auth)
        assert code(r) == ("timeout" if isinstance(exc, httpx.TimeoutException) else "network_error")
    fake_ai.reply = httpx.ConnectError("boom")
    assert client.post("/settings/ai/test", headers=auth).status_code == 502


def test_gemini_bad_key_answered_with_400(client, auth, fake_ai):
    put(client, auth, provider="gemini", key=KEY_A)
    fake_ai.reply = resp(400, {"error": {"code": 400, "message": "API key not valid. Please pass a valid API key.",
                                          "status": "INVALID_ARGUMENT", "details": [{"reason": "API_KEY_INVALID"}]}})
    assert code(client.post("/settings/ai/test", headers=auth)) == "invalid_key"


def test_invalid_key_never_uses_401_so_the_ui_does_not_sign_you_out(client, auth, fake_ai):
    put(client, auth, key=KEY_A)
    fake_ai.reply = resp(401, {"error": {}})
    assert client.post("/settings/ai/test", headers=auth).status_code != 401


def test_encryption_key_empty_or_invalid_gives_clear_bilingual_error(client, auth, monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "encryption_key", "")
    r = put(client, auth)
    assert r.status_code == 503 and code(r) == "encryption_not_configured" and "ENCRYPTION_KEY" in r.json()["detail"]["en"] and r.json()["detail"]["ta"]
    monkeypatch.setattr(s, "encryption_key", "this-is-not-a-fernet-key")
    r = put(client, auth)
    assert r.status_code == 503 and code(r) == "encryption_key_invalid" and r.json()["detail"]["ta"]
    assert rows() == 0 and KEY_A not in r.text                                            # nothing saved, nothing echoed
    assert client.get("/settings/ai", headers=auth).status_code == 200                    # reading status needs no decryption


def test_key_saved_under_another_encryption_key_fails_loudly_instead_of_using_the_server_key(client, auth, fake_ai, monkeypatch):
    put(client, auth)
    s = get_settings()
    monkeypatch.setattr(s, "encryption_key", Fernet.generate_key().decode())              # ENCRYPTION_KEY was changed
    monkeypatch.setattr(s, "default_ai_api_key", SERVER_KEY)
    r = client.post("/settings/ai/test", headers=auth)
    assert r.status_code == 409 and code(r) == "key_unreadable" and fake_ai.calls == []
    assert put(client, auth, key=KEY_B).status_code == 200                                # saving again repairs it
    assert client.post("/settings/ai/test", headers=auth).json()["source"] == "user"


def test_login_required(client):
    for method, path in [("get", "/settings/ai"), ("put", "/settings/ai"), ("delete", "/settings/ai"), ("post", "/settings/ai/test")]:
        assert getattr(client, method)(path).status_code == 401, (method, path)
        assert getattr(client, method)(path, headers={"Authorization": "Bearer junk"}).status_code == 401


def test_test_endpoint_is_rate_limited(client, auth, fake_ai, monkeypatch):
    put(client, auth)
    monkeypatch.setattr(get_settings(), "ai_test_rate_limit", "2/minute")
    assert [client.post("/settings/ai/test", headers=auth).status_code for _ in range(2)] == [200, 200]
    r = client.post("/settings/ai/test", headers=auth)
    assert r.status_code == 429 and code(r) == "rate_limited" and r.json()["detail"]["ta"]
    assert len(fake_ai.calls) == 2                                                        # the 3rd never reached the provider


def test_deleting_the_account_deletes_the_saved_key(client, auth):
    put(client, auth)
    assert rows() == 1
    assert client.delete("/me", headers=auth).status_code == 200
    assert rows() == 0


def test_frontend_has_the_ai_card_and_never_stores_a_key_in_the_browser(client):
    html = client.get("/").text
    assert '<input id="aik" type="password"' in html and 'id="aip"' in html and 'id="aim"' in html
    assert set(re.findall(r"localStorage\.\w+Item\('([^']+)'", html)) <= {"nira_tok"}     # only the session token
    assert "sessionStorage" not in html and "indexedDB" not in html
