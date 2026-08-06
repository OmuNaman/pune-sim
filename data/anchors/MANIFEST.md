# Data anchors — pinned 2026-07-31, extended 2026-08-06

Reality anchors vendored into the repo so runs are reproducible even if the
sources rot. Per architecture §9.3, the anchor manifest hash joins the
determinism hash (ruling 16 disposition list). SHA256 prefixes below; full
hashes in `CHECKSUMS.txt`; verify with `Get-FileHash -Algorithm SHA256`.

| File | SHA256 (first 16) | Source | Notes |
|---|---|---|---|
| `pmpml_gtfs.zip` | `591426C1919E60DC` | github.com/croyla/pmpml-gtfs (main, 2026-07) | 494 routes / 6,203 stops / 10,728 trips / 455,820 stop_times; unofficial scrape — this pinned zip is the build input, never the live feed. Re-verified 2026-08-06: upstream `raw.githubusercontent.com/croyla/pmpml-gtfs/main/pmpml_gtfs.zip` is still byte-identical (no drift) |
| `datameet_pune_wards.zip` | `7FA7A305E16771F6` | github.com/datameet/Pune_wards (master) | 2012-vintage electoral wards + admin wards GeoJSON — sketch layer only (see §9.3 ward-geometry caveat) |
| `pmc_wardwise_census2011.csv` | `84B47692D99ABC13` | data.opencity.in (PMC ward-wise Census 2011) | 160 data rows: households, population by sex, 0–6, SC, ST per census ward |
| `pune_censuswards_2011.csv` | `BD7A9C3E6135976F` | data.opencity.in (census-wards level) | companion table, census-ward granularity |

## Added 2026-08-06

| File | Source URL | Fetched | Size | SHA256 (first 16) | Notes |
|---|---|---|---|---|---|
| `osm_kasba_block_raw.json` | POST https://overpass-api.de/api/interpreter (bbox 18.510,73.850,18.522,73.862; query in `scripts/fetch_osm_block.py`) | 2026-08-06 | 2,118,494 B | `6DCCA9383EA232FD` | Raw Overpass response, OSM base timestamp 2026-08-06T11:31:13Z. 3,582 elements: 499 highway ways, 2,926 building ways, 136 tagged POI nodes. Overpass API 0.7.62.11 |
| `kasba_roads.geojson` | derived from `osm_kasba_block_raw.json` by `scripts/fetch_osm_block.py` | 2026-08-06 | 340,192 B | `3CFC517A58A04B23` | 499 LineString features (highway ways; `highway` + name tags) |
| `kasba_places.geojson` | derived from `osm_kasba_block_raw.json` by `scripts/fetch_osm_block.py` | 2026-08-06 | 2,045,191 B | `5E97BEBB260C2CC0` | 3,083 features: 2,947 Polygons (building + POI-tagged closed ways) + 136 Points (POI nodes); keeps name/amenity/shop/religion/denomination/leisure/healthcare/building tags. V0 hand-trimmable map block |
| `pune_ward_census_2011.csv` | https://data.opencity.in/dataset/ed14d426-552a-43eb-adfa-dbfd3afbd068/resource/0418cf26-969c-4190-9542-70b1fc8500d2/download/fcaa315c-2d63-42ad-81b6-07e7c02debf4.csv | 2026-08-06 | 28,504 B | `BD7A9C3E6135976F` | **Byte-identical to `pune_censuswards_2011.csv`** (same SHA256) — kept under both names; this one records the exact resolved resource URL (OpenCity resource id 0418cf26-969c-4190-9542-70b1fc8500d2, license Public Domain, upstream opendata.punecorporation.org). 146 ward rows (Ward No.1–144 + 2 added-area wards) + PMC/Total rows; columns: households, total pop (M/F), SC (M/F), ST (M/F), literates, illiterates, age 0–6, ward names (EN/Marathi) |
| `gadgil_poona_survey_part1.pdf` | https://archive.org/download/in.ernet.dli.2015.83953/2015.83953.Poona-A-Socio-Economic-Survey-Part-I-Economic.pdf | 2026-08-06 | 36,229,622 B | `D2B1A1672EBCEBFE` | Gadgil, *Poona: A Socio-Economic Survey, Part I — Economic* (DLI scan, image PDF). Identity-prior source for the `peth_composition` gazetteer (08-identity §1). **Part II (Social) not found on archive.org** (searched title + creator:gadgil, 2026-08-06). Related: *Poona: A Re-survey* (Sovani et al.) exists as https://archive.org/details/poonaresurveycha0000nvso but is access-restricted (lending only, no direct PDF) — URL recorded, not vendored |
| `western-zone-latest.osm.pbf` | https://download.geofabrik.de/asia/india/western-zone-latest.osm.pbf | 2026-08-06 | 218,913,247 B | `FA78DAB3F974D430` | India Western Zone extract (V3 input; §9.3: no Maharashtra extract exists). **Gitignored** (`data/anchors/*.pbf`) — present locally only; Geofabrik refreshes daily, so this hash pins the 2026-08-06 snapshot; re-fetch requires re-pinning here |

`CHECKSUMS.txt` carries full `sha256  filename` lines for every anchor file
(including the gitignored .pbf).

Still deferred to V3 (do not download yet): Google Open Buildings tiles,
FABDEM DEM, District Census Handbook ward maps (manual georeference of the 4
starting peths), Gadgil Part II — Social (no known digital copy; check
physical-library scans / GIPE Pune if it becomes load-bearing).
