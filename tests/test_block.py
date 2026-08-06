import pytest

from punesim.world.block import Block

pytestmark = pytest.mark.skipif(
    not __import__("pathlib").Path("data/anchors/kasba_places.geojson").exists(),
    reason="anchor data not fetched",
)


@pytest.fixture(scope="module")
def block() -> Block:
    return Block.load()


def test_real_places_loaded(block):
    assert len(block.places) > 100
    kinds = {p.kind for p in block.places}
    assert {"temple", "school", "hospital", "police", "shop"} <= kinds
    assert len(block.homes) > 100
    names = {p.name for p in block.places}
    assert "Kasba Ganpati" in names  # the reality anchor of reality anchors


def test_nearest_and_walk(block):
    home = block.homes[0]
    school = block.nearest(home.id, "school")
    assert school is not None and school.kind == "school"
    secs = block.walk_seconds(home.id, school.id)
    assert 60 <= secs < 3600
    assert secs == block.walk_seconds(school.id, home.id)


def test_load_is_deterministic(block):
    again = Block.load()
    assert [p.id for p in again.places] == [p.id for p in block.places]
    assert [h.id for h in again.homes] == [h.id for h in block.homes]
