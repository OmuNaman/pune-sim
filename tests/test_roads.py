"""Walking along the streets rather than through them."""

import pathlib

import pytest

from punesim.world.block import DETOUR_FACTOR, haversine_m, load_for

pytestmark = pytest.mark.skipif(
    not pathlib.Path("data/anchors/oldcity_roads.geojson").exists(),
    reason="oldcity anchor not fetched",
)


@pytest.fixture(scope="module")
def routed():
    return load_for(400, "oldcity", roads=True)


def test_the_graph_is_connected_enough_to_be_useful(routed):
    g = routed.roads
    assert g is not None
    assert len(g.nodes) > 5000, "the four-peth extract should node into thousands of vertices"
    # every named place must reach a lane, or trips from it silently fall back
    assert len(g._from_place) == len(routed.places)


def test_walking_the_streets_is_never_shorter_than_the_crow_flies(routed):
    """The one property that cannot be negotiated.

    A route along real ways is at least the straight-line distance, always. If
    this fails the graph has an edge that teleports — usually a coordinate
    rounding that welded two distant vertices into one node.
    """
    homes = routed.homes[:120]
    checked = 0
    for h in homes:
        for p in routed.places[:8]:
            m = routed.roads.metres(p.id, (p.lat, p.lon), h.id, (h.lat, h.lon))
            if m is None:
                continue
            straight = haversine_m(h.lat, h.lon, p.lat, p.lon)
            assert m >= straight - 1.0, (
                f"{h.id}->{p.id}: street route {m:.0f}m is shorter than the "
                f"straight line {straight:.0f}m"
            )
            checked += 1
    assert checked > 200, "not enough routable pairs to call this tested"


def test_routing_actually_changes_the_walk(routed):
    """...and in both directions, which is the point of having a graph.

    Measured over 2,000 home-to-place pairs on the four-peth block, the flat
    1.4 detour factor is *generous* far more often than not: 88% of walks get
    shorter once routed (median 0.93x of the estimate) because a peth's grid is
    tighter than 1.4. What it cannot do is know about the 11% that go the long
    way round, and those reach 2.4x. A single constant has to be wrong in one
    direction or the other; a graph is wrong in neither.
    """
    straight = load_for(400, "oldcity", roads=False)
    assert straight.roads is None
    pairs = [(h.id, p.id) for h in routed.homes[:200] for p in routed.places[:10]]
    routed_s = [routed.walk_seconds(a, b) for a, b in pairs]
    flat_s = [straight.walk_seconds(a, b) for a, b in pairs]
    assert all(s >= 60 for s in routed_s)

    changed = sum(1 for r, f in zip(routed_s, flat_s, strict=True) if r != f)
    assert changed > len(pairs) * 0.8, "a road graph that agrees with a constant is not a road graph"

    ratios = [r / f for r, f in zip(routed_s, flat_s, strict=True) if f > 60]
    ratios.sort()
    assert ratios[len(ratios) // 2] < 1.0, "the flat factor should read generous on a typical walk"
    assert max(ratios) > 1.5, (
        "no walk goes appreciably the long way round, which means the graph is "
        "either too sparse to have obstacles or is welding vertices together"
    )


def test_kasba_never_routes(routed):
    """Its travel times are baked into every soak hash in docs/soaks/."""
    assert load_for(80, "kasba").roads is None
    assert DETOUR_FACTOR == 1.4
