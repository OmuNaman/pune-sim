# Pune Sim

A text-first, LLM-driven life simulation of Pune, India — grounded in real data.

Think Kingdom Come: Deliverance 2 / RDR2 NPC schedule simulation crossed with Dwarf
Fortress emergent history, but with LLM minds and a real city: zoom to any person,
follow their day, interview them, inject events (an accident, a rumor, a flood) and
watch consequences propagate through traffic, families, institutions, and gossip.

## Core principles

- **LOD simulation.** A deterministic "clockwork" layer (schedules, traffic, transit,
  stochastic hazards at real base rates) advances the whole city cheaply. LLMs are
  invoked only at decision points and scenes — the LLM is the camera and the judge,
  not the physics.
- **Cognition tiers.** T0 background (no LLM) → T1 household morning scenes, fired
  through a routine-bypass gate (~5–8% of households on a normal day — the rest
  replay cached schedule templates at zero cost) → T2 scenes for non-routine
  interactions → T3 premium-model roleplay for whoever is being watched.
- **Canon DB.** Every generated fact (names, biographies, memories, outcomes) persists
  forever; LLMs retrieve but never contradict. People get detail lazily, only when
  attention reaches them, seeded from real ward-level statistics.
- **Reality anchors are read-only.** Real geography, bus network, demographics,
  institutions, law (BNS/BNSS), calendar, and crime/accident base rates. The model
  fills gaps; it never overwrites facts.
- **Fictional individuals only.** Real places, institutions, and statistics — never
  simulated lives attached to real named private people.
- **Identity is structure; disclosure is governed.** Religion, community, and caste
  shape the real city — names, marriages, neighborhoods, festivals — so they shape
  the sim's structure always. Prompts see identity only at declared disclosure
  tiers, with premium routing + full QC on identity-salient scenes, a
  narrator/character two-voice rule, and narratability caps on violence. Riots,
  bandhs, and processions are clockwork mass behavior with sampled LLM scenes.
  See [docs/subsystems/08-identity.md](docs/subsystems/08-identity.md) and
  [docs/subsystems/09-collective-dynamics.md](docs/subsystems/09-collective-dynamics.md).
- **Generality.** Events, minds, organizations, information, and long arcs are general
  abstractions. An accident, a wedding, a court case, an election, and Ganeshotsav are
  instances, not special cases.

## Starting area

Old City core: Kasba Peth + Shaniwar Peth + Budhwar Peth + Raviwar Peth
(~2–3 km², ~50k residents, ~12k households). Scale target: full Pune (~3.5M).

## Real data sources

*(Table corrected 2026-07-31 after source verification — details in
[docs/architecture.md §9.3](docs/architecture.md).)*

| Data | Source |
|------|--------|
| Road network, buildings, POIs | OpenStreetMap via [Geofabrik **India Western Zone**](https://download.geofabrik.de/asia/india.html) (~209 MB; no Maharashtra extract exists — clip with osmium). Footprints decent in old city, ~99% untyped: derive use from POIs + Google Open Buildings / Microsoft footprints |
| Ward boundaries (GeoJSON/SHP) | [DataMeet Pune wards](https://github.com/datameet/Pune_wards) (2012 electoral vintage), [bharatlas](https://bharatlas.com/view/wards_pune) (likely 2022 draft) — **sketch layers only**; census geometry needs a hand crosswalk from the [District Census Handbook](https://censusindia.gov.in/nada/index.php/catalog/820) maps for the starting peths |
| Ward demographics (Census 2011) | [opendata.pmc.gov.in](https://opendata.pmc.gov.in/) (portal flaky) — use the [OpenCity mirror](https://data.opencity.in/dataset/pune-census-2011-data): 151 ward rows incl. SC/ST; **no ward religion column exists** |
| Identity priors (peth-level, `provenance=estimate`) | Census C-1 religion (town-level), Gadgil *Poona: A Socio-Economic Survey* 1945/52 (peth-by-peth), OSM places of worship, Susewind published booth estimates — see [08-identity](docs/subsystems/08-identity.md) |
| Bus network: 494 routes, 6,203 stops, 10,728 trips (live feed 2026-07; vendor a hash-pinned zip) | [PMPML GTFS](https://github.com/croyla/pmpml-gtfs/) (unofficial scrape, actively maintained) |
| Traffic microsimulation | [Eclipse SUMO](https://eclipse.dev/sumo/) (imports OSM + GTFS; TraCI API) — optional, deferred |
| Police structure: 5 zones, 30 stations, 104+ chowkies | [Pune Police](https://en.wikipedia.org/wiki/Pune_Police), punepolice.gov.in crime review |
| Courts | Pune District & Sessions Court (Shivajinagar) via e-Courts / NJDG pendency data (dashboards, no bulk API) |
| Politics | PMC: **41 prabhags, ~165 corporators; the Jan 2026 PMC election already happened** — sim treats it as a counterfactual-replay test |
| Crime/accident base rates | NCRB city tables (total rioting only; communal split is state-level → Maharashtra rates × exposure share), Pune Police crime review |
| Collective-event calibration | 2014 Hadapsar (Mohsin Shaikh), 2018 Bhima Koregaon violence + bandh; 1894 Pune / 2009 Miraj as festival-trigger templates — see [09-collective-dynamics](docs/subsystems/09-collective-dynamics.md) |
| History | 18 peths (1713–1818), Kasba Peth (5th c.), Shaniwar Wada (1730), 450+ heritage wadas |

## Inference budget

Workhorse: DeepSeek V4-Pro ($0.87/M out; cache hits ~free at $0.0036/M in) for
T1/T2 prose, V4-Flash ($0.28/M out) for structure-only calls, batched with
aggressive prefix caching and peak-window avoidance. Premium model only for T3
focal and identity-salient (tier ≥ 1) scenes. Expected: **~$2–2.4/sim-day** at
Old City scale (~$60–75/mo at daily play; declared event days ~$10–18 from a
reserved budget), ~$12–14/sim-day at full Pune. Local GPU optional
(embeddings, tiny QC judge). Corrected cost audit:
[docs/architecture.md §5](docs/architecture.md).

## Status

- [x] Concept research and feasibility (see `docs/`)
- [x] Architecture document — [docs/architecture.md](docs/architecture.md) (16-agent planning fleet; per-subsystem blueprints + red-team critiques in [docs/subsystems/](docs/subsystems/))
- [x] Second-pass audit (2026-07-31, six-agent fleet): identity layer resolved ([08-identity](docs/subsystems/08-identity.md)), collective dynamics added ([09-collective-dynamics](docs/subsystems/09-collective-dynamics.md)), cost model corrected, data sources verified, 19 coherence rulings ([architecture §9](docs/architecture.md))
- [ ] **V0 — one peth block, full stack thin (~6–10 wks):** constitution kernel (event log, keyed RNG, WorldDelta, fact gate) + ~50–100 households + one scene lane + injection + interview. **Exit: the school-bus-crash scenario runs end-to-end and feels alive; replay bit-identical.**
- [ ] V1 — texture: rumor/INFO, hazards, pressure integrators, minimal map
- [ ] V2 — institutions push back: FIR + hospital procedures, finances-lite, LLM injection compiler, branch-diff, minimal riot probe
- [ ] V3 — real Pune data at scale: full ingest, IPF synthesis, traffic, Procedure interpreter, inference gateway, PMTiles viewer (subsumes "Old City breathing with zero LLM calls")
- [ ] V4+ — arcs, courts, QC depth, collective dynamics in full, counterfactual 2026-election replay, 3.5M scale-out

The doc suite is the *reference architecture*; execution follows the vertical
slices above ([architecture §6](docs/architecture.md)).
