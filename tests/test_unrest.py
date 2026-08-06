"""V2 minimal collective dynamics: the differentiated-mass-behavior exit.
Inject communal tension -> a small crowd mobilizes, police deploy, a curfew
zone forms, the neighbourhood shelters next day — and a low-severity flare
fizzles. All mechanical, zero LLM, deterministic."""

import pytest

from punesim import engine
from punesim.kernel.log import EventLog
from punesim.population import synthesize
from punesim.world import unrest
from punesim.world.block import Block, haversine_m

pytestmark = pytest.mark.skipif(
    not __import__("pathlib").Path("data/anchors/kasba_places.geojson").exists(),
    reason="anchor data not fetched",
)

SEED = 21


@pytest.fixture(scope="module")
def world():
    block = Block.load()
    hhs, people = synthesize(SEED, block, n_households=40)
    return block, hhs, people


def _flashpoint(block, people):
    """A named place with real footfall: someone's workplace/school district."""
    counts = {}
    for p in people.values():
        if p.work_id:
            counts[p.work_id] = counts.get(p.work_id, 0) + 1
    busiest = max(counts, key=lambda k: (counts[k], k))
    return block.get(busiest)


def test_thresholds_are_keyed_and_spread_out(world):
    _, _, people = world
    ths = [unrest.personal_threshold(SEED, pid) for pid in sorted(people)[:60]]
    assert ths == [unrest.personal_threshold(SEED, pid) for pid in sorted(people)[:60]]
    assert min(ths) < 0.3 < max(ths)  # some hotheads, mostly not


def test_severe_unrest_mobilizes_few_shelters_many(tmp_path, world):
    block, hhs, people = world
    spot = _flashpoint(block, people)
    inj = engine.Injection(
        day=0, time_s=17 * 3600 + 30 * 60, type="unrest.communal_tension",
        place=spot.id, severity=0.8,
    )
    log = EventLog(tmp_path / "u.db")
    engine.run_simulation(log, SEED, block, hhs, people, days=3, injections=[inj])

    # a crowd, but a SMALL one — participation is the exception
    crowds = [e for e in log.events(type="crowd.gathered")]
    assert crowds, "nobody at all mobilized at severity 0.8"
    size = crowds[0].payload["size"]
    adults = sum(1 for p in people.values() if p.age >= 16)
    assert size < adults * 0.15, f"crowd of {size} of {adults} adults — mob, not minority"
    assert crowds[0].caused_by is not None

    # police respond when the knot is big enough; the curfew zone forms
    if size >= unrest.CROWD_FOR_POLICE:
        assert list(log.events(type="police.deployed"))
    curfews = [e for e in log.events(type="curfew.imposed")]
    assert curfews and curfews[0].payload["from_day"] == 1

    # next day, the zone shelters: many stay home who normally go out
    day1 = [e for e in log.events() if 86400 <= e.sim_time < 2 * 86400]
    sheltered = {e.payload["person"] for e in day1
                 if e.type == "activity.start" and e.payload.get("activity") == "shelters_at_home"}
    assert len(sheltered) >= 10, "a curfew nobody kept"
    # ...but the crowd is not the shelterers: differentiated behavior
    assert sheltered - set(crowds[0].payload["participants"]), "everyone reacted identically"
    # essential occupations keep moving inside the zone
    for pid in sheltered:
        assert people[pid].occupation not in unrest.ESSENTIAL

    # the flashpoint's story spreads through the ordinary INFO lane
    heard = [e for e in log.events(type="info.heard")
             if "communal_tension" in e.payload.get("claim_key", "")]
    assert heard, "trouble this size and nobody talks?"

    # nobody was selected into the crowd by religion (identity honesty):
    # the crowd's religious mix must not be single-community unless its
    # candidate pool was — thresholds are keyed draws, not identity
    crowd_pids = crowds[0].payload["participants"]
    if len(crowd_pids) >= 4:
        religions = {people[pid].religion for pid in crowd_pids}
        pool_religions = {p.religion for p in people.values() if p.age >= 16}
        assert len(religions) > 1 or len(pool_religions) == 1


def test_low_severity_flare_fizzles(tmp_path, world):
    block, hhs, people = world
    spot = _flashpoint(block, people)
    inj = engine.Injection(
        day=0, time_s=17 * 3600, type="unrest.communal_tension",
        place=spot.id, severity=0.25,
    )
    log = EventLog(tmp_path / "f.db")
    engine.run_simulation(log, SEED, block, hhs, people, days=2, injections=[inj])
    assert not list(log.events(type="curfew.imposed"))
    crowds = [e for e in log.events(type="crowd.gathered")]
    assert not crowds or crowds[0].payload["size"] <= 3
    day1_shelter = [e for e in log.events()
                    if e.sim_time >= 86400 and e.payload.get("activity") == "shelters_at_home"]
    assert not day1_shelter  # life goes on


def test_unrest_is_deterministic_and_isolated(tmp_path, world):
    """Law 4: the unrest draws never perturb an untouched person's routine."""
    block, hhs, people = world
    spot = _flashpoint(block, people)
    inj = engine.Injection(
        day=0, time_s=18 * 3600, type="unrest.communal_tension",
        place=spot.id, severity=0.8,
    )
    log_a = EventLog(tmp_path / "a.db")
    engine.run_simulation(log_a, SEED, block, hhs, people, days=2)
    log_b = EventLog(tmp_path / "b.db")
    engine.run_simulation(log_b, SEED, block, hhs, people, days=2, injections=[inj])
    # someone far from the zone with no zone contact: identical both worlds
    far = next(
        p for p in sorted(people.values(), key=lambda x: x.id)
        if p.work_id is None and haversine_m(
            block.get(p.home_id).lat, block.get(p.home_id).lon,
            spot.lat, spot.lon) > unrest.ZONE_RADIUS_M * 1.5
    )
    def day1_routine(log):
        return [
            (e.sim_time, e.type, e.payload.get("at") or e.payload.get("to"))
            for e in log.events()
            if e.payload.get("person") == far.id and e.sim_time >= 86400
            and e.type in ("trip.start", "trip.end", "activity.start")
        ]
    assert day1_routine(log_a) == day1_routine(log_b)
