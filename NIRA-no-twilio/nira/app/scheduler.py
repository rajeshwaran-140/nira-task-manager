"""Background scheduler: finds due reminders in the existing `deadlines` table and phones the owner.

Source of truth = the existing reminder rules:
  * a reminder is due at (deadline date - user's `lead_days`) at the user's `call_time`, in the user's timezone;
  * only for users with `calls_enabled` and `consent_to_call`, and only for pending (not done) deadlines;
  * each call is an ATTEMPT (`call_attempts`, claimed atomically so it never runs twice). The reminder counts as
    called (`last_call_at`) only when Twilio reports the call was answered (see app/call_results.py). A failed,
    unanswered or busy call is retried after CALL_RETRY_DELAY_MINUTES, at most CALL_MAX_RETRIES times.
"""
import logging
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from . import call_results, calls
from .config import get_settings
from .database import SessionLocal
from .models import CallLog, Deadline, User, UserSettings, utcnow
from .timeutil import UTC, zone

log = logging.getLogger("nira")

CATCH_UP_WINDOW = timedelta(hours=6)   # a reminder missed by more than this (server was down) is skipped, not called late
MAX_CALLS_PER_RUN = 5                  # safety cap per scheduler tick
_scheduler = None


def reminder_time(due_at: datetime, lead_days: int, call_time: str, tz: str) -> datetime | None:
    """Naive-UTC moment at which the reminder call for this deadline is due, or None if call_time is unreadable."""
    try:
        hh, mm = (int(x) for x in call_time.split(":"))
        local_due = due_at.replace(tzinfo=UTC).astimezone(zone(tz))
        day = local_due.date() - timedelta(days=max(lead_days, 0))
        local = datetime(day.year, day.month, day.day, hh, mm, tzinfo=zone(tz))
    except (ValueError, AttributeError):
        return None
    return local.astimezone(UTC).replace(tzinfo=None)


def _attempt(db: Session, d: Deadline, tz: str, now: datetime) -> bool:
    """One call attempt for one reminder (first call when call_attempts == 0, otherwise a due retry).
    The reminder is claimed with a compare-and-set on `call_attempts`, so the same attempt can never run twice.
    The claim only records an ATTEMPT (call_attempts / a history row); success is recorded later, from Twilio's result."""
    expected = d.call_attempts
    where = [Deadline.id == d.id, Deadline.done == False, Deadline.call_attempts == expected,  # noqa: E712
             Deadline.last_call_at.is_(None)]
    where += [Deadline.next_retry_at.is_not(None), Deadline.next_retry_at <= now] if expected else [Deadline.next_retry_at.is_(None)]
    res = db.execute(update(Deadline).where(*where).values(call_attempts=expected + 1, next_retry_at=None)
                     .execution_options(synchronize_session=False))
    if res.rowcount != 1:
        db.rollback()
        return False                       # someone else already took this attempt
    row = CallLog(user_id=d.user_id, deadline_id=d.id, title=d.title[:120], status="queued", attempt=expected + 1,
                  started_at=now, created_at=now)
    db.add(row)
    db.commit()
    try:
        sid = calls.place_call(calls.reminder_twiml(d.title, _spoken(d.due_at, tz)))
    except calls.TwilioCallError as e:     # the message holds no numbers or keys
        log.error("Reminder call request failed for deadline %s: %s", d.id, e)
        call_results.apply_result(db, row.id, "failed", error=str(e), now=now)     # -> retryable
    except Exception as e:  # noqa: BLE001
        log.error("Reminder call request failed for deadline %s: %s", d.id, type(e).__name__)
        call_results.apply_result(db, row.id, "failed", error=type(e).__name__, now=now)
    else:
        db.execute(update(CallLog).where(CallLog.id == row.id).values(provider_call_id=sid or None)
                   .execution_options(synchronize_session=False))
        db.commit()
        log.info("Reminder call requested for deadline %s (attempt %s)", d.id, expected + 1)
    return True


def run_due_reminders(db: Session | None = None, now: datetime | None = None) -> list[int]:
    """One scheduler tick. Returns the ids of the deadlines a call was attempted for (first calls and retries)."""
    if not calls.calls_configured():
        return []
    own = db is None
    db = db or SessionLocal()
    now = now or utcnow()
    s = get_settings()
    owner_phone = s.nira_user_phone_number.strip()
    attempted: list[int] = []
    try:
        try:
            call_results.reconcile_stale(db, now)
        except Exception as e:  # noqa: BLE001  a status check must never stop new reminders
            db.rollback()
            log.error("Checking call results failed: %s", type(e).__name__)
        base = [Deadline.done == False, Deadline.last_call_at.is_(None), Deadline.due_at > now,  # noqa: E712
                User.is_active == True, User.phone_verified == True, User.phone == owner_phone,  # noqa: E712
                UserSettings.calls_enabled == True, UserSettings.consent_to_call == True]  # noqa: E712

        def fetch(*extra):
            return db.execute(select(Deadline, UserSettings).join(User, User.id == Deadline.user_id)
                              .join(UserSettings, UserSettings.user_id == User.id).where(*base, *extra)
                              .order_by(Deadline.due_at)).all()

        retries = fetch(Deadline.call_attempts >= 1, Deadline.call_attempts <= max(s.call_max_retries, 0),
                        Deadline.next_retry_at.is_not(None), Deadline.next_retry_at <= now)
        firsts = fetch(Deadline.call_attempts == 0, Deadline.next_retry_at.is_(None))
        for d, st in retries:              # a retry is not held to the 6-hour window: it follows a call made inside it
            if len(attempted) >= MAX_CALLS_PER_RUN:
                break
            if _attempt(db, d, st.timezone, now):
                attempted.append(d.id)
        for d, st in firsts:
            if len(attempted) >= MAX_CALLS_PER_RUN:
                break
            at = reminder_time(d.due_at, st.lead_days, st.call_time, st.timezone)
            if at is None or not (at <= now <= at + CATCH_UP_WINDOW):
                continue
            if _attempt(db, d, st.timezone, now):
                attempted.append(d.id)
    finally:
        if own:
            db.close()
    return attempted


def _spoken(due_at: datetime, tz: str) -> str:
    local = due_at.replace(tzinfo=UTC).astimezone(zone(tz))
    return local.strftime("%A %d %B at %I:%M %p").replace(" 0", " ")


def start_scheduler():
    """Starts the background job once, only when enabled AND all Twilio call settings are valid."""
    global _scheduler
    s = get_settings()
    if _scheduler is not None or not s.scheduler_enabled:
        return None
    problems = calls.call_config_problems()
    if problems:
        log.warning("Reminder calls are OFF (scheduler not started). Missing/invalid: %s", ", ".join(problems))
        return None
    from apscheduler.schedulers.background import BackgroundScheduler
    _scheduler = BackgroundScheduler(timezone="UTC")
    _scheduler.add_job(run_due_reminders, "interval", seconds=max(s.scheduler_interval_seconds, 10),
                       id="due_reminders", max_instances=1, coalesce=True)
    _scheduler.start()
    log.info("Reminder scheduler started (every %ss)", max(s.scheduler_interval_seconds, 10))
    return _scheduler


def stop_scheduler():
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
