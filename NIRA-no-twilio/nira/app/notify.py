"""Sending OTP SMS and magic-link email. Never logs anything in prod."""
import logging, smtplib
from email.message import EmailMessage
import httpx
from .config import get_settings
from .errors import AppError

log = logging.getLogger("nira")


def send_sms(to: str, body: str):
    s = get_settings()
    if s.sms_provider == "console":
        log.warning("[DEV SMS to %s] %s", to, body)
        return
    if s.sms_provider == "twilio":
        try:
            r = httpx.post(
                f"https://api.twilio.com/2010-04-01/Accounts/{s.twilio_account_sid}/Messages.json",
                auth=(s.twilio_account_sid, s.twilio_auth_token),
                data={"From": s.twilio_from, "To": to, "Body": body},
                timeout=10,
            )
            if r.status_code >= 300:
                log.error("Twilio SMS failed: HTTP %s", r.status_code)
                raise AppError("sms_failed")
            return
        except httpx.HTTPError:
            log.error("Twilio SMS network error")
            raise AppError("sms_failed")
    raise AppError("sms_failed")


def send_email(to: str, subject: str, body: str):
    s = get_settings()
    if not s.smtp_host:
        if s.is_prod:
            raise AppError("email_failed")
        log.warning("[DEV EMAIL to %s] %s\n%s", to, subject, body)
        return
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = s.smtp_from, to, subject
    msg.set_content(body)
    try:
        with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=10) as smtp:
            smtp.starttls()
            if s.smtp_user:
                smtp.login(s.smtp_user, s.smtp_password)
            smtp.send_message(msg)
    except (smtplib.SMTPException, OSError):
        log.error("SMTP send failed")
        raise AppError("email_failed")
