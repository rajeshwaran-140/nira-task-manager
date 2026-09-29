from datetime import datetime
from fastapi import APIRouter, Depends, Request
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from ..codes import check_otp, issue_otp
from ..database import get_db
from ..deps import current_user
from ..errors import AppError
from ..models import AuthCode, User, utcnow
from ..notify import send_sms
from ..config import get_settings
from ..ratelimit import auth_limit, limiter
from ..schemas import PhoneIn, PhoneVerifyIn, ProfileUpdate, SettingsIn, SettingsOut, UserOut
from ..timeutil import zone
from .auth import user_out

router = APIRouter(tags=["me"])


@router.get("/me", response_model=UserOut)
def get_me(user: User = Depends(current_user)):
    return user_out(user)


@router.patch("/me", response_model=UserOut)
def patch_me(body: ProfileUpdate, user: User = Depends(current_user), db: Session = Depends(get_db)):
    user.name = body.name.strip()
    db.commit()
    return user_out(user)


@router.delete("/me")
def delete_me(user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Deletes the account and ALL the user's data."""
    db.execute(delete(AuthCode).where(AuthCode.user_id == user.id))
    db.delete(user)
    db.commit()
    return {"ok": True}


@router.post("/me/phone/request-otp")
@limiter.limit(auth_limit)
def phone_request(request: Request, body: PhoneIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    other = db.scalar(select(User.id).where(User.phone == body.phone, User.id != user.id))
    if other:
        raise AppError("phone_taken")
    code = issue_otp(db, "phone_verify", body.phone, user_id=user.id)
    send_sms(body.phone, f"NIRA: code {code} to verify your number. Valid 5 minutes. / உங்கள் எண்ணை சரிபார்க்க OTP {code}.")
    out = {"sent": True}
    if not get_settings().is_prod and get_settings().sms_provider == "console":
        out["dev_otp"] = code
    return out


@router.post("/me/phone/verify", response_model=UserOut)
@limiter.limit(auth_limit)
def phone_verify(request: Request, body: PhoneVerifyIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    check_otp(db, "phone_verify", body.phone, body.code, user_id=user.id)
    user.phone, user.phone_verified = body.phone, True
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise AppError("phone_taken")
    return user_out(user)


def settings_out(user: User) -> SettingsOut:
    s = user.settings
    return SettingsOut(lead_days=s.lead_days, call_time=s.call_time, language=s.language, timezone=s.timezone,
                       calls_enabled=s.calls_enabled, consent_to_call=s.consent_to_call,
                       speak_sensitive=s.speak_sensitive, ai_voice_enabled=s.ai_voice_enabled,
                       phone=user.phone, phone_verified=user.phone_verified)


@router.get("/settings", response_model=SettingsOut)
def get_settings_ep(user: User = Depends(current_user)):
    return settings_out(user)


@router.put("/settings", response_model=SettingsOut)
def put_settings(body: SettingsIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    s, data = user.settings, body.model_dump(exclude_unset=True, exclude_none=True)
    if "timezone" in data:
        try:
            zone_ok = zone(data["timezone"]).key == data["timezone"]
        except Exception:
            zone_ok = False
        if not zone_ok:
            raise AppError("validation_error", errors=[{"field": "timezone", "msg": "unknown timezone"}])
    if data.get("consent_to_call") is True and not s.consent_to_call:
        s.consent_at = utcnow()
    wants_calls = data.get("calls_enabled", s.calls_enabled)
    withdrawing = data.get("consent_to_call") is False
    if wants_calls and not withdrawing:
        if not user.phone_verified:
            raise AppError("phone_not_verified")
        if not (s.consent_to_call or data.get("consent_to_call")):
            raise AppError("consent_required")
    for k, v in data.items():
        setattr(s, k, v)
    if withdrawing:                    # withdrawing consent always switches calls off
        s.calls_enabled = False
    db.commit()
    return settings_out(user)
