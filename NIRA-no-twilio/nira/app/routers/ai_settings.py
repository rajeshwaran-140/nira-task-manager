from fastapi import APIRouter, Depends, Request
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from ..ai_keys import ai_status, get_ai_provider, get_user_row
from ..ai_providers import DEFAULT_MODELS, AIProviderError
from ..database import get_db
from ..deps import current_user
from ..errors import AppError
from ..models import User, UserAISettings
from ..ratelimit import ai_test_limit, limiter
from ..schemas import AISettingsIn, AISettingsOut, AITestOut
from ..security import encrypt

router = APIRouter(prefix="/settings/ai", tags=["ai-settings"])


def _upsert(db: Session, user_id: int, provider: str, model: str, enc: str, last4: str) -> None:
    for _ in range(2):   # 2nd round only if two first-time saves raced (user_id is unique)
        row = get_user_row(db, user_id)
        if row:
            row.provider, row.model, row.encrypted_key, row.key_last4 = provider, model, enc, last4
        else:
            db.add(UserAISettings(user_id=user_id, provider=provider, model=model, encrypted_key=enc, key_last4=last4))
        try:
            db.commit()
            return
        except IntegrityError:
            db.rollback()
    raise AppError("internal")


@router.get("", response_model=AISettingsOut)
def get_ai(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return ai_status(db, user)


@router.put("", response_model=AISettingsOut)
def put_ai(body: AISettingsIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    model = body.model or DEFAULT_MODELS[body.provider]
    if body.key is None:                                   # empty key = keep the saved one ...
        row = get_user_row(db, user.id)
        if not row or row.provider != body.provider:       # ... but never re-send a key to a different provider
            raise AppError("key_required")
        row.model = model
        db.commit()
    else:
        enc = encrypt(body.key)                            # bilingual error if ENCRYPTION_KEY is empty/invalid
        _upsert(db, user.id, body.provider, model, enc, body.key[-4:])
    return ai_status(db, user)


@router.delete("", response_model=AISettingsOut)
def delete_ai(user: User = Depends(current_user), db: Session = Depends(get_db)):
    row = get_user_row(db, user.id)
    if row:
        db.delete(row)
        db.commit()
    return ai_status(db, user)      # shows whether the server default now applies


@router.post("/test", response_model=AITestOut)
@limiter.limit(ai_test_limit)
def test_ai(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """One tiny real call with whatever the fallback helper resolves (own key first, then server default)."""
    provider, creds = get_ai_provider(db, user)
    try:
        provider.ping()
    except AIProviderError as e:
        raise AppError(e.code)
    return AITestOut(ok=True, provider=creds.provider, model=creds.model, source=creds.source)
