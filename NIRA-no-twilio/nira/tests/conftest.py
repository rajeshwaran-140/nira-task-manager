import os, pathlib
from cryptography.fernet import Fernet
os.environ.update(APP_ENV="dev", DATABASE_URL="sqlite:///./test_nira.db", SECRET_KEY="test-secret-test-secret-test-secret!",
                  AUTH_RATE_LIMIT="1000/minute", OTP_RESEND_COOLDOWN_SECONDS="0", OTP_MAX_SENDS_PER_HOUR="1000",
                  SMS_PROVIDER="console", SMTP_HOST="",
                  # Part 3A: a throw-away Fernet key made fresh on every run (no key is stored in the repo), no server-default
                  # AI key leaking in from a developer's real .env, and a test-endpoint limit that won't trip other tests.
                  ENCRYPTION_KEY=Fernet.generate_key().decode(), DEFAULT_AI_API_KEY="", DEFAULT_AI_PROVIDER="gemini",
                  DEFAULT_AI_MODEL="", AI_TEST_RATE_LIMIT="1000/minute",
                  # voice assistant: many /assistant/command calls per test run must not trip the per-IP limit
                  CHAT_RATE_LIMIT="1000/minute",
                  # scheduler + Twilio: never start the background job in tests, never inherit real Twilio settings
                  SCHEDULER_ENABLED="false", TWILIO_ACCOUNT_SID="", TWILIO_AUTH_TOKEN="", TWILIO_PHONE_NUMBER="",
                  NIRA_USER_PHONE_NUMBER="", TEST_CALL_RATE_LIMIT="1000/minute",
                  # 4B: the tests expect these exact values, so a developer's own .env must never change them
                  APP_BASE_URL="http://localhost:8000", TWILIO_STATUS_CALLBACK_URL="",
                  CALL_MAX_RETRIES="2", CALL_RETRY_DELAY_MINUTES="10")
import pytest
from fastapi.testclient import TestClient
from app.database import Base, engine
from app.main import app


@pytest.fixture(autouse=True)
def fresh_db():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield


@pytest.fixture(autouse=True)
def no_real_twilio(monkeypatch):
    """Safety net for EVERY test: the real Twilio client can never be built, so no test can place a real call."""
    from app import calls

    def _blocked():
        raise AssertionError("A test tried to use the real Twilio client")
    monkeypatch.setattr(calls, "get_twilio_client", _blocked)


@pytest.fixture
def client():
    return TestClient(app, raise_server_exceptions=False)


def phone_login(client, phone="9876543210"):
    code = client.post("/auth/phone/request-otp", json={"phone": phone}).json()["dev_otp"]
    r = client.post("/auth/phone/verify", json={"phone": phone, "code": code})
    assert r.status_code == 200, r.text
    return {"Authorization": "Bearer " + r.json()["access_token"]}


@pytest.fixture
def auth(client):
    return phone_login(client)
