from datetime import timedelta
from typing import Literal
from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session
from ..database import get_db
from ..deps import current_user
from ..errors import AppError
from ..models import Deadline, User, utcnow
from ..schemas import DeadlineIn, DeadlineOut, DeadlineUpdate
from ..timeutil import next_occurrence, to_local_iso, to_utc_naive

router = APIRouter(prefix="/deadlines", tags=["deadlines"])


def out(d: Deadline, tz: str, rolled=False) -> DeadlineOut:
    return DeadlineOut(id=d.id, title=d.title, category=d.category, due_at=to_local_iso(d.due_at, tz),
                       repeat=d.repeat, notes=d.notes, done=d.done, called=d.last_call_at is not None, rolled=rolled)   # answered call (attempts alone don't count)


def owned(db: Session, user: User, did: int) -> Deadline:
    d = db.scalar(select(Deadline).where(Deadline.id == did, Deadline.user_id == user.id))  # always scoped
    if not d:
        raise AppError("not_found")
    return d


def check_range(dt):
    if dt > utcnow() + timedelta(days=3650):
        raise AppError("validation_error", errors=[{"field": "due_at", "msg": "date too far in the future"}])


@router.get("", response_model=list[DeadlineOut])
def list_deadlines(status: Literal["pending", "done", "all"] = "pending", limit: int = Query(200, ge=1, le=500),
                   user: User = Depends(current_user), db: Session = Depends(get_db)):
    q = select(Deadline).where(Deadline.user_id == user.id)
    if status == "pending":
        q = q.where(Deadline.done == False)  # noqa: E712
    elif status == "done":
        q = q.where(Deadline.done == True)   # noqa: E712
    rows = db.scalars(q.order_by(Deadline.due_at).limit(limit)).all()
    return [out(d, user.settings.timezone) for d in rows]


@router.post("", response_model=DeadlineOut, status_code=201)
def create_deadline(body: DeadlineIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    tz = user.settings.timezone
    due = to_utc_naive(body.due_at, tz)
    check_range(due)
    d = Deadline(user_id=user.id, title=body.title, category=body.category, due_at=due,
                 repeat=body.repeat, notes=body.notes.strip())
    db.add(d)
    db.commit()
    return out(d, tz)


@router.get("/{did}", response_model=DeadlineOut)
def get_deadline(did: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return out(owned(db, user, did), user.settings.timezone)


@router.patch("/{did}", response_model=DeadlineOut)
def update_deadline(did: int, body: DeadlineUpdate, user: User = Depends(current_user), db: Session = Depends(get_db)):
    d, tz = owned(db, user, did), user.settings.timezone
    data = body.model_dump(exclude_unset=True)
    for k in ("title", "category", "due_at", "repeat"):
        if k in data and data[k] is None:
            raise AppError("validation_error", errors=[{"field": k, "msg": "cannot be null"}])
    if "notes" in data:
        d.notes = (data.pop("notes") or "").strip()
    if "due_at" in data:
        due = to_utc_naive(data.pop("due_at"), tz)
        check_range(due)
        if due != d.due_at:   # moved: allow NIRA to call for the new date
            d.due_at, d.call_attempts, d.last_call_at, d.next_retry_at = due, 0, None, None
    for k, v in data.items():
        setattr(d, k, v)
    db.commit()
    return out(d, tz)


@router.post("/{did}/complete", response_model=DeadlineOut)
def complete_deadline(did: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    d, tz = owned(db, user, did), user.settings.timezone
    if d.repeat != "none":   # monthly EMI etc.: roll to the next cycle instead of finishing
        d.due_at = next_occurrence(d.due_at, d.repeat, tz)
        d.call_attempts, d.last_call_at, d.next_retry_at, d.done = 0, None, None, False
        d.completed_at = utcnow()
        db.commit()
        return out(d, tz, rolled=True)
    d.done, d.completed_at = True, utcnow()
    db.commit()
    return out(d, tz)


@router.post("/{did}/undo", response_model=DeadlineOut)
def undo_deadline(did: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    d = owned(db, user, did)
    d.done, d.completed_at = False, None
    db.commit()
    return out(d, user.settings.timezone)


@router.delete("/{did}", status_code=204)
def delete_deadline(did: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    db.delete(owned(db, user, did))
    db.commit()
