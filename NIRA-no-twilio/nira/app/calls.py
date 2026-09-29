"""Outbound reminder calls through Twilio Voice.

* Credentials come only from server settings (env / .env). They are never returned by an endpoint or logged.
* `get_twilio_client()` is the single place a real Twilio client is made; tests replace it with a fake, so a test
  can never place a real call.
* 4B: `create_call` can ask Twilio for status callbacks, `fetch_call` asks Twilio for a call's status when no callback
  arrived, and `valid_signature` checks the X-Twilio-Signature of the callback. Results, retries and history live in
  app/call_results.py, app/scheduler.py and app/routers/calls.py.
"""
import base64
import hashlib
import hmac
import logging
import re
from urllib.parse import urlparse
from xml.sax.saxutils import escape

import httpx

from .config import get_settings
from .errors import AppError

log = logging.getLogger("nira")

E164 = re.compile(r"\+[1-9]\d{7,14}")


def is_e164(number: str | None) -> bool:
    """Strict E.164: '+', country code (no leading 0), 8-15 digits in total. No spaces or dashes. e.g. +919876543210"""
    return bool(number) and E164.fullmatch(number) is not None


def mask_phone(number: str) -> str:
    return "•" * max(len(number) - 4, 0) + number[-4:]


def call_config_problems() -> list[str]:
    """Names of settings that are missing or invalid. Empty list = calls can be placed. Never includes values."""
    s = get_settings()
    problems = []
    if not s.twilio_account_sid.strip():
        problems.append("TWILIO_ACCOUNT_SID")
    if not s.twilio_auth_token.strip():
        problems.append("TWILIO_AUTH_TOKEN")
    if not is_e164(s.twilio_phone_number.strip()):
        problems.append("TWILIO_PHONE_NUMBER (must be E.164, e.g. +14155550100)")
    if not is_e164(s.nira_user_phone_number.strip()):
        problems.append("NIRA_USER_PHONE_NUMBER (must be E.164, e.g. +919876543210)")
    elif s.nira_user_phone_number.strip() == s.twilio_phone_number.strip():
        problems.append("NIRA_USER_PHONE_NUMBER (must differ from TWILIO_PHONE_NUMBER)")
    return problems


def calls_configured() -> bool:
    return not call_config_problems()


class TwilioCallError(Exception):
    """Placing the call failed. The message never contains credentials or the phone numbers."""


class TwilioClient:
    """Minimal Twilio Voice client over HTTPS (httpx is already a dependency; no extra package needed)."""

    def __init__(self, account_sid: str, auth_token: str, timeout: float = 10.0):
        self._sid, self._token, self._timeout = account_sid, auth_token, timeout

    def create_call(self, to: str, from_: str, twiml: str, status_callback: str | None = None) -> str:
        """Starts one outbound call that speaks `twiml`. Returns Twilio's call SID."""
        data: dict = {"To": to, "From": from_, "Twiml": twiml}
        if status_callback:
            data.update(StatusCallback=status_callback, StatusCallbackMethod="POST",
                        StatusCallbackEvent=["initiated", "ringing", "answered", "completed"])
        try:
            r = httpx.post(f"https://api.twilio.com/2010-04-01/Accounts/{self._sid}/Calls.json",
                           auth=(self._sid, self._token), data=data, timeout=self._timeout)
        except httpx.HTTPError as e:
            raise TwilioCallError(f"network error ({type(e).__name__})") from None
        if r.status_code >= 300:
            raise TwilioCallError(f"Twilio returned HTTP {r.status_code}")
        try:
            return str(r.json().get("sid", ""))
        except ValueError:
            return ""

    def fetch_call(self, call_sid: str) -> tuple[str, int | None]:
        """Asks Twilio for a call's current (status, duration seconds). Used when a status callback never arrived."""
        try:
            r = httpx.get(f"https://api.twilio.com/2010-04-01/Accounts/{self._sid}/Calls/{call_sid}.json",
                          auth=(self._sid, self._token), timeout=self._timeout)
        except httpx.HTTPError as e:
            raise TwilioCallError(f"network error ({type(e).__name__})") from None
        if r.status_code >= 300:
            raise TwilioCallError(f"Twilio returned HTTP {r.status_code}")
        try:
            j = r.json()
            dur = j.get("duration")
            return str(j.get("status", "")), int(dur) if dur not in (None, "") else None
        except (ValueError, TypeError, AttributeError):
            raise TwilioCallError("unreadable Twilio response") from None


def get_twilio_client() -> TwilioClient:
    s = get_settings()
    return TwilioClient(s.twilio_account_sid.strip(), s.twilio_auth_token.strip())


def _twiml(text: str) -> str:
    return f'<Response><Say voice="alice" language="en-IN">{escape(text)}</Say></Response>'


def reminder_twiml(title: str, when_text: str) -> str:
    return _twiml(f"Hello, this is NIRA. Your reminder: {title}, due {when_text}. Please do not forget it. Thank you.")


def test_twiml() -> str:
    return _twiml("Hello, this is a test call from NIRA. Your phone number is set up correctly. Goodbye.")


def place_call(twiml: str) -> str:
    """Calls NIRA_USER_PHONE_NUMBER from TWILIO_PHONE_NUMBER. Raises AppError('twilio_not_configured') when the
    settings are incomplete and TwilioCallError when Twilio refuses. The destination is always the configured
    number, never a value from a request."""
    if not calls_configured():
        raise AppError("twilio_not_configured")
    s = get_settings()
    client, to, from_, cb = get_twilio_client(), s.nira_user_phone_number.strip(), s.twilio_phone_number.strip(), status_callback_url()
    if cb:
        return client.create_call(to, from_, twiml, status_callback=cb)
    return client.create_call(to, from_, twiml)


def status_callback_url() -> str | None:
    """Public URL Twilio posts call results to, or None when there is none (then results are polled instead).
    Explicit TWILIO_STATUS_CALLBACK_URL wins; otherwise APP_BASE_URL + /twilio/status, only if that is public https."""
    s = get_settings()
    explicit = s.twilio_status_callback_url.strip()
    if explicit:
        return explicit
    base = s.app_base_url.strip().rstrip("/")
    host = urlparse(base).hostname or ""
    if not base.startswith("https://") or host == "localhost" or host.startswith("127.") or host == "::1":
        return None
    return base + "/twilio/status"


def valid_signature(url: str | None, params: list[tuple[str, str]], signature: str) -> bool:
    """Checks Twilio's X-Twilio-Signature: base64(HMAC-SHA1(auth token, url + sorted key+value pairs))."""
    token = get_settings().twilio_auth_token.strip()
    if not (url and token and signature):
        return False
    data = url + "".join(k + v for k, v in sorted(params))
    expected = base64.b64encode(hmac.new(token.encode(), data.encode(), hashlib.sha1).digest()).decode()
    # Compare as bytes: comparing str values raises TypeError on a non-ASCII header, which would turn a forged request into a 500.
    return hmac.compare_digest(expected.encode(), signature.encode("utf-8", "replace"))


def is_call_owner(user) -> bool:
    """Only the account whose VERIFIED phone is NIRA_USER_PHONE_NUMBER may trigger calls to that number."""
    n = get_settings().nira_user_phone_number.strip()
    return bool(n) and user.phone_verified and user.phone == n
