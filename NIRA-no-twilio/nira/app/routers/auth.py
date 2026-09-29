from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from ..codes import check_otp, consume_magic, issue_magic, issue_otp
from ..config import get_settings
from ..database import get_db
from ..deps import current_user
from ..errors import AppError
from ..models import User, UserSettings
from ..notify import send_email, send_sms
from ..ratelimit import auth_limit, limiter
from ..schemas import (AuthOut, LoginIn, MagicRequestIn, MagicVerifyIn, PhoneIn, PhoneVerifyIn,
                       RegisterIn, UserOut)
from ..security import hash_password, make_token, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])


def user_out(u: User) -> UserOut:
    return UserOut(id=u.id, email=u.email, phone=u.phone, phone_verified=u.phone_verified,
                   name=u.name, needs_phone=not u.phone_verified)


def auth_out(u: User) -> AuthOut:
    return AuthOut(access_token=make_token(u), user=user_out(u))


def new_user(db: Session, **kw) -> User:
    u = User(**kw)
    u.settings = UserSettings(timezone=get_settings().default_timezone)
    db.add(u)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise AppError("email_taken" if kw.get("email") else "phone_taken")
    return u


@router.post("/phone/request-otp")
@limiter.limit(auth_limit)
def phone_request_otp(request: Request, body: PhoneIn, db: Session = Depends(get_db)):
    code = issue_otp(db, "phone_login", body.phone)
    send_sms(body.phone, f"NIRA: your login code is {code}. Valid 5 minutes. Do not share it. / உங்கள் NIRA OTP {code}.")
    out = {"sent": True}
    if not get_settings().is_prod and get_settings().sms_provider == "console":
        out["dev_otp"] = code  # dev convenience only
    return out


@router.post("/phone/verify", response_model=AuthOut)
@limiter.limit(auth_limit)
def phone_verify(request: Request, body: PhoneVerifyIn, db: Session = Depends(get_db)):
    check_otp(db, "phone_login", body.phone, body.code)
    u = db.scalar(select(User).where(User.phone == body.phone))
    if not u:
        u = new_user(db, phone=body.phone, phone_verified=True, name=(body.name or "").strip())
    elif not u.is_active:
        raise AppError("account_disabled")
    return auth_out(u)


@router.post("/email/register", response_model=AuthOut)
@limiter.limit(auth_limit)
def email_register(request: Request, body: RegisterIn, db: Session = Depends(get_db)):
    if db.scalar(select(User.id).where(User.email == body.email)):
        raise AppError("email_taken")
    u = new_user(db, email=body.email, password_hash=hash_password(body.password), name=body.name.strip())
    return auth_out(u)


@router.post("/email/login", response_model=AuthOut)
@limiter.limit(auth_limit)
def email_login(request: Request, body: LoginIn, db: Session = Depends(get_db)):
    u = db.scalar(select(User).where(User.email == body.email))
    if not verify_password(body.password, u.password_hash if u else None):
        raise AppError("invalid_credentials")
    if not u.is_active:
        raise AppError("account_disabled")
    return auth_out(u)


@router.post("/email/magic/request")
@limiter.limit(auth_limit)
def magic_request(request: Request, body: MagicRequestIn, db: Session = Depends(get_db)):
    token = issue_magic(db, body.email)
    base = get_settings().app_base_url.rstrip("/")
    send_email(body.email, "Your NIRA sign-in link / உங்கள் NIRA உள்நுழைவு இணைப்பு",
               f"Tap to sign in (valid 15 minutes):\n{base}/?magic={token}\n\n"
               f"உள்நுழைய இதைத் தட்டவும் (15 நிமிடம் செல்லுபடி):\n{base}/?magic={token}\n\n"
               "If you did not request this, ignore this email.")
    return {"sent": True}  # same answer whether or not the email exists


@router.post("/email/magic/verify", response_model=AuthOut)
@limiter.limit(auth_limit)
def magic_verify(request: Request, body: MagicVerifyIn, db: Session = Depends(get_db)):
    email = consume_magic(db, body.token)
    u = db.scalar(select(User).where(User.email == email))
    if not u:
        u = new_user(db, email=email, email_verified=True)
    else:
        if not u.email_verified:
            # Someone may have pre-registered this address with a password they chose.
            # Proving mailbox ownership wipes that password.
            u.password_hash = None
            u.token_version += 1
            u.email_verified = True
            db.commit()
        if not u.is_active:
            raise AppError("account_disabled")
    return auth_out(u)


@router.post("/logout-all")
def logout_all(user: User = Depends(current_user), db: Session = Depends(get_db)):
    user.token_version += 1
    db.commit()
    return {"ok": True}
