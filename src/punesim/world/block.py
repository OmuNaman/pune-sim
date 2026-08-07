"""V0 world: the Kasba Peth block, loaded from the pinned OSM extract.

Real named places anchor the sim (reality anchors are read-only); unnamed
residential buildings become home candidates. There is no routing graph yet —
walking time is haversine distance x a detour factor. V3 replaces this with
the real graph; nothing upstream may depend on how travel time is computed.
"""

import math
from dataclasses import dataclass
from pathlib import Path

import orjson

WALK_SPEED_MPS = 1.2
DETOUR_FACTOR = 1.4

_WORSHIP_BY_RELIGION = {
    "hindu": "temple",
    "muslim": "mosque",
    "christian": "church",
    "jain": "jain_temple",
    "buddhist": "vihara",
    "sikh": "gurdwara",
    "jewish": "synagogue",
}

_KIND_BY_AMENITY = {
    "school": "school",
    "college": "school",
    "kindergarten": "school",
    "hospital": "hospital",
    "clinic": "clinic",
    "doctors": "clinic",
    "pharmacy": "shop",
    "police": "police",
    "bank": "bank",
    "post_office": "office",
    "restaurant": "restaurant",
    "cafe": "restaurant",
    "fast_food": "restaurant",
    "events_venue": "venue",
    "community_centre": "venue",
    "marketplace": "market",
    "bus_station": "bus_stop",
}

_HOME_BUILDINGS = {"yes", "house", "residential", "apartments", "detached", "terrace"}

# The home pool is capped so that `synthesize` draws the same permutation for a
# given seed no matter how many candidates the extract happens to contain. Grow
# it only when a run needs more households than the cap — see `load_for`.
DEFAULT_MAX_HOMES = 400

# Named blocks, built by scripts/fetch_osm_block.py. `kasba` is the V0-V2 pin:
# every determinism hash and every soak in docs/soaks/ is a function of it, so
# it stays the default forever. `oldcity` is V3's block — the same core widened
# to four peths (438 named places, 7,008 buildings; households stack into them
# because a wada is a compound, not a family).
# places file, roads file, and whether walking uses the road graph. Kasba routes
# straight-line for ever: every determinism hash and every soak in docs/soaks/
# is a function of its travel times, and a road graph changes all of them.
BLOCKS = {
    "kasba": ("data/anchors/kasba_places.geojson", "data/anchors/kasba_roads.geojson", False),
    "oldcity": ("data/anchors/oldcity_places.geojson", "data/anchors/oldcity_roads.geojson", True),
}
DEFAULT_BLOCK = "kasba"


@dataclass(frozen=True)
class Place:
    id: str
    name: str
    kind: str
    lat: float
    lon: float


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _centroid(geom: dict) -> tuple[float, float] | None:
    """(lat, lon) for Point/Polygon; None for anything else."""
    if geom["type"] == "Point":
        lon, lat = geom["coordinates"]
        return lat, lon
    if geom["type"] == "Polygon" and geom["coordinates"]:
        ring = geom["coordinates"][0]
        if not ring:
            return None
        lat = sum(c[1] for c in ring) / len(ring)
        lon = sum(c[0] for c in ring) / len(ring)
        return lat, lon
    return None


def _classify(props: dict) -> str | None:
    amenity = props.get("amenity")
    if amenity == "place_of_worship":
        return _WORSHIP_BY_RELIGION.get(props.get("religion", ""), "temple")
    if amenity in _KIND_BY_AMENITY:
        return _KIND_BY_AMENITY[amenity]
    if props.get("shop"):
        return "shop"
    if props.get("office"):
        return "office"
    return None


class Block:
    """Immutable V0 world model: named places + home candidates."""

    def __init__(self, places: list[Place], homes: list[Place], name: str = "kasba",
                 roads=None):
        self.name = name
        self.roads = roads  # RoadGraph | None — None means straight-line walking
        self.places = places
        self.homes = homes
        self._by_id = {p.id: p for p in [*places, *homes]}
        # The block never changes after load, so "which places are shops" and
        # "which shop is nearest this home" are constants. Recomputing them per
        # call meant a haversine to every place, every errand, every day: 1.5M
        # distance calculations in a 4-day 11k-person probe.
        self._of_kind: dict[frozenset[str], list[Place]] = {}
        self._nearest: dict[tuple[str, frozenset[str]], Place | None] = {}
        self._walk: dict[tuple[str, str], int] = {}

    def __getitem__(self, place_id: str) -> Place:
        return self._by_id[place_id]

    def get(self, place_id: str) -> Place | None:
        return self._by_id.get(place_id)

    def of_kind(self, *kinds: str) -> list[Place]:
        key = frozenset(kinds)
        hit = self._of_kind.get(key)
        if hit is None:
            hit = self._of_kind[key] = [p for p in self.places if p.kind in kinds]
        return hit

    def nearest(self, from_id: str, *kinds: str) -> Place | None:
        key = (from_id, frozenset(kinds))
        if key in self._nearest:  # None is a real answer, so `in`, not `.get`
            return self._nearest[key]
        src = self._by_id[from_id]
        candidates = self.of_kind(*kinds)
        found = (
            min(candidates, key=lambda p: (haversine_m(src.lat, src.lon, p.lat, p.lon), p.id))
            if candidates else None
        )
        self._nearest[key] = found
        return found

    def walk_seconds(self, a_id: str, b_id: str) -> int:
        """How long it takes to walk between two places.

        Along the streets where the block has a road graph and both ends reach
        it; a straight line times DETOUR_FACTOR otherwise. The constant is a
        fair average — measured against the real graph its median is 1.29 — but
        it cannot know about the trips that go the long way round, and those
        run to 3.4x. Cached: the same pairs recur every day of a run.
        """
        key = (a_id, b_id)
        hit = self._walk.get(key)
        if hit is not None:
            return hit
        a, b = self._by_id[a_id], self._by_id[b_id]
        d = None
        if self.roads is not None:
            d = self.roads.metres(a_id, (a.lat, a.lon), b_id, (b.lat, b.lon))
        if d is None:
            d = haversine_m(a.lat, a.lon, b.lat, b.lon) * DETOUR_FACTOR
        out = self._walk[key] = self._walk[(b_id, a_id)] = max(60, int(d / WALK_SPEED_MPS))
        return out

    @classmethod
    def load(
        cls,
        places_path: str | Path = "data/anchors/kasba_places.geojson",
        *,
        max_homes: int = DEFAULT_MAX_HOMES,
        name: str = DEFAULT_BLOCK,
    ) -> "Block":
        data = orjson.loads(Path(places_path).read_bytes())
        places: list[Place] = []
        homes: list[Place] = []
        for feat in data["features"]:
            props = feat.get("properties", {})
            ll = _centroid(feat.get("geometry", {}))
            if ll is None:
                continue
            lat, lon = ll
            fid = str(feat.get("id") or props.get("id") or f"{lat:.6f},{lon:.6f}")
            kind = _classify(props)
            label = props.get("name")
            if kind and label:
                places.append(Place(id=f"place:{fid}", name=label, kind=kind, lat=lat, lon=lon))
            elif (
                kind is None
                and not label
                and props.get("building") in _HOME_BUILDINGS
            ):
                homes.append(Place(id=f"home:{fid}", name="", kind="home", lat=lat, lon=lon))
        places.sort(key=lambda p: p.id)
        homes.sort(key=lambda p: p.id)
        return cls(places, homes[:max_homes], name=name)


def load_for(n_households: int, block: str = DEFAULT_BLOCK, roads: bool | None = None,
             **kw) -> Block:
    """The block a run of this size needs.

    Below the cap this is exactly `Block.load()`, so every existing run keeps
    its determinism hash; above it the pool grows to fit. Home assignment is a
    permutation over the whole pool, so *any* change to the pool size reshuffles
    everyone — which is why the cap does not simply track the extract.
    """
    if block not in BLOCKS:
        raise ValueError(f"unknown block {block!r}; known: {', '.join(sorted(BLOCKS))}")
    places_path, roads_path, route = BLOCKS[block]
    if roads is not None:
        route = roads
    world = Block.load(places_path, max_homes=max(DEFAULT_MAX_HOMES, n_households),
                       name=block, **kw)
    if route and Path(roads_path).exists():
        from .roads import RoadGraph

        graph = RoadGraph.load(roads_path)
        graph.prepare({p.id: (p.lat, p.lon) for p in world.places})
        world.roads = graph
    return world
