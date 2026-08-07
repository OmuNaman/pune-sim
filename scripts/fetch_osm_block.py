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

import argparse
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

# Named extracts, south,west,north,east. `kasba` is the V0-V2 pin: every
# determinism hash and every soak in docs/soaks/ is a function of it, so its
# bbox is frozen. `oldcity` is V3's block — the same old-city core widened to
# Kasba + Shaniwar + Budhwar + Raviwar, because Kasba alone yields 2,880 home
# candidates and V3 needs 12k households.
EXTRACTS = {
    "kasba": {
        "bbox": "18.510,73.850,18.522,73.862",
        "raw": "osm_kasba_block_raw.json",
        "roads": "kasba_roads.geojson",
        "places": "kasba_places.geojson",
    },
    "oldcity": {
        "bbox": "18.505,73.845,18.532,73.870",
        "raw": "osm_oldcity_raw.json",
        "roads": "oldcity_roads.geojson",
        "places": "oldcity_places.geojson",
    },
}

OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]

def query(bbox: str) -> str:
    return f"""[out:json][timeout:600];
(
  way["building"]({bbox});
  way["highway"]({bbox});
  nwr["amenity"]({bbox});
  nwr["shop"]({bbox});
  nwr["leisure"]({bbox});
  nwr["healthcare"]({bbox});
  nwr["religion"]({bbox});
  node["place_of_worship"]({bbox});
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


def download(dest: Path, bbox: str) -> None:
    data = urllib.parse.urlencode({"data": query(bbox)}).encode("ascii")
    last_err: Exception | None = None
    for url in OVERPASS_ENDPOINTS:
        req = urllib.request.Request(
            url, data=data, headers={"User-Agent": "pune-sim/0.0.1 anchor fetch"}
        )
        try:
            print(f"POST {url} ...")
            with urllib.request.urlopen(req, timeout=900) as resp:
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
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--extract", choices=sorted(EXTRACTS), default="kasba",
                    help="which named block to build (kasba is the frozen V0-V2 pin)")
    ap.add_argument("--force-download", action="store_true")
    args = ap.parse_args()

    spec = EXTRACTS[args.extract]
    raw_path = ANCHORS / spec["raw"]
    roads_path = ANCHORS / spec["roads"]
    places_path = ANCHORS / spec["places"]

    ANCHORS.mkdir(parents=True, exist_ok=True)
    if args.force_download or not raw_path.exists():
        download(raw_path, spec["bbox"])
    else:
        print(f"reusing existing {raw_path.name} (pass --force-download to refetch)")

    raw = _loads(raw_path.read_bytes())
    roads, places = convert(raw)
    roads_path.write_bytes(_dumps(roads))
    places_path.write_bytes(_dumps(places))

    n_poly = sum(
        1 for f in places["features"] if f["geometry"]["type"] == "Polygon"
    )
    n_pt = len(places["features"]) - n_poly
    print(f"{roads_path.name}: {len(roads['features'])} LineString features")
    print(f"{places_path.name}: {len(places['features'])} features "
          f"({n_poly} Polygons, {n_pt} Points)")
    print(f"bbox {spec['bbox']}  raw {raw_path.stat().st_size:,} B  "
          f"places {places_path.stat().st_size:,} B")


if __name__ == "__main__":
    main()
