from punesim.kernel.timebase import (
    SECONDS_PER_DAY,
    day_of,
    sim_time,
    tick_of,
    to_datetime,
)


def test_day_tick_roundtrip():
    t = sim_time(day=5, tick=97, offset_s=42)
    assert day_of(t) == 5 and tick_of(t) == 97


def test_epoch_is_ist_jan_1():
    dt = to_datetime(0)
    assert (dt.year, dt.month, dt.day, dt.hour) == (2026, 1, 1, 0)
    assert dt.utcoffset().total_seconds() == 5.5 * 3600


def test_day_is_288_ticks():
    assert SECONDS_PER_DAY == 288 * 300 == 86_400
