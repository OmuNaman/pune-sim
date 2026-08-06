# 7. Orchestration & interface

## Summary

The Orchestration & Interface subsystem is the conductor of Pune Sim: a single-process asyncio "kernel" that advances a simulated day as 288 five-minute ticks through a fixed pipeline (nightly reflection batch → staggered morning household planning → interleaved clockwork ticks + scene resolution → evening consolidation), invoking other subsystems only through narrow async contracts. Its foundation is event sourcing over SQLite: an append-only event log is the sole source of truth, world state and the canon DB are deterministic projections of it, and LLM outputs are captured as input-events ("recorded nondeterminism") so replay is bit-exact without re-calling any model. Randomness uses counter-based Philox draws keyed by (seed, domain, entity, tick), so injecting or removing an event never perturbs unrelated entities' rolls — enabling clean what-if branches forked at any event id with copy-on-write log views. The god console (prompt_toolkit REPL mirrored over websocket JSON) provides follow/interview/inject/query/advance/branch/stats; free-text injections compile through an LLM structured-output pass into the general Event schema, get grounded against OSM/canon (synthesizing missing participants via lazy-gen), and are previewed before commit. The map viewer is FastAPI + MapLibre GL JS over a static PMTiles Pune basemap, pushing viewport-culled agent positions, per-segment congestion, scalar field overlays (floods, rumor spread), and an event ticker at ~2 Hz; SUMO-GUI is relegated to an optional on-demand "traffic lens" for a bounding box, never the primary window. An AttentionField (user focus + perturbation + arc activity) is the single mechanism that assigns cognition tiers and level-of-detail anywhere in the city, which is what makes all eight probe scenarios instances of one pipeline. Testing rests on cassette-recorded LLM replay (record/replay/live modes), event-log hash determinism tests, canon-conflict validators, and per-tier cost-ceiling regression tests. Five milestones take the system from M1 (Old City clockwork breathing with zero LLM calls, deterministic, visible on the live map) to M5 (full-Pune scale + the 2026 ward election).

## Design

# Orchestration & Interface — Detailed Design

## 0. Position in the system

Orchestration owns TIME, TRUTH, and the USER: the tick loop, the event log + determinism + branching, the god console, and the map viewer. It treats every other subsystem (clockwork, minds, events/schema, canon, world/lazy-gen, llm) as a library called through a narrow async interface. Nothing in this subsystem knows what a "wedding" or a "flood" is — it only knows Events, Scenes, Fields, Intents, and Attention.

## 1. Time model and the day pipeline

- `sim_time` = integer minutes since sim epoch (2026-01-01 00:00 IST). One **tick = 5 sim-minutes**; one day = 288 ticks. Sub-tick ordering is fixed and deterministic (see §3).
- Wall time is decoupled. The conductor paces ticks per the current mode (§4); asyncio is used for concurrency of LLM calls and the websocket server, never for sim timing.

### Day pipeline (phases are scheduling policy, not special-cased content)

```
Phase R (03:00, for day D-1):  minds.reflect() batch — memory consolidation, arc
                               progression proposals. Launched async; clockwork keeps
                               ticking through the cheap night hours in parallel.
Phase P (04:00–09:30):         morning household planning. For each household, at
                               (wake_tick - 6 ticks): T0 → template plan (no LLM);
                               T1 → minds.plan_household() async batch call.
                               If a T1 result isn't back by wake_tick, the household
                               starts on its template plan and HOT-SWAPS when the plan
                               arrives (a PlanRevised event) — graceful degradation.
Phase T (all 288 ticks):       main tick loop (below).
Phase E (18:00–22:00):         evening scenes are just Phase-T scenes; no special code.
Phase S (03:00):               daily snapshot of all subsystem states (see §6).
```

### Main tick loop (core pseudocode)

```python
async def run_day(self, date: SimDate):
    self.jobs.launch(minds.reflect_batch(date.prev()))          # Phase R, fire-and-track
    for tick in day_ticks(date):                                # 288 iterations
        await self.conductor.gate(tick)                         # §4: pause/step/focus/pace
        self.jobs.launch_due_plans(tick)                        # Phase P staggered launches

        ev  = clockwork.tick(tick)                              # deterministic movement/traffic/transit
        ev += hazards.sample(tick)                              # Philox keyed draws vs base rates
        ev += self.event_log.due(tick)                          # scheduled events fire (hearings, weddings…)

        derived = self.bus.publish_all(ev)                      # subsystems react: follow-up events,
                                                                # scene triggers, field updates
        triggers = self.scene_scheduler.collect(tick,           # attention-ranked, budget-capped
                                                self.attention, self.budget)
        results  = await self.scene_pool.run(triggers)          # semaphore-bounded T2 LLM calls;
                                                                # T3 focal scenes go interactive (§4)
        self.commit(tick, ev + derived + results.events)        # single atomic append batch
        self.attention.decay_and_update(tick, ev + derived)
        self.viewer.push_frame(tick)                            # ~2 Hz wall-throttled
```

`commit` is the ONLY writer to the event log. Everything downstream (canon, viewer, stats) is a projection.

## 2. Event sourcing, schema, storage

**SQLite** (WAL mode, `PRAGMA synchronous=NORMAL`), file per run: `runs/<run>/events.db`. Boring, transactional, single-writer matches our single conductor.

```sql
CREATE TABLE branch (
  branch_id INTEGER PRIMARY KEY, name TEXT UNIQUE, parent_branch INTEGER, fork_seq INTEGER,
  created_wall TEXT);
CREATE TABLE event (
  seq        INTEGER,             -- monotonic per branch
  branch_id  INTEGER,
  sim_time   INTEGER NOT NULL,    -- minutes since epoch
  tick       INTEGER NOT NULL,
  type       TEXT NOT NULL,       -- from the Events subsystem taxonomy registry
  payload    BLOB NOT NULL,       -- orjson; validated by pydantic model for `type`
  caused_by  TEXT,                -- json array of (branch_id,seq) — causality DAG
  provenance TEXT NOT NULL,       -- 'clockwork'|'hazard'|'llm'|'user'|'schedule'
  actor_ref  TEXT,                -- entity id if attributable
  wall_meta  BLOB,                -- wall time, llm usage/cost/model — EXCLUDED from determinism hash
  PRIMARY KEY (branch_id, seq));
CREATE INDEX ev_time ON event(branch_id, tick);
CREATE INDEX ev_type ON event(branch_id, type, tick);
CREATE TABLE fact_projection (    -- canon materialized view; REBUILDABLE from events
  branch_id INTEGER, entity_id TEXT, attr TEXT, value BLOB,
  established_seq INTEGER, superseded_by_seq INTEGER,
  PRIMARY KEY (branch_id, entity_id, attr, established_seq));
CREATE TABLE llm_call (           -- the cassette store, also lives in-log as wall_meta
  prompt_hash TEXT, branch_id INTEGER, seq INTEGER, model TEXT,
  in_tokens INTEGER, out_tokens INTEGER, cost_usd REAL, response BLOB,
  PRIMARY KEY (prompt_hash, branch_id, seq));
```

**Recorded nondeterminism (key decision):** every LLM response is appended as an event (`LLMResultRecorded` wrapping the typed result, e.g. `ScenePlayed`, `PlanMade`, `FactEstablished`). Replay NEVER calls a model — it folds recorded events. Thus the sim is fully deterministic given (seed, injected user events, recorded LLM events), and the clockwork layer alone is deterministic given (seed, event set).

**Canon-as-projection:** the Canon subsystem consumes `FactEstablished`/`FactSuperseded` events via `canon.project(event)` and maintains `fact_projection`. Branch-awareness falls out for free: a branch's canon = fold of its ancestor chain up to fork + its own tail. Rejected alternative: separate mutable canon DB (would need its own branching/merge machinery and could drift from the log).

## 3. Determinism and RNG

**Counter-based RNG.** `numpy.random.Generator(Philox(key=...))` used STATELESSLY: every draw is keyed by `(run_seed, blake2s(domain), blake2s(entity_id), tick, draw_index)`. Consequences:
- Adding/removing an entity or injecting an event does not shift any other entity's draws → clean A/B causal comparison across branches ("same accident dice, different flood").
- No RNG state to snapshot; replay from any point is trivial.
- Domains are registered strings: `hazard.road_accident`, `demography.birth`, `gossip.mutation`, `transit.dwell_jitter`, `weather.cell`, etc.

**Sub-tick ordering:** within a tick, events are processed in a canonical sort: `(phase_rank, type_rank, entity_id)`. Scene results from the async pool are sorted by `(trigger_seq)` before commit, so concurrency never leaks into the log order.

**Determinism hash:** `blake2s` chained over canonical orjson of each event with `wall_meta` stripped. Exposed as `stats hash` and used by tests (§9).

## 4. Conductor: run modes, yielding to the user, interviews

```python
class Mode(Enum): FREE_RUN, STEP, FOCUS, PAUSED
class Conductor:
    cmd_q: asyncio.Queue[ConsoleCommand]   # fed by REPL and websocket
    async def gate(self, tick):
        while True:
            self.drain_commands()          # apply follow/speed/pause/inject-preview...
            if self.mode is PAUSED:            await self.wake.wait()
            elif self.mode is STEP:            self.steps -= 1; return  # until 0 → PAUSED
            elif self.mode is FOCUS:
                # run at 1:1..8:1 pace; if focal person enters a scene this tick,
                # scene_pool runs it at T3 interactively and the loop awaits user turns
                await self.pace(tick, self.focus_speed); return
            else:                              await self.pace(tick, self.speed); return
```

- **FREE_RUN(speed)**: target N sim-days/wall-hour; automatically throttled by scene-pool depth and the budget governor (§8).
- **FOCUS(entity)**: the viewer camera locks on, tier ceiling for that entity's scenes is T3 (premium model), and the loop pauses at their scene boundaries awaiting the user.
- **Interview = time bubble**: `interview <person>` pauses the tick clock and runs a T3 dialogue scene stamped at the paused tick. Two diegetic modes: default **journalist** (the person canonically experienced "a conversation with a stranger"; `ConversationHeld` + `MemoryFormed` events commit) or `--ghost` (no memory event; pure observation). This keeps interviews inside the general Scene mechanism instead of a special channel.
- Cooperative yield points: the conductor checks `cmd_q` between ticks and between scene turns, so `pause`/`inject` land within one tick of being typed even at high speed.

## 5. God console

**Surface:** `prompt_toolkit` REPL with `rich` rendering, and the identical commands as JSON frames over the viewer websocket (`{"cmd": "...", "args": {...}}`) so map clicks and terminal are one language. Grammar (EBNF-ish):

```
cmd      := follow | unfollow | interview | inject | advance | pause | resume | speed
          | where | timeline | query | stats | branch | checkout | diff | save | budget | ls
follow   := "follow" entity_ref
interview:= "interview" entity_ref [ "--ghost" ] [ quoted_opening ]
inject   := "inject" FREETEXT [ "at" simtime ] [ "--dry" ]
advance  := "advance" ( DURATION | "until" ( simtime | "event" cond ) )
query    := "query" FREETEXT                     # read-only NL→SQL
branch   := "branch" NAME [ "at" ("evt:"SEQ | simtime) ]
diff     := "diff" NAME NAME [ "--entity" entity_ref | "--type" TYPE ]
entity_ref := "person:"ID | "hh:"ID | "place:"OSMREF | NAME_SEARCH
```

**Inject → event compilation pipeline** (the generality workhorse):

```python
async def compile_injection(text, at=None):
    draft = await llm.call(tier=T2, schema=InjectDraft,          # structured output
        prompt=COMPILER_PROMPT(text, event_taxonomy_card, world_summary_card))
    # InjectDraft: {event_type, sim_time, location_spec, participants:[ParticipantSpec],
    #               magnitude, payload_fields, followup_schedule:[...]}
    grounded = ground(draft)
    #  location_spec  → OSM node/way via local geocoder index (data/osm_index)
    #  ParticipantSpec→ "existing": canon search  |  "synthesize": world.materialize(role_constraints)
    #                   (this is how S1's truck driver comes to exist on demand)
    issues = validate(grounded)   # pydantic schema, anchor conflicts (can't demolish a real
                                  # landmark), plausibility vs base rates (warn, not block)
    return Preview(grounded, issues)          # user sees a diff card; `y` → commit with
                                              # provenance='user'; --dry never commits
```

**Query engine:** NL → SQL via a schema-card prompt; executed on a `file:events.db?mode=ro` connection (hard read-only), plus registered safe views (`v_person`, `v_events_today`, `v_congestion`). Results as rich tables; `stats` is a curated set of the same views (population, cost ledger, event counts by type, hash).

## 6. Persistence, save/load, branching

- **Snapshot** = per-subsystem `msgpack` blobs (versioned) written at Phase S and on `save`: `runs/<run>/snapshots/<branch>/<seq>/{clockwork,minds,world,attention}.mp`. Load = nearest snapshot ≤ target seq, then replay the event tail through the fold. Target: load ≤ 5 s for Old City.
- **Resume:** `punesim run --resume runs/<name>` (default resumes latest branch head).
- **Branching:** `branch monsoon-test at evt:184223` inserts a `branch` row (copy-on-write view over ancestor events). After the fork point, hazard draws are identical (counter-based RNG); LLM calls after the fork are LIVE again by default (`--replay-llm` reuses ancestor cassettes where prompt hashes still match). `diff A B` = anti-join of event tables after fork, groupable by entity/type — this is the "watch consequences propagate" instrument.
- Canon per branch = projection fold with per-branch cache tables; rebuildable via `punesim project --branch B`.

## 7. Map viewer

- **Server:** FastAPI + uvicorn inside the same process (asyncio task), `/ws` websocket + static files. No build step: vanilla JS + MapLibre GL JS + pmtiles.js from vendored files (CSP-clean, offline).
- **Basemap:** Pune OSM extract as a single **PMTiles** file served statically (rejected: running tileserver-gl — one more moving part for zero gain).
- **Frame push** (~2 Hz wall, coalesced; only deltas where cheap):

```json
{ "tick": 97, "sim_time": "2026-07-31T08:05", "mode": "FOCUS",
  "agents":  [{"id":"p:8812","lat":18.5304,"lon":73.8567,"mode":"bus","activity":"commute","flag":"focal"}],
  "segments":[{"way":123456,"cong":0.82}],
  "fields":  [{"name":"flood_depth","kind":"grid","cells":[[18.51,73.85,0.4]]}],
  "ticker":  [{"seq":184301,"sev":3,"text":"Collision: truck vs school bus, FC Road x JM Road","loc":[18.5292,73.8412]}] }
```

- **Culling:** client sends viewport bbox + zoom; server sends only materialized agents in bbox (cap ~2k markers; beyond that, aggregate to density hexbins — same `fields` channel). Focal entity always included. Fields are the universal overlay: congestion, flood depth, rumor penetration, procession crowd density — anything scalar-over-geography renders without new viewer code.
- **Interaction:** click agent/place → detail panel (canon card + recent timeline) → buttons emit the same console JSON (`follow`, `interview`, `timeline`).
- **SUMO-GUI boundary:** SUMO is never the main window. If the traffic subsystem escalates a hot zone to microsimulation, orchestration offers `lens sumo <bbox>` which exports the mesoscopic state to a SUMO scenario and launches SUMO-GUI as an external debug/showcase viewer. Default congestion is our own mesoscopic model rendered in MapLibre.

## 8. LLM layer contracts and the budget governor

- `llm.call(tier, prompt_parts, schema, cassette_key) -> (parsed, usage)`; provider = OpenAI-compatible client (DeepSeek-class) with prefix-cache-friendly prompt layout (static system card first), `tenacity` retries, strict pydantic parsing with one repair round.
- Modes via `PUNESIM_LLM=live|record|replay` — replay serves from `llm_call` by prompt hash (the VCR cassette), record does live+store, replay+miss = hard error in tests.
- **Budget governor:** leaky buckets per wall-day and per sim-day from the cost ledger (usage lives in `wall_meta`). Demotion ladder when projected spend > cap: shrink T1 planning cohort → batch T2 scenes into multi-scene calls → resolve low-attention T2 triggers with T0 template outcomes (logged as `SceneResolvedTemplated` so replay stays exact). `budget` command shows ledger + current ladder rung.
- **AttentionField** (owned here): score per entity = w1·user_focus + w2·recent_perturbation(decay) + w3·arc_activity + w4·event_proximity. Tier assignment = pure function of (score, ladder rung). This single mechanism is the LOD dial for the whole city.

## 9. Repo layout (D:/Coding_Workspace/pune-sim)

```
pyproject.toml            # uv-managed, Python 3.12, single package + extras
src/punesim/
  kernel/     conductor.py loop.py attention.py jobs.py budget.py
  events/     taxonomy.py models.py log.py bus.py           # Events subsystem
  canon/      projection.py query.py
  clockwork/  movement.py traffic_meso.py transit.py hazards.py
  minds/      tiers.py scenes.py plan.py reflect.py
  world/      geography.py materialize.py institutions.py calendar.py
  console/    repl.py grammar.py compiler.py nlq.py
  viewer/     app.py frames.py static/{index.html,app.js,maplibre*,pmtiles*}
  llm/        client.py recorder.py budgetmeter.py prompts/
  persistence/ snapshots.py branches.py replay.py
data/         pune.pmtiles gtfs/ census/ rates/ osm_index/   # read-only anchors
runs/         <run_name>/{events.db, snapshots/, config.toml}
tests/        unit/ invariants/ scenarios/ cassettes/
scripts/      build_pmtiles.py ingest_gtfs.py synth_population.py
```

Libraries (all boring/proven): sqlite3 stdlib + `orjson`, `pydantic` v2, `numpy` (Philox), `rustworkx` (OSM routing graph — networkx is too slow for 12k daily routes), `pyrosm`/`osmium` (extract ingest), `gtfs-kit` (PMPML), `fastapi`+`uvicorn`, `prompt_toolkit`+`rich`+`typer`, `httpx`/openai SDK, `tenacity`, `msgpack`, `pytest`+`hypothesis`. Windows notes: default Proactor loop works with uvicorn; use `multiprocessing` spawn only if ever needed (M1–M4 are single-process); SQLite WAL is fine on NTFS.

## 10. Testing strategy

1. **Determinism:** run M1 city 3 sim-days twice with same seed → identical event-log hash. Property: snapshot at random seq + replay tail == straight run (hypothesis-driven seq choice).
2. **Scenario replay (golden):** each probe S1–S8 = fixture (config + inject script + recorded cassettes). CI runs in `replay` mode; downstream event log must hash-match golden. Cassette staleness policy: prompt-hash miss fails loudly; `record` refresh is a reviewed commit.
3. **Canon consistency:** structural validator — no two live `FactEstablished` rows conflict on (entity, attr) without supersedes link; every scene prompt's retrieval set (logged) contains all facts its output references (checked by id, LLM-free); sampled LLM-judge audit run manually pre-release.
4. **Cost regression:** replay each scenario counting calls/tokens per tier; assert ceilings (e.g. S1 ≤ 40 T2 calls, ≤ 1 T3; a full quiet sim-day at Old City ≤ $1.50 equivalent).
5. **Invariant folds (debug mode):** person in exactly one place; movement speed ≤ mode max; household membership conserved; event `caused_by` DAG acyclic; hazard incidence over 90 sim-days within CI of NCRB base rates (statistical test).

## 11. Milestones

- **M1 — Clockwork Old City (zero LLM).** Synthesized 12k-household population from ward stats (unnamed, template schedules), OSM movement + PMPML boarding, mesoscopic congestion, hazard sampling with scripted institutional responses (ambulance dispatch, jam formation), live map, console (`advance/pause/speed/where/stats/inject --dry` for road closures), snapshots+resume. **Accept:** 30 sim-days ≤ 15 wall-min; replay hash identical across 2 runs; AM/PM congestion peaks visible; injected road closure visibly reroutes commutes; zero LLM calls asserted.
- **M2 — Minds v1 + canon.** T1 planning for top-50-attention households, lazy naming/biography on materialize, interview (T3, time bubble), canon projection + retrieval, nightly reflection, cassette recorder. **Accept:** interview a random resident twice 10 sim-days apart → no canon contradictions (validator + human check); S2 rumor probe runs end-to-end; day cost ledger accurate to provider dashboard ±5%.
- **M3 — Event compiler + scenes at scale + branching.** Free-text inject with grounding/synthesis, T2 scene scheduler, institutional consequence chains (FIR, hospital admission as scheduled events), branch/checkout/diff. **Accept:** S1 injected at 08:10 → by 10:10 sim: map jam, ambulance trip to Sassoon, school absence events, FIR event, parental-panic scenes — with no S1-specific code; `diff` vs no-accident branch lists only causally-downstream events; S3 flood field renders and damages commutes.
- **M4 — Arcs + long-horizon institutions + budget governor.** Slow arcs (S5), court docket with adjournment rescheduling (S6), calendar-driven gatherings (S4; S8 at district scale), demotion ladder under a monthly cap. **Accept:** 90 continuous sim-days under $150; job-loss arc shows staged consequences (debt events, fee-miss, tension scenes) across ≥ 60 sim-days; ≥ 3 court hearings with ≥ 1 adjournment appear on schedule.
- **M5 — Scale + election + polish.** Full-Pune ingestion behind unchanged interfaces, attention-driven LOD citywide, S7 ward election as calendar process, viewer polish (hexbin density, ticker filters, timeline scrubber over the event log). **Accept:** ≥ 500k persons at ≥ 1 sim-day/2 wall-min free-run; election produces per-ward results + shifted civic-priority facts; all M1–M4 golden tests still green.

## Key decisions

- **Event sourcing on SQLite as the single source of truth; world state and canon are deterministic projections; snapshots are an optimization only** — Gives replay, save/load, branching, auditability, and canon-consistency checking from one mechanism; single-writer conductor matches SQLite's model; rebuildable projections mean no drift between state and history
  - Rejected: Mutable world-state save files with periodic dumps — loses causality, makes what-if branching and replay tests nearly impossible, and canon can silently diverge from history
- **Recorded nondeterminism: every LLM response is committed as an input-event; replay folds recorded events and never re-calls a model** — Makes the whole sim bit-deterministic given (seed, user injects, recorded LLM events), enables cassette-based golden tests and zero-cost replays, and cleanly separates the deterministic clockwork guarantee from the stochastic LLM layer
  - Rejected: Hoping temperature-0 API calls are reproducible — they are not across provider updates, and re-calling on replay costs money and breaks golden tests
- **Counter-based RNG (numpy Philox) with stateless draws keyed by (run_seed, domain, entity_id, tick, draw_index)** — Injecting events or materializing new entities never shifts unrelated entities' random draws, so branch comparisons isolate true causal consequences; no RNG state in snapshots
  - Rejected: Sequential per-stream generators — any change in draw order cascades noise through the whole city, poisoning branch diffs and determinism tests
- **Interviews run in a paused-time bubble as ordinary T3 Scenes, with 'journalist' (memory-forming) and '--ghost' (no-trace) modes** — Keeps the user's presence inside the general scene/memory mechanism instead of a special channel; the diegetic choice is an explicit knob rather than an accident
  - Rejected: Real-time interviews while the city keeps ticking — forces the user to race the clock and creates concurrent-scene paradoxes for the focal person
- **Own mesoscopic congestion model rendered in MapLibre as the primary view; SUMO-GUI only as an optional on-demand 'lens' for an escalated bounding box** — One coherent UI with agents, fields, and ticker; mesoscopic is cheap enough for citywide 5-min ticks; SUMO adds a heavyweight dependency that earns its keep only for hot-zone microsimulation
  - Rejected: SUMO as the primary traffic engine and window — couples the whole sim's pace to a microsimulator, splits the UI across two windows, and is painful to keep deterministic alongside the event log
- **Static PMTiles basemap + vanilla JS MapLibre client served by the in-process FastAPI app** — Single file, offline, no tile server, no frontend build step — right-sized for a solo developer on Windows; websocket frames carry all dynamic data
  - Rejected: tileserver-gl or hosted tiles plus a React build — more moving parts and an online dependency for zero functional gain
- **Free-text injection compiles via LLM structured output into the general Event schema, then a deterministic ground/validate/preview pipeline with participant synthesis via world.materialize** — Any user-described situation becomes a first-class event without new code; grounding against OSM/canon and a preview diff keep the LLM from silently corrupting the world
  - Rejected: A library of hand-written inject templates per scenario type — exactly the special-casing the design value forbids, and it can never cover unseen situations
- **Single-process asyncio conductor with a semaphore-bounded scene pool; concurrency only for LLM latency and the websocket** — Deterministic commit ordering is trivial (sort scene results by trigger seq), debugging is sane, and Old City throughput needs no parallelism; overlapping LLM batches with cheap night/morning ticks hides latency
  - Rejected: Actor/multiprocess architecture (e.g. Ray) — nondeterministic interleaving would have to be re-serialized anyway, and it complicates Windows deployment for a solo dev
- **AttentionField owned by orchestration is the single LOD/tier dial: score = f(user focus, recent perturbation, arc activity, event proximity); tier assignment is a pure function of score and budget rung** — One mechanism decides where detail and money go for ALL scenarios; the budget governor demotes tiers globally without touching content logic
  - Rejected: Per-subsystem or per-scenario tier rules — duplicated policy that drifts, and no single throttle point for cost control
- **Canon implemented as a rebuildable projection of FactEstablished/Superseded events rather than an independently mutable database** — Branch-aware canon falls out of the event log's branch semantics for free; consistency is checkable by folding; corruption is always repairable by re-projection
  - Rejected: Standalone canon DB written directly by scenes — needs its own branching, can contradict the log, and conflicts are discovered only at read time

## Interfaces

- **Events (schema & taxonomy)**: Orchestration consumes the registry: EventModel = events.taxonomy.model_for(type) for payload validation; appends via its own commit(); reads schedules via event_log.due(tick) -> list[Event]; publishes via bus.publish_all(list[Event]) -> list[Event] (derived events + SceneTrigger objects returned by subscribers). Events subsystem never writes the log directly.
- **Clockwork (movement/traffic/transit/hazards)**: clockwork.tick(tick:int) -> list[Event] (deterministic); clockwork.apply(event) for state-affecting events (road closure, flood field); clockwork.viewer_slice(bbox, zoom) -> (agents, segments, fields) for frame building; hazards.sample(tick) -> list[Event] using orchestration-provided keyed_rng(domain, entity, tick).
- **Minds (cognition tiers)**: await minds.plan_household(hh_id, date, context_card) -> PlanMade event; await minds.run_scene(SceneRequest{trigger_seq, kind, participants, tier, location, sim_time, retrieval_set}) -> SceneResult{events:list[Event], transcript}; await minds.reflect_batch(date) -> list[Event]. T3 interactive scenes yield an async turn iterator the conductor drives with user input.
- **Canon**: canon.project(event) called by orchestration's fold for FactEstablished/FactSuperseded; canon.get(entity_id, attrs) and canon.search(text|filters) for grounding and detail panels; canon.readonly_conn() for the NL-query engine (mode=ro enforced).
- **World / lazy generation**: world.materialize(selector|role_constraints, detail_level) -> list[entity_id] (emits FactEstablished events, seeded from ward stats via keyed RNG) — used by the inject compiler to synthesize participants; world.geocode(location_spec) -> osm_ref; world.calendar.due(date) -> list[Event] for festivals/elections/hearings.
- **LLM layer**: await llm.call(tier, prompt_parts, schema, cassette_key) -> (parsed:BaseModel, usage:Usage); llm.mode in {live, record, replay} from PUNESIM_LLM; usage flows into budget.record(usage) and event wall_meta; recorder exposes cassette store keyed by prompt_hash for golden tests.
- **Map viewer client (browser)**: WS server->client: Frame JSON {tick, sim_time, mode, agents[], segments[], fields[], ticker[]} at <=2 Hz, viewport-culled; WS client->server: {cmd, args} identical to console grammar plus {viewport: bbox, zoom}; HTTP: GET /static/*, GET /entity/{id} -> canon card + recent timeline.
- **Testing/CI harness**: punesim replay --scenario S1 --assert-hash <h>; punesim project --branch B (rebuild canon); punesim run --resume runs/<name>; determinism hash exposed via stats hash; cost ledger exposed via budget --json.

## Scenario traces

## S1 — School bus collision (acute physical)
User types `inject "a truck rear-ends a school bus carrying a father and daughter at 8:10am near Shivajinagar"`. The **compiler** produces an InjectDraft (type=PHYSICAL_INCIDENT, magnitude, location_spec "Shivajinagar"); **grounding** geocodes to an OSM junction, resolves the bus from clockwork's actual 08:10 transit state (or a school-run vehicle itinerary), and synthesizes the truck driver via `world.materialize(role: commercial driver)` — emitting his FactEstablished events. Preview shown; user confirms; event committed with provenance=user at tick 98. The **bus publishes** it: clockwork applies a lane blockage (congestion field rises on the map within 2 frames), the institutions subscriber schedules AmbulanceDispatched→HospitalAdmission(Sassoon) and FIRRegistered as future events via event_log scheduling, the school subscriber emits absence events. **AttentionField** spikes for all participants → scene_scheduler queues T2 scenes (mother gets the phone call; classmates gossip), resolved in the semaphore pool and committed in trigger order. If the user `follow`s the father, his hospital scene runs at T3 interactively. Everything used: compiler, grounding/synthesis, bus, scheduled events, attention, scene pool — zero S1-specific code.

## S3 — 48-hour cloudburst (area-ambient field)
Injected (or calendar/weather-driven) as an ENVIRONMENTAL_FIELD event whose payload is a scalar field spec (flood_depth grid near the Mutha, ramp over 48h). Clockwork.apply degrades affected road segments and home cells each tick — a pure field-consumes-field mechanism, same code path as congestion. The viewer renders flood_depth through the generic `fields` channel with no new client code. Failed commutes surface as itinerary re-plans (clockwork) and, where attention is high, T1/T2 scenes ("do we send the kids to school?"). PMC-complaint and disease-worry events come from institution and gossip subscribers reacting to sustained field exposure — threshold rules on the same event stream. Branching shines here: `branch dry-run at evt:<pre-storm>` then `diff` isolates exactly the flood-caused event set, because Philox keying keeps all other dice identical.

## S5 — Job loss spiral (slow personal arc)
An EmploymentEnded event (from an economic hazard draw, a T2 scene outcome, or user inject) raises the person's arc_activity term in the AttentionField, which keeps his household in the T1 morning-planning cohort for months at a few cheap calls per sim-day. The arc itself lives in the Minds subsystem; orchestration's role is that nightly `reflect_batch` returns arc-progression proposals as ordinary scheduled events (DebtPaymentMissed, SchoolFeeUnpaid, TenseDinnerScene trigger), which the loop fires on their due ticks like any hearing or wedding. The **budget governor** matters here: dozens of concurrent slow arcs stay affordable because tier assignment is centrally demotable; if the month's spend runs hot, this household's scenes resolve as templated outcomes until attention (or the user's `follow`) re-promotes them. Recovery vs spiral is decided by scene outcomes + keyed draws — replayable, and forkable at any decision event to explore the counterfactual.

## S6 — Truck driver's court case (institutional long-horizon)
The FIR from S1 causes the court institution to schedule HearingScheduled events into the event log months ahead — the log's `due(tick)` mechanism is the docket. Each hearing tick, the institution subscriber either advances the case or emits Adjourned + a rescheduled hearing (keyed RNG draw against real adjournment base rates); no LLM needed unless attention is high (user following the driver → T2/T3 courtroom scene with retrieval of the full case canon). Three years of hearings cost near zero: it is just scheduled events firing in FREE_RUN, with snapshots letting the user `advance until event type=HearingScheduled` and jump the boring months. The determinism hash and cassettes make this a golden long-horizon replay test.

## S8 — Ganeshotsav (mass event)
`world.calendar.due()` emits the festival's umbrella event, which expands (via the institutions/events subsystem) into scheduled sub-events: mandal pandal setups (SCHEDULED_GATHERING at real Old City locations), road closures (same clockwork.apply path as S1's blockage), police bandobast (institutional staffing events), procession crowd fields (same scalar-field channel as S3's flood — rendered as moving density on the map), commerce demand modifiers. Households' morning planning simply sees the festival context card and template weights shift (visits, shopping) — T0 for most, T1 where attention is high. The viewer's hexbin aggregation absorbs the crowd inflow without per-agent rendering. S4 (wedding) is literally the same machinery at household scale: SCHEDULED_GATHERING + lane-blocking pandal + invitation events through the social graph; no festival- or wedding-specific orchestration code exists.

## Generality argument

The subsystem never branches on scenario semantics. It manipulates exactly six abstractions — Event (typed, scheduled, causal), Field (scalar-over-geography), Scene (LLM-resolved interaction with a tier), Intent/Plan (clockwork-executable itinerary), Arc (attention-sustaining event generator owned by Minds), and Attention (scalar per entity) — and every probe scenario is a configuration of these, not a code path. Three mechanisms carry the generality load. (1) The inject compiler maps arbitrary free text onto the event taxonomy with LLM structured output, then grounds it deterministically (geocoding, canon search, participant synthesis via world.materialize), so an unforeseen situation — a gas leak, a lottery win, a bandh, a celebrity visit — becomes a valid committed event with zero new code; validation warns on implausibility but does not enumerate allowed situations. (2) The AttentionField plus budget-rung tier function is the single answer to "where does detail go": any perturbation anywhere raises attention, which upgrades cognition tiers and materialization depth locally, so acute accidents, decade-long court cases, and citywide festivals all get proportionate fidelity from one dial. (3) Event-sourcing with counter-based RNG makes consequences composable and inspectable for any event type: subscribers react to types they understand and ignore the rest, scheduled follow-ups implement any long horizon (a hearing in 2029 and a wedding next week are the same mechanism), and branch/diff isolates the causal cone of any injection. The viewer is general for the same reason: agents, segments, fields, ticker — a flood, a rumor's penetration, a procession crowd, and rush-hour congestion are all just fields. Where a scenario seems to need special handling (ambulances, FIRs, adjournments), that knowledge lives in institution subscribers inside other subsystems — orchestration only guarantees that events fire, scenes resolve, money is metered, and history is replayable, which is scenario-invariant by construction.

## Open questions

- Branch merge semantics: branches fork cleanly, but is merging ever needed (e.g. cherry-picking a what-if outcome back to mainline), or do we declare branches terminal exploration only? Current design assumes terminal.
- Interview time-bubble edge case: if a T2 batch for the same tick is mid-flight when the user pauses to interview a participant of one of those scenes, do we await the batch (interviewee may 'already know' the scene outcome) or abort/requeue it? Proposed: await batch, stamp interview after — needs playtesting for perceived causality.
- Canon projection scale: fact_projection per branch is fine at Old City size, but at 3.5M people with many branches the copy-on-write cache policy (what to materialize vs re-fold) needs measurement — possibly LRU per-branch overlay tables.
- Cassette staleness economics: golden scenario tests break whenever a prompt template changes (prompt-hash miss). Is a normalized 'semantic prompt hash' (ignoring whitespace/version fields) worth the added fragility, or do we accept re-record commits as routine?
- T1 household batching granularity at full scale: one call per household per morning is ~12k calls/day if attention is broad; the demotion ladder caps cost, but should T1 support multi-household batched calls (N households per prompt) as a standard rung rather than an emergency measure?
- Viewer throughput ceiling: 2 Hz frames with viewport culling is fine for Old City; at full Pune with wide zoom, hexbin aggregation happens server-side per frame — need a benchmark to decide between per-tick precomputed aggregates vs on-demand.
- How much institutional behavior (ambulance dispatch, FIR, adjournments) is clockwork rules vs Minds scenes is owned by other subsystems, but orchestration's cost ceilings depend on that split — need an agreed budget envelope per institution subscriber.
- Windows long-run robustness: multi-week FREE_RUN sessions on a desktop (sleep/hibernate, antivirus locking SQLite) — do we need a watchdog + auto-checkpoint-on-anomaly beyond the daily snapshot?
- Does the god console need scripting (e.g. Lua/Python snippets scheduling conditional injects like 'if X happens, inject Y') for scenario authoring, or is the inject + advance-until grammar sufficient through M5?

## Red-team critique (verdict: needs_changes)

- **[critical]** The main tick loop awaits the scene pool inline (`results = await self.scene_pool.run(triggers)` before `commit`), so tick advancement is coupled to LLM wall latency. Any tick with even one T2 scene takes 2–10s; a busy evening tick with 20 scenes behind a semaphore takes tens of seconds. The M5 acceptance target (500k persons at 1 sim-day per 2 wall-min = 2.4 ticks/sec free-run) is arithmetically unachievable whenever any scene fires. The 'latency hiding' claim only covers Phase R/P, not Phase T scenes.
  - Fix: Decouple scene execution from tick advancement the same way Phase P plans are decoupled: launch scene calls async, commit `SceneResolved` at the completion tick with a narrative timestamp equal to the trigger tick, and record the completion tick in the event so replay folds at the same tick. Keep only T3 focal/interactive scenes blocking (that is the point of FOCUS mode). Add a max-outstanding-scenes backpressure knob instead of blocking the whole city.
- **[critical]** No simulation stratum below 'materialized per-agent'. Per-agent Python/numpy clockwork ticks, per-(entity,tick) Philox hazard draws, and O(N) attention decay cannot reach 3.5M people: 500k agents at the M5 pace already requires ~1µs/agent fully vectorized; 3.5M is 7x that, plus ~1e9 hazard draws/day and multi-hundred-MB daily clockwork snapshots. Hexbin aggregation exists only in the viewer, not in the sim substrate. The 50k→3.5M path is asserted, not designed.
  - Fix: Introduce an explicit two-stratum population: materialized agents (current design) and statistical cohorts (ward x activity x mode OD flows as numpy arrays) that clockwork advances in aggregate. world.materialize is the promotion boundary (already exists); add the demotion path (agent folds back into cohort with facts persisted to canon). Congestion, transit load, hazard incidence, and viewer hexbins read the cohort layer directly. Name this in M5's acceptance criteria so it cannot be deferred silently.
- **[critical]** There is no deterministic behavioral-adaptation layer for T0 agents. Template plans are fixed at synthesis time; the only mechanism that changes behavior is attention buying LLM tiers. Any scenario whose realism lives in diffuse, cheap, mass adaptation — metro opening shifting commutes, price competition shifting shoppers, a new flyover, a fare hike — silently produces NO behavior change while all tests pass. This is the overfitting hole: all eight probes concentrate realism in high-attention foci (accident, arc, gathering) or pure fields (flood, rumor), so the gap never surfaced.
  - Fix: Add a clockwork-owned deterministic choice layer as a first-class abstraction alongside Event/Field/Scene/Plan/Arc/Attention: discrete-choice models (mode choice, destination choice) parameterized by canon facts and fields, drawn with keyed Philox per (entity-or-cohort, decision, week), with adoption dynamics driven by an awareness field reusing the existing gossip/scalar-field channel. Re-evaluation triggers on NetworkDelta/PriceChanged-class events. Zero LLM calls; fully replayable.
- **[critical]** Quantitative facts have no ground truth and no write protection: cheap-model T2 scenes can emit FactEstablished for sales figures, rents, salaries, casualty counts, and later scenes can supersede them with contradictory numbers. The canon validator is structural only (supersedes links), so the log stays 'valid' while the economics and numbers drift into self-contradictory noise over long runs — the core slop vector for the price war, S5's debts, and any commerce.
  - Fix: Provenance-tiered canon: register quantitative attributes (prices, sales, wages, casualty counts, dates) as clockwork-writable only; deterministic models emit them (e.g., DailySales from a demand model), scenes receive them read-only in retrieval, and the validator rejects any llm-provenance supersession of a clockwork-provenance fact. Scenes decide intent and strategy; clockwork computes consequences.
- **[major]** data/ anchors (pune.pmtiles, GTFS, osm_index, rates) are unversioned implicit inputs to determinism, and infrastructure cannot change through the event log at all — the only way to add a metro line is to rebuild data/, which silently invalidates every golden hash and every branch, and makes 'replay is bit-exact' false across data changes.
  - Fix: Stamp an anchor manifest (content hashes of every data/ file) into runs/<run>/config.toml and mix it into the determinism hash so drift fails loudly. Add a NetworkDelta event type whose payload fully describes a service/topology change (stops, edges, headways, effective date) so in-run infrastructure change is event-sourced and clockwork.apply owns graph mutation + route-cache invalidation.
- **[major]** AttentionField has positive feedback with no damping: scenes emit events, which raise recent_perturbation/event_proximity for participants, which schedules more scenes. Two coupled arc-active entities (price war, feuding families) ping-pong scene triggers indefinitely; a mass-attention incident (collapse, riot) spikes hundreds of entities at once. The budget governor caps spend only by degrading everything to templates — cost blowup or slop, pick one.
  - Fix: Discount the attention contribution of llm-provenance events (e.g., 0.3x weight), add per-entity and per-arc scene cooldowns (min ticks between beats), and give the scene scheduler an explicit per-incident scene cap per tick. Make cadence a config surface of the Arc contract, not an emergent property of decay constants.
- **[major]** The demotion ladder can resolve emotionally or narratively significant scenes as SceneResolvedTemplated — a child mauled by a dog, a family confronting an inter-religious marriage — which is exactly where templated output is either generic slop or offensive caricature. Separately, the cheap DeepSeek-class workhorse will refuse or caricature communal/religious-conflict scenes, and the pipeline's only failure path is 'one pydantic repair round' then undefined behavior.
  - Fix: Add a significance floor: event types/tags (death, violence to a child, communal tension, grief, marriage conflict) are non-demotable — under budget pressure they DEFER (queued until a budget window; clockwork consequences proceed meanwhile) rather than degrade. Add a sensitivity router: flagged scene kinds pin to the premium model with curated cultural prompt cards (real Pune texture, no stereotype shortcuts), and define the refusal path explicitly (reframe-and-retry once, then escalate tier, then defer — never silently drop or template).
- **[major]** In live mode, events whose commit tick depends on wall latency (PlanRevised hot-swaps, async scene completions, reflect_batch results) land at different ticks run-to-run. Replay is still exact, but the flagship branch/diff instrument compares two LIVE branches — so `diff` reports latency jitter as causal difference, polluting exactly the 'watch consequences propagate' use case the Philox design was built to protect.
  - Fix: Deterministic stamping: async results always commit at trigger_tick + fixed_k (k per tier, generous enough to cover p99 latency; pad with await if early, degrade to template only if truly late and log it). Alternatively/additionally, make `diff` operate on caused_by cones rooted at the fork-divergent events rather than raw event-set anti-joins.
- **[major]** 'What is an event' is undefined at scale. If boardings/movements/arrivals are logged events, 3.5M agents produce 1e7–1e8 rows/day — GB/day of SQLite growth, a sim-year becomes unmanageable, and diff/anti-join costs explode. If they are not logged, the determinism hash covers almost none of clockwork and viewer positions come from unlogged state — the design never says which. Snapshot sizing (msgpack of 3.5M-agent state daily) and retention are similarly unspecified.
  - Fix: Write the logging-tier policy into the Events contract: the log records state-changing and notable events only; bulk movement is deterministic recompute from (snapshot, seed) and is covered by a separate per-day clockwork state hash included in `stats hash`. Add snapshot retention (daily for trailing week, weekly beyond) and delta snapshots for large clockwork arrays.
- **[major]** Canon drift over long runs: T2/T3 transcripts invent details (a cousin's name, a shop's founding year) that never become FactEstablished, so later scenes re-invent them differently — the classic long-run contradiction engine. The structural validator checks retrieval-set ids only; the LLM-judge audit is 'manual pre-release', which will not hold across 90-day continuous runs.
  - Fix: Make fact extraction part of every scene call's output schema (new_facts[] alongside events/transcript) so inventions are either committed as canon or stripped from the transcript before storage. Add a sampled contradiction sweep (LLM-judge over N random entities' fact+transcript bundles) to the 90-day soak test as a scheduled job with a drift-rate threshold, not a manual pre-release step.
- **[major]** Inject grounding has no state reconciliation for participants. Synthesizing or selecting people at a specified place/time (families inside a collapsing wada at 2am, a student at a dusk chai stall) can contradict existing itineraries and the person-in-exactly-one-place invariant; `ground()` geocodes and materializes but never checks clockwork placement. The wada-collapse holdout hits this immediately: occupancy history must be retroactively consistent.
  - Fix: Add a placement-reconciliation step to ground(): check each participant's clockwork position at sim_time; on conflict, prefer synthesizing fresh entities whose template schedules are generated consistent with the required placement, or surface an explicit itinerary-retcon in the preview diff for user approval. Never commit an inject that violates the one-place invariant.
- **[minor]** Milestone gating depends on code this subsystem does not own: M3's acceptance (ambulance dispatch, FIR, school absences, hospital admission 'with no S1-specific code') requires institution subscribers from other subsystems to exist and behave. For a solo dev this is schedule risk hidden as an interface. M1 itself (GTFS ingest + mesoscopic traffic + PMTiles pipeline + determinism + snapshots + viewer) is realistically 2–3 months alone.
  - Fix: Ship reference/stub institution subscribers inside this repo (scripted rule tables: dispatch delay draws, FIR filing, admission) as the M3 contract fixtures, replaceable later by the real subsystems. Split M1 into M1a (movement + live map + console skeleton) and M1b (transit + hazards + determinism hash + snapshots) with separate accept gates.
- **[minor]** Determinism-hash golden tests break on every behavior-affecting clockwork code change (any reordering or rate tweak shifts the whole downstream event stream), creating a permanent re-bless treadmill that will train the developer to rubber-stamp hash updates — destroying the tests' value.
  - Fix: Scope hashes per subsystem with versioned expected values, and pair the full-run hash with semantic golden assertions (event counts by type, canon diff summaries, key milestone events present) that survive benign refactors. Reuse the cassette re-record review workflow: hash re-bless is a reviewed commit with a required justification line.
- **[minor]** AttentionField update/decay as written is O(all entities) per tick; at 3.5M that is a silent per-tick tax larger than the tick budget. Likewise, per-draw blake2s key derivation for Philox at ~1e9 draws/day makes hashing, not generation, the bottleneck.
  - Fix: Store attention sparsely (nonzero scores + last-touched tick, exponential decay applied lazily on read; entries below epsilon evicted). For RNG, precompute each entity's key words once at materialization and vectorize counter blocks per (domain, tick) across entity arrays so key hashing is amortized.
- **[minor]** Per-scene prompt size creeps over long runs as canon and memory accumulate (retrieval sets grow monotonically), so cost-per-scene rises silently month over month; the cost tests only assert per-day/per-scenario ceilings at a fixed point in time, not the growth curve.
  - Fix: Hard token budget per retrieval set (rank + truncate), memory compaction in nightly reflection with summary-replaces-detail semantics, and a 90-day soak assertion that mean tokens/scene in month 3 is within X% of month 1.

### Novel holdout-scenario traces

CHOICE OF HOLDOUTS. The design's eight probes all concentrate realism either in high-attention foci (accident, job-loss arc, court case, wedding) or in scalar fields (flood, rumor, procession crowds). Four of the six holdouts land inside that envelope: the stray-dog attack and chain-snatching are S1-clones (they degrade only via the significance-floor and non-person-actor gaps noted in issues); the wada collapse is S1+S3 plus the participant-reconciliation and rehousing gaps; the inter-religious couple is Minds-heavy and hits the sensitivity-router and shared-arc gaps. The two holdouts that this design CANNOT produce at all without new mechanism — not merely degrade on — are the METRO OPENING and the SAREE PRICE WAR, because both put the realism in diffuse, cheap, mass behavior change and in ground-truth economics, which are precisely the two things the AttentionField-buys-LLM-tiers premise does not cover. Traced below.

=== TRACE 1: NEW METRO STATION OPENS, SHIFTS COMMUTE PATTERNS ===

Step 1 — inject compile. `inject "Pune Metro's Civil Court interchange opens on March 6, commutes shift"`. The compiler must map to a taxonomy type. Nothing shown (PHYSICAL_INCIDENT, ENVIRONMENTAL_FIELD, SCHEDULED_GATHERING, employment/FIR/hearing types) fits a permanent transport-network change; the LLM will shoehorn it into SCHEDULED_GATHERING — which simulates the inauguration ribbon-cutting, not the commute shift. BREAK 1: taxonomy needs an INFRA/NetworkDelta class; the "any free text becomes a valid event with zero new code" claim fails on the first structural-change input.

Step 2 — grounding. Geocoding the station works (OSM). But the line's service pattern — headways, stop sequence, travel times, fares — is not in data/gtfs (PMPML only), and data/ is declared read-only. world.materialize synthesizes PEOPLE, not infrastructure; world.geocode returns refs, not schedules. BREAK 2: there is no lazy-gen path for infrastructure, and the only workaround (rebuild the GTFS + routing graph offline) mutates an unversioned implicit input to determinism — every golden hash and every existing branch silently invalidates (data/ is not in the hash).

Step 3 — apply. clockwork.apply must insert a new mode's nodes/edges into the rustworkx multimodal graph and invalidate route caches; the contract's examples are edge-weight changes (closure, flood), not topology+schedule insertion. Buildable, but it is new special-cased clockwork code — acceptable in itself, EXCEPT:

Step 4 — the real break. Who changes behavior? T0 template plans encode itineraries fixed at synthesis time. T1 households re-plan via LLM, but the metro's significance is 100k+ T0 commuters gradually shifting mode over weeks. Attention cannot help: event_proximity spikes near the station on opening day, buys a few inauguration scenes, decays within days — and then the sim shows NOTHING changing, forever, because no mechanism re-evaluates T0 mode choice. This is the worst failure type: no crash, all tests green, the city simply fails to respond. The generality argument's central claim — "AttentionField is the single answer to where detail goes" — is falsified: here the needed detail is zero-LLM deterministic model fidelity BELOW the attention floor (a mode-choice logit with awareness/adoption dynamics), which no subsystem owns and no abstraction in the six-item list (Event/Field/Scene/Plan/Arc/Attention) names.

Step 5 — observation instrument. Even with a choice model added, the promised instrument (branch pre-opening, `diff`) compares logged event sets; a commute-pattern shift is a distributional change (mode shares, corridor volumes over weeks). `diff`'s anti-join returns either nothing (if movements aren't logged) or millions of undifferentiated rows (if they are). Needs aggregate stat views (`diff --stat mode_share by ward`) — more unplanned code.

Verdict on trace 1: breaks at taxonomy (1), infrastructure event-sourcing vs read-only anchors (2), and fundamentally at mass T0 adaptation (4); needs special-casing at apply (3) and diff reporting (5). Fixes: NetworkDelta event type carrying full service payload; anchor-manifest hash in determinism hash; a deterministic discrete-choice layer in clockwork with awareness diffusion reusing the existing field channel; aggregate diff views.

=== TRACE 2: PRICE WAR BETWEEN TWO LAXMI ROAD SAREE SHOPS ===

Step 1 — inject compile + grounding. `inject "Two rival saree shops on Laxmi Road start a price war"`. Canon search finds no saree shops (unmaterialized); world.materialize(role: saree shop, Laxmi Road) x2 works and emits FactEstablished — good. But a "war" is a PROCESS, not an event. The InjectDraft's only process tool is followup_schedule, i.e., the compiler LLM would script the war's whole plot at inject time — canned beats, the opposite of emergence. The alternative home is an Arc, but Arcs are per-entity (arc_activity is a per-entity attention term; reflect_batch returns per-person proposals). BREAK 1: a coupled two-party adversarial arc with shared rivalry state has no representation; orchestration's minds contract has no channel for it.

Step 2 — the reaction loop. Shop A's PriceCut event commits. For B to respond, some bus subscriber must map "competitor's price event" → SceneTrigger(B). The bus routes by TYPE; nothing knows "competitor_of," and nothing established that relationship at materialization. BREAK 2: cross-entity reactive triggering requires a new market subscriber plus a canon relationship-graph convention — new code, disproving "configuration not code path."

Step 3 — ground truth. Suppose scenes fire. The T2 cheap model narrates outcomes: "sales fell 40% this week" → FactEstablished. There is no demand model (the design's entire economy is "commerce demand modifiers" for festivals), so every number is hallucinated; next week's scene retrieves and escalates or flatly contradicts it. The canon validator passes throughout — it checks supersedes links, not sense. BREAK 3: with no economic substrate, the war is two LLMs improvising at each other; `branch war / no-war` then `diff` — the design's flagship instrument — diffs fiction against fiction.

Step 4 — the deciding variable is missing. What actually settles a price war is footfall: thousands of T0 shoppers' destination choice shifting between shops. Same hole as the metro trace, from the demand side: attention cannot buy this (promoting 5,000 shoppers to T1 to decide where to buy a saree is absurd), and there is no cheap deterministic alternative.

Step 5 — cost and termination. Both shopkeepers are arc-active; each scene's events raise the other's recent_perturbation → scene ping-pong with no cadence limit (attention has positive feedback on its own scene output). The budget governor's only brake demotes to SceneResolvedTemplated — "the price war continues" template slop. And nothing can END the war: a shop closing requires ground-truth insolvency, which either the LLM invents (a hallucinated, canon-heavy, irreversible fact) or never happens.

Verdict on trace 2: breaks at process/shared-arc representation (1), relationship-scoped triggering (2), economic ground truth (3), mass demand response (4), and cadence/termination (5). Fixes: make Arc a first-class shared entity (participants, state, bounded beat cadence; attention attaches to the arc, scenes are arc beats ≤1/sim-day); a minimal deterministic retail demand model (cohort footfall, logit over price/distance/loyalty, keyed Philox) emitting DailySales as clockwork-provenance facts that LLM scenes may read but never supersede; registered canon relationships (competitor_of, landlord_of, creditor_of) with subscriber hooks.

=== CONVERGENT FINDING ===
Both traces break at the same load-bearing wall: the design silently equates behavioral fidelity with LLM tier, so any scenario whose realism lives in mass cheap-agent adaptation to changed facts (network topology, prices — also fare hikes, rent shifts, a new mall, the election's actual vote-choice model) has no mechanism at all. The probe set never exposed this because all eight probes were chosen where attention-concentration works. The fix is one new core abstraction (a deterministic choice-model layer in clockwork, parameterized by canon + fields, keyed-RNG, replayable) plus provenance-tiered canon so LLM narration can never overwrite model-computed quantities. With those two additions the six holdouts — including the four not traced — reduce to configurations again; without them, four of six need bespoke code and two produce silent non-responses.