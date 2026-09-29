from urllib.parse import parse_qsl

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import call_results, calls
from ..config import get_settings
from ..database import get_db
from ..deps import current_user
from ..errors import AppError
from ..models import CallLog, User, utcnow
from ..ratelimit import limiter, test_call_limit
from ..schemas import CallHistoryOut
from ..timeutil import to_local_iso

router = APIRouter(prefix="/calls", tags=["calls"])
webhook = APIRouter(tags=["twilio"])      # called by Twilio, not by the browser
MAX_WEBHOOK_BYTES = 20_000                # a Twilio status callback is a few hundred bytes


@router.post("/test")
@limiter.limit(test_call_limit)
def make_test_call(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Places ONE real call to NIRA_USER_PHONE_NUMBER so the owner can check the number. Login required; the
    destination comes from server config (the request body is ignored), and only the account whose verified phone
    is that number may use it. The call shows up in Call History like any other."""
    if not calls.calls_configured():
        raise AppError("twilio_not_configured")
    if not calls.is_call_owner(user):
        raise AppError("call_forbidden")
    now = utcnow()
    row = CallLog(user_id=user.id, deadline_id=None, title="Test call", status="queued", attempt=1,
                  started_at=now, created_at=now)
    db.add(row)
    db.commit()
    try:
        sid = calls.place_call(calls.test_twiml())
    except calls.TwilioCallError as e:
        call_results.apply_result(db, row.id, "failed", error=str(e))
        raise AppError("call_failed")
    row.provider_call_id = sid or None
    db.commit()
    return {"ok": True, "calling": calls.mask_phone(get_settings().nira_user_phone_number.strip())}


@router.get("/history", response_model=list[CallHistoryOut])
def call_history(limit: int = Query(50, ge=1, le=200), user: User = Depends(current_user), db: Session = Depends(get_db)):
    """The signed-in user's own calls, newest first. The phone is masked; Twilio ids and error text are never returned."""
    n = get_settings().nira_user_phone_number.strip()
    phone = calls.mask_phone(n) if n else ""
    tz = user.settings.timezone
    rows = db.scalars(select(CallLog).where(CallLog.user_id == user.id)
                      .order_by(CallLog.created_at.desc(), CallLog.id.desc()).limit(limit)).all()
    return [CallHistoryOut(id=r.id, title=r.title, phone=phone, call_time=to_local_iso(r.started_at or r.created_at, tz),
                           status=r.status, retry_count=max(r.attempt - 1, 0), duration_sec=r.duration_sec) for r in rows]


async def _read_limited(request: Request) -> bytes | None:
    """The request body, or None if it is bigger than MAX_WEBHOOK_BYTES. Checks the declared size first and never
    buffers more than the limit, so an oversized body is refused without being read into memory."""
    try:
        declared = int(request.headers.get("content-length", "0"))
    except ValueError:
        declared = 0
    if declared > MAX_WEBHOOK_BYTES:
        return None
    buf = bytearray()
    async for chunk in request.stream():
        buf += chunk
        if len(buf) > MAX_WEBHOOK_BYTES:
            return None
    return bytes(buf)


@webhook.post("/twilio/status", status_code=204)
async def twilio_status(request: Request, db: Session = Depends(get_db)):
    """Twilio's call status callback. No login (Twilio can't sign in): every request must carry a valid
    X-Twilio-Signature made with the auth token, otherwise it is refused and nothing is read from it."""
    raw = await _read_limited(request)
    if raw is None:
        return Response(status_code=413)
    pairs = parse_qsl(raw.decode("utf-8", "replace"), keep_blank_values=True)
    if not calls.valid_signature(calls.status_callback_url(), pairs, request.headers.get("X-Twilio-Signature", "")):
        return Response(status_code=403)
    form = dict(pairs)
    sid, status = form.get("CallSid", ""), form.get("CallStatus", "")
    if sid and status:
        row = db.scalar(select(CallLog).where(CallLog.provider_call_id == sid))
        if row is not None:      # unknown ids (other apps on the same Twilio account) are ignored
            dur = form.get("CallDuration", "")
            seconds = int(dur) if dur.isascii() and dur.isdigit() and len(dur) <= 6 else None
            call_results.apply_result(db, row.id, status, duration=seconds)
    return Response(status_code=204)
