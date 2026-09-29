"""Which AI credentials does a user get?  1) their own saved key  2) the server default  3) error ai_not_configured.

`resolve_ai()` is THE helper every AI feature (the Part 3B chat, the voice option) must call, so the fallback
order lives in exactly one place."""
import logging
from dataclasses import dataclass, field
from sqlalchemy import select
from sqlalchemy.orm import Session
from .ai_providers import DEFAULT_MODELS, PROVIDERS, BaseProvider, make_provider
from .config import get_settings
from .errors import AppError
from .models import User, UserAISettings
from .schemas import AISettingsOut
from .security import decrypt

log = logging.getLogger("nira")


@dataclass(frozen=True)
class AICredentials:
    provider: str
    model: str
    api_key: str = field(repr=False)     # repr=False: a stray log/print of this object can't leak the key
    source: str                          # "user" | "server"


def get_user_row(db: Session, user_id: int) -> UserAISettings | None:
    return db.scalar(select(UserAISettings).where(UserAISettings.user_id == user_id))


def server_default() -> AICredentials | None:
    s = get_settings()
    key = s.default_ai_api_key.strip()
    if not key:
        return None
    provider = s.default_ai_provider.strip().lower()
    if provider not in PROVIDERS:
        log.warning("DEFAULT_AI_PROVIDER must be one of: %s. The server default AI key is ignored.", ", ".join(PROVIDERS))
        return None
    return AICredentials(provider, s.default_ai_model.strip() or DEFAULT_MODELS[provider], key, "server")


def resolve_ai(db: Session, user: User) -> AICredentials:
    row = get_user_row(db, user.id)
    if row:
        key = decrypt(row.encrypted_key)      # AppError if ENCRYPTION_KEY is empty/invalid
        if not key:
            # Saved with a different ENCRYPTION_KEY. Fail loudly: silently billing the server's key instead of the
            # user's own would surprise everybody.
            raise AppError("key_unreadable")
        return AICredentials(row.provider, row.model, key, "user")
    server = server_default()
    if server:
        return server
    raise AppError("ai_not_configured")


def get_ai_provider(db: Session, user: User) -> tuple[BaseProvider, AICredentials]:
    """Resolve + build the provider object in one step."""
    creds = resolve_ai(db, user)
    return make_provider(creds.provider, creds.model, creds.api_key), creds


def ai_status(db: Session, user: User) -> AISettingsOut:
    """What the Settings screen shows. Never decrypts anything, so it works even if ENCRYPTION_KEY is broken."""
    row = get_user_row(db, user.id)
    if row:
        provider, model, last4, source = row.provider, row.model, row.key_last4, "user"
    elif (server := server_default()):
        provider, model, last4, source = server.provider, server.model, None, "server"
    else:
        provider, model, last4, source = "gemini", DEFAULT_MODELS["gemini"], None, "none"
    return AISettingsOut(provider=provider, model=model, configured=source != "none", key_last4=last4,
                         source=source, default_models=dict(DEFAULT_MODELS))
