"""
Local time for a workshop that happens in one place.

Every timestamp is stored in UTC, which is right, and was displayed in UTC,
which was not: a 10:52 submission in Bangalore showed as 05:22 in admin. That
is merely confusing until you want to filter on the time of day -- "the group
that filled Pre between 9 and 10" -- at which point reading the stored hour
gives the wrong students entirely.

Everything an admin sees or types is therefore in TIMEZONE (default
Asia/Kolkata). Nothing about how times are stored changes.
"""

from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

from .config import settings

_zone = None


def zone():
    global _zone
    if _zone is None:
        try:
            _zone = ZoneInfo(settings.TIMEZONE)
        except Exception:                            # unknown zone name in .env
            print(f"[time] unknown TIMEZONE {settings.TIMEZONE!r}, falling back to UTC")
            _zone = timezone.utc
    return _zone


def to_local(dt):
    """A stored (UTC) timestamp as local time. Naive values are assumed UTC,
    which is what every document written by this app holds."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(zone())


def local_day(dt):
    """The calendar day a timestamp falls on locally, 'YYYY-MM-DD'."""
    return to_local(dt).strftime("%Y-%m-%d")


def fmt(dt, pattern="%d %b %Y, %H:%M"):
    return to_local(dt).strftime(pattern) if dt else ""


def parse_hhmm(value):
    """'09:00', '9:00' or '9' -> a time. None for anything unparseable, so an
    empty or malformed filter field means 'no bound' rather than an error."""
    value = (value or "").strip()
    if not value:
        return None
    for pattern in ("%H:%M", "%H"):
        try:
            parsed = datetime.strptime(value, pattern)
        except ValueError:
            continue
        return time(parsed.hour, parsed.minute)
    return None


def in_window(dt, start, end):
    """Is this timestamp's local time of day inside [start, end]?

    Both bounds are inclusive, and either may be None to leave that end open.
    A window whose end is before its start wraps past midnight, so 22:00-02:00
    behaves the way somebody writing it down would expect.
    """
    if start is None and end is None:
        return True
    at = to_local(dt).time()
    if start is not None and end is not None:
        if start <= end:
            return start <= at <= end
        return at >= start or at <= end            # wraps midnight
    if start is not None:
        return at >= start
    return at <= end


def window_label(start, end):
    """How a window reads on the page: '09:00-10:00', 'from 09:00', or ''."""
    if start and end:
        return f"{start.strftime('%H:%M')}–{end.strftime('%H:%M')}"
    if start:
        return f"from {start.strftime('%H:%M')}"
    if end:
        return f"until {end.strftime('%H:%M')}"
    return ""
