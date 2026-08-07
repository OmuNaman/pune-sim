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
- [x] **V0 — one peth block, full stack thin:** constitution kernel (event log, keyed RNG, WorldDelta, fact gate) + 80 households on the real Kasba OSM block + morning scene lane + injection + interview. Exit met: the crash scenario runs end to end, replay is hash-identical at zero API cost.
- [x] **V1 — texture:** INFO v1 (claims as data, mechanical distortion ops, logit belief update, Maki-Thompson stifling), thin hazards with tiered percepts, two pressure integrators, Leaflet viewer. An injected false rumour spread to 27 people through 5 drifting variants and changed 10 people's behaviour; an un-injected school fire became the block's biggest news on its own. All three exits met: cost $0.0029/sim-day against a $1 bar, every rumour rose and died, and 30 sim-days on a followed family with **zero canon contradictions** — verified by a skeptic pass and by the same gate still failing the first soak. Four soaks and the full trail are in [docs/soaks/](docs/soaks/).
- [x] **V2 — institutions push back:** hospital admission → stay → bill → loan, police FIR taken from the complainant's *own* drifted account, finances-lite, LLM injection compiler (free text → grounded, validated injection), branch-lite (fork a world, diff the timelines), and a minimal collective-dynamics instance (Granovetter mobilization, police, curfew, shelter).
- [x] **V1.1 — the 30-day soak's findings, fixed:** the first soak passed on cost and rumour lifecycle and *failed* the continuity exit with four contradictions, so every failure was root-caused against the event log and closed — scenes no longer see their own prior output (64% of every prompt block used to be the household's own words), ids arrive with names and events with dates, witnesses keep what they saw, attention rotates instead of freezing, work is counted by absence rather than by activity strings, and `scripts/audit_run.py` turns all of it into 27 mechanical probes. It took four soaks. Each one's failures collapsed into fewer, deeper causes — three mechanisms, then two, then one (*the world fixed the power and told nobody*), then none. The last run passes both gates.
- [ ] **V3 — real Pune data, at scale** *(in progress)*. **Step 0, the scale probe, is done** — and it was worth doing before the data work rather than after: the day pipeline ran at n^1.86 in population, so V3's 12k households would have cost twelve minutes per sim-day with zero LLM calls. Three superlinear defects, none architectural — a crowd was being treated as a room and every overlapping pair enumerated, the day's whole log was rescanned inside a per-household loop, and a hazard's distance to a place was measured once per person instead of once per place. All fixed; co-presence is now linear, and the fix made rumours spread *more*, because all-pairs had been killing them by saturation. **Step 1 is done**: the world is a four-peth old-city extract (438 named places, 7,008 buildings) and **12,000 households / 49,578 people now run at 62s per sim-day** — a 30-day clockwork soak of a 50,000-person city in half an hour, with no model in the loop, audited at **0 FAIL**. **Step 2 is done**: that population is no longer a guess. Its household size, sex ratio and under-7 share are fitted to the 2011 Kasbavishrambaug ward-office marginals and land within 0.007, 0.0014 and 0.0009 of them. The clearest single number: under all-pairs, how many people you exchanged news with in a day was a function of how big the city is (20/day at 306 people, 225/day at 11k, still climbing); it is now flat at 17–21 across a 160× population range. **The new world has been soaked**: 30 sim days, 6.8M events, 38 minutes, audited in four windows with **0 FAIL** — and it found three defects nothing smaller could have, all of the same kind. A rumour that never died, because freshness decayed from when the *teller* heard it rather than when the event happened, so every new hearer restarted the clock and a big enough population outran saturation: "the power is back" reached 40% of the city a fortnight on. A belief lane that got slower the longer the run went. An avoidance 1,138 people did not have. None are visible below about ten thousand people. **Step 3 is done**: people walk on the streets. The 2,057 pinned OSM ways become a 7,978-node walking graph and a trip is a shortest path along real lanes — which turns out to make 88% of walks *shorter* than the old flat 1.4 detour factor, and the other 11% up to 2.4× longer, because a constant cannot know which trips go the long way round. And the viewer can now open a 6.8M-event log at all: it was reading the whole thing into memory, ~7.6 GB, so the one command whose purpose is *look at this* could not open the runs V3 exists to produce. **Step 4 is done**: institutional procedures are data. V2 wrote two by hand — a hospital stay and a police FIR — as seventy lines of the same shape; they are now a 97-line interpreter plus a catalog, and the claim that a third is cheap is a test that defines a court summons in five lines rather than a sentence saying so. **And the cost exit holds**: 2 days of scenes at 12,000 households cost **$0.0031/sim-day** against V3's $2 bar — V1 measured $0.0029 at *80* households. A hundred and fifty times the people for seven percent more money, because the routine-bypass gate makes LLM spend a function of attention rather than population. The honest other half: 14 of 12,000 households were on camera. **Step 5 is done**: event classes are data too — `data/classdefs/hazards.json` with a validating loader — and one of their fields is a safety rule the architecture settled and nobody had built. `narratability: numeric` means an event happens, is counted, seeds no gossip and can never open a scene, however hard attention is pointed at it; NCRB calibration will generate classes that need it, and the machinery is there before the data is. Still to come: cohorts, the full inference gateway, PMTiles viewer. [docs/perf/scale-probe.md](docs/perf/scale-probe.md) · [docs/soaks/v3-scale-soak.md](docs/soaks/v3-scale-soak.md).
- [ ] V4+ — arcs, courts, QC depth, collective dynamics in full, counterfactual 2026-election replay, 3.5M scale-out

The doc suite is the *reference architecture*; execution follows the vertical
slices above ([architecture §6](docs/architecture.md)).

## Running it

```bash
uv sync
uv run pytest -q                       # 112 tests, no API key needed

# the whole block breathing, zero LLM calls, deterministic
uv run punesim run --days 7 --db runs/dev/events.db

# the V3 world: four peths, 12k households, 49,578 census-calibrated people —
# still zero LLM, ~62s per sim-day (`--block kasba` stays the frozen V0-V2 pin)
uv run punesim run --days 7 --households 12000 --block oldcity --db runs/oldcity/events.db

# with minds: morning scenes for the attention-gated households
uv run punesim run --days 30 --scenes --follow hh:000 --db runs/soak/events.db

# the map viewer: follow anyone, read their scenes, inject, interview
uv run punesim serve --db runs/soak/events.db      # http://127.0.0.1:8618

# free text -> a grounded, validated injection (no event-specific code)
uv run punesim compile "the mandal treasurer stole two lakh rupees"

# fork a world, run the what-if, and diff the two timelines
uv run punesim branch --db runs/soak/events.db --inject runs/injections/ui_compiled.json

# mechanical audit of any run: duplication, identity, spotlight, hazards, cost
uv run python scripts/audit_run.py --db runs/soak/events.db --seed 108
```
