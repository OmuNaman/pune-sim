# Data anchors — pinned 2026-07-31

Reality anchors vendored into the repo so runs are reproducible even if the
sources rot. Per architecture §9.3, the anchor manifest hash joins the
determinism hash (ruling 16 disposition list). SHA256 prefixes below; verify
with `Get-FileHash -Algorithm SHA256`.

| File | SHA256 (first 16) | Source | Notes |
|---|---|---|---|
| `pmpml_gtfs.zip` | `591426C1919E60DC` | github.com/croyla/pmpml-gtfs (main, 2026-07) | 494 routes / 6,203 stops / 10,728 trips; unofficial scrape — this pinned zip is the build input, never the live feed |
| `datameet_pune_wards.zip` | `7FA7A305E16771F6` | github.com/datameet/Pune_wards (master) | 2012-vintage electoral wards + admin wards GeoJSON — sketch layer only (see §9.3 ward-geometry caveat) |
| `pmc_wardwise_census2011.csv` | `84B47692D99ABC13` | data.opencity.in (PMC ward-wise Census 2011) | 160 data rows: households, population by sex, 0–6, SC, ST per census ward |
| `pune_censuswards_2011.csv` | `BD7A9C3E6135976F` | data.opencity.in (census-wards level) | companion table, census-ward granularity |

Deferred to V3 (do not download yet): Geofabrik India **Western Zone** .pbf
(~209 MB, refreshed daily — pin at ingest time), Google Open Buildings tiles,
FABDEM DEM, District Census Handbook ward maps (manual georeference of the 4
starting peths), Gadgil 1945/52 survey PDFs (archive.org, for the
`peth_composition` gazetteer).
