"""Hazard rates are per-capita, and the one measured rate stays tied to its source.

Task #25 in full: `p_per_day` was absolute, so the same ~0.25 hazards a day fell
on 306 people and on 49,578 — 298 per 1,000 people per year against 1.84, and
0.03 at Pune's 3.1M. Growing the world walked the number down through plausible
without anyone choosing a value.

These tests hold the two halves of the fix apart. The shape (a rate is a
property of a population) is tested against arithmetic. The level (0.394 road
collisions per 1,000 per year) is tested against the vendored MoRTH table, so a
number cannot drift from its own anchor unnoticed.
"""

import importlib.util
import pathlib
import sys

import pytest

from punesim.world import classdefs, hazards

_SPEC = importlib.util.spec_from_file_location(
    "hazard_rates", pathlib.Path(__file__).resolve().parents[1] / "scripts" / "hazard_rates.py"
)
hazard_rates = importlib.util.module_from_spec(_SPEC)
sys.modules["hazard_rates"] = hazard_rates
_SPEC.loader.exec_module(hazard_rates)

pytestmark = pytest.mark.skipif(
    not pathlib.Path("data/anchors/morth_road_accidents_large_cities_2023.csv").exists(),
    reason="anchor data not fetched",
)


def test_the_shipped_road_rate_is_the_one_the_anchor_implies():
    """0.393668 is not a preference — it is 1,230 / 3,124,458 x 1000."""
    morth_accidents = hazard_rates.morth_accidents
    pmc_population, road_rate_per_1k = hazard_rates.pmc_population, hazard_rates.road_rate_per_1k

    assert morth_accidents("Pune", "2023") == 1230
    assert pmc_population() == 3_124_458
    shipped = {c.type: c for c in classdefs.load()}["hazard.road.collision"]
    assert shipped.rate_per_1k_per_year == pytest.approx(road_rate_per_1k(), abs=5e-7)
    assert shipped.provenance == "morth-2023:pune"


def test_a_class_without_a_source_says_so():
    """The other three are the old absolute setting held at the V3 population.

    They are honest estimates and must stay labelled as such: `provenance` is
    how a later calibration knows which numbers it is allowed to replace without
    argument, and `measured` is the property any report should print.
    """
    by_type = {c.type: c for c in classdefs.load()}
    assert by_type["hazard.road.collision"].measured
    for t in ("hazard.water.supply_cut", "hazard.power.outage", "hazard.fire.small"):
        cd = by_type[t]
        assert not cd.measured and cd.provenance == "estimate@49578"
        # held at the reference population, they reproduce the old absolute rate
        assert cd.expected_per_day(classdefs.REFERENCE_POPULATION) == pytest.approx(
            {"hazard.water.supply_cut": 0.06, "hazard.power.outage": 0.07,
             "hazard.fire.small": 0.02}[t], abs=1e-6)


def test_a_world_twice_the_size_has_twice_the_trouble():
    """The defect itself, as an assertion."""
    cd = {c.type: c for c in classdefs.load()}["hazard.road.collision"]
    assert cd.expected_per_day(2000) == pytest.approx(2 * cd.expected_per_day(1000))
    assert cd.expected_per_day(306) < cd.expected_per_day(49_578) < cd.expected_per_day(3_124_458)
    # and the level is now the city's own: a year of the real thing, city-sized
    assert cd.expected_per_day(3_124_458) * 365 == pytest.approx(1230, rel=1e-5)


def test_an_absolute_rate_is_refused_at_load(tmp_path):
    """The old field name must not load as if nothing had changed.

    A p_per_day of 0.10 read as a per-1k rate would be 15 collisions a day at V3
    scale. Silence here is worse than a crash.
    """
    import json

    spec = {
        "type": "hazard.test", "p_per_day": 0.1, "window": ["08:00", "09:00"],
        "shape": "point", "predicate": "test", "topics": ["safety"], "charge": 0.5,
    }
    path = tmp_path / "old.json"
    path.write_text(json.dumps({"classes": [spec]}), encoding="utf-8")
    with pytest.raises(ValueError, match="per-capita"):
        classdefs.load(path)


def test_a_big_world_can_have_more_than_one_of_a_kind_in_a_day():
    """A Bernoulli caps trouble at one per class per day; Pune passes that at
    ~927,000 people, so the 3.5M path would have silently flattened."""
    cd = {c.type: c for c in classdefs.load()}["hazard.road.collision"]
    assert cd.expected_per_day(3_500_000) > 3.0
    counts = [
        int(hazards.keyed_rng(108, "hazard", "hazard.road.collision", d, "realize")
            .poisson(cd.expected_per_day(3_500_000)))
        for d in range(60)
    ]
    assert max(counts) > 1, "a city of 3.5M never had two collisions in one day"
