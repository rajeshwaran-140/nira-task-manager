"""One-time codes: 6-digit OTPs and magic-link tokens (hashed, expiring, attempt-limited)."""
import hmac, secrets
from datetime import timedelta
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session
from .config import get_settings
from .errors import AppError
from .models import AuthCode, utcnow
from .security import keyed_hash


def issue_otp(db: Session, purpose: str, target: str, user_id: int | None = None) -> str:
    s, now = get_settings(), utcnow()
    last = db.scalar(select(AuthCode).where(AuthCode.purpose == purpose, AuthCode.target == target)
                     .order_by(AuthCode.id.desc()).limit(1))
    if last and (now - last.created_at).total_seconds() < s.otp_resend_cooldown_seconds:
        raise AppError("otp_cooldown")
    sent = db.scalar(select(func.count()).select_from(AuthCode).where(
        AuthCode.purpose == purpose, AuthCode.target == target, AuthCode.created_at > now - timedelta(hours=1)))
    if sent >= s.otp_max_sends_per_hour:
        raise AppError("otp_too_many")
    db.execute(update(AuthCode).where(AuthCode.purpose == purpose, AuthCode.target == target,
                                      AuthCode.used == False).values(used=True))  # noqa: E712
    code = f"{secrets.randbelow(10**6):06d}"
    db.add(AuthCode(purpose=purpose, target=target, user_id=user_id,
                    code_hash=keyed_hash(purpose, target, code),
                    expires_at=now + timedelta(seconds=s.otp_ttl_seconds)))
    db.commit()
    return code


def check_otp(db: Session, purpose: str, target: str, code: str, user_id: int | None = None) -> None:
    s, now = get_settings(), utcnow()
    row = db.scalar(select(AuthCode).where(AuthCode.purpose == purpose, AuthCode.target == target,
                                           AuthCode.used == False, AuthCode.expires_at > now)  # noqa: E712
                    .order_by(AuthCode.id.desc()).limit(1))
    if not row or (user_id is not None and row.user_id != user_id):
        raise AppError("invalid_otp")
    if row.attempts >= s.otp_max_attempts:
        raise AppError("otp_locked")
    if not hmac.compare_digest(row.code_hash, keyed_hash(purpose, target, code)):
        row.attempts += 1
        db.commit()
        raise AppError("invalid_otp")
    row.used = True
    db.commit()


def issue_magic(db: Session, email: str) -> str:
    s, now = get_settings(), utcnow()
    last = db.scalar(select(AuthCode).where(AuthCode.purpose == "magic", AuthCode.target == email)
                     .order_by(AuthCode.id.desc()).limit(1))
    if last and (now - last.created_at).total_seconds() < s.otp_resend_cooldown_seconds:
        raise AppError("otp_cooldown")
    token = secrets.token_urlsafe(32)
    db.add(AuthCode(purpose="magic", target=email, code_hash=keyed_hash("magic", token),
                    expires_at=now + timedelta(minutes=15)))
    db.commit()
    return token


def consume_magic(db: Session, token: str) -> str:
    """Returns the email the token was issued for."""
    row = db.scalar(select(AuthCode).where(AuthCode.purpose == "magic", AuthCode.used == False,  # noqa: E712
                                           AuthCode.code_hash == keyed_hash("magic", token),
                                           AuthCode.expires_at > utcnow()))
    if not row:
        raise AppError("invalid_link")
    row.used = True
    db.commit()
    return row.target
