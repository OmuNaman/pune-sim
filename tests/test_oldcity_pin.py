"""A determinism pin for V3's world, covering the lanes kasba's pin cannot.

`tests/test_scale_guard.py` pins `f4d83a2c…` — 80 households, 3 days, kasba, no
hazards. It is pure clockwork by design, which is why every soak report in this
repo carries the same caveat: *the hash baseline cannot catch info-lane
regressions, that is what soaks are for*. Soaks take half an hour and get run
when somebody remembers.

oldcity has been the V3 world since 2026-08-07 and had no pin at all. Nothing
stopped a commit from silently changing how 49,578 people move or what they
pass on — the three optimisation passes this month were each checked by hand
against an ad-hoc 16-day run that lives nowhere.

So: 200 households, 3 days, oldcity, routed, with a rumour and a collision
injected so the run actually exercises

  - the road graph (kasba never routes),
  - claim seeding, co-presence transmission, mutation and credence,
  - belief -> action,
  - and the hospital procedure end to end,

in **0.6 seconds**. The counts are asserted beside the hash on purpose: a bare
hash failure says "something moved", and these say which lane moved, which is
the difference between a five-minute diagnosis and an afternoon.
"""

import pathlib

import pytest

from punesim import engine
from punesim.kernel.log import EventLog
from punesim.population import synthesize
from punesim.world.block import load_for

pytestmark = pytest.mark.skipif(
    not pathlib.Path("data/anchors/oldcity_places.geojson").exists(),
    reason="oldcity anchor data not fetched",
)

SEED, HOUSEHOLDS, DAYS = 108, 200, 3

# Both ids exist on kasba and oldcity alike — kasba's named places are a strict
# subset — so this pin moves if the shared anchors ever move.
WATER = "place:node/10172994194"   # Tulshibaug Mandir
SCHOOL = "place:node/3681735096"   # Ratanben Chunilal Mehta (RCM) Gujarati High School

# Re-pinned 2026-08-11, from 0dc63d4e…, for the three info-lane fixes: rumour
# details are now drawn from places within five minutes' walk of the subject
# instead of from the whole block, blame is constrained to targets that could
# plausibly answer for the claim's topic, and co-presence windows are clipped
# to waking hours. Every count below is unchanged — the same 11,824 events, the
# same 487 hearings by the same people — because none of the three changes what
# spreads or how far. What moved is the *content* and the *clock*: this run's
# blame targets were a median 23 minutes' walk from the accident they were
# blamed for and are now 5, twelve of its texts named a culprit and five do,
# and its three 03:00 conversations are gone. If a future change moves the
# lane counts too, that is a different kind of change and needs its own reason.
PINNED_HASH = "3b68ca6e428a2cf7f7d57072bee6ae1e173f48b285e0ca710dc13291e3e02f8e"
PINNED_EVENTS = 11824
PINNED_LANES = {"info.heard": 487, "belief.action": 4, "hospital.admitted": 1}


def _injections(people) -> list:
    adults = sorted(p.id for p in people.values() if p.age >= 18)[:2]
    kid = sorted(p.id for p in people.values() if p.occupation == "student")[0]
    return [
        engine.Injection.parse({
            "day": 0, "time": "18:30", "type": "info.rumor", "place": WATER,
            "participants": adults,
            "payload": {"credence": 0.85, "claim": {
                "key": "cl:pin_water", "subject": WATER, "predicate": "contaminated",
                "topics": ["water", "health"], "charge": 0.8, "specificity": 0.5,
                "veracity": "false", "valence": -0.7}},
        }),
        engine.Injection.parse({
            "day": 1, "time": "07:20", "type": "hazard.road.collision",
            "place": SCHOOL, "participants": [kid], "severity": 0.6,
        }),
    ]


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    block = load_for(HOUSEHOLDS, "oldcity")
    hhs, people = synthesize(SEED, block, n_households=HOUSEHOLDS)
    log = EventLog(tmp_path_factory.mktemp("oldcity") / "events.db")
    n, _state = engine.run_simulation(
        log, SEED, block, hhs, people, days=DAYS, block_name="oldcity",
        hazards=False, injections=_injections(people),
    )
    lanes = {k: sum(1 for _ in log.events(type=k)) for k in PINNED_LANES}
    out = {"events": n, "hash": log.determinism_hash(), "lanes": lanes,
           "people": len(people)}
    log.close()
    return out


def test_the_population_is_what_it_was(run):
    assert run["people"] == 847


@pytest.mark.parametrize("lane", sorted(PINNED_LANES))
def test_each_lane_carries_what_it_carried(run, lane):
    """Named separately so a failure says which lane moved."""
    assert run["lanes"][lane] == PINNED_LANES[lane]


def test_the_oldcity_hash_is_unchanged(run):
    assert run["events"] == PINNED_EVENTS
    assert run["hash"] == PINNED_HASH, (
        "oldcity's behaviour changed. If that was deliberate, re-pin here AND say "
        "in the commit what moved — this hash covers routing, the info lane and "
        "the hospital procedure, so 'just a refactor' is not a sufficient reason "
        "for it to differ."
    )
