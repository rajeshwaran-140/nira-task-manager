import hashlib, hmac
from datetime import timedelta
import bcrypt, jwt
from cryptography.fernet import Fernet, InvalidToken
from .config import get_settings
from .errors import AppError
from .models import utcnow

_DUMMY = bcrypt.hashpw(b"dummy", bcrypt.gensalt()).decode()


def hash_password(pw: str) -> str:
    return bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode()


def verify_password(pw: str, hashed: str | None) -> bool:
    """Always burns a bcrypt round so 'no such user' and 'wrong password' take the same time."""
    try:
        ok = bcrypt.checkpw(pw.encode(), (hashed or _DUMMY).encode())
    except ValueError:
        return False
    return ok and hashed is not None


def make_token(user) -> str:
    s = get_settings()
    exp = utcnow() + timedelta(minutes=s.jwt_expire_minutes)
    return jwt.encode({"sub": str(user.id), "tv": user.token_version, "exp": exp}, s.secret_key, algorithm="HS256")


def decode_token(token: str) -> dict:
    return jwt.decode(token, get_settings().secret_key, algorithms=["HS256"])


def keyed_hash(*parts: str) -> str:
    return hmac.new(get_settings().secret_key.encode(), "|".join(parts).encode(), hashlib.sha256).hexdigest()


# ---- Encryption of per-user API keys ----
def _fernet() -> Fernet:
    """Strict: empty or malformed ENCRYPTION_KEY raises a bilingual AppError instead of falling back."""
    key = get_settings().fernet_key()
    if not key:
        raise AppError("encryption_not_configured")
    try:
        return Fernet(key)
    except (ValueError, TypeError):
        raise AppError("encryption_key_invalid")


def encrypt(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt(token: str | None) -> str | None:
    """None when there is nothing stored or the token was made with a different ENCRYPTION_KEY."""
    if not token:
        return None
    f = _fernet()
    try:
        return f.decrypt(token.encode()).decode()
    except InvalidToken:
        return None


def mask(value: str | None) -> str:
    """Show only the last 4 characters."""
    if not value:
        return ""
    return "•" * 8 + value[-4:]
