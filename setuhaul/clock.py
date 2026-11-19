"""Time helpers. ALL time logic lives here, in Python, with explicit IST offsets.

Why: the snapshot stores '2026-08-04T21:30:00+05:30'. SQLite's time()/datetime()
silently convert that to UTC ('16:00:00'), so a SQL filter like
time(slot_start_ts) <= '21:00' passes everything. We never do time maths in SQL.
"""
from __future__ import annotations

from datetime import datetime, time, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30), "IST")


def parse_ts(value: str | datetime) -> datetime:
    """Parse an ISO timestamp into an offset-aware datetime (assume IST if naive)."""
    dt = value if isinstance(value, datetime) else datetime.fromisoformat(value)
    return dt if dt.tzinfo else dt.replace(tzinfo=IST)


def iso(dt: datetime) -> str:
    """Store timestamps exactly like the snapshot does: local IST with +05:30."""
    return dt.astimezone(IST).isoformat(timespec="seconds")


def hhmm(value: str | datetime) -> str:
    return parse_ts(value).astimezone(IST).strftime("%H:%M")


def local_time(dt: datetime) -> time:
    """Wall-clock time at the facility (IST), used for rules like 'no start after 21:00'."""
    return dt.astimezone(IST).time()


def at_local(day: datetime, hh_mm: str) -> datetime:
    """Build an IST datetime on the same facility day as `day` from 'HH:MM'."""
    h, m = (int(x) for x in hh_mm.split(":"))
    d = day.astimezone(IST)
    return d.replace(hour=h, minute=m, second=0, microsecond=0)


class Clock:
    """Real-ish clock. Scenarios pass an explicit `now`; this is the fallback."""

    def __init__(self, now: str | datetime | None = None):
        from .config import DEFAULT_NOW

        self._now = parse_ts(now or DEFAULT_NOW)

    def now(self) -> datetime:
        return self._now


class FakeClock(Clock):
    """Test clock you can move forward, e.g. to expire a 5-minute hold."""

    def advance(self, seconds: int) -> None:
        self._now = self._now + timedelta(seconds=seconds)
