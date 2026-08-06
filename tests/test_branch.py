"""Branch-lite: fork, what-if, diff — offline, deterministic (V2 exit)."""

import pytest

from punesim import branch as branch_mod
from punesim import engine
from punesim.kernel.diff import diff_logs
from punesim.kernel.log import EventLog
from punesim.population import synthesize
from punesim.world.block import Block

pytestmark = pytest.mark.skipif(
    not __import__("pathlib").Path("data/anchors/kasba_places.geojson").exists(),
    reason="anchor data not fetched",
)

SEED = 21


@pytest.fixture(scope="module")
def world():
    block = Block.load()
    hhs, people = synthesize(SEED, block, n_households=20)
    return block, hhs, people


def _source(tmp_path, world, days=3):
    block, hhs, people = world
    inj = engine.Injection(
        day=0, time_s=9 * 3600, type="hazard.road.collision",
        place=block.places[0].id,
        participants=(next(p.id for p in people.values() if p.age >= 18),),
        severity=0.5,
    )
    log = EventLog(tmp_path / "src.db")
    engine.run_simulation(log, SEED, block, hhs, people, days=days, injections=[inj])
    log.close()
    return tmp_path / "src.db", inj


def test_run_meta_and_reconstruction(tmp_path, world):
    src, inj = _source(tmp_path, world)
    log = EventLog(src)
    meta = branch_mod.read_meta(log)
    assert meta and meta["seed"] == SEED and meta["households"] == 20
    back = branch_mod.reconstruct_injections(log)
    log.close()
    assert len(back) == 1
    b = back[0]
    assert (b.day, b.time_s, b.type, b.place, b.participants, b.severity) == (
        inj.day, inj.time_s, inj.type, inj.place, inj.participants, inj.severity,
    )


def test_branch_shares_prefix_and_diverges_at_the_what_if(tmp_path, world):
    block, _hhs, people = world
    src, _ = _source(tmp_path, world)
    rumor = engine.Injection(
        day=1, time_s=10 * 3600, type="info.rumor", place=block.places[0].id,
        participants=(min(people),),
        payload={"credence": 0.85, "claim": {
            "key": "cl:whatif", "subject": block.places[0].id,
            "predicate": "contaminated", "topics": ["water"], "charge": 0.8,
        }},
    )
    res = branch_mod.branch_run(
        src, tmp_path / "b" / "events.db", block=block, synthesize=synthesize,
        extra_injections=[rumor], add_days=2,
    )
    assert res.seed == SEED and res.days == 5 and res.injections == 2

    a, b = EventLog(src), EventLog(res.db_path)
    rep = diff_logs(a, b, {p.id: p.name for p in people.values()})
    a.close(), b.close()
    assert not rep.identical
    # the injected what-if is the branch point; knock-ons start no earlier
    assert rep.branch_point and rep.branch_point["day"] == 1
    assert rep.first_divergence["day"] >= 1
    assert rep.people_changed, "a water rumor changed nobody's day"
    assert any(d["key"] == "cl:whatif" for d in rep.rumor_deltas)
    assert any("Branch point: day 1" in line for line in rep.headline)
    assert any("knock-on divergence" in line for line in rep.headline)


def test_diff_of_identical_worlds_is_clean(tmp_path, world):
    block, hhs, people = world
    src, inj = _source(tmp_path, world)
    log2 = EventLog(tmp_path / "twin.db")
    engine.run_simulation(log2, SEED, block, hhs, people, days=3, injections=[inj])
    log2.close()
    a, b = EventLog(src), EventLog(tmp_path / "twin.db")
    rep = diff_logs(a, b)
    a.close(), b.close()
    assert rep.identical
    assert rep.headline == ["The two worlds are identical."]


def test_branch_is_deterministic(tmp_path, world):
    block, _hhs, _people = world
    src, _ = _source(tmp_path, world)
    extra = engine.Injection(
        day=1, time_s=12 * 3600, type="hazard.fire.small",
        place=block.places[1].id, severity=0.5,
    )
    hashes = []
    for name in ("b1", "b2"):
        res = branch_mod.branch_run(
            src, tmp_path / name / "events.db", block=block, synthesize=synthesize,
            extra_injections=[extra],
        )
        log = EventLog(res.db_path)
        hashes.append(log.determinism_hash())
        log.close()
    assert hashes[0] == hashes[1]
