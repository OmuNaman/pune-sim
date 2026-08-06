"""One time substrate (architecture §9.1 ruling 2).

Canonical time is int64 **seconds** since 2026-01-01 00:00 IST. The kernel owns
the clock; a "tick" is a 300-second frame (288 per day). Subsystem cadences
(15-min hazards, hourly institutions) are timers on the single queue — no
subsystem keeps a private clock or a private time unit.
"""

from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))
EPOCH = datetime(2026, 1, 1, 0, 0, 0, tzinfo=IST)

SECONDS_PER_TICK = 300
TICKS_PER_DAY = 288
SECONDS_PER_DAY = SECONDS_PER_TICK * TICKS_PER_DAY  # 86_400


def sim_time(day: int, tick: int = 0, offset_s: int = 0) -> int:
    """Absolute sim seconds for (day, tick-of-day, extra seconds)."""
    return day * SECONDS_PER_DAY + tick * SECONDS_PER_TICK + offset_s


def day_of(t: int) -> int:
    return t // SECONDS_PER_DAY


def tick_of(t: int) -> int:
    """Tick within the day, 0..287."""
    return (t % SECONDS_PER_DAY) // SECONDS_PER_TICK


def to_datetime(t: int) -> datetime:
    return EPOCH + timedelta(seconds=t)
