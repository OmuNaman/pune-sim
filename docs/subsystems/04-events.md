# 4. Events & cascades

## Summary

The Events & Cascades subsystem models every happening in Pune Sim with three primitives: immutable Events (a causal DAG of happenings), stateful Conditions (persistent consequences attached to entities, each with a staged lifecycle and a wake-up timer), and Processes (data-driven state machines for anything longer than a scene: court cases, elections, weddings, festivals, personal arcs). Event types are not code — they are rows in a YAML ClassDef registry declaring shape (acute/ambient/informational/scheduled-social/process-step/mass-gathering/milestone), payload schema, hazard-rate formula, effect templates, visibility profile, and adjudication tier, so any new life situation is a registry entry, not an engine change. Generation is unified behind one emit-and-commit pipeline fed by five sources: NCRB-calibrated thinned-Poisson hazard sampling, the real calendar, agent decisions, process/condition timers, and user injection. Propagation deliberately uses BOTH a causal graph (for canon, explanation, and cascade budget accounting) and scoped pub/sub (geo cells, routes, social edges, org rosters, media channels) with lazy two-tier fanout: analytic diffusion curves per scope, materialized into per-person Percepts only on observation via deterministic seeded RNG, so a city-wide event costs O(1) writes but every person gives a consistent answer forever. When rules cannot resolve an outcome, a strict LLM adjudication contract sends a compact context capsule plus an allowed-effects menu and receives a schema-validated WorldDelta (conditions, child events, relationship deltas, memory writes, process ops) that is clamped and canon-checked before commit. Cascades are governed by a severity budget with depth-multiplied costs, attention-based refill, and an escalation-check gate for genuinely severe children; a persistent timer wheel guarantees cold-but-alive threads (a hearing in 6 weeks, a debt accruing monthly) never die from attention decay. Storage is SQLite+JSON1 with pydantic v2 schemas; all randomness is hierarchically seeded for replayable canon.

## Design

# Events & Cascades — Detailed Design

## 0. Design stance

One sentence: **an Event is a fact, a Condition is a state, a Process is a machine, a Percept is knowledge, and everything else is registry data.** The engine knows nothing about buses, weddings, or courts; it knows shapes, budgets, timers, scopes, and deltas. All domain knowledge lives in a declarative registry (`registry/*.yaml`) so coverage grows by adding data, never by adding engine branches.

## 1. Primitives and schemas (SQLite, WAL mode, JSON1; pydantic v2 models mirror every table)

### 1.1 `event` — immutable happening (the causal DAG)

```sql
CREATE TABLE event (
  event_id       TEXT PRIMARY KEY,      -- ULID (python-ulid); sortable by time
  sim_time       INTEGER NOT NULL,      -- minutes since world epoch
  duration_min   INTEGER NOT NULL DEFAULT 0,   -- 0 = instantaneous
  class          TEXT NOT NULL,         -- dotted id, e.g. 'hazard.road.collision'
  shape          TEXT NOT NULL,         -- denormalized from ClassDef: acute|ambient|informational|scheduled_social|process_step|mass_gathering|milestone
  status         TEXT NOT NULL,         -- 'scheduled'|'occurred'|'cancelled'  (scheduled events are canon and perceivable in advance: invitations, warnings)
  location_kind  TEXT,                  -- 'point'|'edge'|'area'|'venue'|'org'|'virtual'
  location_ref   TEXT,                  -- OSM way/node id, H3 cell, GeoJSON blob id, venue_id, channel_id; NULL for pure informational
  severity       REAL NOT NULL,         -- 0..1 normalized WITHIN class (class rubric in registry); cross-class comparison uses impulse (1.4)
  visibility     TEXT NOT NULL,         -- JSON VisibilityProfile (1.5)
  participants   TEXT NOT NULL,         -- JSON [{"entity_id":..., "role":...}]; open role vocab, core set: agent, patient, witness, instrument, authority, beneficiary, organizer, invitee
  cause_event_id TEXT REFERENCES event, -- causal parent; NULL for roots
  cascade_id     TEXT NOT NULL,         -- root event of this cascade
  cascade_depth  INTEGER NOT NULL DEFAULT 0,
  budget_spent   REAL NOT NULL DEFAULT 0,  -- impulse debited from cascade budget by this event
  source         TEXT NOT NULL,         -- 'hazard'|'calendar'|'agent'|'process'|'condition'|'adjudicator'|'user'
  payload        TEXT NOT NULL,         -- class-specific JSON, validated against ClassDef.payload_schema
  narration      TEXT,                  -- 1-3 canon sentences (from rules template or adjudicator)
  adjudication_id TEXT                  -- FK to adjudication log if LLM-resolved
);
CREATE INDEX ev_time ON event(sim_time); CREATE INDEX ev_cascade ON event(cascade_id);
CREATE INDEX ev_loc ON event(location_kind, location_ref); CREATE INDEX ev_class ON event(class, sim_time);
```

Events are append-only. Corrections are new events (`meta.retcon` is not allowed; the canon rule is "LLMs retrieve but never contradict", and the engine holds the same rule for itself).

### 1.2 `condition` — persistent consequence with lifecycle

```sql
CREATE TABLE condition (
  condition_id    TEXT PRIMARY KEY,
  entity_id       TEXT NOT NULL,     -- person | household | vehicle | edge/lane | venue | org | relationship(a,b) | area(h3 set)
  entity_type     TEXT NOT NULL,
  kind            TEXT NOT NULL,     -- registry-defined: 'injury','road_block','vehicle_disabled','flooded','debt','unemployed','grief','reputation_hit','disease_risk','displaced','case_pending','fee_arrears', ...
  source_event_id TEXT NOT NULL REFERENCES event,
  started_at      INTEGER NOT NULL,
  expected_end    INTEGER,           -- NULL = indefinite (resolved only by events/reviews)
  stage           TEXT NOT NULL,     -- kind-specific: injury: 'er'|'admitted'|'home_rest'|'recovered'; road_block: 'active'|'clearing'|'cleared'
  intensity       REAL NOT NULL,     -- 0..1, may decay per spec
  decay_spec      TEXT,              -- JSON: {"model":"stage_schedule"|"half_life","half_life_days":...,"stages":[{"stage":...,"days":...,"effects":{...}}]}
  effects         TEXT NOT NULL,     -- JSON stat modifiers consumed by other subsystems, e.g. {"mobility":-0.7,"income_factor":0.0,"mood":-0.3,"edge_capacity_factor":0.2,"school_attendance":false}
  next_review     INTEGER,           -- sim_time timer; NULL = passive until externally resolved
  status          TEXT NOT NULL      -- 'active'|'resolved'|'superseded'
);
CREATE INDEX cond_entity ON condition(entity_id, status); CREATE INDEX cond_review ON condition(next_review) WHERE status='active';
```

`effects` is the ONLY channel by which events change ongoing world behavior: the mobility layer reads `edge_capacity_factor`, the cognition layer reads `mood`/`mobility`/constraints at planning time, the economy layer reads `income_factor`. This keeps consequence persistence subsystem-agnostic: "damaged bus out of service" = condition(kind=vehicle_disabled, effects={in_service:false}, next_review=+9 days at workshop capacity); "lane blocked by pandal" = condition on the OSM way with `edge_capacity_factor:0.3` and `expected_end` = festival teardown; "injury" = staged schedule from a KSI severity table.

### 1.3 `process` — durable state machine for anything longer than a scene

```sql
CREATE TABLE process (
  process_id   TEXT PRIMARY KEY,
  kind         TEXT NOT NULL,        -- 'court_case','ward_election','festival','wedding','pmc_complaint','insurance_claim','arc.job_loss','arc.illness','construction', ...
  template_id  TEXT NOT NULL,        -- registry state-machine definition
  state        TEXT NOT NULL,        -- current node name
  owner_org    TEXT,                 -- institution or household that runs the clock (court, PMC ward office, election commission, family)
  parties      TEXT NOT NULL,        -- JSON [{"entity_id","role"}] e.g. accused, complainant, advocate, candidate, bride's household
  vars         TEXT NOT NULL,        -- JSON process variables: {"sections":["BNS 281","BNS 125(a)"],"claim_amount":...,"hearings_held":4,"months_unemployed":3}
  next_wake    INTEGER,              -- timer-wheel entry
  priority     REAL NOT NULL DEFAULT 0,   -- attention score; affects fidelity, not liveness
  source_event_id TEXT, status TEXT NOT NULL
);
```

Process template (registry YAML) example, court case:

```yaml
process.court_case.motor_accident:
  states: [fir_registered, chargesheet, cognizance, hearings, arguments, judgment, appeal_window, closed]
  wake_policy: {hearings: {interval_days: [35, 70], calendar: court_working_days, capacity_queue: "org:shivajinagar_court:causelist"}}
  transitions:
    hearings: {sample: {adjourned: 0.55, evidence_recorded: 0.30, arguments_heard: 0.15}, emit: institution.court.hearing}
    judgment: {resolution_tier: R1, allowed_outcomes: [convicted, acquitted, compounded], rubric: "BNS sections, evidence strength var, precedent-plausible"}
  pendency_calibration: {median_years: 4.2, source: "NJDG district-court pendency, Pune district"}
```

State transitions emit `process_step` events through the normal pipeline, so a hearing is perceivable, gossipable, and causally linked to the crash three years earlier via `cascade_id` lineage.

### 1.4 Cascade ledger

```sql
CREATE TABLE cascade (
  cascade_id TEXT PRIMARY KEY,       -- = root event_id
  impulse    REAL NOT NULL,          -- initial consequence budget f(class, severity), registry-defined per class: minor collision 3, fatal crash 12, cloudburst 80 (area-distributed), festival: process-owned sub-budgets
  spent      REAL NOT NULL DEFAULT 0,
  attention  REAL NOT NULL DEFAULT 0 -- user attention level 0..3; refills effective budget (1.8)
);
```

### 1.5 VisibilityProfile (JSON on event)

```json
{"mode":"public_scene",              // public_scene | private_scene | secret | broadcast | official_record
 "sensory_radius_m":200,             // for acute events: witnesses inside radius at t
 "channels":["street","org:school_x","whatsapp","news_local","official:police"],
 "secrecy":0.0,                      // 0 open .. 1 actively concealed (suppresses fanout rate, raises gossip value)
 "newsworthiness_tau_days":1.5}      // class-default decay constant, overridable
```

### 1.6 `percept` — a mind learning of an event (delivery product of propagation)

```sql
CREATE TABLE percept (
  percept_id TEXT PRIMARY KEY, person_id TEXT NOT NULL, event_id TEXT NOT NULL,
  learned_at INTEGER NOT NULL,
  channel    TEXT NOT NULL,          -- 'witness'|'participant'|'told:{person_id}'|'whatsapp:{group_id}'|'news:{outlet}'|'official:{org}'|'inferred'
  fidelity   REAL NOT NULL,          -- 1.0 participant/witness; degrades per hop (gossip subsystem owns content mutation; I own routing + fidelity bookkeeping)
  claim_version_id TEXT,             -- for informational events: which mutation of the claim (rumor version chain, see S2)
  salience   REAL NOT NULL           -- initial care-level: f(involvement, relationship to participants, geo proximity, topical interest); memory subsystem decays it thereafter
);
CREATE UNIQUE INDEX per_pe ON percept(person_id, event_id);
```

### 1.7 Timer wheel (crash-safe future queue)

```sql
CREATE TABLE timer (timer_id TEXT PRIMARY KEY, fire_at INTEGER NOT NULL, kind TEXT NOT NULL, -- 'condition_review'|'process_wake'|'scheduled_event'|'reach_checkpoint'
                    target_id TEXT NOT NULL, status TEXT NOT NULL);
CREATE INDEX t_fire ON timer(fire_at) WHERE status='pending';
```
In-memory `heapq` mirror rebuilt from table on boot.

### 1.8 `reach` — aggregate information diffusion (lazy fanout tier A)

```sql
CREATE TABLE reach (event_id TEXT, scope TEXT,   -- 'geo:h3:{cell}'|'org:{id}'|'channel:{id}'|'ward:{n}'
                    model TEXT,                  -- JSON logistic params {r0, k_per_day, cap_fraction, start_t}
                    PRIMARY KEY(event_id, scope));
```

## 2. Event-class system (the generality mechanism)

A **ClassDef** registry entry fully defines an event type:

```yaml
hazard.road.collision:
  shape: acute
  payload_schema: {vehicles:[{type,vehicle_id}], injuries:[{person_id, ksi: killed|serious|slight|none}], mechanism: str}
  participant_roles: {required:[agent, patient], optional:[witness, instrument, authority]}
  hazard: {exposure_unit: vehicle_km, base_rate: 2.1e-7,      # calibrated: ~1300 injury accidents/yr Pune city / modeled VKT; minor non-reported x8
           modifiers: [tod_curve: night_x1.8, rain_x1.6, festival_crowd_x1.3, junction_x2.2]}
  severity_rubric: "0.1 scrape .. 0.5 injury+ambulance .. 1.0 multi-fatality"
  impulse: "3 + 9*severity"
  visibility_default: {mode: public_scene, sensory_radius_m: 150, channels:[street, news_local], newsworthiness_tau_days: 1.0}
  effect_templates:                                            # R0 rule consequences
    - {if: "any ksi>=slight", condition: {kind: injury, entity: patient, stage_schedule: ksi_table_pune}}
    - {condition: {kind: road_block, entity: edge, intensity: "0.3+0.6*severity", stages: [{active: "10+90*severity min"}]}}
    - {if: "ksi>=serious or hit_and_run", process: {kind: police_case, template: process.fir.bns}}
  resolution: {default: R0, escalate_R1_if: ["participants include focal person", "dispute_at_scene sampled", "severity>=0.7"]}
  adjudication_menu: [injury, road_block, vehicle_disabled, reputation_hit]   # allowed_effects for LLM
```

The seven **shapes** give the engine everything it needs mechanically:

| shape | location | duration | physical affect query | typical fanout |
|---|---|---|---|---|
| acute | point/edge | minutes | `who_is_at(loc,t)` | sensory + channels |
| ambient | area (H3 set/polygon) | hours-days | footprint join on homes/routes/venues | area scopes + official |
| informational | virtual (channels) | while spreading | none (percepts only) | channel diffusion |
| scheduled_social | venue | planned span | invitee roster | invitations = percepts of a `scheduled` event |
| process_step | org/venue | minutes | parties | parties + official record + news if newsworthy |
| mass_gathering | area+venues | days | crowd field modifiers on cells/edges | city-wide channels |
| milestone | person/household | instant | self/household | social-graph edges above severity threshold |

Adding "factory strike", "building collapse", "exam results day", "chain-snatching", "power outage" = new YAML entries choosing a shape and filling the slots. No probe scenario is named anywhere in engine code.

## 3. Generation — five sources, one pipeline

```python
def emit(intent: EventIntent, *, source: str, force: bool = False) -> CommitResult
```

1. **HazardSampler** (stochastic, per 15-min clockwork tick): thinned Poisson. Sample expected COUNT per class city-area-wide from aggregate rate x exposure (VKT from mobility layer, person-days, household-days), then assign locations proportional to exposure density (traffic volume per edge, footfall per cell). O(events), not O(locations). Calibration table `hazard_rate(class, base, unit, source)` sourced from NCRB Pune city tables, Pune Police annual crime review, accident KSI distributions; sim-area scaling by exposure share, not population share. Seeded stream: `PCG64(hash(world_seed,'hazard',class,day))` — replayable.
2. **CalendarScheduler**: real calendar (Ganeshotsav, Palkhi, Diwali, monsoon onset distribution, school terms, court working days) plus sampled social calendar (weddings from marriage rate x muhurat date weighting → instantiate `wedding` Process weeks ahead, which emits its own `scheduled_social` events).
3. **AgentEmitter**: cognition-layer decisions call `emit()` (quit job, file complaint, propose, start a quarrel). Engine validates against world state (plausibility gate: can't board a `vehicle_disabled` bus) and may bounce with a reason the mind can react to.
4. **Timers**: condition reviews and process wakes emit derivative events (`condition`/`process` sources) — the long-tail engine.
5. **UserInjector**: same API with `source='user', force=True` — bypasses the plausibility gate but never consistency (cannot injure the dead; conflicts are reported, not silently absorbed). User injection of a novel situation = an inline anonymous ClassDef (shape + payload + impulse) synthesized by a one-shot R1 call, then committed like any event — this is how unseen event types enter without code.

**Commit pipeline** (single transaction per event):

```python
def commit(ev):
    validate_schema(ev); check_canon_consistency(ev)          # death/uniqueness/location existence
    charge = impulse_cost(ev)                                  # severity x depth_multiplier(depth): [1, 1.5, 2.5, 4, 6...]
    if not cascade.try_debit(ev.cascade_id, charge): 
        return absorb_as_condition_or_percept(ev)              # budget exhausted -> no new event node (1.8/§6)
    tier = classdef[ev.class].resolution_tier(ev, ctx)
    delta = resolve_R0(ev) if tier==R0 else enqueue_adjudication(ev)   # R1 batched async, sim-time barrier
    apply_delta(delta)          # conditions + timers + child event intents + relationship/memory forwards
    fanout(ev)                  # §4
    write(event, cascade_ledger)
```

## 4. Propagation — hybrid causal graph + scoped pub/sub (justification)

**Why both, explicitly.** A causal graph answers *why* (lineage, blame, narrative, budget accounting) but cannot answer *who is affected* — a flood affects 4,000 households who caused nothing. Pub/sub answers *who/when/how learned* but a subscription hop is not causation — my neighbor telling me about the crash did not cause the crash. Using one structure for both conflates knowledge with causation and breaks both interviews ("why were you late?") and damping (you'd damp information spread when you meant to damp consequences). So: `cause_event_id`/`cascade_id` DAG for consequence physics; scope-based pub/sub for information and affect delivery.

**Scopes** (standing subscriptions, maintained by their owning subsystems, consumed by me):
- `geo:h3:{cell}` — H3 res-9 cells (~0.1 km²); persons subscribe via home, workplace, and commute cells (precomputed by mobility layer).
- `route:edge:{way}` — commuters on that edge in a time window.
- `social:{person}` — ego-network edges; auto-subscribed to each other's milestones above per-relationship severity thresholds.
- `org:{org}` — rosters: school→parents, employer→staff, police-station jurisdiction, ward, temple congregation, court cause-list parties.
- `channel:{id}` — WhatsApp groups, news outlets, notice boards; membership owned by information subsystem.

**Three delivery products**, resolved per event shape:
1. **Physical affect**: query mobility/spatial layer for entities in the footprint (`who_is_at(edge,t)` for acute; STRtree polygon join for ambient) → attach Conditions.
2. **Plan invalidation**: notify the clockwork scheduler of capacity/constraint changes (`edge_capacity_factor`, venue closed, person hospitalized) so T0 schedules replan cheaply without LLM.
3. **Percepts** (information): **lazy two-tier fanout**. Tier A writes only `reach` rows: per scope, a logistic diffusion curve (channel-specific rate constants: witness=instant, street-talk k≈2/day within cell, WhatsApp k≈8/day within group with forward probability from the information subsystem, news=step at publication, official=step at notice). Tier B materializes a concrete Percept only when a person is instantiated/attended: `learned = seeded_bernoulli(hash(world_seed, person_id, event_id), reach(scope_best, now))`, with `learned_at` sampled from the curve's inverse CDF — same seed, same answer, forever; once observed, persisted to canon. A city event therefore costs a handful of `reach` rows, not 50k percept rows, yet every interviewed person has a stable, consistent story of when and how they heard.

**Informational events** (S2-type) are first-class: `payload.claim = {claim_id, version, assertions[], about_entities[], truth_value}`; the gossip subsystem mutates claims and re-emits child informational events whose `cause_event_id` is the prior version — rumors get canonical mutation chains with the same causal machinery as physical cascades, and the same budget damping keeps them from spreading forever.

## 5. LLM adjudication contract

Tiers per ClassDef: **R0** rules (outcome tables, formulas — the default; most hazards fully rule-resolved), **R1** cheap-model structured call (outcome hinges on personality/relationship/context), **R2** premium focal scene (delegated to cognition T3, but MUST return the same WorldDelta).

**AdjudicationRequest** (input; static preamble + class rubric prefix-cached):
```json
{"event": {...}, "question": "resolve_outcome" | "resolve_reaction:{person_id}" | "resolve_process_step" | "synthesize_classdef",
 "world_context": {
   "participants": [{"persona_capsule": "~150 tokens", "active_conditions": [...], "relationships": [...], "memories_topk3": [...]}],
   "location_capsule": "1-2 lines", "time_weather_calendar": "...",
   "causal_chain": ["<=5 ancestors, one line each"],
   "canon_constraints": ["facts that must not be contradicted"],
   "allowed_effects": {"conditions": [{"kind":"injury","intensity":[0,0.6]}, ...], "event_classes": [...], "relationship_dims": ["trust","warmth","obligation"]},
   "remaining_budget": 4.5},
 "output_schema": "<json-schema of WorldDelta>"}
```

**WorldDelta** (output; pydantic-validated, clamped, canon-checked; one retry with error feedback, then R0 fallback table):
```json
{"narration": "1-3 sentences (becomes canon event narration)",
 "conditions": [{"entity_id","kind","intensity","stage","expected_duration_days"}],
 "emitted_events": [{"class","delay_min","participants","severity","payload"}],
 "relationship_deltas": [{"a","b","dim","delta"}],
 "memory_writes": [{"person_id","salience","summary"}],
 "process_ops": [{"op":"create|advance|cancel","kind","template_id","vars"}],
 "canon_facts": [{"subject","predicate","value"}]}
```

The `allowed_effects` menu is the safety rail: the model chooses from enumerated levers with parameter ranges; the validator rejects unknown kinds, clamps ranges, resolves entity references against canon, and debits every emitted child from the cascade budget. All adjudications are logged (`adjudication` table: request hash, response, model, tokens) → replay + audit + cost accounting. R1 calls within a tick are batched (~20 concurrent) behind a **sim-time barrier**: the tick does not advance until its adjudications commit, preserving determinism-given-logged-LLM-outputs.

## 6. Cascade dynamics — budgets, decay, anti-death

- **Budget**: root impulse per §1.4; child cost = `severity × depth_multiplier(depth)` with multipliers [1, 1.5, 2.5, 4, 6, ...] so deep chains need increasing justification. Exhausted budget → consequences are absorbed as condition-intensity adjustments or memory-only percepts, never new event nodes → combinatorial explosion is structurally impossible.
- **Escalation gate**: a child adjudicated at severity ≥ 0.8 may petition (one R1 call, rubric-scored) to open a NEW cascade with fresh impulse. This is exactly how a fender-bender usually ends in an exchange of words, but S1's serious crash legitimately spawns a court-case cascade and a family-finance cascade.
- **Attention refill**: `effective_budget = remaining × (1 + 0.5×attention)`; watched threads simulate at higher fidelity (LOD principle: user attention is fuel). Attention never *creates* events, only un-throttles fidelity — otherwise watching would distort the world.
- **Three decay clocks**: (a) physical — condition stage schedules (jam clears via traffic model, pandal lane reopens at teardown, injury heals per KSI table); (b) newsworthiness — `news_value(t) = severity × novelty × proximity × exp(-t/τ_class)`; below threshold, `reach` curves freeze (no new percept fanout; event is cold but canon-retrievable); (c) mind-salience — owned by the memory subsystem, seeded by my percept salience.
- **Anti-death guarantee**: attention decay throttles ONLY information spread and discretionary child events. Timer-wheel entries (condition `next_review`, process `next_wake`) fire regardless — a hearing in six weeks, monthly debt accrual, monsoon-end review of a damp house. Cold-but-alive is the default fate of every long thread, and reheating is free: the next process_step event re-enters normal fanout with fresh novelty.

## 7. Core loop

```python
def tick(t, dt=15):                            # minutes
    for tm in timer_wheel.due(t):              # 1. long-tail first
        fire(tm)                               #    condition reviews advance stages / emit events; process wakes transition
    for cls in hazard_classes:                 # 2. stochastic hazards
        lam = rate(cls,t)*exposure(cls,t)*dt
        for _ in rng(cls,day).poisson(lam):
            loc = sample_location(cls, exposure_density)
            emit(instantiate(cls, loc, clockwork.who_is_at(loc,t)), source='hazard')
    drain_commit_queue()                       # 3. §3 pipeline; R1 batch + sim-time barrier
    checkpoint_reach_curves(t)                 # 4. cheap: only touched scopes
```

## 8. Public API (contracts other subsystems call)

```python
events.emit(intent, *, source, force=False) -> CommitResult          # everyone
events.query(filters) -> [Event]                                      # canon retrieval, FTS5 over narration
events.explain(event_id, depth=5) -> CausalChain                      # UI + interview grounding
events.timeline(entity_id, t0, t1) -> [Event|ConditionChange|Percept] # "follow their day" / interviews
events.conditions_of(entity_id) -> [Condition]                        # cognition planning, mobility capacity
events.percepts_of(person_id, since) -> [Percept]                     # cognition; triggers Tier-B lazy materialization
events.set_attention(target, level)                                   # UI; budget refill + fidelity promotion
events.subscribe(scope, entity_id, params) / unsubscribe(...)         # mobility/social/org layers maintain scopes
events.register_classdef(yaml_blob)                                   # content authoring + user-injected novel types
```

## 9. Libraries & stack (boring, Windows-proven)

- **SQLite** (stdlib `sqlite3`, WAL) + JSON1 + FTS5 — single file, zero admin; event volume at 50k residents ≈ 40–100 notable events/day, trivially within SQLite; schema ports to Postgres unchanged if 3.5M-scale ever needs it (it likely doesn't: events stay sparse, ~5–8k/day city-wide).
- **pydantic v2** — every table row, EventIntent, WorldDelta; JSON-schema handed verbatim to LLM structured output.
- **h3** (Uber H3 python bindings) res 9 for geo scopes; **shapely** + STRtree for ambient footprints (flood polygons).
- **numpy** `Generator(PCG64)` with hierarchical seeds `hash(world_seed, stream, key, day)` — lazy determinism everywhere.
- **python-ulid** for ids; **PyYAML** for the registry; stdlib `heapq` for the timer mirror.
- LLM calls through the project-wide model gateway (DeepSeek-class for R1 with prefix caching on the static preamble + class rubrics; premium only for R2 focal scenes). Cost envelope: hazard-driven R1 ≈ 10–30 calls/day at ~1.2k in / 300 out tokens ≈ cents/day; the events subsystem is not the budget driver.

## 10. Trade-offs accepted
- Aggregate reach curves sacrifice per-message realism (no simulated individual forwards in T0) for O(1) fanout; the information subsystem re-introduces message-level realism only inside attended scenes.
- Budget constants (impulse table, depth multipliers, τ per class) are tunable priors, not truths — shipped with a replay-calibration harness (run 30 sim-days, histogram cascade sizes vs. sanity targets: median cascade 2–5 events, p99 < 40).
- SQLite single-writer is fine because the sim is single-process with a commit queue; the barrier design already serializes writes.

## Key decisions

- **Three persistent primitives — immutable Event, stateful Condition, state-machine Process — instead of a single universal event record** — Happenings, ongoing states, and long-horizon trajectories have different lifecycles, query patterns, and timers; separating them lets events stay append-only canon while consequences evolve and multi-year arcs survive attention loss via wake timers.
  - Rejected: Pure event-sourcing (everything is an event, state derived by fold): elegant but makes 'what conditions affect this person now' and 'when does this case wake next' expensive derived queries, and gives LLM adjudication no stable state objects to reference.
- **Hybrid propagation: causal DAG (cause_event_id/cascade_id) for consequence physics + scoped pub/sub with lazy fanout for information/affect delivery** — Causation and knowledge are different relations: a flood affects thousands who caused nothing, and hearing about a crash is not causing it. The DAG serves explanation, blame, narrative, and budget accounting; scopes serve who-learns-when. Conflating them breaks both interviews and damping.
  - Rejected: Pure pub/sub (no lineage: cannot answer 'why', cannot budget cascades) and pure causal graph (fanout to affected non-causes has no home).
- **Lazy two-tier percept fanout: analytic logistic reach curves per scope, materialized into individual Percepts only on observation via deterministic seeded RNG, then persisted** — A city-wide event costs a handful of reach rows instead of 50k percept writes, yet hash(world_seed, person, event) seeding makes every person's 'when/how did you hear' answer stable forever — consistent with the lazy-generation canon doctrine and required for 3.5M-scale.
  - Rejected: Eager per-person fanout: write amplification kills scaling; per-message agent-based diffusion: costs LLM/compute for unobserved background spread with no observable benefit.
- **Data-driven ClassDef registry (YAML: shape, payload schema, hazard formula, effect templates, visibility, adjudication tier, impulse) instead of event subclasses in code** — New life situations become content authoring, not engine changes; the generality requirement demands the engine be domain-blind. Also enables user-injected novel event types via LLM-synthesized inline ClassDefs.
  - Rejected: Python class hierarchy per event type: every new domain touches engine code, tempts special-casing, and cannot be extended at runtime by the adjudicator or the user.
- **Cascade severity budget with depth-multiplied costs, attention-based refill, and an R1 escalation gate for severe children to open fresh cascades** — Makes combinatorial explosion structurally impossible (exhausted budget degrades to condition tweaks/percepts, never new events) while still letting a serious crash legitimately spawn a court case and a family-finance arc; attention refill implements the LOD 'attention is fuel' principle without letting watching create events.
  - Rejected: Fixed depth cutoff (arbitrary: kills legitimate deep chains like crash→FIR→hearing→conviction→job loss) and unlimited propagation with probabilistic decay only (no hard guarantee against explosion).
- **Persistent timer wheel (condition next_review, process next_wake) that fires regardless of attention; decay throttles only information spread and discretionary children** — This is the anti-death guarantee: court hearings, debt accrual, and recovery milestones must survive months of user inattention. Separating 'cold' (no news value) from 'dead' (resolved) is what makes S5/S6-style long arcs possible at zero LLM cost.
  - Rejected: Attention-driven scheduling only: unwatched threads silently die, which is exactly the unrealistic ripple-death the spec forbids.
- **LLM adjudication constrained by an allowed_effects menu with parameter ranges, returning schema-validated WorldDelta, clamped and canon-checked, with R0 fallback after one retry** — Free-form LLM output cannot be safely applied to persistent world state; a typed delta with enumerated levers makes every mutation auditable, budget-debitable, and replayable, and lets a cheap model do the job reliably with prefix-cached rubrics.
  - Rejected: Free-text adjudication parsed heuristically (canon corruption risk) and rules-only resolution (loses personality/relationship-contingent outcomes that justify an LLM sim at all).
- **SQLite (WAL, JSON1, FTS5) as the sole store, single-process commit queue with sim-time barrier for batched R1 calls** — Boring, zero-admin, Windows-native, and sufficient: events are sparse (~10^2/day at 50k residents, ~10^4/day city-wide); the barrier preserves determinism-given-logged-LLM-outputs so any run is replayable canon. Schema ports to Postgres unchanged if ever needed.
  - Rejected: Kafka/event-store or Postgres from day one: operational overhead for a solo developer with no concurrency need; fire-and-forget async adjudication: nondeterministic interleaving destroys replayability.

## Interfaces

- **Clockwork / Mobility & Traffic**: I call who_is_at(location_ref, t) -> [entity_id] and flows_through(edge, window) -> flow stats for physical-affect resolution and exposure densities; I push Condition effects it must consume (edge_capacity_factor, vehicle in_service=false, venue closed) via conditions_of(entity); it calls events.emit() for traffic-born incidents and maintains route:edge and geo:h3 subscriptions per person via events.subscribe(scope, person, params).
- **Cognition / Minds (T0-T3)**: Minds consume events.percepts_of(person_id, since) -> [Percept] (triggers lazy Tier-B materialization) and events.conditions_of(person_id) as planning constraints/modifiers; agent decisions enter via events.emit(intent, source='agent') and may bounce with a machine-readable reason; T3 focal scenes must return the standard WorldDelta (same schema as R1 adjudication) for commit; I forward memory_writes and relationship_deltas from WorldDeltas to its memory/relationship stores.
- **Information / Gossip**: I emit informational events carrying payload.claim {claim_id, version, assertions, truth_value} and maintain reach curves per channel scope using its forward-probability parameters; it owns claim content mutation and re-emits mutated versions as child informational events (cause_event_id = prior version) through events.emit(); percept fidelity bookkeeping is mine, content distortion is theirs.
- **Canon DB / Persistence**: All event/condition/process/percept/adjudication rows are canon writes; I call canon consistency checks (entity exists, alive, uniqueness) during commit and write canon_facts triples from WorldDeltas; retrieval side exposes events.query(filters), events.timeline(entity, t0, t1), events.explain(event_id) with FTS5 over narrations for interview grounding.
- **Institutions (courts, police, PMC, hospitals, schools)**: Process wake scheduling calls org.reserve(capacity_queue, preferred_window) -> slot (court cause lists, hospital beds, workshop bays) so institutional throughput constraints shape timelines; institutions receive official-channel percepts and process_step events for their rosters; their own actions (FIR registration, complaint disposal) enter as events.emit(source='process').
- **User Interface / Director**: events.set_attention(target, level 0-3) registers watching (budget refill + fidelity promotion + R2 eligibility); user injection via events.emit(intent, source='user', force=True) with conflict reporting; events.explain and events.timeline power the 'why did this happen' and 'follow their day' views; novel user scenarios route through register_classdef(yaml) possibly synthesized by one adjudication call.
- **Calendar / Weather**: I consume the real calendar service (festivals, court working days, school terms, muhurat dates) and weather stream (rain intensity fields) as hazard-rate modifiers and as sources of scheduled/ambient events; weather warnings are emitted by me as informational events referencing a predicted event.

## Scenario traces

## S1 — School bus crash, 8:10am Shivajinagar (acute → multi-cascade)
HazardSampler draws `hazard.road.collision` on an edge near Shivajinagar (rate elevated by AM-peak tod_curve). `who_is_at(edge, 8:10)` returns the truck, the bus, and the clockwork-scheduled passenger manifest — father and daughter get lazily instantiated if not yet detailed. Severity 0.6 → R0 effect templates fire: injury conditions (KSI table stages: er → admitted@Sassoon via org.reserve(sassoon:beds) → home_rest → recovered, each stage adjusting mobility/school_attendance effects), road_block condition on the edge (capacity 0.25, clearing ~64 min — mobility layer replans commuters, the jam IS the capacity change), vehicle_disabled on the bus (in_service=false, workshop timer), and a police_case Process (template process.fir.bns, sections in vars). Fanout: sensory radius witnesses via geo cell reach; org:school roster gets an official-channel step (parents' panic percepts have high salience: child-involvement term); news_local channel curve (newsworthiness τ=1 day). Severity ≥0.8 escalation gate passes → the FIR opens a fresh court-case cascade (→S6). School absences are just the injury conditions' school_attendance=false effect read by the clockwork layer. Every mechanism used — sampler, effect templates, capacity reservation, escalation — is generic.

## S2 — Temple donation scam rumor (informational)
An `info.claim.circulating` event with payload.claim v1 enters via agent emit (someone's suspicion) or hazard sampling of rumor-genesis. No physical footprint; fanout is purely channel curves: street-talk within the temple's geo cells (k≈2/day), whatsapp:{devotee_group} (k≈8/day × forward-probability from information subsystem). Gossip subsystem mutates the claim (amount inflates, a name gets attached) and re-emits child informational events — cause_event_id chains give a canonical rumor phylogeny; percept.claim_version_id records which mutation each person heard, fidelity decaying per hop. Cascade budget damps the chain (each mutation event debits), newsworthiness τ≈2 weeks; if a mutation names a person, a reputation_hit condition attaches to them and an R1 adjudication may let the temple trust emit a rebuttal informational event. Cold, not dead: the claim resurfaces (novelty reset) if a later real event matches it.

## S3 — 48-hour cloudburst floods Mutha-adjacent lanes (ambient)
Weather stream emits `env.rain.extreme` (ambient shape, H3 footprint from low-lying-lane polygons, duration 48h, impulse 80 distributed over the footprint). Footprint STRtree join attaches conditions: flooded on households (habitability/mood effects), road_block on lanes (capacity 0), disease_risk (low-intensity, long half-life — the 'worry' persists past the water). Commute failure is emergent: mobility reads capacity effects and fails routes; no flood-specific code. Affected households' cognition layer emits complaint intents → pmc_complaint Processes queue against org:pmc_ward capacity (slow disposal = realistic frustration). Official warnings beforehand = informational events referencing the predicted event. After rain ends, condition stage schedules dry out lanes over days; disease_risk conditions keep periodic next_review timers that can (low probability, budget-gated) emit illness events for weeks — long tail without attention.

## S5 — Job loss spiral (slow personal arc)
Trigger from any source: employer-org event, agent decision, or hazard (layoff rate). Milestone event `work.employment.terminated` (social-graph fanout above severity threshold — family learns, distant friends may not) creates Process arc.job_loss with monthly next_wake. Each wake: rule-based ledger update (savings drawdown → debt condition accrues, intensity rising; fee_arrears condition on children's school roster when threshold crossed → school sends official percept) plus branch sampling modulated by the mind's actual decisions (cognition emits job-application intents; interview outcomes are R1 adjudications using persona capsule + conditions like mood/debt in context). Family tension = relationship_deltas from adjudicated quarrel events, budget-gated so not every month explodes. Recovery or spiral is not scripted: it is the trajectory of process vars under decisions + samples; the process closes on a re-employment milestone or transitions to deeper states (asset sale, migration intent). Months pass at zero LLM cost between wakes; user attention any time reheats fidelity.

## S6 — Truck driver's case, 3+ years in Shivajinagar court (institutional process)
The court_case Process from S1 wakes via org.reserve(shivajinagar_court:causelist) against real working days and pendency-calibrated intervals (median 4+ years emerges from adjournment probability 0.55 × capacity queue congestion, not from a scripted duration). Each hearing is a process_step event: parties get participant percepts (the driver's percept salience stays high; his family's conditions — income loss if license suspended — persist via effects), official record channel updates, news only if newsworthiness recomputes above threshold (it mostly doesn't — realistically cold). Judgment state is R1-adjudicated with allowed_outcomes and BNS sections in vars; conviction emits milestone events opening (budget-gated) consequence conditions. The entire 3-year thread costs ~30 timer fires and ~1-3 LLM calls total.

## S7 — 2026 PMC ward election (city-scale process)
CalendarScheduler instantiates Process ward_election per ward (template phases: announcement → nomination → campaign → polling → counting → aftermath). Campaign phase wakes emit scheduled_social events (rallies = venue events with rosters; door-to-door = geo-cell percept pushes by candidate orgs) and informational events (promises = claims, attackable by S2 machinery — the same rumor mechanics do election misinformation for free). Ward issues are read from aggregate condition statistics (count of active flooded/road_block/complaint conditions per ward — the world's actual state IS the campaign material). Polling: R0 turnout + preference model over ward demographics modulated by percept-weighted issue salience; the losing corporator gets a milestone event; aftermath transitions write canon_facts (new corporator) and shift org:pmc_ward priority vars, which changes complaint-disposal rates — civic priorities shift as parameter changes, observable in S3-style processes thereafter.

## S8 — Ganeshotsav, 10 days (mass gathering)
Calendar instantiates Process festival.ganeshotsav with per-day mass_gathering events: crowd fields on H3 cells and edges (modifier conditions: footfall ×N → hazard exposure multipliers rise, so pickpocketing/accident rates climb automatically via the sampler's modifier hooks — no festival-specific hazard code), road_block conditions for procession routes and pandal lanes (S4's pandal uses the identical condition), org:police bandobast reservations consuming station capacity (other response times degrade realistically), commerce spike as an ambient economic modifier condition read by the economy layer. Visarjan processions are scheduled_social events with area footprints. Teardown timers reopen lanes. The festival's own sub-budget lets it spawn many child events without draining unrelated cascades. (S4 wedding = the same machinery at household scale: Process wedding, scheduled_social ceremony with invitee-roster fanout — invitations are percepts of a status='scheduled' event — a shopping-surge modifier condition on nearby commerce, and one pandal road_block.)

## Generality argument

Generality is achieved by making the engine operate only on domain-free mechanics — shapes, budgets, timers, scopes, deltas — while ALL domain content lives in a declarative registry. (1) Any happening reduces to one of seven spatiotemporal shapes (acute, ambient, informational, scheduled_social, process_step, mass_gathering, milestone), and each shape fully determines the mechanical questions the engine must answer: who is physically affected (point query vs footprint join vs roster vs none), how information spreads (sensory radius vs channels vs invitee lists), and how long it persists. A never-anticipated situation — a building collapse, a bank run, a viral dance trend, a caste-panchayat dispute, an exam-paper leak — picks a shape and fills ClassDef slots; the engine has no branch that could fail to cover it. (2) Anything longer than a scene is a Process (state machine + wake timers + capacity queues), which covers court cases, elections, weddings, festivals, job-loss arcs, insurance claims, and constructions with one mechanism; new long-horizon phenomena are new templates. (3) Consequences of any kind flow through exactly one channel — Conditions with stat-modifier `effects` — so downstream subsystems (mobility, cognition, economy) never need to know WHICH event hurt someone or blocked a lane, only that a modifier exists; a novel event type's consequences are automatically consumed everywhere. (4) When rules run out, the LLM adjudicator is a general-purpose outcome function constrained by an allowed-effects menu, so novel situations resolve into the same typed WorldDelta rather than free text — and user-injected genuinely-novel event types are handled by synthesizing an inline ClassDef via one adjudication call, closing the loop without code. (5) Cascade budgets, decay clocks, and the timer wheel are content-blind, so pacing (neither explosion nor premature death) holds for scenarios never tested. The probe scenarios exercised every shape and every generation source without any scenario-specific code path; the honest residual specialization is calibration data (rates, stage schedules, pendency medians), which is exactly the part that SHOULD be per-domain.

## Open questions

- Cloudburst/rainfall hazard calibration needs a real data source — IMD Shivajinagar station daily rainfall series (or Pune AWS network) must be acquired and fitted to a monsoon intensity distribution; who owns weather-stream ingestion, calendar subsystem or events?
- Exact ownership boundary with the Information/Gossip subsystem for claim mutation: I propose I own routing, reach curves, and fidelity bookkeeping while gossip owns content mutation and forward probabilities — the claim_version schema and fidelity semantics need a joint spec review.
- Do relationship_deltas and memory_writes in WorldDelta get applied by me (forwarding to cognition's stores) or returned to cognition for application? Proposed: I forward through a cognition-owned apply API so validation rules live with the data owner — needs cognition subsystem sign-off.
- Cascade constants (impulse table per class, depth multipliers, newsworthiness tau values, escalation threshold 0.8) are priors requiring empirical tuning — the replay-calibration harness (30 sim-days, cascade-size histograms vs sanity targets) should be built in week one; what are the agreed sanity targets for cascade size distribution?
- H3 dependency vs a plain 250m grid for the 2-3 km2 start area: H3 wins at city scale but is an extra native dep on Windows; decide before the mobility layer bakes scope keys into subscriptions.
- Interaction between user interview mid-tick and the sim-time adjudication barrier: if the user interrupts while R1 batch is in flight, does the interview see pre-commit or post-commit state? Proposed: interviews always see last committed tick (sim pauses at barriers), but UI subsystem should confirm the UX is acceptable.
- Sensitive-dimension guardrail placement: hazard-rate modifiers must never key on religion/caste (only geography, exposure, time, weather, socioeconomic ward aggregates) and adjudication rubrics need an explicit style constraint — where does the enforcement test suite live, and does the registry schema need a lint rule forbidding such modifier keys?
- Scaling checkpoint: at full-Pune 3.5M, reach-curve checkpointing per touched scope and hazard sampling stay cheap, but the percept table grows with attention history — is a cold-percept archival policy (move to attic table after N sim-months) acceptable to the Canon DB subsystem?

## Red-team critique (verdict: needs_changes)

- **[critical]** Percepts are inert knowledge with no path to behavior. Reactions are only produced when cognition consumes percepts, but Tier-B percepts materialize only when a person is 'instantiated/attended'. So for any unattended agent, learning never mechanically happens, and consequences of information (father discovers elopement, shopkeeper sees rival's discount, employer hears a rumor) occur only where the user is looking. This is 'attention never creates events' inverted: attention creates the world's social trajectory. Nothing in the pipeline ever enqueues resolve_reaction — adjudication triggers hang off event ClassDefs, not percept arrival.
  - Fix: Add an eager reaction-critical tier at Tier-A commit time: for each event, compute a bounded set (participants' household members, top-k social edges by relationship_weight x salience, role-relevant org officers — cap ~20/event) and materialize those percepts eagerly with an R0 reaction-threshold check (salience x stake > theta -> enqueue agent intent or R1 resolve_reaction). Keep lazy Bernoulli fanout only for percepts that are pure interview/color knowledge. This preserves O(1)-ish fanout while making the world's behavior observation-independent.
- **[critical]** Neither the three primitives nor WorldDelta can create entities or permanently mutate world structure. WorldDelta ops are conditions/events/relationship_deltas/memory_writes/process_ops/canon_facts — no entity_create, no topology change. Births, new households, a shop opening, a building demolished, and a metro station are all inexpressible; a Condition's decay/stage/review semantics are the wrong shape for a permanent baseline improvement. Downstream, hazard exposure tables and precomputed commute-cell subscriptions silently go stale, corrupting the NCRB-calibrated sampler (accidents keep sampling on pre-metro VKT).
  - Fix: Add world_ops to WorldDelta (entity_create, entity_modify, topology_change) plus an eighth shape 'structural_change' whose commit hook (a) versions exposure tables into epochs so the hazard sampler references the current epoch, (b) triggers mobility-layer re-baseline, (c) schedules staged batch re-subscription of affected geo/route scopes, and (d) writes a canon_fact anchoring the change for interviews. Also add an 'opportunity' variant of the plan-invalidation channel so new options (not just new constraints) trigger cheap T0 replanning.
- **[major]** commit() order bug: cascade.try_debit runs before tier resolution and before the escalation gate, so a severe child in an exhausted cascade is 'absorbed as a condition tweak' without ever petitioning. Concretely: a KSI 'serious' injury condition progresses to death 5 days after the crash; if the cascade budget is spent, the death event is absorbed. Deaths and other deterministic physical outcomes must never be budget-gated — budget is meant to throttle discretionary social ripple, not physics.
  - Fix: Reorder commit(): evaluate the escalation gate for severity >= threshold BEFORE the absorb branch; exempt R0 deterministic physical consequences (condition stage transitions incl. death, scheduled process steps) from budget debits entirely. Budget governs discretionary/social children only. Add a unit test: exhausted cascade + fatal stage transition must still emit a canon death event.
- **[major]** The depth-multiplied budget conflates depth with implausibility. Legitimate low-severity strategic loops — the Laxmi Road price war, a family pressure campaign, a neighbor feud, complaint tit-for-tat, rumor/rebuttal cycles — are alternating chains of severity ~0.1-0.4 events at depth 5-15. Root impulse of 'shop cuts price' is tiny, multipliers hit 6x by depth 4, and the escalation gate needs severity >= 0.8 which these never reach. The loop silently dies at depth 2-3 — exactly the ripple-death the spec forbids, caused by the anti-explosion mechanism itself.
  - Fix: Two changes: (1) auto-promotion — a cheap R0 detector (same participant set, related class family, N>=3 alternations within a window) promotes the chain into a Process (rivalry/feud/dispute template) with its own refillable sub-budget, reusing the mechanism festivals already have; (2) rescore the escalation gate on cumulative stakes (summed relationship/economic delta across the chain) instead of instantaneous event severity.
- **[major]** Independent per-person seeded Bernoulli draws destroy close-tie correlation in information spread. A crash participant's wife has her own independent u; the seeded math can canonically decide she 'never learned' or learned three weeks later, despite sharing a bed with a man in a hospital ward. Interviews will surface these absurdities constantly and they are un-retconnable (percepts persist once observed). This is a top 'feels like slop' generator.
  - Fix: Hierarchical draws: mix a shared household/close-tie component into the hash (u_person = f(u_household, u_individual)) so co-resident learning is highly correlated, and give participants' households and top-k ties guaranteed eager percepts (same set as the reaction-critical tier fix, so one mechanism serves both).
- **[major]** Retroactive learned_at creates behavior/knowledge contradictions: lazy materialization at query time t_q backfills learned_at = tau < t_q, but the person's T0 plans between tau and t_q ran without that knowledge. Canon then asserts 'she knew her cousin died Tuesday' while her recorded Wednesday behavior shows she attended a party and mentioned nothing. Accumulates as un-fixable canon drift over long runs.
  - Fix: Floor the backfill: learned_at must be >= the person's last behavioral checkpoint (last committed scene, plan revision, or interaction); if the curve's inverse-CDF sample lands earlier, clamp it to the checkpoint and log a reconciliation (optional cheap mood/memory backfill). Document the invariant: no percept may predate behavior that contradicts it.
- **[major]** Sim-time barrier stalls wall-clock at scale. At 3.5M, ~5-8k events/day with even 5% R1 escalation is 250-400 calls/day; 96 ticks/day each blocking on batched network calls (2-10s latency per batch) makes 'fast-forward 6 months' take hours. Dollar cost stays small (DeepSeek-class pricing keeps it under $1/day); wall-clock is the real blowup, and it is coupled to the determinism design.
  - Fix: Adjudicate ahead of the play head: hazard Poisson draws for a sim-day are known at day start (clockwork), so pre-resolve R1s for outcomes not immediately observable, committing children via the existing delay_min mechanism; enforce the strict barrier only for entities currently under attention. Determinism is preserved by the adjudication log (determinism-given-logged-outputs already accepts this).
- **[major]** Canon-consistency gate is too weak for long runs: check_canon_consistency covers death/uniqueness/location existence only, while cheap-model narration and canon_facts triples accumulate contradictions (wrong hospital name, wrong shop, injury side) fed by a 150-token persona capsule and top-3 memories. FTS5 retrieval later surfaces these contradictions verbatim in interviews. Drift is monotone because events are append-only and retcons are forbidden.
  - Fix: (1) Validator rule: proper nouns in R1 narration must resolve to entities present in the context capsule, else strip or regenerate (one retry, then template fallback). (2) canon_facts writes pass a (subject, predicate) uniqueness/contradiction check; conflict rejects the delta back to the adjudicator. (3) Replace most R0 narration with hand-written template pools (slot-filled, Marathi-English code-mixed fragments) — cheaper AND more textured than cheap-model prose.
- **[major]** The registry DSL is an inner-platform trap for a solo dev: effect templates embed an expression language ('0.3+0.6*severity', '10+90*severity min', conditional if-clauses like 'any ksi>=slight'), plus wake policies, capacity queues, hazard modifier hooks, and transition samplers. That is a parser, evaluator, sandbox, error reporter, and debugger — a compiler project hiding inside 'it's just YAML'. Combined with authoring hundreds of ClassDefs/stage tables/process templates, the generality claim shifts all difficulty onto one person's content authoring.
  - Fix: v1 grammar: literals, piecewise lookup tables, and a whitelisted safe evaluator (simpleeval or equivalent) only; JSON-schema-validate the registry itself; require one golden instantiate-and-commit test per ClassDef in CI. Defer any richer DSL until at least 30 ClassDefs exist and the patterns are known. Budget registry authoring explicitly in the plan — it is the majority of the remaining work.
- **[major]** Inline ClassDef synthesis is wired only for user injection. An agent-emitted intent with no matching ClassDef fails schema validation and bounces, so agent behavioral novelty is hard-capped by whatever the solo dev hand-authored. The 'engine has no branch that could fail to cover it' claim is true but vacuous — coverage failures just relocate to 'no ClassDef exists' and are invisible.
  - Fix: Route unknown agent intents through the same one-shot R1 ClassDef synthesis with a conservative default shape and a human review queue for the synthesized entries; add coverage telemetry (count of bounced/synthesized intents per class family per sim-week) so authoring effort follows demand.
- **[major]** Adjudication routing has severity- and attention-based escalation but no content-sensitivity dimension. A cheap model doing R1 on inter-religious marriage, caste, or communal dynamics will produce either stereotype-driven outcomes or sanitized instant-acceptance — allowed_effects clamps magnitude, not distribution. The open-questions list flags rate-modifier guardrails but has no answer for outcome-distribution realism on sensitive classes.
  - Fix: Add a sensitivity flag to ClassDef that (a) routes to the premium model regardless of attention, (b) injects hand-authored outcome priors keyed to per-family/per-person variables (traditionalism score, prior inter-group ties in the ego-network) and never to group identity, and (c) gates registry changes to these classes behind a human-reviewed golden scenario suite. Accept the cost: these events are rare enough that premium routing is cents.
- **[minor]** No explicit governor on R1 bursts: election season (41 ward processes emitting claims/counter-claims into the S2 rumor machinery, each named-person reputation_hit eligible for rebuttal adjudication) or festival crowd-surge weeks can 10-100x daily R1 volume for sustained periods.
  - Fix: Per-class daily R1 token budget with graceful degradation to the R0 fallback tables (the fallback path already exists), priority-ordered by attention; alert in the calibration harness when any class saturates its budget 3 days running.
- **[minor]** The hazard-calibrated registry skews the world into a police blotter: NCRB/accident/pendency data gives calibrated misfortune, the calendar gives festivals, and nothing generates ordinary good days — promotions, exam success, new friendships, a good harvest of small joys. A world of calibrated calamities punctuated by weddings will not feel like life.
  - Fix: Seed the registry with positive/neutral milestone and ambient classes under plausible priors (employment stats, board-result calendars, matchmaking rates), and add a valence histogram to the week-one calibration harness next to cascade-size targets.
- **[minor]** explain() elides knowledge-mediated causation: a confrontation caused by a discovery percept has cause_event_id pointing at the marriage decision (or nothing), because the DAG deliberately excludes knowledge links. 'Why did the father show up at the registrar' is unanswerable from lineage alone.
  - Fix: Allow a cause reference of type percept (event_id + person_id + channel) in causal chains, rendered as 'because X learned of E via C' — keeps the causation/knowledge separation in storage while letting explain() traverse both.
- **[minor]** The replay-determinism promise breaks on every code change: any bugfix that alters RNG consumption order invalidates prior replays, and a solo dev will change code weekly. Hierarchical seeding isolates streams but not within-stream consumption order.
  - Fix: Scope the promise: replays valid only within a world-version; checkpoint the canon DB at version bumps and treat old canon as data (append-only import), not as re-derivable. Say this out loud in the design so nobody builds on cross-version replay.

### Novel holdout-scenario traces

SELECTION. Of the six holdouts: chain-snatching is literally named in design section 2 as an example YAML entry — it is in-distribution and proves nothing about generality (and its presence hints the 'holdouts' were anticipated). The dog attack reduces to an acute hazard plus an area-level 'stray_dog_menace' condition feeding the sampler's exposure-multiplier hooks — the machinery genuinely covers it. The wada collapse is acute+ambient composite (venue occupancy query, displaced conditions, rescue process, PMC survey process) — mostly covered, thin only on building-stock vulnerability data for the hazard rate. The saree price war exposes a real flaw (budget depth-multipliers kill legitimate low-severity strategic loops — filed as an issue) but shares its root cause with scenario B below. The two MOST stressing are the metro opening and the inter-religious marriage, because they attack the design's two proudest mechanisms: the everything-is-Event/Condition/Process ontology, and lazy deterministic fanout.

TRACE A — NEW METRO STATION OPENS, COMMUTE PATTERNS SHIFT.
Step 1, generation: CalendarScheduler emits the inauguration as scheduled_social; news spreads via channel reach curves. Works.
Step 2, the station existing henceforth — BREAK: WorldDelta has no entity-creation or topology op (conditions/events/relationship_deltas/memory_writes/process_ops/canon_facts only). A new venue/edge cannot be committed through the event pipeline at all. None of the seven shapes fits: milestone attaches to person/household, ambient has hours-days duration, acute is minutes. The claimed taxonomy ('the engine has no branch that could fail to cover it') has a hole: permanent structural change. Encoding it as an eternal Condition abuses decay/stage/review semantics and still cannot create the station entity.
Step 3, behavior shift — BREAK: adoption requires ~10^5 catchment commuters to re-evaluate mode choice. They HEAR about the metro (reach curves fine), but the plan-invalidation channel notifies only capacity/constraint changes — negative deltas. A new OPTION invalidates nothing; no T0 replan triggers. The sim would canonically show a metro nobody rides. The S-curve of adoption over months has no owning primitive: not a Condition (no entity), not a Process (no org runs 'everyone reconsiders'), not per-person R1 (cost-insane).
Step 4, second-order — SILENT CORRUPTION: hazard sampling is exposure-based (to its credit, this part WOULD adapt if mobility re-baselines VKT/footfall), but nothing tells mobility to re-baseline, and precomputed geo:h3 commute-cell subscriptions for 10^5 people go stale with no re-subscription trigger in the API; a mass resubscribe through the single-writer commit queue is a write storm nobody owns.
Step 5, economy — SPECIAL-CASE: rickshaw stand income collapse at the old node needs a cohort-level effect ('income_factor for profession=rickshaw within area'), but conditions attach to entities, not cohorts; economy would need a bespoke join.
Step 6, canon — BREAK: 'why is Deccan traffic lighter since March' has no causal lineage because the behavioral shift never passed through events; explain() and interviews ground out.
Net: the ceremony and the news trace cleanly; the actual scenario — the structural change and its behavioral equilibrium shift — requires engine changes (world_ops in WorldDelta, a structural_change shape with re-baseline/re-subscription hooks, an opportunity-variant plan invalidation), directly contradicting 'any new life situation is a registry entry'.

TRACE B — INTER-RELIGIOUS COUPLE MARRIES AGAINST BOTH FAMILIES' WISHES.
Step 1, genesis: cognition emits milestone 'relationship.marriage_decision', visibility secret, secrecy 0.9. Registry-expressible; severity is class-normalized so a physically harmless event can carry high in-class severity. Works.
Step 2, Special Marriage Act: a process template (notice_filed -> 30-day objection window -> registration) with the mandatory public notice as an official-channel informational event. Genuine credit: the SMA notice board becomes the emergent leak vector with zero special-casing — this is the design at its best.
Step 3, discovery — CENTRAL BREAK: the father learning is a Tier-B lazy draw. If the user is not watching the family, the percept never materializes, so no confrontation, no pressure campaign, no estrangement — the couple's entire arc happens only under observation. When the user later attends, learned_at backfills three weeks into the past while the father's recorded behavior shows nothing — maximal canon contradiction at maximal dramatic salience. The design's own principle ('attention never creates events') is violated in reverse: attention creates the reaction, therefore the world.
Step 4, reaction — BREAK: even with the percept materialized, nothing enqueues resolve_reaction. Adjudication triggers hang off event ClassDef resolution tiers; a percept arriving is not an event, and no condition or timer watches percepts. A new trigger type (percept-arrival reaction check) is an engine change, i.e., special-casing.
Step 5, the pressure arc: months of calls, visits, boycott threats, partial reconciliation — fits a Process arc with wakes IF something creates it (adjudicator process_ops can, once step 4 exists). Relationship dims (trust/warmth/obligation) lack an approval/pressure axis; household reputation_hit conditions exist. Registry-fixable, borderline.
Step 6, budget — BREAK (shared with the price war): confrontation -> quarrel -> couple moves out -> housing search -> uncle revokes job referral is a legitimate depth-5+ chain of severity 0.3-0.4 events; depth multipliers hit 4-6x, the 0.8 escalation gate never fires, and the family-side cascade is silently absorbed by depth 3 unless the impulse table special-cases marriage classes — a workaround that concedes the budget model conflates depth with implausibility.
Step 7, sensitivity — BREAK: R1 on a cheap model fed 'Hindu family, Muslim groom' capsules will produce stereotype-driven or sanitized outcome distributions; allowed_effects clamps WHAT can happen, not HOW LIKELY. No calibration data exists for family-acceptance dynamics, and the design's only escalation to premium models is attention-driven (focal scenes), not content-driven. Realism here needs hand-authored outcome priors on family-level variables plus premium routing — currently absent.
Net: institutions, secrecy, and process machinery genuinely fit (better than I expected), but the scenario's core — knowledge causing behavior in unwatched people — is exactly what lazy fanout cannot do, and the cascade budget starves the aftermath. Both traces converge on the same root flaws: no structural-change primitive, inert percepts, and a budget that punishes depth rather than implausibility.