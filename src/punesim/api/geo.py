"""The city's shape, which no endpoint has ever served.

`/api/places` gave the client 438 labelled points. The block underneath holds
7,360 building polygons, 7,008 of which are somebody's home, and 2,057 road
LineStrings with their `highway` class and 575 street names — all vendored,
checksummed, and never once sent to a browser. The old map drew grey tiles from
a CDN and put dots on top.

Two joins make the geometry usable rather than decorative:

`Place.id` is `place:way/22840813` and the GeoJSON feature id is `way/22840813`,
so a building knows which simulated place it is by string suffix — for free,
with no spatial index. A clicked polygon becomes a place card.

And a peth is just a bounding box here. The four are real historical
neighbourhoods with real edges, but the extract carries no boundary polygons, so
this assigns by rectangle and says so rather than pretending to a precision it
does not have. It is for tinting a map, not for answering questions.
"""

from pathlib import Path

import orjson

from ..world.block import BLOCKS

# Approximate centres of the four peths in the oldcity extract, used to tint
# buildings by district. Boundaries between peths are genuinely irregular —
# these are nearest-centre cells, which is a cartoon of the real thing and
# exists so the map has districts, not so anything can be measured by peth.
PETH_CENTRES = {
    "kasba": (18.5195, 73.8555),
    "shaniwar": (18.5190, 73.8530),
    "budhwar": (18.5165, 73.8560),
    "raviwar": (18.5130, 73.8570),
}

# Which highway classes get drawn how thick. Same vocabulary the road graph
# already walks on (world/roads.py `_WALKABLE`).
ROAD_CLASS = {
    "motorway": 0, "trunk": 0, "primary": 1, "secondary": 2, "tertiary": 3,
    "residential": 4, "unclassified": 4, "living_street": 4, "service": 5,
    "pedestrian": 6, "footway": 7, "path": 7, "steps": 7, "track": 7,
}


def _peth_of(lat: float, lon: float) -> str:
    return min(
        PETH_CENTRES,
        key=lambda k: (lat - PETH_CENTRES[k][0]) ** 2 + (lon - PETH_CENTRES[k][1]) ** 2,
    )


def _ring_centroid(coords) -> tuple[float, float] | None:
    """Mean of the first ring — the same approximation `block._centroid` uses,
    kept identical on purpose so a building's tint agrees with its place's dot."""
    if not coords or not coords[0]:
        return None
    ring = coords[0]
    return (sum(c[1] for c in ring) / len(ring), sum(c[0] for c in ring) / len(ring))


class GeoLayers:
    """Built once per block, held in memory. ~7 MB of GeoJSON for oldcity."""

    def __init__(self, block_name: str):
        if block_name not in BLOCKS:
            raise ValueError(f"unknown block {block_name!r}")
        self.block_name = block_name
        places_path, roads_path, _routes = BLOCKS[block_name]
        self.places_path, self.roads_path = Path(places_path), Path(roads_path)
        self._cache: dict[str, bytes] = {}

    def layer(self, which: str) -> bytes:
        """Serialised GeoJSON for one layer, cached."""
        hit = self._cache.get(which)
        if hit is None:
            build = {"buildings": self._buildings, "roads": self._roads}.get(which)
            if build is None:
                raise KeyError(which)
            hit = self._cache[which] = orjson.dumps(build())
        return hit

    def _buildings(self) -> dict:
        """Every polygon, tagged with its peth and its sim id where it has one.

        `role` is what the client colours by: a named amenity is a `place` the
        sim knows and can be clicked into; a residential building with no name
        is a `home` somebody lives in; the rest is fabric — it exists so the
        street has walls.
        """
        data = orjson.loads(self.places_path.read_bytes())
        feats = []
        for f in data["features"]:
            geom = f.get("geometry") or {}
            if geom.get("type") != "Polygon":
                continue
            c = _ring_centroid(geom.get("coordinates"))
            if c is None:
                continue
            props = f.get("properties", {}) or {}
            fid = str(f.get("id") or "")
            named = bool(props.get("name"))
            residential = props.get("building") in (
                "yes", "house", "residential", "apartments", "detached", "terrace")
            role = "place" if named else ("home" if residential else "fabric")
            feats.append({
                "type": "Feature",
                "geometry": geom,
                "properties": {
                    "sim_id": f"place:{fid}" if named else (f"home:{fid}" if residential else None),
                    "osm": fid,
                    "role": role,
                    "name": props.get("name") or "",
                    "peth": _peth_of(*c),
                },
            })
        return {"type": "FeatureCollection", "features": feats}

    def _roads(self) -> dict:
        data = orjson.loads(self.roads_path.read_bytes())
        feats = []
        for f in data["features"]:
            geom = f.get("geometry") or {}
            if geom.get("type") != "LineString":
                continue
            props = f.get("properties", {}) or {}
            hw = props.get("highway", "")
            feats.append({
                "type": "Feature",
                "geometry": geom,
                "properties": {
                    "name": props.get("name") or "",
                    "highway": hw,
                    "rank": ROAD_CLASS.get(hw, 5),
                },
            })
        return {"type": "FeatureCollection", "features": feats}


_LAYERS: dict[str, GeoLayers] = {}


def layers_for(block_name: str) -> GeoLayers:
    hit = _LAYERS.get(block_name)
    if hit is None:
        hit = _LAYERS[block_name] = GeoLayers(block_name)
    return hit
