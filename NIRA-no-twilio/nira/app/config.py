import logging
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict

log = logging.getLogger("nira")
WEAK = {"", "change-me", "dev-secret-change-me"}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    app_env: str = "dev"
    database_url: str = "sqlite:///./nira.db"
    app_base_url: str = "http://localhost:8000"
    cors_origins: str = "http://localhost:8000,http://localhost,https://localhost,capacitor://localhost"
    default_timezone: str = "Asia/Kolkata"
    secret_key: str = "dev-secret-change-me"
    encryption_key: str = ""
    jwt_expire_minutes: int = 43200
    auth_rate_limit: str = "10/minute"
    chat_rate_limit: str = "20/minute"
    ai_test_rate_limit: str = "5/minute"     # POST /settings/ai/test (each call is a real, billable provider call)
    otp_ttl_seconds: int = 300
    otp_max_attempts: int = 5
    otp_resend_cooldown_seconds: int = 30
    otp_max_sends_per_hour: int = 5
    sms_provider: str = "console"
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_from: str = ""
    # Reminder phone calls (Twilio Voice). Account SID / auth token above are shared with SMS; these are call-only.
    # Server-side only: never sent to the browser, never logged.
    twilio_phone_number: str = ""            # the Twilio number calls come FROM (E.164, e.g. +1415...)
    nira_user_phone_number: str = ""         # the number NIRA calls (E.164, e.g. +91XXXXXXXXXX)
    scheduler_enabled: bool = True           # background reminder scheduler (needs the four Twilio settings above)
    scheduler_interval_seconds: int = 60
    call_max_retries: int = 2                # retries AFTER the first attempt (failed / no-answer / busy), so at most 3 calls per reminder
    call_retry_delay_minutes: int = 10       # wait before each retry
    twilio_status_callback_url: str = ""     # public https URL of POST /twilio/status; empty = APP_BASE_URL + /twilio/status when APP_BASE_URL is public https
    test_call_rate_limit: str = "1/minute"   # POST /calls/test places one real call per click
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = "NIRA <no-reply@example.com>"
    # Server-side AI fallback, used only when a user has not saved their own key.
    default_ai_provider: str = "gemini"      # gemini | openai
    default_ai_api_key: str = ""
    default_ai_model: str = ""               # empty = built-in default model for the provider

    @property
    def is_prod(self) -> bool:
        return self.app_env.lower() == "prod"

    @property
    def origins(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    def fernet_key(self) -> bytes | None:
        """The configured Fernet key, or None when ENCRYPTION_KEY is empty.
        Deliberately NO fallback derived from SECRET_KEY: a silent fallback would make every stored user key
        unreadable the day SECRET_KEY changes. Empty/invalid now produces a clear bilingual error instead."""
        key = self.encryption_key.strip()
        return key.encode() if key else None

    def check_production(self):
        if not self.is_prod:
            return
        problems = []
        if self.secret_key in WEAK or len(self.secret_key) < 32:
            problems.append("SECRET_KEY must be a random string of 32+ chars")
        if not self.encryption_key:
            problems.append("ENCRYPTION_KEY must be set (Fernet key)")
        else:
            try:
                from cryptography.fernet import Fernet
                Fernet(self.encryption_key.strip().encode())
            except (ValueError, TypeError):
                problems.append("ENCRYPTION_KEY is not a valid Fernet key")
        if self.sms_provider not in ("twilio", "off"):
            problems.append("SMS_PROVIDER must be 'twilio' or 'off' in prod (console is dev-only; an empty value would break OTP login)")
        elif self.sms_provider == "twilio":
            missing = [n for n, v in (("TWILIO_ACCOUNT_SID", self.twilio_account_sid), ("TWILIO_AUTH_TOKEN", self.twilio_auth_token),
                                      ("TWILIO_FROM", self.twilio_from)) if not v.strip()]
            if missing:
                problems.append("SMS_PROVIDER=twilio needs " + ", ".join(missing))
        # SMS_PROVIDER=off: no SMS and no reminder calls. Sign in with email + password; phone OTP is refused.
        if not self.app_base_url.strip().lower().startswith("https://"):
            problems.append("APP_BASE_URL must be the public https address (magic links and Twilio call callbacks are built from it)")
        if problems:
            raise RuntimeError("Unsafe production config: " + "; ".join(problems))


@lru_cache
def get_settings() -> Settings:
    return Settings()
