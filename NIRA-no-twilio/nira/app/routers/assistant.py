from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session
from .. import assistant
from ..database import get_db
from ..deps import current_user
from ..models import User
from ..ratelimit import chat_limit, limiter
from ..schemas import AssistantCancelOut, AssistantCommandIn, AssistantCommandOut, AssistantConfirmOut

router = APIRouter(prefix="/assistant", tags=["assistant"])


@router.post("/command", response_model=AssistantCommandOut)
@limiter.limit(chat_limit)
def command(request: Request, body: AssistantCommandIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """The browser sends the spoken text. The reply is 1-2 spoken sentences; `action` (if any) is a PROPOSAL, nothing is saved yet."""
    return assistant.handle_command(db, user, body.text)


@router.post("/confirm/{action_id}", response_model=AssistantConfirmOut)
def confirm(action_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """The only place the assistant's proposal changes data: once, for the owner, before it expires."""
    return assistant.confirm_action(db, user, action_id)


@router.post("/cancel/{action_id}", response_model=AssistantCancelOut)
def cancel(action_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    assistant.cancel_action(db, user, action_id)
    return AssistantCancelOut(status="cancelled")
