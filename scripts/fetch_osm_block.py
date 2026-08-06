"""Fetch and convert the Kasba Peth OSM block (V0 map slice).

Downloads a small Overpass extract of the old-city core — bbox
(south=18.510, west=73.850, north=18.522, east=73.862) — and converts it to
two GeoJSON files:

  data/anchors/osm_kasba_block_raw.json   raw Overpass response (provenance pin)
  data/anchors/kasba_roads.geojson        highway ways as LineStrings
  data/anchors/kasba_places.geojson       building ways as Polygons + tagged POI
                                          nodes as Points (name/amenity/shop/
                                          religion/building tags kept)

Usage (from repo root):
    uv run python scripts/fetch_osm_block.py [--force-download]

If the raw JSON already exists it is reused (the pin is the point); pass
--force-download to refetch. Stdlib + orjson only.
"""

from __future__ import annotations

import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

try:
    import orjson as _oj

    def _loads(b: bytes):
        return _oj.loads(b)

    def _dumps(obj) -> bytes:
        return _oj.dumps(obj, option=_oj.OPT_INDENT_2)

except ImportError:  # pragma: no cover - orjson is a project dep
    import json as _j

    def _loads(b: bytes):
        return _j.loads(b)

    def _dumps(obj) -> bytes:
        return _j.dumps(obj, indent=2, ensure_ascii=False).encode("utf-8")


REPO_ROOT = Path(__file__).resolve().parent.parent
ANCHORS = REPO_ROOT / "data" / "anchors"
RAW_PATH = ANCHORS / "osm_kasba_block_raw.json"
ROADS_PATH = ANCHORS / "kasba_roads.geojson"
PLACES_PATH = ANCHORS / "kasba_places.geojson"

BBOX = "18.510,73.850,18.522,73.862"  # south,west,north,east

OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]

QUERY = f"""[out:json][timeout:180];
(
  way["building"]({BBOX});
  way["highway"]({BBOX});
  nwr["amenity"]({BBOX});
  nwr["shop"]({BBOX});
  nwr["leisure"]({BBOX});
  nwr["healthcare"]({BBOX});
  nwr["religion"]({BBOX});
  node["place_of_worship"]({BBOX});
);
out geom;"""

# Tag keys copied onto output features (plus `highway` for roads).
KEEP_TAGS = (
    "name",
    "amenity",
    "shop",
    "religion",
    "denomination",
    "leisure",
    "healthcare",
    "building",
)

# A way with any of these keys is a "place" even without a building tag.
POI_KEYS = ("amenity", "shop", "religion", "leisure", "healthcare")


def download(dest: Path) -> None:
    data = urllib.parse.urlencode({"data": QUERY}).encode("ascii")
    last_err: Exception | None = None
    for url in OVERPASS_ENDPOINTS:
        req = urllib.request.Request(
            url, data=data, headers={"User-Agent": "pune-sim/0.0.1 anchor fetch"}
        )
        try:
            print(f"POST {url} ...")
            with urllib.request.urlopen(req, timeout=200) as resp:
                body = resp.read()
            _loads(body)  # validate before writing
            dest.write_bytes(body)
            print(f"  saved {dest.name} ({len(body):,} bytes)")
            return
        except (urllib.error.URLError, TimeoutError, ValueError) as err:
            print(f"  failed: {err}", file=sys.stderr)
            last_err = err
    raise SystemExit(f"all Overpass endpoints failed: {last_err}")


def _keep(tags: dict, extra: tuple[str, ...] = ()) -> dict:
    return {k: tags[k] for k in (*KEEP_TAGS, *extra) if k in tags}


def _ring(geometry: list[dict]) -> list[list[float]]:
    coords = [[pt["lon"], pt["lat"]] for pt in geometry]
    if coords and coords[0] != coords[-1]:
        coords.append(coords[0])
    return coords


def convert(raw: dict) -> tuple[dict, dict]:
    roads: list[dict] = []
    places: list[dict] = []
    for el in raw.get("elements", []):
        tags = el.get("tags") or {}
        etype, eid = el.get("type"), el.get("id")
        if etype == "way" and "highway" in tags and el.get("geometry"):
            roads.append(
                {
                    "type": "Feature",
                    "id": f"way/{eid}",
                    "properties": _keep(tags, ("highway",)),
                    "geometry": {
                        "type": "LineString",
                        "coordinates": [
                            [pt["lon"], pt["lat"]] for pt in el["geometry"]
                        ],
                    },
                }
            )
        if etype == "way" and el.get("geometry"):
            is_place = "building" in tags or any(k in tags for k in POI_KEYS)
            if is_place and "highway" not in tags:
                ring = _ring(el["geometry"])
                if len(ring) >= 4:  # valid closed ring
                    places.append(
                        {
                            "type": "Feature",
                            "id": f"way/{eid}",
                            "properties": _keep(tags),
                            "geometry": {"type": "Polygon", "coordinates": [ring]},
                        }
                    )
        elif etype == "node" and tags:
            places.append(
                {
                    "type": "Feature",
                    "id": f"node/{eid}",
                    "properties": _keep(tags),
                    "geometry": {
                        "type": "Point",
                        "coordinates": [el["lon"], el["lat"]],
                    },
                }
            )
    fc = lambda feats: {"type": "FeatureCollection", "features": feats}
    return fc(roads), fc(places)


def main() -> None:
    ANCHORS.mkdir(parents=True, exist_ok=True)
    if "--force-download" in sys.argv or not RAW_PATH.exists():
        download(RAW_PATH)
    else:
        print(f"reusing existing {RAW_PATH.name} (pass --force-download to refetch)")

    raw = _loads(RAW_PATH.read_bytes())
    roads, places = convert(raw)
    ROADS_PATH.write_bytes(_dumps(roads))
    PLACES_PATH.write_bytes(_dumps(places))

    n_poly = sum(
        1 for f in places["features"] if f["geometry"]["type"] == "Polygon"
    )
    n_pt = len(places["features"]) - n_poly
    print(f"{ROADS_PATH.name}: {len(roads['features'])} LineString features")
    print(f"{PLACES_PATH.name}: {len(places['features'])} features "
          f"({n_poly} Polygons, {n_pt} Points)")


if __name__ == "__main__":
    main()
