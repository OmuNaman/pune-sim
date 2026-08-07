"""Guards for the scale probe's findings (docs/perf/scale-probe.md).

These pin the two things that a well-meaning change can silently undo: the
determinism hash the V0-V2 soaks were validated against, and the assumption
that makes that hash safe — that an 80-household day never crowds a place
enough for the co-presence cap to engage.
"""

from collections import Counter

import pytest

from punesim import engine
from punesim.kernel.log import EventLog
from punesim.kernel.timebase import SECONDS_PER_DAY
from punesim.minds import info
from punesim.population import synthesize
from punesim.world.block import load_for

pytestmark = pytest.mark.skipif(
    not __import__("pathlib").Path("data/anchors/kasba_places.geojson").exists(),
    reason="anchor data not fetched",
)

# The hash every soak in docs/soaks/ was measured against: 80 households, 3
# days, no sampled hazards. Perf work may not move it; a deliberate behaviour
# change must move it in the same commit that explains why.
SOAKED_HASH = "f4d83a2cebed03991df12bfcc3fe6757d6a11638fcddef574381cf84833e00c9"

ROUTINE = ("trip.start", "trip.end", "activity.start")


def _spans_per_place(log: EventLog, people: dict, day: int) -> Counter:
    routine = [
        (e.sim_time, e.payload.get("person"), e.type, e.payload)
        for e in log.events(since_time=day * SECONDS_PER_DAY,
                            until_time=(day + 1) * SECONDS_PER_DAY)
        if e.type in ROUTINE
    ]
    counts: Counter = Counter()
    for spans in info.presence_intervals(routine, people, day).values():
        for place, _t0, _t1 in spans:
            counts[place] += 1
    return counts


def test_soaked_determinism_hash_is_unchanged(tmp_path):
    block = load_for(80)
    hhs, people = synthesize(108, block, n_households=80)
    log = EventLog(tmp_path / "gate.db")
    engine.run_simulation(log, 108, block, hhs, people, days=3,
                          gateway=None, scenes_k=0, hazards=False)
    assert log.determinism_hash() == SOAKED_HASH


def test_the_crowd_cap_cannot_engage_at_the_soaked_size(tmp_path):
    """The 80-household hash is only stable while every place stays exact.

    `_copresence_windows` enumerates all pairs below CROWD_EXACT_SPANS and
    keyed-samples above it. An 80-household day's busiest place holds 92 spans
    against a threshold of 128 — comfortable, but not so comfortable that a
    schedule change could not cross it, and if one did the hash would break with
    nothing naming the cause. This test names it.
    """
    block = load_for(80)
    hhs, people = synthesize(108, block, n_households=80)
    log = EventLog(tmp_path / "crowd.db")
    engine.run_simulation(log, 108, block, hhs, people, days=3,
                          gateway=None, scenes_k=0, hazards=False)
    worst = max(max(_spans_per_place(log, people, d).values()) for d in range(3))
    assert worst < info.CROWD_EXACT_SPANS, (
        f"busiest place now holds {worst} spans against a cap of "
        f"{info.CROWD_EXACT_SPANS}: the crowd cap has started engaging at the "
        f"soaked size, so {SOAKED_HASH[:8]}… no longer means what it meant"
    )


def test_the_crowd_cap_does_engage_once_a_place_is_crowded(tmp_path):
    """...and that it actually bites when it should.

    At 320 households the busiest place is well over the threshold. Without the
    cap this run emitted 85k co-presence windows a day and the term grew as
    n^1.69; with it, 20k and n^0.96. The ceiling is loose — it is here to catch
    a return to all-pairs, not to pin an exact count.
    """
    block = load_for(320)
    hhs, people = synthesize(108, block, n_households=320)
    log = EventLog(tmp_path / "big.db")
    engine.run_simulation(log, 108, block, hhs, people, days=2,
                          gateway=None, scenes_k=0, hazards=False)
    assert max(_spans_per_place(log, people, 1).values()) > info.CROWD_EXACT_SPANS
    intervals_day = 1
    routine = [
        (e.sim_time, e.payload.get("person"), e.type, e.payload)
        for e in log.events(since_time=intervals_day * SECONDS_PER_DAY,
                            until_time=(intervals_day + 1) * SECONDS_PER_DAY)
        if e.type in ROUTINE
    ]
    intervals = info.presence_intervals(routine, people, intervals_day)
    windows = info._copresence_windows(intervals, 108, intervals_day)
    assert len(windows) < 40_000, (
        f"{len(windows)} co-presence windows in one day at 320 households; "
        "all-pairs produced 85k and the cap brought it to 20k, so this is a "
        "regression toward enumerating every pair in a crowd"
    )
