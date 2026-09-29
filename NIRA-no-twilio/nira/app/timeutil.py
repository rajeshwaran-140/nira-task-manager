from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from dateutil.relativedelta import relativedelta
from .models import utcnow

UTC = timezone.utc


def zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("Asia/Kolkata")


def to_utc_naive(dt: datetime, tz: str) -> datetime:
    """Client datetimes without an offset are read as the user's local time."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=zone(tz))
    return dt.astimezone(UTC).replace(tzinfo=None)


def to_local_iso(dt: datetime, tz: str) -> str:
    return dt.replace(tzinfo=UTC).astimezone(zone(tz)).isoformat(timespec="minutes")


def next_occurrence(due: datetime, repeat: str, tz: str) -> datetime:
    """Next occurrence strictly after now (skips missed cycles). Wall-clock time in tz is preserved."""
    step = relativedelta(months=1) if repeat == "monthly" else relativedelta(years=1)
    z = zone(tz)
    local = due.replace(tzinfo=UTC).astimezone(z)
    now = utcnow()
    for _ in range(600):
        local = local + step
        n = local.astimezone(UTC).replace(tzinfo=None)
        if n > now:
            return n
    return n
