"""Does the synthesized population match the place it claims to be?

The architecture backlog asks for "calibration targets with acceptance bands"
as a cross-cutting policy before EVENTS lands. This is the first one: three
marginals from the pinned 2011 census, checked against what `synthesize`
actually produces on V3's block.

Bands, not equalities. The census's old-city unit is the Kasbavishrambaug ward
office (13 wards, 43,138 households) and the block is four peths inside it, so
these are ratio targets that transfer, not counts that tile. Per-ward household
size across those 13 wards runs 3.66-4.92; a band of +/-0.10 on the mean is
tighter than the real spread between neighbouring wards.
"""

import pathlib

import pytest

from punesim.population import synthesize
from punesim.population.demography import KASBA, OLD_CITY_TARGETS, for_block
from punesim.world.block import load_for

pytestmark = pytest.mark.skipif(
    not pathlib.Path("data/anchors/oldcity_places.geojson").exists(),
    reason="oldcity anchor not fetched",
)

HOUSEHOLDS = 6000
BANDS = {"household_size": 0.10, "male_share": 0.010, "under_7_share": 0.010}


@pytest.fixture(scope="module")
def marginals():
    block = load_for(HOUSEHOLDS, "oldcity")
    hh, people = synthesize(108, block, n_households=HOUSEHOLDS)
    n = len(people)
    return {
        "household_size": n / len(hh),
        "male_share": sum(1 for p in people.values() if p.sex == "m") / n,
        "under_7_share": sum(1 for p in people.values() if p.age < 7) / n,
    }


@pytest.mark.parametrize("marginal", sorted(OLD_CITY_TARGETS))
def test_oldcity_matches_its_census(marginal, marginals):
    got, want, band = marginals[marginal], OLD_CITY_TARGETS[marginal], BANDS[marginal]
    assert abs(got - want) <= band, (
        f"{marginal}: synthesis gives {got:.4f}, the Kasbavishrambaug wards give "
        f"{want:.4f} (band +/-{band}). Re-fit with scripts/fit_synthesis.py rather "
        f"than widening the band."
    )


def test_kasba_still_draws_from_the_frozen_table():
    """The V0-V2 block must never pick up V3's demography.

    Its determinism hash is a function of these exact numbers, so a well-meant
    "let's calibrate everything" would silently invalidate every soak in
    docs/soaks/. test_scale_guard.py pins the hash; this names the reason.
    """
    assert for_block("kasba") is KASBA
    assert for_block("something-nobody-defined") is KASBA
    assert KASBA.p_grandparent_widowed == 0.0, (
        "a non-zero widow rate adds a keyed draw to every joint household, "
        "which reorders kasba's entire draw sequence"
    )
