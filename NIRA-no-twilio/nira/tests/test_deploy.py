"""5A: the production start-up check (app/config.py). Pure settings objects: no database, no network, no Twilio."""
import pytest
from cryptography.fernet import Fernet

from app.config import Settings


def prod(**over):
    base = dict(app_env="prod", secret_key="k" * 40, encryption_key=Fernet.generate_key().decode(), sms_provider="twilio",
                twilio_account_sid="ACx", twilio_auth_token="tok", twilio_from="+14155550100",
                app_base_url="https://nira.example.com")
    base.update(over)
    return Settings(**base)


def problems(s: Settings) -> str:
    with pytest.raises(RuntimeError) as e:
        s.check_production()
    return str(e.value)


def test_a_complete_production_config_starts():
    prod().check_production()


def test_sms_off_needs_no_twilio_settings():
    prod(sms_provider="off", twilio_account_sid="", twilio_auth_token="", twilio_from="").check_production()


def test_dev_is_never_blocked():
    Settings(app_env="dev", sms_provider="console", secret_key="dev-secret-change-me").check_production()


@pytest.mark.parametrize("over,needle", [
    ({"secret_key": "change-me"}, "SECRET_KEY"),
    ({"secret_key": "short"}, "SECRET_KEY"),
    ({"encryption_key": ""}, "ENCRYPTION_KEY"),
    ({"encryption_key": "not-a-fernet-key"}, "ENCRYPTION_KEY"),
    ({"sms_provider": "console"}, "SMS_PROVIDER"),
    ({"sms_provider": ""}, "SMS_PROVIDER"),            # an empty value used to slip through and break OTP login
    ({"twilio_from": ""}, "TWILIO_FROM"),
    ({"twilio_auth_token": " "}, "TWILIO_AUTH_TOKEN"),
    ({"app_base_url": "http://localhost:8000"}, "APP_BASE_URL"),
    ({"app_base_url": "http://nira.example.com"}, "APP_BASE_URL"),
])
def test_unsafe_production_config_is_refused(over, needle):
    assert needle in problems(prod(**over))


def test_error_message_never_contains_secret_values():
    msg = problems(prod(secret_key="weakweak", twilio_auth_token="", twilio_account_sid="ACsecretvalue"))
    assert "ACsecretvalue" not in msg and "weakweak" not in msg
