"""Event classes as data — and the one field that is a rule, not a setting."""

import json
import pathlib

import pytest

from punesim import engine
from punesim.kernel.log import EventLog
from punesim.population import synthesize
from punesim.world import classdefs, hazards
from punesim.world.block import Block

pytestmark = pytest.mark.skipif(
    not pathlib.Path("data/anchors/kasba_places.geojson").exists(),
    reason="anchor data not fetched",
)

SEED = 33


def test_the_shipped_registry_loads_and_is_ordered():
    defs = classdefs.load()
    assert [c.type for c in defs] == [
        "hazard.road.collision", "hazard.water.supply_cut",
        "hazard.power.outage", "hazard.fire.small",
    ], "order fixes the sequence of keyed draws in sample_day, and so the hash"
    for c in defs:
        assert 0 < c.p_per_day < 1
        assert c.window[0] < c.window[1]
        assert c.narratability in classdefs.NARRATABILITY
        assert c.topics, f"{c.type} belongs to no topic, so no belief can act on it"


def test_a_bad_class_is_refused_at_load(tmp_path):
    """A typo in a data file must not become a silently different world."""
    for bad, field in (({"shape": "everywhere"}, "shape"),
                       ({"narratability": "cinematic"}, "narratability")):
        spec = {
            "type": "hazard.test", "p_per_day": 0.1, "window": ["08:00", "09:00"],
            "shape": "point", "predicate": "test", "topics": ["safety"], "charge": 0.5,
        }
        spec.update(bad)
        path = tmp_path / f"{field}.json"
        path.write_text(json.dumps({"classes": [spec]}), encoding="utf-8")
        with pytest.raises(ValueError, match=field):
            classdefs.load(path)


def _run_with(monkeypatch, narratability: str, tmp_path) -> list:
    """Inject one event of a test class and return the claims it seeded."""
    cd = classdefs.ClassDef(
        type="hazard.test.thing", p_per_day=0.0, window=(9 * 3600, 10 * 3600),
        shape="area", predicate="dangerous", topics=("safety",), charge=0.9,
        narratability=narratability,
    )
    monkeypatch.setitem(hazards.BY_TYPE, cd.type, cd)
    block = Block.load()
    hhs, people = synthesize(SEED, block, n_households=40)
    place = block.of_kind("temple", "shop", "market")[0].id
    inj = engine.Injection(day=0, time_s=9 * 3600 + 600, type=cd.type,
                           place=place, severity=0.8)
    log = EventLog(tmp_path / f"{narratability}.db")
    engine.run_simulation(log, SEED, block, hhs, people, days=2, injections=[inj])
    heard = [e for e in log.events(type="info.heard")
             if e.payload.get("claim_key", "").startswith("cl:test.thing")]
    log.close()
    return heard


def test_a_numeric_class_is_counted_but_never_narrated(monkeypatch, tmp_path):
    """The content-safety rule from 08-identity §5, as machinery.

    NCRB calibration will generate classes the sim must be able to count
    without ever staging — suicides, domestic violence, crimes against
    children. `numeric` means the event happens and sits in the log, and no
    claim is seeded from it, so nobody gossips about it and no scene can open
    on it however hard attention is pointed at it.
    """
    full = _run_with(monkeypatch, "full", tmp_path)
    numeric = _run_with(monkeypatch, "numeric", tmp_path)
    assert full, "control: a narratable class of the same shape does seed claims"
    assert not numeric, (
        f"{len(numeric)} people gossiped about an event the world is only "
        "allowed to count"
    )


def test_the_event_itself_is_still_committed(monkeypatch, tmp_path):
    """Not narratable is not the same as not happening — it must still count."""
    cd = classdefs.ClassDef(
        type="hazard.test.thing", p_per_day=0.0, window=(9 * 3600, 10 * 3600),
        shape="area", predicate="dangerous", topics=("safety",), charge=0.9,
        narratability="numeric",
    )
    monkeypatch.setitem(hazards.BY_TYPE, cd.type, cd)
    block = Block.load()
    hhs, people = synthesize(SEED, block, n_households=40)
    place = block.of_kind("temple", "shop", "market")[0].id
    inj = engine.Injection(day=0, time_s=9 * 3600 + 600, type=cd.type,
                           place=place, severity=0.8)
    log = EventLog(tmp_path / "counted.db")
    engine.run_simulation(log, SEED, block, hhs, people, days=1, injections=[inj])
    assert [e for e in log.events(type=cd.type)], "the event must still be in the log"
    log.close()
