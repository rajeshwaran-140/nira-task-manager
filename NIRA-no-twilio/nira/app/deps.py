import jwt
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session
from .database import get_db
from .errors import AppError
from .models import User
from .security import decode_token

bearer = HTTPBearer(auto_error=False)


def current_user(creds: HTTPAuthorizationCredentials | None = Depends(bearer), db: Session = Depends(get_db)) -> User:
    if not creds:
        raise AppError("unauthorized")
    try:
        payload = decode_token(creds.credentials)
        uid = int(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        raise AppError("unauthorized")
    user = db.get(User, uid)
    if not user or user.token_version != payload.get("tv"):
        raise AppError("unauthorized")
    if not user.is_active:
        raise AppError("account_disabled")
    return user
