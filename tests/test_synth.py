from collections import Counter

import pytest

from punesim.population import synthesize
from punesim.world.block import Block

pytestmark = pytest.mark.skipif(
    not __import__("pathlib").Path("data/anchors/kasba_places.geojson").exists(),
    reason="anchor data not fetched",
)


@pytest.fixture(scope="module")
def block():
    return Block.load()


def test_same_seed_same_population(block):
    h1, p1 = synthesize(42, block, n_households=40)
    h2, p2 = synthesize(42, block, n_households=40)
    assert [h.home_id for h in h1] == [h.home_id for h in h2]
    assert {k: v.name for k, v in p1.items()} == {k: v.name for k, v in p2.items()}


def test_different_seed_differs(block):
    _, p1 = synthesize(1, block, n_households=40)
    _, p2 = synthesize(2, block, n_households=40)
    assert {k: v.name for k, v in p1.items()} != {k: v.name for k, v in p2.items()}


def test_structure_is_sane(block):
    hhs, people = synthesize(7, block, n_households=80)
    assert len(hhs) == 80
    # Household religion is quota-assigned: exact largest-remainder shares.
    hh_rel = Counter(h.religion for h in hhs)
    assert hh_rel["muslim"] == 10 and hh_rel["buddhist_navayana"] == 4
    assert hh_rel["jain"] == 2 and hh_rel["christian"] == 1
    religions = Counter(p.religion for p in people.values())
    assert religions["hindu"] > religions.get("muslim", 0) > 0
    for p in people.values():
        assert block.get(p.home_id) is not None
        if p.occupation == "student" and p.age >= 5:
            assert p.work_id is not None, f"{p.id} student without school"
        if p.household_id != p.id.rsplit(".", 1)[0].replace("person:", "hh:"):
            pytest.fail("household id mismatch")


def test_names_match_identity_pools(block):
    _, people = synthesize(9, block, n_households=80)
    from punesim.population import names

    for p in people.values():
        assert p.surname in names.SURNAME[p.religion], f"{p.name} surname off-pool for {p.religion}"
