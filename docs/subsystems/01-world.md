# 1. World — space, time, movement

## Summary

The WORLD subsystem is the non-LLM physical substrate of Pune Sim: it owns space (OSM-derived road graph, real+synthesized buildings, POIs, layered jurisdictions), time (a hybrid 5-minute ambient tick plus a second-resolution discrete event queue, a data-driven Indian calendar, and a seeded monsoon weather/hydrology generator), and movement (an event-driven trip engine with three traffic LODs: analytic trips, a mesoscopic BPR+queue congestion model as the workhorse, and an optional SUMO/TraCI micro-window used only for focal scenes and offline calibration). All storage is boring and Windows-friendly: GeoPackage + SQLite WAL, in-memory shapely STRtree and igraph, no servers. Every stochastic draw flows through Philox RNG streams keyed by (domain, entity, day) so the whole city replays deterministically given the same injected events. Other subsystems interact through a small set of general contracts — snap/nearest/jurisdiction/route/where/disrupt/exposure/copresence/place_context/clock — so a crash, a flood, a pandal, a procession, and an election rally are all just disruptions + trips + copresence + calendar phases, never special cases. The design runs the 50k-person Old City on a laptop CPU with zero LLM cost inside WORLD itself, and scales to 3.5M people via cohort trips (aggregated flows that individuate lazily when attention or a hazard sample lands on them). WORLD emits typed signals (jam thresholds, flood depths, festival phases, trip arrivals) that EVENTS/INFO/MINDS subscribe to, and exposes structured PlaceContext so LLM prompts are grounded in real street names, weather, crowding, and jurisdiction facts.

## Design

# WORLD Subsystem — Space, Time, Movement

## 0. Principles
- **WORLD uses zero LLM tokens.** It is deterministic-or-seeded-stochastic machinery that *feeds* LLM prompts (via `PlaceContext`) and *consumes* structured commands (trips, disruptions, timers).
- **Fields, not agents.** Congestion, rain, floodwater, crowding are per-zone/per-edge scalar fields updated on a coarse tick. Individual movement is event-driven trips *sampled against* fields — never per-tick stepping of every person.
- **Everything observable is a signal.** Threshold crossings (jam forms, lane floods, festival phase starts) are published on an in-process bus; WORLD never decides who cares.
- **Determinism.** All randomness via `rng(stream, key)` = numpy Philox keyed `(stream_name, entity_id, sim_day)`. Replay = same seed + same injected-event log.

## 1. Conventions
- CRS: internal **EPSG:32643** (UTM 43N, metres); WGS84 only at I/O edges.
- SimTime: `int64` seconds since sim epoch (default epoch `2026-06-01T00:00 Asia/Kolkata`, configurable).
- IDs (stable strings): `e:<way>:<seq>` edge, `n:<id>` node, `b:osm:<id>` / `b:gob:<id>` / `b:s:<zone>:<n>` building, `z:<n>` zone, `poi:<type>:<n>`, `d:<n>` disruption, `t:<n>` trip, `jl:<layer>` jurisdiction layer.
- Two-tier geography from day one: **full-Pune coarse graph** (arterials + macro-zones, for boundary trips) + **detail area** (Old City: Kasba/Shaniwar/Budhwar/Raviwar Peths polygon) with buildings/POIs/fine zones. Scaling = enlarging the detail area; no schema change.

## 2. Ingestion Pipeline (`world_build/` CLI, idempotent stages, each writes a versioned artifact)
Libraries: **osmium-tool** (conda-forge) for PBF clip, **pyrosm** for parsing, **geopandas/shapely 2.x/pyproj/pyogrio**, **rasterio** for DEM, **igraph** for graph build, **gtfs-kit** for GTFS.

```
stage 1 clip:      geofabrik maharashtra-latest.osm.pbf
                   -> osmium extract --polygon pune_metro.geojson  -> pune.osm.pbf
                   -> osmium extract --polygon old_city.geojson    -> oldcity.osm.pbf
stage 2 graph:     pyrosm nodes/edges -> simplify (merge degree-2), classify
                   {arterial, sub_arterial, collector, peth_lane, alley, footpath},
                   infer lanes/oneway/mode_mask from tags + class defaults
                   -> edges.gpkg, nodes.gpkg; build igraph per mode, pickle.
stage 3 buildings: merge OSM buildings + Google Open Buildings v3 (conf>=0.7, dedup by IoU>0.3);
                   compute per-ward capacity vs Census-2011-scaled-2026 target;
                   SYNTH infill: road-network faces (shapely polygonize) -> block polygons ->
                   strip-subdivide along frontage (8-15m frontage, depth 10-18m) with peth
                   typology prior (2-3 floors, mixed ground-floor commercial on collector+);
                   assign floors/units/pop_cap; snap address point to nearest edge (edge_id, offset_m).
stage 4 elevation: sample FABDEM (fallback Copernicus GLO-30) -> building elev_m/plinth_m,
                   edge flood_sill_m (min elevation along segment).
stage 5 zones:     TAZ = contiguous block clusters of 200-500 buildings (KMeans on centroids
                   constrained by road barriers), ~40-60 zones in Old City; zone adjacency graph;
                   per-zone drainage_mm_h prior (10-30 by age/lane width), river_adjacent flag
                   (within 250m of Mutha or elev within 3m of bank).
stage 6 juris:     load PMC prabhag polygons (41), police station areas (30, official if available),
                   chowky areas = Voronoi of 104 chowky points clipped within parent station area;
                   generic loader: any (layer_id, authority_id, polygon) CSV/GeoJSON registers a layer.
stage 7 pois:      OSM amenity/shop/religion tags -> POI taxonomy; real institutions pinned
                   (Sassoon Hospital, Shivajinagar court, stations, PMC offices); density gap-fill
                   from ward commercial stats (kirana ~1/300 pop, tea stall, medical, temple) — synthetic
                   POIs get placeholder names, ORG subsystem fills identity lazily.
stage 8 transit:   PMPML GTFS -> filter trips touching detail area + 2km buffer; map GTFS shapes
                   to edge sequences (map-matching: shapely project + igraph shortest-path stitch);
                   -> stops/routes/trips/stop_times tables + shape_edge_map.
stage 9 calendar:  curated data files -> holidays_2026_30.csv, festivals.csv (per-day phases,
                   key sites e.g. Manache Ganpati incl. Kasba Ganapati), school_terms.csv,
                   market_days.csv, wedding_season_windows.csv; sunrise/sunset via astral.
stage 10 validate: connectivity check, capacity vs census delta < 5%, GTFS stop snap < 50m, report.
```
Output bundle: `world.gpkg` (all geo layers), `world_static.db` (SQLite: POIs, transit, calendar), `graph_<mode>.pkl`, `build_manifest.json` (versions/hashes — canon references a world build version).

## 3. Schemas (DDL sketch; geo layers in world.gpkg, state in world_state.db SQLite WAL)
```sql
edges(edge_id TEXT PK, u, v, osm_way_id, class TEXT, name_en, name_mr,
      length_m REAL, lanes_dir INT, oneway INT, mode_mask INT,  -- bitmask walk|cycle|2w|car|auto|bus|truck
      free_kph_car REAL, free_kph_2w REAL, cap_pcu_h REAL,
      zone_id, ward_id, station_id, chowky_id, flood_sill_m REAL, geom LINESTRING);
nodes(node_id PK, signalized INT, geom POINT);
buildings(bld_id PK, source TEXT, use TEXT,  -- res|comm|mixed|inst|rel|ind
      floors INT, units INT, pop_cap INT, ward_id, zone_id,
      edge_id, edge_offset_m REAL, elev_m REAL, plinth_m REAL, geom POLYGON);
pois(poi_id PK, type, subtype, name, name_is_synth INT, bld_id, edge_id, offset_m,
      org_id NULL, hours_json, capacity INT, attractivity REAL, geom POINT);
jurisdiction_layers(layer_id PK, authority_type, soft INT);       -- soft=1: weighted catchment
jurisdiction_areas(area_id PK, layer_id, authority_id, geom MULTIPOLYGON);
zones(zone_id PK, area_m2, pop_est, drainage_mm_h REAL, river_adj INT, geom POLYGON);
zone_adj(zone_a, zone_b, kind);            -- road|drainage(downhill)
corridors(corridor_id PK, edge_ids_json, class, length_m, cap_pcu_h, storage_veh REAL);
transit_stops/routes/trips/stop_times (GTFS-shaped) + shape_edge_map(trip_id, edge_ids_json);
-- runtime state (world_state.db, snapshotted daily 03:00 + on demand)
edge_state(edge_id PK, speed_now_car, speed_now_2w, closed_mask INT, water_depth_m, updated_at);
zone_state(zone_id PK, congestion_idx, water_depth_m, rain_mm_h, crowd_idx, noise_idx, updated_at);
corridor_state(corridor_id PK, flow_pcu_h, queue_veh, tt_now_s, spillback INT);
disruptions(disruption_id PK, kind, edge_ids_json, severity_json, t_start, t_end_est,
      certainty, cause_event_id, trajectory_json NULL, active INT);   -- append-only, canon
trips(trip_id PK, actor_kind TEXT, actor_id,   -- agent|cohort|bus|service_vehicle
      mode, legs_json, route_edges_blob, state, depart_t, eta_t, eta_p90_t, next_event_t);
weather_days(day PK, regime, rain_mm, tmax, tmin, cells_json);      -- canon
route_templates(key PK, edge_ids_blob, t0_s, hit_count);            -- (o50m,d50m,mode,tod_bin)
```

## 4. Spatial Query API (in-proc Python; all geo queries hit shapely STRtree + igraph, <1ms typical)
```python
snap(lat,lon | x,y) -> Anchor{edge_id, offset_m, bld_id?, zone_id, xy}
nearest(poi_type, origin:Anchor, k=3, mode='walk', open_at=None) -> [POIHit{poi_id, tt_s, dist_m}]
jurisdiction(layer:str, point:Anchor) -> AuthorityRef{authority_id, area_id}      # 'chowky','ward','police_station',...
catchment(layer, authority_id) -> [(zone_id, weight)]                              # soft layers (schools)
entities_within(geom|zone_id, kinds:[edge|building|poi|agent_presence]) -> ids
isochrone(origin, mode, minutes, at:SimTime) -> [zone_id]
affected_zones(geom, buffer_m) -> [zone_id]
place_context(anchor, t) -> PlaceContext   # see §9 — the LLM grounding payload
```

## 5. Time Engine
- **Hybrid clock**: ambient tick every **300 sim-seconds** (weather, hydrology, traffic fields, transit delay propagation, crowd index); **heapq event queue at 1-second resolution** for trips, timers, disruption starts/ends, calendar phase boundaries. Daily rollover job at 03:00 sim (instantiate day's bus trips from GTFS, refresh matrices, snapshot, sample next weather day).
- **Run modes** (contract with UI/NARRATIVE): `PAUSED`; `SCENE` (~1:1 realtime, focal bubble); `FAST(n×)`; `SKIP(to_t)` (ambient layers computed in batch, trips resolved analytically). Any subsystem registers **interrupt predicates** (`severity>=X`, `focal agent involved`, `signal topic match`) that drop FAST/SKIP to SCENE. During a T3 focal scene the city clock advances in lockstep with scene beats; scene-local elasticity limited to ±30 min reconciled at scene end (WORLD replays trips analytically across the gap).
- **Calendar service**: `calendar(date) -> DayContext{weekday, school_day, public_holiday, festival_phases:[{festival_id, day_n, intensity, key_sites}], market_day, wedding_season, monsoon_phase, sunrise, sunset}`. Festival dates are **curated data**, not computed astronomy. Festival phases only set *ambient modifiers* (attractivity multipliers on POIs, demand multipliers) — actual closures/processions are disruptions injected by EVENTS/ORGS using our disruption API, with WORLD supplying closure *templates* per festival as data.
- `schedule(t, topic, payload) -> timer_id` — generic timer any subsystem uses (court hearing dates in S6, wedding muhurta in S4, election day in S7 are all just timers + calendar flags).

## 6. Weather + Hydrology (seeded, injectable)
- **Daily**: seasonal 2-state Markov chain (wet/dry) with monthly params fitted to IMD Pune normals (~720mm/yr, Jun–Sep ≈ 85%); wet-day depth ~ mixed gamma; regime label {dry, drizzle, steady_monsoon, heavy, cloudburst}. Cloudburst = tail draw (p≈0.01/monsoon-day) **or injected override** — injection uses the same schema as sampling, so S3 is just a forced draw: `inject_weather({day, cells:[{center, radius_m, mm_h, t0, dur}]})`.
- **Intraday**: rain cells (start, duration, intensity, spatial footprint) disaggregate daily depth; per-zone `rain_mm_h` each tick (near-uniform for 2–3 km² except cell footprints). Temperature/heat-index similarly (matters for Apr–May demand shifts).
- **Hydrology-lite** per tick per zone: `dW = rain + inflow_from_uphill_zones(zone_adj drainage) − drainage_mm_h − infiltration`; `water_depth_m = W/1000 · ponding_factor`. **River module**: `mutha_stage(t) = f(basin_rain_48h, dam_release(t))`; dam release is an ordinary injectable event; riverside zones flood when `stage > elev`. Edge closure by mode when `water_depth > sill + threshold`: walk 0.5m (risk-flag >0.3), 2w 0.25m, car 0.3m, bus/truck 0.5m. Signals: `world.flood.zone{zone_id, depth}` on 0.1m threshold crossings; building exposure list (`plinth_m < depth`) published for EVENTS to convert into damage.

## 7. Movement Layer
### 7.1 Trip lifecycle (event-driven — the core scalability trick)
A `Trip` = legs `[WALK, WAIT, RIDE(bus trip), DRIVE(2w/car/auto), ...]` with a pinned edge route per leg. The engine schedules **only decision points**: leg boundaries, boardings, arrival, plus a 10-min checkpoint on long legs (bounded staleness). Between events, `where(agent)` interpolates along the route polyline by elapsed/expected time — O(1), no ticking.
```python
def on_trip_event(ev):
    trip = trips[ev.trip_id]
    if congestion_dirty(trip.remaining_corridors):        # subscribed corridor changed >20%
        trip.eta = reprice(trip); maybe_signal('world.trip.delayed', trip)
        if trip.actor_kind=='agent' and delay > threshold: notify MINDS (replan hook — choice is theirs)
    if ev.kind == LEG_END: start_next_leg(trip)           # WAIT legs resolve vs live bus delay
    elif ev.kind == ARRIVE: complete(trip); emit('world.trip.arrived')
    heappush(queue, next_decision_point(trip))
```
### 7.2 Traffic LODs
- **LOD0 analytic** (default for everything unwatched): trip cost from zone-to-zone travel-time matrix + Philox noise; contributes demand to corridors but no per-edge trace.
- **LOD1 mesoscopic** (always on): network aggregated into ~150–300 directed **corridors** (edge chains between major junctions). Per 5-min tick: assign active trip flows to corridors → BPR `tt = t0·(1 + 0.6·(v/c)^3.2)` (α,β per class, **calibrated offline via SUMO**) → **vertical queue with spillback**: excess demand stored as `queue_veh`; when `queue_veh·7m/lanes > corridor length`, set spillback flag → upstream corridors' capacity cut 40%. Mode realism: two-wheeler excess delay ×0.55 (filtering); ambulance/police priority: excess delay ×0.35, floor speed 8 km/h; PCU factors 2w 0.4, auto 0.8, car 1.0, LCV 1.5, bus/truck 3.0. Class defaults: arterial 3000 PCU/h/dir @35 km/h; collector 1200 @25; peth_lane 500 @15; alley 2w/walk only.
- **LOD2 SUMO micro-window** (via TraCI/libsumo, netconvert network pre-built per detail area). **Escalation predicate — SUMO runs iff ALL hold**: (1) user focus intersects the area OR EVENTS tags the event `requires_microsim`; (2) required outputs are per-vehicle positions/interactions or signal-level queue dynamics that mesoscopic cannot express (crash-scene blockage geometry, procession–vehicle interleaving); (3) window ≤ 3 km² and ≤ 2 sim-hours; (4) no other window active. Demand seeded from current LOD1 corridor flows; tracked agents inserted as named vehicles. **Write-back**: per-edge speeds/queues override mesoscopic state during the window; tracked-agent ETAs come from SUMO. Additional non-runtime use: **offline calibration** of BPR params and pre-validation of festival closure plans. **v1 ships without runtime SUMO** — `MicrosimAdapter` interface stubbed; mesoscopic queue/spillback already yields correct *consequences* (delays, reroutes, spillback); SUMO adds narration fidelity in month 3+.
- Matrix maintenance: zone matrix entries recomputed lazily via igraph on corridor costs when queried and stale >5 sim-min (dirty-flag per corridor).
### 7.3 Routing & modes
- **igraph** shortest path per mode graph with live corridor costs; **route_templates** cache keyed (origin 50m-cell, dest 50m-cell, mode, tod-bin) — commutes hit cache; habitual-route realism: `plan(..., prefs={habitual_route_id})` re-prices the known route, replanning is MINDS' choice.
- Modes: walk 4.5 km/h (×0.85 rain>10mm/h, ×0.5 water 0.1–0.3m, ×0.6 crowd_idx>0.7); cycle 12; 2w; car; **auto-rickshaw** = hail model: per-zone supply pool, `wait_s = f(supply, demand, rain×1.6, night×1.3)`, meter fare (base+per-km, tariff table data); bus (GTFS); school bus/van (fixed circuits registered as service trips); truck/tempo (old-city daytime entry restrictions as standing disruptions); ambulance/police (priority class).
### 7.4 Transit riding (PMPML)
Daily 03:00: instantiate day's GTFS trips as bus Trips pinned to `shape_edge_map`; delays accumulate from corridor states and propagate along the trip. Journey planning: 2-transfer **RAPTOR** (~300 lines, own implementation over stop_times arrays). Rider chain: WALK→arrive stop→subscribe to bus-trip arrival→board (load-factor check; if full → next departure event)→RIDE (rider position = bus position)→alight. Crowding: per-trip load factor from demand + festival multipliers → feeds copresence and PlaceContext. Disrupted edges → bus detour via reroute of the shape's edge chain (or short-turn if no detour ≤ 1.5× length); emits `world.transit.disrupted{route, stops_skipped}`.
### 7.5 Road-disruption API (the universal consequence hook)
```json
disrupt({ "kind":"closure|capacity|speed|hazard_zone|moving_closure",
  "edge_ids":[...] | "geometry":{...}, 
  "severity":{"capacity_pct":0.3,"speed_cap_kph":10,"modes_blocked":["car","truck"]},
  "t_start":..., "t_end_est":..., "certainty":"scheduled|estimated|unknown",
  "cause_event_id":"ev:...", "trajectory":[{"t":..., "edge_ids":[...]}]? }) -> disruption_id
clear(disruption_id, actual_t_end)
```
Effects: future plans avoid/re-cost; en-route trips repriced at next decision point (MINDS notified for choice); corridors dirty → matrix refresh; `world.traffic.disruption` signal carries `visible_from_zones` (line-of-sight ≈ same/adjacent zone) for INFO. `moving_closure` (trajectory) covers processions and VIP convoys. Flood closures are auto-generated by hydrology through this same API (cause = weather event).
### 7.6 Exposure & copresence (hazard + gossip substrate)
```python
exposure_field(t, mode_filter) -> per-edge E[occupancy]      # EVENTS samples crash/crime locations against this
presence_sample(edge_id|zone_id, t, filter, n) -> [PresenceHit{actor_kind, actor_id|cohort_slice}]
   # cohort hits individuate via POPULATION.materialize(cohort_id, slice_seed) — lazy identity
copresence(zone_id, window) -> [ContactOpportunity{context: bus_ride|queue|market_street|temple|
   waterlogging_wait|pandal_crowd, expected_contacts, sample_pairs(seed)}]
```
### 7.7 Cohort trips (city-scale)
Unwatched households don't emit individual trips: POPULATION registers **cohort demand** (zone A→B, mode split, departure profile, size). WORLD carries one flow object per cohort per period; `presence_sample`/focus landing on a cohort **individuates** members on demand (POPULATION assigns identity; the trip retroactively gets a concrete trace consistent with the flow). Budget math: Old City 50k ppl ≈ 125k trips/day ≈ 500k queue events/day — trivial. Full Pune 3.5M ≈ 8.75M trips/day; with cohorts covering ~95% of unwatched demand, individual events stay < 2M/day; Python heapq at 50–100k dispatches/s → minutes per sim-day, acceptable.

## 8. Signals (in-proc pub/sub; blinker or 50-line registry)
Topics: `world.weather.{regime_change,cell_start}`, `world.flood.{zone,edge,building_exposure}`, `world.traffic.{jam,disruption,cleared}` (jam = corridor tt/t0 crosses 2.0/3.5 bands), `world.trip.{arrived,delayed,failed}`, `world.transit.disrupted`, `world.calendar.phase`, `world.clock.{rollover,mode_change}`. Payloads carry ids + zone lists; INFO decides who *learns* of them, EVENTS decides what *escalates*.

## 9. PlaceContext (LLM grounding payload — structured, MINDS renders prose)
```json
{"street":"Kasba Peth Main Rd","ward":"Prabhag 15","chowky":"Kasba Chowky",
 "nearby_pois":[{"type":"temple","name":"Kasba Ganapati","dist_m":80}],
 "ambient":{"time":"08:10","weather":"heavy_rain 22mm/h","water_depth_m":0.1,
   "jam_level":"severe (tt 3.2x)","crowd":"moderate","noise":"high"},
 "active_disruptions":[{"kind":"closure","cause":"pandal","since":"2d"}],
 "calendar":{"festival":"Ganeshotsav day 3","school_day":true}}
```

## 10. Persistence & determinism
- Static bundle versioned by `build_manifest.json`; canon rows reference the build hash.
- Append-only canon from WORLD: disruptions, weather_days, transit-disruption log, per-day zone stats. Individual trip traces persisted only for materialized (attended) agents.
- Snapshots: full runtime state daily 03:00 + on `snapshot()`; restore = load snapshot + replay injected-event log (deterministic RNG makes this exact).

## 11. Named stack
osmium-tool, pyrosm, geopandas, shapely 2.x, pyproj, pyogrio, rasterio (FABDEM/GLO-30), python-igraph, numpy (Philox), scipy.sparse, gtfs-kit, astral, blinker, pydantic v2 (contract models), SQLite (stdlib, WAL) + GeoPackage, SUMO ≥1.19 (netconvert, libsumo/traci) — optional at runtime, required for calibration harness. All pip/conda-installable on Windows 11; no servers.

## 12. Build order
M1 (wk1–3): stages 1–7 ingest, snap/nearest/jurisdiction/route, clock+calendar. M2 (wk4–6): trips LOD0/1, corridors+BPR+spillback, disruption API, GTFS riding. M3 (wk7–9): weather+hydrology, festival ambient modifiers, exposure/copresence, snapshots/replay. M4: SUMO calibration harness, then runtime MicrosimAdapter; full-Pune coarse graph + cohort scale test.

## Key decisions

- **Mesoscopic corridor model (BPR + vertical queue + spillback) is the always-on traffic engine; SUMO/TraCI only for focal micro-windows meeting an explicit predicate, plus offline calibration of BPR parameters; v1 ships with the SUMO adapter stubbed.** — Consequence propagation (jams, delays, reroutes, spillback) needs only mesoscopic fidelity; per-vehicle narration is rare and attention-bound. SUMO always-on is fragile on Windows, slow at city scale, and adds nothing to unwatched areas. Using SUMO offline to calibrate alpha/beta/capacities gives micro-realism to the cheap model.
  - Rejected: Always-on SUMO microsimulation (cost, brittleness, no benefit unwatched); pure analytic travel times with no queueing (no emergent jams, breaks S1/S3/S8 propagation).
- **Event-driven trips (heapq of decision points, interpolated positions) + cohort trips for unwatched demand with lazy individuation, instead of per-tick agent stepping.** — O(events) not O(agents x ticks): 50k people is trivial and 3.5M stays minutes-per-sim-day in pure Python; position queries interpolate in O(1); cohorts keep the same contracts at every scale so no redesign.
  - Rejected: Stepping every agent every tick (kills 3.5M scale in Python); fully individual trips city-wide (memory and event blowup with zero observable benefit).
- **Hybrid clock: 300s ambient tick for fields + 1s-resolution event queue; run modes PAUSED/SCENE/FAST/SKIP with subsystem-registered interrupt predicates; scene-time elasticity capped at +/-30 min reconciled at scene end.** — Fields don't need finer than 5 min; narrative needs second precision only at decision points; SKIP with batch ambient computation makes multi-month arcs (S5, S6) cheap; interrupt predicates let any subsystem pull attention without WORLD knowing why.
  - Rejected: Uniform fine tick (waste); pure discrete-event with no field tick (weather/congestion updates become tangled in the queue).
- **Storage: GeoPackage + SQLite WAL + in-memory shapely STRtree and igraph pickles; single-process, in-proc pub/sub (blinker).** — Solo dev on Windows 11: zero servers, zero DLL pain, geopandas/pyogrio read-write GeoPackage natively; STRtree answers all runtime spatial queries in-memory at <1ms; SQLite WAL is durable enough for snapshots and canon.
  - Rejected: PostGIS (server ops burden), SpatiaLite (Windows DLL friction), microservices/message broker (unneeded complexity for one process).
- **Routing on python-igraph with a route-template cache and lazily-refreshed zone matrices, not networkx and not OSRM.** — igraph's C core does Old City shortest paths in microseconds and full Pune in ms; route templates exploit habitual commutes (also a realism feature MINDS can lean on); lazy dirty-flag matrices bound recomputation to what changed.
  - Rejected: networkx (10-100x too slow at full-Pune scale); OSRM/Valhalla server (painful on Windows, can't price live sim congestion into edge costs).
- **Buildings = OSM + Google Open Buildings v3 merge, then procedural strip-subdivision infill of network-face blocks to hit Census-scaled ward capacity, with peth typology priors and per-building DEM elevation/plinth.** — GOB gives real footprints where OSM is patchy; procedural infill is only a residual, keeping geography honest; capacity reconciliation against ward stats is what POPULATION seeds from; plinth+DEM makes flood exposure (S3) a query not a special case.
  - Rejected: Pure procedural synthesis (wastes available real footprints, distorts the peths' wada fabric); OSM-only (large population placement error in exactly the start area).
- **Jurisdictions as a generic layered-coverage mechanism (hard polygons or soft weighted catchments), with chowky areas as Voronoi-within-station fallback when official boundaries are unavailable.** — One function serves police/ward/court/school/fire/hospital and any future authority; soft layers model Indian school choice (distance-decay weights) without pretending strict catchments exist.
  - Rejected: Per-institution bespoke lookup code (violates generality); strict school catchments (factually wrong for Pune private schools).
- **Festival/holiday/muhurta dates are curated data tables; WORLD's calendar exposes phases and ambient modifiers, while actual closures/processions are injected by EVENTS/ORGS through the ordinary disruption API using WORLD-supplied templates.** — Computing lunisolar panchang is error-prone with no gameplay benefit; keeping closures out of the calendar keeps one universal disruption path (crash = pandal = procession = flood) and lets institutions own their own decisions.
  - Rejected: Astronomical computation of Hindu calendar; hardcoding Ganeshotsav road closures inside WORLD (scenario special-casing, the exact thing the design bans).
- **Weather is a seeded stochastic generator (seasonal Markov chain + gamma depths + intraday cells) with injection using the identical schema, plus hydrology-lite (zone bathtub with downhill inflow, drainage rates, river stage from basin rain and dam releases) and per-mode depth thresholds auto-emitting flood disruptions.** — Makes S3 an ordinary tail sample; downstream cannot distinguish injected from natural weather; bathtub+sill model is 200 lines yet produces the real texture (low lanes fail first, 2w before cars, riverside zones on dam release).
  - Rejected: Full 2D hydraulic modeling (weeks of work, data we lack); scripted flood events (not general, no partial/graded flooding).
- **All randomness through named Philox RNG streams keyed (domain, entity, sim_day); world state snapshot daily + injected-event log gives exact replay.** — Determinism makes bugs reproducible, lets canon stay consistent under re-simulation, and enables counterfactual replays (a likely user feature) for free.
  - Rejected: Global RNG (any code-path change or query reorders draws and diverges the whole city).
- **WORLD is strictly LLM-free; it exposes structured PlaceContext for prompt grounding and never generates prose.** — Keeps the token budget entirely in MINDS/scenes; guarantees WORLD facts are canon-consistent (no hallucinated geography); PlaceContext gives every scene real street names, jurisdiction, weather, and crowd state at zero cost.
  - Rejected: WORLD-side LLM description generation (cost, drift, and duplication of MINDS' voice).
- **Internal CRS EPSG:32643 (UTM 43N, metres); two-tier geography (full-Pune coarse graph + detail-area polygon) from day one.** — Metric CRS makes every buffer/length/speed computation trivial; boundary trips (Old City to Hinjewadi) need the coarse graph immediately; scaling to full Pune is enlarging a polygon and re-running stages, not a redesign.
  - Rejected: WGS84 internally (degree math bugs); detail-area-only world (boundary trips would need fake external travel times).

## Interfaces

- **MINDS / agent runtime**: plan(origin, dest, mode?, depart|arrive_by, prefs{habitual_route_id?}) -> ItinerarySet{legs, cost, eta, eta_p90, fare}; start_trip(agent_id, itinerary) -> trip_id; where(agent_id) -> Presence{anchor, activity, trip_id?}; eta(trip_id); place_context(anchor, t) -> PlaceContext JSON (street/ward/chowky, nearby POIs, ambient weather/jam/flood/crowd, active disruptions, calendar) for prompt grounding; nearby(agent_id, radius_m, kinds) for scene assembly; delay/failure notifications pushed via world.trip.* signals with a replan hook (choice stays in MINDS).
- **EVENTS / hazards**: Consumes: exposure_field(t, mode_filter) -> per-edge expected occupancy for base-rate hazard sampling; presence_sample(edge|zone, t, filter, n) -> concrete actors (individuating cohorts via POPULATION); disrupt(spec JSON: kind, edge_ids|geometry, severity{capacity_pct|speed_cap|modes_blocked}, t_start, t_end_est, certainty, cause_event_id, trajectory?) -> disruption_id; clear(disruption_id); inject_weather(cells)/dam_release as ordinary injectable events. Emits to EVENTS: world.flood.{zone,building_exposure}, world.traffic.jam, world.trip.failed threshold signals.
- **ORGS / institutions**: jurisdiction(layer, point) -> AuthorityRef and catchment(layer, authority_id) -> weighted zones (register_layer(layer_id, areas|points+voronoi) for new institution types); nearest(poi_type, ...) and register_poi(type, anchor, org_id, hours, attractivity) incl. temporary POIs (pandals, campaign offices); dispatch_route(vehicle_class in {ambulance, police, fire}, from, to) -> priority-costed Route with honest jam ETA; transit ops: route detour/short-turn notifications; festival closure templates as data for ORGS to enact via disrupt().
- **INFO / gossip & media**: copresence(zone_id, window) -> ContactOpportunities{context: bus_ride|queue|market_street|temple|pandal_crowd|waterlogging_wait, expected_contacts, sample_pairs(seed)} as the physical substrate for rumor hops; disruption/jam/flood signals carry visible_from_zones so INFO decides who plausibly witnessed what; crowd and load-factor fields for media colour.
- **POPULATION / canon seeding**: Reads buildings(units, pop_cap, use, ward_id) and zone stats as the placement frame for households; registers cohort demand register_cohort(o_zone, d_zone, mode_split, departure_profile, size) for unwatched movement; implements materialize(cohort_id, slice_seed) callback so WORLD's presence_sample/focus landing can individuate members; receives world build_manifest hash so canon rows pin to a world version.
- **NARRATIVE / UI / camera**: set_focus(FocusSpec{agent_id|area, fidelity}) drives traffic/scene LOD escalation incl. the SUMO-window predicate; clock control run(mode in {PAUSED, SCENE, FAST(n), SKIP(to_t)}) with registered interrupt predicates; snapshot render queries: GeoJSON of zone fields (rain, flood, congestion, crowd) and entity positions for the map view; calendar(date) -> DayContext for the HUD.
- **PERSISTENCE / canon DB**: Append-only writes: disruptions, weather_days, transit disruption log, daily per-zone aggregates, trip traces for materialized agents only; snapshot()/restore(snapshot_id) of runtime state; replay(snapshot, injected_event_log) reproduces exactly via Philox streams; schedule(t, topic, payload) -> timer_id generic timers (hearing dates, muhurtas, election day) persisted in canon.

## Scenario traces

## S1 — School-bus crash, Shivajinagar edge, 08:10 (acute physical)
1. EVENTS samples crash location/time against `exposure_field(t, modes={school_bus, truck})` — nothing crash-specific in WORLD; it is one Poisson draw against occupancy-weighted edge risk at NCRB-calibrated base rates. 2. `presence_sample(edge, 08:10)` returns the school-bus service trip (tracked) + truck (cohort goods flow → individuated driver via POPULATION.materialize). Father and daughter are riders on the bus trip, so they surface automatically. 3. EVENTS calls `disrupt({kind:'capacity', edge_ids:[e], severity:{capacity_pct:0.35}, cause_event_id})` → corridor dirty → queue builds → spillback flag to upstream corridors → `world.traffic.jam` signal; commuter trips reprice at next decision points; MINDS gets delay notifications (parents stuck). 4. ORGS dispatches: `nearest('hospital', crash_anchor, mode='ambulance')` → Sassoon; `plan(..., mode='ambulance')` uses priority delay factor through the jam it itself is stuck behind — ETA honest, not teleported. 5. `jurisdiction('police_station', anchor)` → Shivajinagar PS for the FIR; `jurisdiction('court', anchor)` → Shivajinagar district court, seeding S6. 6. If user zooms in, focus predicate can escalate a ≤2h SUMO window for per-vehicle scene narration; consequences (delays) were already correct at LOD1. 7. School absence emerges: the bus trip never `ARRIVE`s; school (ORG) subscribed to `world.trip.failed`.

## S2 — Temple-donation-scam rumor (informational)
WORLD's only role — and that is the point — is the **contact substrate**: INFO drives spread, but seeds and hops come from `copresence(zone, window)` returning ContactOpportunities (temple queue at a POI with high attractivity, bus_ride load factors, market_street). `sample_pairs(seed)` deterministically picks concrete pairs; cohort members individuate lazily only when a rumor actually lands on them. WhatsApp hops need no geography, but each face-to-face retelling is a copresence draw, so the rumor's spatial footprint (which peths hear it first) emerges from real movement patterns. No rumor-specific code exists in WORLD.

## S3 — 48-hour cloudburst, Mutha-adjacent lanes (area-ambient)
1. Injected via `inject_weather({cells:[...]})` — same schema as a sampled tail event, so "God-mode" and "natural" are indistinguishable downstream. 2. Each 5-min tick: zone water balance + downhill inflow via `zone_adj(drainage)`; riverside zones additionally take `mutha_stage(basin_rain_48h, dam_release)` — a dam release is itself an injectable event. 3. Depth crossings auto-emit flood closures through the standard `disrupt` API per mode threshold (2w blocked at 0.25m before cars at 0.3m — two-wheeler commuters fail first, a very Pune detail that falls out of a threshold table). 4. Commute failure = trips repriced to infeasible → `world.trip.failed` → MINDS decides (wade risk-flagged, wait, turn back). 5. `building_exposure` signal lists buildings with `plinth_m < depth` → EVENTS converts to damage; ORGS(PMC) receives complaints referencing `jurisdiction('ward', ...)`. 6. Disease worry: post-flood copresence contexts (`waterlogging_wait`) and standing water fields give EVENTS/HEALTH an exposure surface. 7. Buses detour or short-turn via transit disruption handling.

## S5 — Job loss, months of debt (slow personal arc)
Mostly MINDS/ECONOMY, but WORLD carries the texture cheaply across months: `SKIP`/`FAST` run modes batch ambient layers; the man's job-search trips are ordinary LOD0 trips whose *costs* (bus fare from tariff data, time from matrices) feed the household budget; dropping the two-wheeler for bus commute is just a mode change with honest travel-time consequences; calendar timers fire school-fee due dates and festival expenses (Diwali DayContext). Nothing arc-specific: WORLD supplies time passage, movement cost, and calendar pressure as generic services.

## S8 — Ganeshotsav, 10 days (mass event)
1. `calendar()` exposes festival phases day 1–10 with key sites (Manache Ganpati incl. Kasba Ganapati) — pure data. 2. Pandals = temporary POIs with high attractivity (registered by ORGS) → crowd_idx field rises from inflow cohort trips POPULATION generates against those attractors. 3. Immersion-day processions = `moving_closure` disruptions with trajectories along procession routes; police bandobast = ORGS-registered capacity modifications; both are the same API a crash used. 4. Transit: PMPML festival load multipliers → boarding failures → riders' WAIT legs extend, feeding copresence (gossip-rich crowds). 5. Commerce spike is ECONOMY's, keyed off attractivity and crowd fields. 6. A procession crossing the user's focus is the canonical LOD2 SUMO trigger (crowd–vehicle interleaving). One-line instances of the rest: **S4** wedding = timer (muhurta window) + pandal disruption on one lane + guest trips; **S6** court = `jurisdiction('court')` + hearing-date timers over years under SKIP mode; **S7** election = ward layer gives constituency structure, campaign rallies are trips + small disruptions, ward-level civic grievances (flood complaints from S3, road state) are fields aggregated per prabhag that ORGS reads as issue salience.

## Generality argument

WORLD reduces every physical situation to five orthogonal primitives, each closed under composition: (1) a **field** (any per-zone/per-edge scalar: rain, floodwater, congestion, crowding, noise — new ambient phenomena like a heatwave, smog episode, or water-tanker shortage are new fields with the same tick/threshold/signal machinery); (2) a **trip** (any movement by any actor — commuter, bus, ambulance, wedding guest, campaign rally van, funeral procession — is legs over the same graph with the same LOD rules); (3) a **disruption** (any capacity/speed/closure change with static or moving footprint and any cause — crash, pandal, procession, floodwater, VIP visit, road works — enters through one API and propagates identically: dirty corridors → repriced trips → signals); (4) a **jurisdiction layer** (any authority-to-territory mapping — chowky, ward, school catchment, hospital service area, court district — is one generic layered coverage function, so a new institution type registers a layer rather than new code); (5) a **calendar/timer** (any temporal structure — festival phase, hearing date, election day, school term, muhurta — is data-driven day-context plus generic timers). Situations the probes never mention fall out for free: a bandh is a broad disruption + demand modifier; a building collapse is a hazard disruption + building-exposure signal; a metro line opening is a new GTFS feed through stage 8; a new suburb is an enlarged detail polygon. Crucially, WORLD contains no scenario nouns: nothing in the schema says "crash", "wedding", or "election" — those live in EVENTS/ORGS/MINDS, which compose WORLD primitives. Scale generality comes from the same abstraction: cohort trips and lazy individuation mean the identical contracts serve 50k and 3.5M people, and the exposure/copresence API gives every downstream stochastic process (accidents, gossip, disease, pickpocketing) one uniform way to ask "who is physically where, with whom" without WORLD knowing why.

## Open questions

- PMPML GTFS feed vintage and quality: does the 366-route static feed match the current network well enough, and is there any headway/frequency data for festival supplements? Needs validation in stage 8; fallback is manual correction of key Old City routes.
- Official chowky boundary data: are the 104+ chowky areas published anywhere, or is Voronoi-within-station the permanent answer? (Affects fidelity of 'which chowky covers this lane' answers users will probe.)
- PMC prabhag GIS boundaries for the 2026 election structure (41 wards / 162 corporators): confirm an authoritative shapefile source and its vintage vs the Census-2011 ward geometry used for demographic scaling.
- FABDEM license is CC-BY-NC — acceptable for this project's future? If not, quantify Copernicus GLO-30 error in the dense peth fabric before trusting flood sills.
- Google Open Buildings quality in the wada fabric of the Peths (contiguous roofs may merge into blobs): validate IoU-dedup and, if poor, raise the confidence threshold and lean more on procedural infill.
- Memory budget for the full-Pune coarse graph loaded from day one (est. 300-500k edges): confirm igraph + pickle footprint stays under ~2GB alongside detail-area structures.
- SUMO on Windows: libsumo in-process vs traci socket performance for a 3 km2 window at faster-than-realtime — benchmark in M4 before committing the write-back design.
- Ownership boundary with EVENTS: proposal is EVENTS samples hazards against WORLD's exposure_field (WORLD never rolls accidents itself) — needs sign-off from the EVENTS design so base-rate calibration (NCRB) lives in exactly one place.
- Scene-time elasticity contract with MINDS/NARRATIVE: is +/-30 min reconciliation after a T3 scene acceptable, or do focal scenes need hard lockstep with the city clock (which constrains scene pacing)?
- Cohort individuation protocol details with POPULATION: who guarantees consistency when a materialized member's retroactive trip trace must match both the cohort flow and their canon biography (e.g., workplace on the far side of the crash edge)?
- Two-wheeler filtering factor (0.55 excess-delay multiplier) and BPR alpha/beta per road class are placeholders: schedule the offline SUMO calibration run in M4 and treat published Indian HCM values as priors.
- Does the auto-rickshaw hail model need driver-side agents at v1 (rickshaw drivers as an occupation POPULATION will want anyway), or is the zonal supply-pool abstraction sufficient until a scenario puts a driver on camera?

## Red-team critique (verdict: needs_changes)

- **[critical]** The static world is immutable at runtime. Buildings, POIs, transit, and the network live in a hash-versioned build bundle (world.gpkg / world_static.db / graph pickles); only POIs have a runtime registration path. A wada that collapses, a metro station that opens, a building demolished for road widening — canon records the event but every spatial query, PlaceContext, and POPULATION read still serves the old geometry. LLM prompts will describe a collapsed building as intact: exactly the canon-contradiction slop the design promises to prevent. The generality argument's 'metro = new GTFS feed through stage 8' is only true between campaigns, never within one.
  - Fix: Add an append-only world-delta log as a first-class injected-event type: add/retire edges, transit stops/trips, POIs, and a building_state runtime table (condition, habitable_units, destroyed) mirroring edge_state. All spatial queries and PlaceContext consult base+delta; snapshots store a delta cursor; replay applies deltas at their sim timestamps. Buildings move from 'static forever' to 'static base + mutable state'.
- **[critical]** The field layer is not actually extensible, and the generality argument leans on it repeatedly. zone_state has hardcoded columns (congestion_idx, water_depth_m, rain_mm_h, crowd_idx, noise_idx). 'A heatwave, smog episode, dog-menace, or water-tanker shortage is just a new field' is false as specified: each one is a schema migration plus WORLD code, i.e., the exact special-casing the design bans, hidden inside WORLD instead of eliminated.
  - Fix: Build a generic field registry: register_field(field_id, level in {zone,edge}, decay_fn, threshold_bands) plus set_field/add_contribution APIs callable by EVENTS/ORGS. Store registered fields in a keyed table (zone_field_state(zone_id, field_id, value)), auto-emit world.field.<id> threshold signals, and auto-merge registered fields into PlaceContext.ambient. The five built-in fields become the first registrations.
- **[critical]** Cohorts, presence, and exposure are demographic-blind. register_cohort carries only (o_zone, d_zone, mode_split, departure_profile, size); presence_sample's 'filter' and materialize(cohort_id, slice_seed) have no vocabulary for age, gender, or role. Nearly every hazard/crime scenario needs a typed victim — a child at a school gate, a college student at dusk, families asleep in a wada at 2am, gendered route avoidance. EVENTS cannot currently ask WORLD the one question it exists to answer: 'who, of what kind, is here.'
  - Fix: Cohort registration carries a composition vector (age band × gender × trip-purpose/role shares). presence_sample and copresence sample_pairs accept predicates over it; materialize(cohort_id, slice_seed, predicate) draws a member satisfying the predicate deterministically (Philox-keyed rejection sampling); exposure_field gains an optional group_by=demographic. This is a POPULATION contract change — settle it before M2, it is load-bearing for EVENTS and INFO.
- **[major]** Lazy individuation is a one-way ratchet that silently falsifies the 3.5M claim. Every rumor hop (sample_pairs), hazard draw, and focus landing materializes cohort members; nothing ever re-aggregates them, and there is no stated conservation rule (does the cohort flow decrement when a member individuates, or is demand double-counted?). INFO runs citywide gossip continuously, so over months the materialized fraction grows monotonically toward the fully-individual regime whose event and canon volume the design explicitly rejected.
  - Fix: Specify both halves now: (1) conservation — cohort flow objects hold remaining_size; materialize atomically decrements and the retro-trace must be feasible against the flow (WORLD validates, POPULATION owns biography); (2) fold-back — agents untouched by attention/events for N sim-days de-materialize: identity and canon summary persist in POPULATION, movement re-enters cohort flows, trip-trace persistence stops. Add a materialized-agent count to daily zone stats so the ratchet is observable.
- **[major]** The determinism/replay claim has holes. set_focus, run-mode changes (PAUSED/SCENE/FAST/SKIP), and SUMO-window activations change individuation timing, LOD escalation, and query order, but are not defined as part of the injected-event log — so 'replay = seed + injected events' is false the moment a user touches the camera. Worse, SKIP-mode batch ambient computation changes the statistics EVENTS samples against (exposure integrated differently than under FAST), so the same seed yields different accident/crime histories depending on how the user watched. World history must not depend on spectatorship.
  - Fix: Log attention and run-mode transitions as first-class injected events. Define exposure and field integration on the fixed 300s grid regardless of run mode (SKIP computes the identical grid in batch), and require all hazard sampling to use Philox keys of (domain, edge/zone, tick) so draws are order- and mode-invariant. Add a CI test: two runs, same seed, different focus scripts, canon tables must byte-match except attention-derived rows.
- **[major]** exposure_field promises per-edge E[occupancy], but 95% of demand exists only at corridor (LOD1) or matrix (LOD0) resolution with 'no per-edge trace'. Per-edge occupancy near a specific school gate is undefined — crash/crime placement degrades to a uniform smear along a corridor while the API's signature claims edge precision. EVENTS will calibrate NCRB base rates against numbers that are quietly made up.
  - Fix: Specify the disaggregation: corridor flow distributes to member edges by deterministic weights (frontage POI attractivity, intersection presence, edge length), documented as approximate in the contract; POI-anchored trip ends add a within-50m kernel around destination anchors so gates/entrances get their real occupancy spike.
- **[major]** Units bug in the flood-closure rule: flood_sill_m is defined as absolute DEM elevation ('min elevation along segment'), but the closure test is water_depth > sill + threshold where water_depth_m is relative ponded depth from the zone bathtub. As written, an edge at 560m elevation never floods; the whole per-mode threshold cascade (2w at 0.25m before cars at 0.3m — the design's showpiece detail) sits on top of a dimensionally inconsistent comparison.
  - Fix: Store sill as height above a zone reference datum: sill_rel_m = edge_min_elev − zone_p10_elev, computed in stage 4; compare ponded depth against sill_rel_m + mode threshold. Add a stage-10 validation assertion that all sills are in [0, ~5m].
- **[major]** 30m DEM (FABDEM/GLO-30) cannot resolve lane-scale waterlogging in the peth fabric (lanes 3–8m wide, building-height artifacts everywhere). 'Low lanes fail first' will be DEM-noise-random: the sim will flood the wrong lanes with total confidence, and Pune-resident users will catch it immediately — the open-questions list worries about the FABDEM license but not about resolution, which is the real problem.
  - Fix: Treat DEM as a broad prior only (river adjacency, macro gradient). Overlay a curated hotspot layer: PMC publishes an annual chronic-waterlogging-spots list, and monsoon news archives give lane-level ground truth for the Old City. Hand-encode those as authoritative sill/drainage overrides in stage 5; ~2 days of data entry buys more realism than any raster.
- **[major]** Replan-notification fan-out is a downstream token bomb. A citywide monsoon disruption delays 10^4–10^5 individual (non-cohort) trips; the contract says delayed agents get a MINDS replan hook where 'choice is theirs.' If MINDS answers with LLM calls, one bad rain day costs more than a month of normal operation. WORLD's contract shape actively invites this.
  - Fix: Tier the contract: only focal/watched agents get MINDS notifications; all other delayed trips resolve via a deterministic WORLD-side replan policy (min-cost of wait/reroute/mode-shift/abort with Philox tie-noise), decision logged for later narrative backfill. MINDS opts agents in per attention tier, never per event.
- **[major]** plan() prices travel time only; prefs carries just habitual_route_id. There is no generalized cost a field or disruption can write and MINDS can weight — so nobody can avoid the lane with the dog pack, the dark isolated stretch at dusk, or the known waterlogged underpass except via a fake hard closure. Avoidance behavior is half of how real people move through a city, and it is the central behavioral consequence of most hazard scenarios; without it routes will feel like a nav app, not a life.
  - Fix: Add per-edge, per-mode extra-cost components (risk, comfort, familiarity) stored beside corridor costs, writable by registered fields and disruptions; plan(prefs.weights={risk:..,comfort:..}) folds them into igraph edge costs. Default weight profiles per demographic (child, woman-at-night, elderly) shipped as data.
- **[major]** The LOD1/scale story stops at the Old City. '150–300 corridors' and 40–60 zones are detail-area numbers; full Pune needs thousands of directed corridors and 200+ macro-zones, and tick wall-time, matrix refresh cost, spillback graph behavior, and memory at that size are unbenchmarked. The 2GB open question covers only the graph — a citywide buildings layer (~1.2–1.5M footprints, needed the moment POPULATION places 3.5M people) plus its STRtree is absent from every budget.
  - Fix: Add a hard scale gate to M4: synthetic full-Pune load test with explicit pass budgets (corridor count, 5-min tick < 2s wall, matrix refresh amortized, RSS < 8GB). Tile the buildings STRtree and lazy-load tiles; keep only detail-area tiles resident. If corridor assignment in Python misses budget, vectorize assignment in numpy over a corridor-incidence sparse matrix (scipy.sparse is already in the stack).
- **[major]** Buildings carry no age/condition/typology, so structural-hazard sampling has no exposure surface. A rain-triggered wada collapse — an actual annual occurrence in the Peths — cannot be sampled by EVENTS against the building stock any more than a crash could be sampled without exposure_field; 'a building collapse is a hazard disruption + building-exposure signal' only covers the aftermath, not the where/which/why.
  - Fix: Stage 3 adds typology (wada/chawl/RCC/apartment), age band, and condition prior per building, seeded from ward-level census housing-condition tables, PMC's published dangerous-buildings list, and heritage inventories; expose structural_exposure(zone, rain_72h) analogous to flood exposure. Condition lives in the building_state runtime table so damage and repairs persist.
- **[minor]** No per-POI footfall accounting: trips end at anchors but nothing counts arrivals per POI, and zone-level crowd_idx cannot distinguish two saree shops on the same Laxmi Road block. ECONOMY (price war outcomes), ORGS (temple donation volumes), and micro-crowd texture (school gate at dispersal, the design's own pandal attractivity loop) all need POI-resolution occupancy, and the attractivity column is write-once with no runtime update API.
  - Fix: Increment per-POI daily arrival counters from trip completions and copresence draws; expose footfall(poi_id, window) and update_poi(poi_id, attractivity, hours). Derive a POI-local crowd estimate (footfall vs capacity) that PlaceContext prefers over zone crowd_idx when the anchor is within 50m of a POI.
- **[minor]** Zone-uniform ambient produces identical PlaceContext for every lane in a 2–3km² zone: same weather string, same crowd, same noise. Over hundreds of scenes the prompts converge and the LLM output converges with them — the classic path to samey slop. Meanwhile real Pune street texture (hawker lines, open drains, cattle sheds, alternate-day water-supply timings, garbage-van rounds, load shedding) appears nowhere, so non-event days have nothing local to narrate.
  - Fix: Two cheap layers: (1) deterministic per-edge micro-variation — Philox-keyed noise on crowd/noise/rain within the zone envelope so adjacent lanes differ stably; (2) a static per-edge texture-tag layer authored in stage 7 (hawker_line, open_drain, banyan, tanker_point) plus a per-ward utilities schedule (water-supply windows, load-shed calendar) surfaced via DayContext/PlaceContext.
- **[minor]** route_templates are never invalidated: keyed (o-cell, d-cell, mode, tod) with cached edge_ids, they will serve routes through permanently closed edges (years-long metro barricades, one-way reversals) forever; whether reprice() checks closed_mask is unstated. Habitual-route inertia is realistic, immortal routes are not.
  - Fix: Version templates by (build_hash, world_delta_cursor); reprice validates cached edges against closed_mask and standing disruptions, evicting and re-planning on failure; hit_count decays so dead habits age out.
- **[minor]** Clock-edge interactions are unspecified: a focal scene spanning the 03:00 rollover gets matrices refreshed and the day's bus trips re-instantiated mid-scene; ±30min scene elasticity reconciled at scene end can write canon rows (disruptions, trip completions) with non-monotonic timestamps relative to rows written during the scene; blinker's synchronous dispatch lets a signal handler call disrupt() while the tick is iterating corridor state (mutation under iteration).
  - Fix: Defer rollover while a focal scene is active; canon timestamps always use the city clock with scene-elastic time stored as scene-local offset; signal handlers enqueue commands (disrupt/clear/schedule) applied at tick boundaries rather than mutating state inline.
- **[minor]** Solo-dev schedule and Windows-stack risk are understated: osmium-tool on conda-forge for win-64 has historically been unreliable (verify before committing the pipeline to it); GTFS-shape map-matching and the procedural block-subdivision infill are each 'fiddly week-eater' territory on messy Peth OSM data; three of the open questions (PMPML feed vintage, prabhag shapefiles, chowky boundaries) are data-acquisition risks that gate M1. Nine weeks for M1–M3 assumes zero of these bite. Separately, the determinism discipline (every draw keyed) dies the first time a stray np.random call ships.
  - Fix: Pin fallbacks now: pyosmium-based clip script (wheels exist on Windows) or WSL for build-time only, never runtime. Add ~30% schedule buffer to M1–M2 and make M2 exit criteria include a replay-equality CI test (two seeded runs, byte-compare canon + snapshots) plus a grep/lint gate banning un-keyed RNG imports — determinism enforced from week 4, not retrofitted.

### Novel holdout-scenario traces

I picked the two holdouts that attack this design's two proudest claims: "a metro line opening is a new GTFS feed through stage 8" (world evolution) and "no scenario nouns, everything composes from five primitives" (primitive completeness).

=== TRACE 1: New metro station opens and shifts commute patterns (Swargate/Mandai underground — literally on the Old City detail-area boundary, so this is not an exotic case) ===

Step 1, supply side. The design's own generality argument routes this through stage 8. But stage 8 is an OFFLINE build stage writing world_static.db and shape_edge_map inside a hash-versioned immutable bundle; canon rows pin the build hash. Opening a station mid-campaign requires a rebuild -> new hash -> every existing canon row references the old world; snapshot/restore and the replay contract ("snapshot + injected-event log") have no concept of a bundle swap at sim-day N. BREAK: there is no runtime world-mutation path. The flagship claim is true only between campaigns.

Step 2, geometry. shape_edge_map is built by map-matching GTFS shapes onto the ROAD graph ("shapely project + igraph shortest-path stitch"). Metro runs on exclusive viaduct/tunnel. Matching the Swargate–Mandai tunnel onto peth lanes either fails stage-10 validation or silently produces a bogus road path — after which LOD1 corridor congestion would delay metro trains behind road jams. Absurd, and nothing in the design prevents it. BREAK: transit is structurally assumed to run on road edges.

Step 3, modes. mode_mask is a closed bitmask walk|cycle|2w|car|auto|bus|truck. No rail bit. Per-mode graphs, PCU factors, and RAPTOR labels key on it. BREAK: metro is a schema + code change, not data.

Step 4, journey planning. Walk->metro->auto egress via 2-transfer RAPTOR over merged stop_times actually works if the feed merges and stations are POIs. This half is fine.

Step 5, demand side — which is what the scenario is actually about. WORLD holds supply; commute patterns live in POPULATION cohort registrations (FIXED mode_split, FIXED departure profile) and MINDS habits (route_templates, habitual_route_id). No contract anywhere triggers re-registration of thousands of cohorts when accessibility changes; there is no signal like "zone-pair travel time improved 40%". route_templates keep serving pre-metro habits with no decay trigger (inertia is realistic; immortality is not). BREAK: the commute shift cannot propagate through any existing interface — the city would ignore its own metro. This is the silent special-case: someone will hand-write a one-off cohort re-registration script, which is exactly the scenario-specific code the design bans.

Step 6, replay. Restore a pre-opening snapshot and replay across the opening: which bundle, swapped when? Undefined. Determinism breaks precisely at world-evolution boundaries.

Note what DOES work: years of metro construction barricades are perfect standing disruptions — the design handles the construction beautifully and the opening not at all. Required fixes (all in the issues list): world-delta log applied at sim timestamps, rail edges off the road graph (build transit edges directly from GTFS shapes, flagged off-road), widen mode_mask now, and a world.access.changed signal + POPULATION demand-refresh contract.

=== TRACE 2: Stray dog attacks a child near a school gate (lane off Kasba Peth, 13:05 dispersal) ===

Step 1, the dog. The five primitives (field/trip/disruption/jurisdiction/timer) have no slot for a persistent non-human hazard with identity and territory. Every encoding misfits: (a) a "dog_menace" field — but zone_state has HARDCODED columns and there is no register_field/set_field API, so "new phenomena are just new fields" means editing WORLD's schema and code: the generality argument's most-used escape hatch does not exist as a mechanism; (b) a hazard_zone disruption — severity speaks only capacity_pct/speed_cap/modes_blocked; a dog pack blocks nothing, so EVENTS smuggles risk_multiplier into severity_json as an untyped convention WORLD ignores — a silent special-case by construction; (c) dogs as agents — POPULATION scope explosion. And the pack must persist to be caught by the PMC van, return, menace the lane for weeks: fields have no identity, disruptions are road-state rows.

Step 2, the victim. EVENTS samples exposure_field(edge, 13:05, mode=walk) then presence_sample. Two failures: (i) 95% of demand is cohort flows at corridor/matrix resolution — per-edge occupancy at a specific gate is an undefined disaggregation, so "near the school gate" precision is fictional; (ii) register_cohort carries no demographic composition and materialize() takes only a slice_seed, so the filter cannot express "unaccompanied child on foot." The scenario's essential fact — the victim is a child — is unexpressible in WORLD's contracts.

Step 3, the gate. crowd_idx is zonal (200–500 buildings, ~2-3 km2). At dispersal the gate is the densest 30 metres in the zone; PlaceContext will report zone-average "moderate" crowd to the scene prompt. The LLM narrates from wrong ambient facts — precisely the grounded-prompt guarantee WORLD exists to provide.

Step 4, response — works. Injury -> nearest('hospital', mode='ambulance') -> Sassoon with honest jam ETA; jurisdiction('chowky') for the complaint; PMC dog-squad van as a service trip; follow-up sterilization drive as timers. This half composes cleanly and credibly.

Step 5, aftermath behavior — breaks. Parents route children around that lane for weeks. plan() prices time only; prefs has only habitual_route_id; no per-edge risk/comfort cost exists for a field or disruption to write and MINDS to weight. The emotionally central consequence — fear changing paths — cannot exist in WORLD terms short of faking a hard closure. Same gap kills dusk-avoidance in the chain-snatching holdout and gendered route choice generally.

Step 6, the rumor. Copresence-driven spread of "dog bit a child at the school" works well — except the context enum (bus_ride|queue|market_street|temple|waterlogging_wait|pandal_crowd) is hardcoded and has no school_gate; another closed enum posing as data.

Net: detection/dispatch/jurisdiction infrastructure genuinely composes; the hazard's existence, the victim's identity, the gate's crowd, and the avoidance behavior all require WORLD changes (field registry API, demographic cohort composition, POI-local crowd resolution, generalized routing costs). Both traces show the same failure signature: the design's runtime machinery is strong, but its extensibility claims are narrative — the extension points (new fields, new modes, new demand, mutated geography) are described as data but implemented as code.