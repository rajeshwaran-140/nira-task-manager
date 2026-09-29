"""Records what Twilio says happened to a call and updates the reminder ONLY from that result.

Deadline fields (existing columns, no schema change):
  call_attempts  calls made so far for this reminder cycle (an ATTEMPT, not a success). Also the compare-and-set token
                 that stops duplicate calls / duplicate retries.
  last_call_at   set ONLY when the call was really answered (in-progress / completed). "called" in the API = this field.
  next_retry_at  when the next retry is due; set only after a failed / no-answer / busy result, cleared when it is claimed.
  in flight      call_attempts >= 1, last_call_at empty, next_retry_at empty: waiting for Twilio's result.
CallLog (existing table) = the call history: one row per call, `attempt` 1 = first call, 2 = first retry, ...
"""
import logging
from datetime import datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from . import calls
from .config import get_settings
from .models import CallLog, Deadline, utcnow

log = logging.getLogger("nira")

# Twilio's order of events. A result may only move a call forward, so late or repeated callbacks are harmless.
RANK = {"queued": 0, "ringing": 1, "in-progress": 2,
        "completed": 3, "busy": 3, "failed": 3, "no-answer": 3, "canceled": 3}
TERMINAL = {"completed", "busy", "failed", "no-answer", "canceled"}
ANSWERED = {"in-progress", "completed"}
RETRYABLE = {"failed", "no-answer", "busy"}       # "canceled" is final: someone stopped the call on purpose
STALE_AFTER = timedelta(minutes=10)               # no result for this long -> ask Twilio directly
STALE_GIVE_UP = timedelta(hours=24)


def apply_result(db: Session, log_id: int, status: str, duration: int | None = None, error: str | None = None,
                 now: datetime | None = None) -> bool:
    """Applies one Twilio status to one call row (and its reminder). Returns True only if something changed.
    Safe to call repeatedly and concurrently: the row is moved with a conditional UPDATE, so exactly one caller wins
    the transition, and the retry is scheduled by that winner only."""
    status = (status or "").strip().lower()
    if status not in RANK:
        return False
    now = now or utcnow()
    row = db.get(CallLog, log_id, populate_existing=True)
    if row is None or row.status in TERMINAL or RANK[status] <= RANK.get(row.status, -1):
        return False
    vals: dict = {"status": status}
    if duration is not None:
        vals["duration_sec"] = max(int(duration), 0)
    if error:
        vals["error"] = error[:200]
    won = db.execute(update(CallLog).where(CallLog.id == row.id, CallLog.status == row.status).values(**vals)
                     .execution_options(synchronize_session=False))
    if won.rowcount != 1:
        db.rollback()
        return False
    did, attempt = row.deadline_id, row.attempt
    if did is not None:   # test calls have no reminder
        newest = db.scalar(select(func.max(CallLog.id)).where(CallLog.deadline_id == did))
        if newest == row.id:   # a moved/rolled reminder has a newer cycle: an old call must not touch it
            _update_deadline(db, did, attempt, status, now)
    db.commit()
    return True


def _update_deadline(db: Session, did: int, attempt: int, status: str, now: datetime):
    same_attempt = (Deadline.id == did, Deadline.call_attempts == attempt, Deadline.last_call_at.is_(None))
    if status in ANSWERED:
        db.execute(update(Deadline).where(*same_attempt).values(last_call_at=now, next_retry_at=None)
                   .execution_options(synchronize_session=False))
    elif status in RETRYABLE:
        s = get_settings()
        info = db.execute(select(Deadline.done, Deadline.due_at).where(Deadline.id == did)).first()
        if info is None or info.done or info.due_at <= now or attempt > max(s.call_max_retries, 0):
            return   # finished, past due, or retries used up: no more calls
        db.execute(update(Deadline).where(*same_attempt, Deadline.next_retry_at.is_(None))
                   .values(next_retry_at=now + timedelta(minutes=max(s.call_retry_delay_minutes, 1)))
                   .execution_options(synchronize_session=False))


def reconcile_stale(db: Session, now: datetime | None = None, limit: int = 10):
    """Calls with no result for STALE_AFTER (callback missing, e.g. no public URL in local dev): ask Twilio directly.
    Never guesses: if Twilio can't be asked, the call stays as it is (no retry, so no accidental second call)."""
    now = now or utcnow()
    rows = db.scalars(select(CallLog).where(
        CallLog.status.in_(("queued", "ringing", "in-progress")),
        CallLog.created_at < now - STALE_AFTER, CallLog.created_at > now - STALE_GIVE_UP)
        .order_by(CallLog.id).limit(limit)).all()
    for row in rows:
        if not row.provider_call_id:   # the server stopped before Twilio accepted the call
            apply_result(db, row.id, "failed", error="call was not sent to Twilio", now=now)
            continue
        try:
            status, dur = calls.get_twilio_client().fetch_call(row.provider_call_id)
        except calls.TwilioCallError as e:
            log.warning("Could not check call %s with Twilio: %s", row.id, e)
            continue
        apply_result(db, row.id, status, duration=dur, now=now)
