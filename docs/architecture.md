# Pune Sim — Master Architecture

Synthesized from a 16-agent planning fleet (7 subsystem architects, 7 red-team
critics, integration critic, completeness critic; ~958k tokens of design work).
Full per-subsystem blueprints with their critiques live in [`subsystems/`](subsystems/).

**Revised 2026-07-31 by a second six-agent audit fleet:** the identity firewall is
replaced by graduated disclosure ([subsystems/08-identity.md](subsystems/08-identity.md)),
a collective-dynamics layer is added ([subsystems/09-collective-dynamics.md](subsystems/09-collective-dynamics.md)),
the cost model is corrected (§5), the build order is re-cut into vertical slices
(§6), and cross-doc contradictions plus dropped red-team fixes are catalogued
with binding rulings (§9). Where an older subsystem doc contradicts §9 or 08/09,
the ruling wins.

**Verdict:** the seven designs independently converged on the same philosophy —
single-process SQLite-on-Windows storage, seeded determinism, lazy materialization
with touch-activated canon, data-driven generality over special-casing, and
attention-directed LLM spend. The conflicts found are ownership and contract
problems, not physics problems. They are resolved below as constitutional law
before any code is written.

---

## 1. The five constitutional laws (Phase 0 — cheapest now, ruinous to retrofit)

1. **The event log is truth.** One append-only, event-sourced log owned by the
   orchestrator kernel; world state and the canon DB are deterministic
   *projections* of it. Every LLM response is committed as an input-event
   ("recorded nondeterminism"), so replay is bit-exact without re-calling any
   model. Snapshots are checkpoint optimizations, always validated against the
   log-fold determinism hash; on disagreement the log wins.
2. **One fact gate.** `assert_facts()` (POPULATION's predicate registry: schemas,
   cardinality, mutability, supersede rules, sensitivity, entity resolution) is
   the single *semantic* gate for all facts — but it emits
   `FactEstablished`/`FactSuperseded` events into the orchestrator's log rather
   than writing tables directly. Nobody else writes canon. Quantitative facts
   (prices, wages, sales, casualty counts) are **clockwork-writable only**;
   scenes receive them read-only.
3. **One WorldDelta schema.** A single superset output schema for every LLM
   result — child events, conditions, process ops, facts, memory/relationship/
   belief deltas, transcript — used identically by T2 scenes, T3 focal scenes,
   and rules-escalation adjudication. Pipeline, strictly ordered: model output →
   INFERENCE structured-output ladder (syntax) → WorldDelta → orchestrator
   commit → EVENTS propagation → `assert_facts` (semantics). WorldDelta includes
   `world_ops` (entity_create / entity_modify / topology_change) so births, new
   shops, collapsed wadas, and metro stations are expressible.
4. **One RNG doctrine.** Counter-based numpy Philox as a kernel service, keyed
   `(run_seed, domain, entity_id, tick_or_day, purpose, draw_index)`. Injecting
   or removing an event never perturbs unrelated entities' draws — this is what
   makes clean what-if branches possible. No subsystem keeps private RNG state.
5. **One attention/budget authority.** The orchestrator's `AttentionField` is the
   single LOD/tier dial (score = f(user focus, perturbation, arc activity, event
   proximity)). COGNITION's trigger taxonomy feeds it as salience *inputs*;
   INFERENCE keeps the spend ledger and pushes back `BudgetPressure`. Tier↔call
   mapping is fixed: T1→household_plan, T2→scene, T3→focal_turn, micro→
   micro_update, adjudication→scene/judge.

---

## 2. System overview

| # | Subsystem | Owns | Blueprint |
|---|-----------|------|-----------|
| 1 | **WORLD** | Space (OSM graph + synthesized buildings + jurisdictions), time (hybrid 300s tick + 1s event queue — the *single* timer substrate all subsystems register into), weather/hydrology, movement (3 traffic LODs: analytic / mesoscopic BPR+queues workhorse / SUMO micro-window for focal only), generic field registry, disruption API, PlaceContext for prompts. Strictly LLM-free. | [01-world.md](subsystems/01-world.md) |
| 2 | **POPULATION** | Synthetic Punekars: "household grammar" synthesis calibrated to ward Census marginals; two-layer existence (regenerable statistical D0 + touch-activated bitemporal canon); retro-history sampling (forward hazard tables run backwards at first touch, so biographies decorate structure, never invent it); demographic dynamics; `mint_person`/`mint_place`; prefix-cache segment provider. | [02-population.md](subsystems/02-population.md) |
| 3 | **MINDS** | Person state as numeric integrators (financial/health/family/job/social/legal pressure with hysteresis) + threshold-gated LLM scenes; routine-bypass gate (~92–95% of households replay cached schedule templates daily with zero LLM); 8+1-class escalation taxonomy (incl. E9 "affordance delta" for *positive* opportunities); memory as a view over the shared event log; relationship graph; INFO module — information as first-class objects with mechanical mutation ops, source-trust belief updating. | [03-cognition.md](subsystems/03-cognition.md) |
| 4 | **EVENTS** | Three primitives: immutable Event (causal DAG), stateful Condition (staged lifecycle + wake timers — also the runtime *health* object), Process (only for non-org arcs; org-anchored processes belong to INSTITUTIONS). YAML ClassDef registry — new event types are data, not code. Five generation sources (NCRB-calibrated hazards, calendar, agent decisions, timers, user injection). Lazy two-tier percept fanout + eager reaction-critical tier (~20 people/event) so unattended agents still react. Cascade severity budgets with decay. | [04-events.md](subsystems/04-events.md) |
| 5 | **INSTITUTIONS** | One Organization abstraction (OrgType templates) + one declarative Procedure interpreter (~400-line engine, JSON state machines, closed effect vocabulary) runs police/courts/hospitals/schools/PMC/newsrooms/temples/shops/employers and ephemeral orgs (weddings, mandals, election). Strict double-entry ledger = sole balance authority. Asset/tenancy/lien tables. Court pendency *emergent* from cause-list slot scarcity + calibrated adjournments. Demand-side commerce (per-org prices, weekly business reviews). | [05-institutions.md](subsystems/05-institutions.md) |
| 6 | **INFERENCE** | Provider-agnostic gateway; five call classes only. DeepSeek-class workhorse ($0.87/M out, $0.28/M in-miss, $0.028/M in-hit), premium model only for the T3 focal stream. Crash-resumable SQLite job queue, deterministic request IDs (= replay cassette keys). Four-segment prefix-cache architecture (city preamble / ward-day / household slice / volatile tail, ≈0.67 hit rate). Six-rung structured-output ladder ending in deterministic clockwork fallback. QC: slop detection, canon-contradiction checks, observer-effect (drama-bias) monitoring. Local GPU: embeddings + optional tiny QC judge only. | [06-inference.md](subsystems/06-inference.md) |
| 7 | **INTERFACE** | The asyncio kernel/conductor: 288-tick day pipeline, sole log writer, commit(), keyed_rng(), AttentionField. Scenes run async — tick advancement never awaits LLM latency. God console (follow/interview/inject/query/advance/branch); free-text injection compiles via LLM into the Event schema with ground/validate/preview. Map viewer: FastAPI + MapLibre + PMTiles basemap, live positions, congestion, event ticker; SUMO-GUI as optional lens. What-if branches via copy-on-write log forks. | [07-interface.md](subsystems/07-interface.md) |
| 8 | **IDENTITY** (cross-cutting policy layer) | Religion/community/latent-jati as first-class structure (names, marriage kernel, peth micro-geography, festivals, membership networks) under graduated prompt disclosure (tiers 0/1/2); `att.stance` attitude canon; two-voice enforcement + narratability tiers; refusal detection & premium rerouting. Supersedes the sensitive-attribute firewall. | [08-identity.md](subsystems/08-identity.md) |
| 9 | **COLLECTIVE** (cross-cutting, distributed ownership) | Riots/bandhs/processions/crushes/panics via five general mechanisms: civic fields (unrest/tension/fear in WORLD's registry), claim-coupled mobilization (INFO), Granovetter threshold crowds (clockwork, zero-LLM mass behavior), public-order procedures + crowd-as-ephemeral-org (INSTITUTIONS), field-mediated cascade re-seeding (EVENTS). Scene sampling + event budget reserve (INTERFACE). | [09-collective-dynamics.md](subsystems/09-collective-dynamics.md) |

**Deterministic adaptation layer (cross-cutting, owned by kernel+WORLD):**
discrete-choice models (mode/destination/shop choice) parameterized by canon
facts and fields, drawn with keyed Philox — so a metro opening or a price cut
shifts thousands of T0 lives mechanically, without a single LLM call. LLM scenes
are for *meaning*, clockwork is for *behavior mass*.

---

## 3. Ownership rulings (seam conflicts, resolved)

1. Canon = projection of the log; `assert_facts` survives as the semantic gate (law 2).
2. One commit pipeline for LLM facts (law 3); EVENTS/INFERENCE direct-write paths deleted.
3. Orchestrator is the sole log writer; EVENTS becomes a taxonomy+validation+propagation library.
4. Org-anchored long processes (cases, hearings, elections, permits, weddings) run on INSTITUTIONS' Procedure engine; EVENTS' Process keeps only non-org arcs (personal arcs, festivals-as-city-phases).
5. INFO is a named module inside MINDS; EVENTS owns *exposure* (who could know, when), MINDS owns *content* (what they believe it says).
6. AttentionField is the only tier gate (law 5).
7. RNG standardized (law 4).
8. One LLMRequest schema owned by INFERENCE; tier→class mapping fixed; blake2b request_id = cassette key.
9. One WorldDelta superset schema (law 3).
10. WORLD's event queue is the single timer substrate; calendar_register added to WORLD's contract.
11. EVENTS adopts statistical presence: `exposure_field` for rates, seeded `presence_sample` for casting concrete victims/witnesses (stable on re-query); exhaustive `who_is_at` only for already-materialized agents.
12. Health: EVENTS' Condition is the runtime object; treatment = event with registered effect templates; MINDS' p_health is a derived integrator; outcomes → canon via the gate.
13. INSTITUTIONS' ledger is the sole balance authority; MINDS' financial state is a read-view.
14. Log-fold replay is doctrine; WORLD snapshots are validated checkpoints.
15. POPULATION implements the prefix-cache segment provider to INFERENCE's byte-stability spec; MINDS supplies only the volatile tail.

---

## 4. Red-team fixes folded into the design (the critical ones)

- **WORLD:** append-only world-delta log for runtime structural change (collapsed
  wadas, new metro stops, retired edges); generic field registry (a heatwave or
  dog-menace field is a registration, not a schema migration); demographically
  typed cohorts (composition vectors + predicate sampling so "a child at the
  school gate" is castable).
- **POPULATION:** current_view projection so queries never read stale D0 for
  canon-touched people; epoch-versioned D0 (infrastructure change re-derives
  untouched entities); place lifecycle (`mint_place`, open/close); sensitivity
  disclosure policy (below).
- **MINDS:** E9 opportunity trigger + mechanical template re-optimization;
  two-level identity policy (narrator vs character voice); household lifecycle
  (spawn/split/merge/dissolve with account partitioning and obligation
  reassignment); spatial/narrative LOD on scene eligibility so scene volume does
  not grow linearly to 875k households.
- **EVENTS:** eager reaction-critical percept tier (bounded ~20/event) so
  off-screen people still react to news that concerns them; `world_ops` in
  WorldDelta (structural change is expressible).
- **INSTITUTIONS:** demand-side economy (durable per-org prices, footfall
  reallocation, weekly business-review procedures); asset/tenancy/lien layer
  (pagdi tenancy, seizure, muddemal); LOD-0 transfer coarsening (weekly rows +
  monthly compaction with conservation checksums).
- **INFERENCE:** cross-household scene assembly rule (primary household slice +
  ~150-token participant sheets); deterministic template migration on
  infrastructure change (zero-LLM rewrite of commute legs); sensitive-scene
  escalation path.
- **INTERFACE:** async scene decoupling (ticks never await LLM); two-stratum
  population (materialized agents + statistical cohorts with promotion AND
  demotion); deterministic choice layer; provenance-tiered canon for
  quantitative facts.

---

## 5. Cost model (corrected 2026-07-31 — audit summary in §9.3)

Pricing basis (verified): `deepseek-chat` is retired; workhorse = **DeepSeek
V4-Pro** ($0.435/M input-miss, $0.003625/M input-hit, $0.87/M output), with
**V4-Flash** ($0.14 / $0.0028 / $0.28) for structure-only classes (T1 plans,
micro, QC, repair) — a 3× lever the original model missed. The assumed 50%
off-peak discount **no longer exists**; it inverted into a 2× *peak surcharge*
(Beijing 09–12 & 14–18) the scheduler must avoid. At 120:1 hit:miss pricing the
input lever is **miss-token diet** (tail + household-slice size), not hit rate —
h degrades mechanically as contexts grow.

| Scale | Best | Expected | Worst |
|---|---|---|---|
| Old City 50k / 12k hh, background | $1.3–1.6/day | **$2.0–2.4/day** | $5–8/day; declared event days $10–18 |
| + T3 focal (2 focal-hrs/day, amortized) | +$0.5/day | +$0.7–1.3/day | +$3–5/day |
| Full Pune 3.5M / 875k hh, background | $5–7/day (Flash tiering) | **$12–14/day** | $30–60/day (epicenter/migration days) |

The previous "$1–2/day" and "$10–40/day" figures survive only as governor
clamps, not expected spend; 03-cognition §7's "×1.3 input factor ⇒ $25–35/mo" is
an arithmetic error (input ≈ 1–1.8× output at the design's own token profiles).
Naive per-household remains ~$220/day at full Pune — the ~20× LOD advantage, the
actual product claim, survives the audit. **Event days (riot, festival,
migration wave) get a pre-authorized reserve outside the daily governor** (~$20
Old City / ~$50 full Pune per declared event) so the governor stops buying
solvency with fidelity on exactly the days worth watching. Hidden costs to book:
QC judge (route to Haiku-batch or local, not Sonnet-priced-as-DeepSeek), regen
stacking (ledger alarm at >8%), Devanagari output inflation, nightly digests,
cross-household participant sheets, T3 context growth (hard 12k cap + rolling
digest + cadence-aware cache promotion).

Levers, in effectiveness order: V4-Flash tiering for structured classes,
miss-token diet (C ≤ 800 tok, terse tails), peak-window blackout, scene sampling
rate, frozen-household fraction, T3 context cap, premium-model scope.

---

## 6. Build order — revised 2026-07-31: vertical slices

The original M0–M8 horizontal sequence (kept below as the *reference phase map*)
put the first LLM-visible life ~8–14 solo-dev months out — after ingest,
synthesis, traffic, events, and institutions — and deferred the project's only
unproven bet (do cheap-model scenes + canon + consequence propagation *feel
alive and stay coherent*?) to the second-to-last milestone. README's own
milestone list and 07-interface's internal plan both put minds at M2; only this
document said M7. Ruling: the doc suite is the **reference architecture**;
execution follows vertical slices, each exiting on a runnable demo. Effort
figures assume one solo dev at ~15–25 focused hrs/wk.

- **V0 — One peth block, full stack thin (6–10 wks).** M0's constitution kernel
  unchanged (all five laws, thin: log + `commit()` + keyed Philox + WorldDelta +
  `assert_facts` + attention top-k), plus: hand-edited GeoJSON block (~30–60
  places, real names), ~50–100 hand-sampled households, walking + one scripted
  YAML bus, ONE morning household scene/day for ~5 attention-selected households
  (cheap model, cassette record/replay from day one), percept fanout by hand
  rules, stub institution subscribers (scripted ambulance/hospital/school),
  structured YAML injection with preview, interview mode (time bubble,
  journalist/ghost), text-first follow view. **Exit: the school-bus-crash
  scenario runs end-to-end and feels alive** — inject at 08:10, consequences
  fire on schedule, the family's scenes reference it consistently for days, a
  gossip hop reaches neighbors, the day-3 interview matches canon; replay is
  hash-identical with zero API calls. V0 also probes the workhorse model's
  refusal behavior on identity-salient content (08-identity §4) — the earliest
  possible test of the design's riskiest external dependency.
- **V1 — Texture (4–6 wks).** Routine-bypass gate; 3 of 9 E-triggers; 2 pressure
  integrators; INFO v1 (mechanical mutation ops, belief update, claim keys);
  thin hazards + percept tiers; minimal map (Leaflet dots suffice). Exit: an
  injected rumor propagates, mutates, and changes one household's behavior over
  3 sim-days; a *random* hazard produces a believable un-injected ripple; 30
  sim-days with zero canon contradictions on a followed family; <$1/sim-day.
- **V2 — Institutions push back (4–6 wks).** Two hand-written procedures (police
  FIR, hospital admission) as plain Python; finances-lite (obligations,
  p_financial); LLM injection compiler with ground/validate/preview; branch-lite
  (copy-db fork + diff). A *minimal* collective-dynamics instance (fear field,
  shelter/continue choice, one hand-authored unrest ClassDef, scripted police
  response — 09 §Build note) to de-risk tier-2 scene quality early. Exit: the
  crash yields an FIR + hospital bill that raises p_financial and triggers a
  money scene weeks later; free-text injection compiles with zero new code.
- **V1.1 — What the 30-day soak taught (unplanned, 1 wk).** The first soak met
  the cost exit by two orders of magnitude ($0.0017/sim-day) and the rumour
  exit cleanly (five claim families, all rose and died, max reach 73%), and
  **failed the continuity exit** with four contradictions on the followed
  family. Root-causing them against the log found that three of the four shared
  one mechanism and that the mechanical defects underneath were larger than the
  narrative ones:
  - **Scenes were reading their own output.** `recent_notable_events` excluded
    only routine movement, so a scene's memories, moods, dialogue and (after
    V2) its whole narration came back the next morning as RECENT EVENTS. 64% of
    every context block was the household's own prior words; 53 of 118 prompts
    were 100% self-output with zero world events. The model did the reasonable
    thing and completed the pattern. **Rule: a generator must never be shown its
    own output as if it were observation.** Memory is read back deliberately,
    dated, under a header that says it is background.
  - **Bare ids are an invitation.** Handed `person:022.4` with no name, the
    model invented an adult colleague for a six-year-old pupil and kept her four
    days. Every id in a prompt now arrives as "Name (age, occupation) [id]", and
    `apply_delta` rejects references to people who do not exist — the registry
    is canon and a scene does not get to extend it.
  - **Uniform exponential decay is order-preserving**, so the attention field
    froze the moment perturbations stopped: eleven days on the same five
    households, and a family that was never bumped sat at exactly 0.0, locked
    out forever. Attention needs a *render-feedback* term (staleness), not just
    a decay term. Being on camera is itself an event.
  - **Absence is observable; work is not.** Counting a workday by matching an
    activity string was a one-way ratchet for every occupation with no fixed
    workplace, and no whitelist can survive scene-authored free text. Detect the
    rare, observable thing (in hospital, convalescing, sheltering) and treat
    everything else as an ordinary day.
  The standing consequence is a two-part gate, both scripted: `audit_run.py`
  (28 mechanical probes, exits nonzero) and `continuity_read.py` (a judge model
  reading a followed family against canon, citations required). Eyeball audits
  under-count — the mechanical sweep found 11 verbatim duplications where the
  hand-read found 1, and a defect class nobody had looked for.

  **Known and deliberately left open**, so they are followed up rather than
  rediscovered:
  - *Absence detection still reads strings.* A scene that narrates someone
    staying home writes free text ("stays home, calls the school"), which is not
    in ABSENT_ACTIVITIES, so the ledger pays them for the day. The robust rule
    is presence-based — a person with a workplace who never emitted a trip is
    not at work — and it needs the committed day, not the planned one.
  - *The memory digest has no forgetting.* Salience decays with age so nothing
    pins the digest, but nothing is ever dropped either; a year-long run needs
    consolidation, not just re-ranking.
  - *Street talk renders one exchange a day.* That is a deliberate floor, not a
    model of a block's social life — it exists so the information graph is
    visible at all. Scaling it means sampling by attention, not by rank.
  - *`--follow` is additive to the scene budget*, so following a family raises
    per-day cost above k and narrows coverage of everyone else (measured: 78 of
    80 households reached in 30 days instead of 80). Intentional, but it should
    become a budget the user sets rather than a side effect.
- **V3 — Real Pune data, at scale (8–12 wks).** The original M1–M6 machinery,
  built against known requirements with V0's cassette suite as the regression
  harness for *feel*: full ingest (OSM Western-Zone clip, GTFS, jurisdictions,
  calendar), IPF synthesis + retro-history + rehydration, trip engine + cohorts,
  ClassDef registry + cascade/field machinery, Procedure interpreter
  generalizing V2's procedures, full INFERENCE gateway (queue, segments, ladder
  incl. refusal rung 2b, QC), PMTiles viewer. Exit: V0's scenario re-runs
  unchanged on 4 real peths / 12k households; "Old City breathing with zero LLM
  calls" is a *subset* of this exit; <$2/sim-day background.

  *Progress 2026-08-07.* **Step 0 (scale probe, done)** — measured before
  building, and it changed the order of nothing but saved the rest: the day
  pipeline was n^1.86 in population and would have cost 12 min/sim-day at V3
  scale. Three local defects, no architectural limit; see
  [perf/scale-probe.md](perf/scale-probe.md). The one deliberate behaviour
  change is a co-presence contact cap — under all-pairs, how many people you
  exchange news with in a day was a function of how big the city is (20/day at
  306 people, 225/day at 11k, still climbing), which is wrong on its face.
  **Step 1 (the V3 block, done)** — `oldcity`, a four-peth extract, opt-in via
  `--block` so the Kasba pin and every soak hash stay frozen. 12k households /
  49.6k people run at 86 s/sim-day.
  **Step 2 (population calibration, done)** — demography is now a per-block
  table chosen by `block.name` (`population/demography.py`), fitted offline by
  `scripts/fit_synthesis.py` to the Kasbavishrambaug ward office's marginals:
  household size 4.1315 vs 4.1380, male share 0.4940 vs 0.4954, under-7 share
  0.0717 vs 0.0726. Kasba keeps the V0 numbers to the digit. Fitted to *ratios*,
  not counts — the ward office is 13 wards and 43,138 households, larger than the
  four-peth block, so totals do not tile.

  Not the IPF of the original plan, and deliberately so: real IPF needs the
  age×sex×ward joint tables from the District Census Handbook, which §7 still
  defers. Reweighting templates against the marginals we *do* hold is sufficient
  to reach them, and the honest limit is written into the module.

  The methodological finding is worth carrying into every later calibration:
  left unconstrained, the fit matched all three marginals through knobs that
  mean nothing — PG rooms at 42% male in a student city, single-elder households
  at 2%. The error it was absorbing is really widowhood (couples plus children
  land near 51% male; the route below 50% is that men in the senior cohort die
  first). Bounding the knobs and adding the mechanism made the search find it.
  **A calibration that hits its targets is not thereby correct.**

  **Step 3 (trip engine, done)** — `world/roads.py` builds a 7,978-node walking
  graph from the pinned ways; shortest paths run from the 438 places rather than
  the 7,008 homes, since walking is symmetric. Per block like demography: kasba
  never routes. The flat 1.4 detour factor turns out generous on 88% of walks
  (median 0.93×) and blind to the 11% that reach 2.4× — a constant is wrong in
  one direction or the other. +18% per sim-day.

  **First soak of the new world (done)** — 30 days, 49,578 people, 6.8M events,
  0 FAIL across four audit windows; three defects found that nothing smaller
  could have. See [soaks/v3-scale-soak.md](soaks/v3-scale-soak.md). The viewer
  was rebuilt to read logs at that size rather than hold them.

  **Step 4 (Procedure interpreter, done)** — `institutions/interpreter.py` (97
  lines) plus `catalog.py`. The two V2 procedures are ported byte-identically
  (hash `0625050f…` on a run exercising admission → discharge → bill → payment
  and FIR → update). The closed effect vocabulary is real: a procedure may
  schedule events and mark someone in hospital or resting, nothing else. The
  binder stays Python deliberately — `min(adults)` and `block.nearest(place,
  "police")` are world queries, and a JSON expression language for them would
  be a worse Python.

  **Step 5 (ClassDef registry, done)** — `data/classdefs/hazards.json` +
  `world/classdefs.py`, validating loader, order still load-bearing. The
  `narratability` field of 08-identity §5 is now machinery rather than a
  ruling: `numeric` events are committed and countable, seed no claim, and
  no scene opens on them at any attention level. Nothing shipped is numeric
  yet — the rule exists before the NCRB classes that will need it.

  **Cost exit met** — 12,000 households with scenes on: **$0.0031/sim-day**
  against the $2 bar, versus $0.0029 at 80 households in V1. The gate caps
  scenes, so spend tracks attention rather than population; 14 of 12,000
  households were on camera, which is the trade it makes.

  **Cohorts — measured, and one obvious approach ruled out (2026-08-08).** At
  8,312 people the info lane builds 132k co-presence windows a day, of which
  **64.5% have neither party carrying any claim**, 14.5% one, 21.1% both; a mean
  of 25% of the city holds something on a given day. The 64.5% looks like free
  money — build only the windows that can matter — and it is not. Filtering at
  generation needs the carrier set up front, and holdings *grow during the day*:
  someone who hears a rumour at 09:00 passes it on at 14:00, which is what
  "multi-hop within a day works because windows are processed chronologically"
  means. Tried it; the determinism hash caught it immediately. Same-day
  multi-hop is load-bearing and a static carrier set silently removes it.

  So cohorts cannot be a filter on the existing lane — they have to be a
  different *representation* for people far from attention (a per-claim
  exposure count rather than per-person holdings), with the aggregate spread
  validated against the individual model. That is a soak-and-compare piece of
  work, not an optimisation.

  **And the obvious aggregate is known to be wrong for this model
  (2026-08-10).** "A per-claim exposure count" is a first-order mean-field
  approximation, and the info lane is Maki-Thompson with a forgetting term —
  `STIFLE_P = 0.3` plus `e^(-age/FRESHNESS_TAU_DAYS)` — which is precisely the
  variant [Ferraz de Arruda et al., *Nature Communications* 13:3049
  (2022)](https://www.nature.com/articles/s41467-022-30683-z) studied. Their
  result is that the model has a second-order phase transition **that
  first-order mean-field does not capture**: mean-field says a rumour always
  reaches some fraction of the population, and the true stochastic dynamics say
  it dies below a critical spreading rate. Worse for us, the subcritical regime
  has rumour lifespan diverging as a *power law* as the rate falls — long-lived
  rumours that mean-field cannot see.

  Rumour death is not a detail here; it is the thing the info lane exists to
  get right, and this repo has already shipped one immortal rumour (freshness
  keyed to the teller, 40% of the city, sixteen days) and built
  `RUMOR-IMMORTAL` to catch it. A mean-field cohort would put that failure mode
  back *in the representation*, where no probe watching individual holdings can
  see it. The V3 soak's claims reached 3,425–17,537 of 49,578 people and all
  died — a partial-reach regime, which is exactly where the transition lives
  rather than safely away from it.

  So the cohort design starts one rung up, at a pair approximation or better —
  something that keeps the correlation between who has heard and who they meet,
  since that correlation is what mean-field discards and what the transition is
  made of. And the soak-and-compare must gate on the **lifespan and reach
  distribution per claim**, not on mean reach: matching the average is exactly
  what a mean-field approximation does while getting the dynamics wrong.

  **Open:** `nuclear_nokids` sits at 0.04 because nothing in three marginals
  distinguishes a childless couple from an empty nest; a fourth (literacy or the
  age bands) would pin it. Hazard rates are absolute rather than per-capita and
  blocked on NCRB data nobody has vendored. Then cohorts, which is what the
  residual linear constant in the scale probe is waiting for.
- **V4+ — Arcs, courts, QC depth, scale-out.** 90-day soak, budget governor,
  evaluation harness, full collective dynamics (09), election-class process
  test — note the real Jan 2026 PMC election already happened (41 prabhags),
  so the end-to-end test becomes a *counterfactual replay*, a better test of
  the branching instrument anyway — then the 3.5M path.

Principles kept: irreversibility first (M0 unchanged, now inside V0); viewer
grown continuously as the debugging instrument. Principle retired: "LLM spend
last" — cassettes make LLM-touching code free to iterate after first recording,
and spend was never the scarce resource; solo-dev motivation and the untested
aliveness bet were.

### Reference phase map (target architecture; superseded as an execution plan)

- **M0 — Constitution (1–2 wks):** the five laws as code: log schema, `commit()`,
  `keyed_rng()`, WorldDelta pydantic schema, determinism-hash test. Thin kernel.
- **M1 — WORLD static (Phase 1):** ingest pipeline (OSM clip → graph; buildings
  merge+infill; jurisdictions; calendar), clock + timer substrate.
- **M2 — POPULATION D0 + canon gate (Phase 2):** household-grammar synthesis,
  predicate registry, `assert_facts`, segment provider.
- **M3 — WORLD dynamic (Phase 3):** trip engine, cohorts + materialize/demote,
  weather, disruptions. **Exit: the Old City breathing deterministically with
  zero LLM calls, visible in a minimal map viewer.**
- **M4 — EVENTS (Phase 4):** ClassDef registry, hazards, Conditions, percepts,
  R0 rules-only adjudication.
- **M5 — INSTITUTIONS (Phase 5):** Procedure interpreter, ledger, LOD-0
  commerce, staffing; NJDG-calibrated court pendency — all clockwork, still $0.
- **M6 — INFERENCE (Phase 6, parallelizable from M2):** gateway, queue, ladder,
  segments, QC, budget ledger; embeddings first.
- **M7 — MINDS (Phase 7):** integrators → scenes/memory/relationships → INFO
  module. Deepest fan-in, built last of the core.
- **M8 — INTERFACE completion (Phase 8):** AttentionField wiring, injection
  compiler, branching, interviews; scale-out; **end-to-end test: the 2026 PMC
  ward election running across all subsystems.**

(Original sequencing principle — "irreversibility first, dependency depth
second, LLM spend last" — retained here for the record; superseded by the
vertical-slice ruling above. V3 implements M1–M6; 08/09 land incrementally:
fields with WORLD-dynamic, unrest classes with EVENTS, procedures with
INSTITUTIONS, mobilization with MINDS, sampling/routing with INTERFACE.)

---

## 7. Backlog from the completeness critic (23 gaps, grouped)

**Life-stage realism:** childhood (school days, exams, tuition, play), elderly
life & care dependency, chronic illness trajectories, death as a multi-week
process (rituals, inheritance, bureaucracy), the marriage market (matchmaking,
negotiation processes), non-family households (student hostels/PGs/messes,
labor camps — Pune is a student city), gendered daily life (mobility, time
budgets, intra-household labor).

**Economy realism:** the informal economy (hawkers, domestic workers, naka
labor — over half of Pune's workforce), the credit hierarchy (banks → MFI →
gold loans → moneylenders), the housing market (rent, pagdi tenancy, wada
redevelopment), long-run price/wage formation and inflation, everyday political
brokerage and petty corruption.

**City texture:** transit as a supply system (headways, crowding, fares, metro),
pollution/AQI fields, utilities as lived infrastructure (water supply hours,
load shedding, LPG), stray animals, persistent crime networks (not memoryless
Poisson). *(Strikes/bandhs/riots left the backlog 2026-07-31 — resolved by
[subsystems/09-collective-dynamics.md](subsystems/09-collective-dynamics.md).)*

**Cross-cutting policies (must exist before EVENTS is implemented):**
- **Representation & sensitivity policy — RESOLVED 2026-07-31:** the
  firewall-vs-erasure paradox is settled by the graduated-disclosure identity
  layer ([subsystems/08-identity.md](subsystems/08-identity.md)): identity
  conditions structure always; prompts see identity at declared disclosure
  tiers (ClassDef flag ∨ canon attitude trigger ∨ tension field ∨ user flag);
  narrator/character two-voice enforcement; tier ≥ 1 routes premium with 100%
  judge coverage and a review queue; refusals are a detected, rerouted,
  first-class outcome. The blanket firewall in 02-population §6 / 03-cognition
  §8 is superseded (migration checklist: 08-identity §7).
- **Content-safety tiers — RESOLVED:** `narratability: full|abstract|numeric`
  is now a ClassDef schema field (08-identity §5). NCRB calibration *will*
  generate suicides, DV, and crimes against children statistically; they are
  countable but not narratable, and the renderer refuses to open a scene on a
  `numeric` event regardless of attention.
- **Real-entity policy:** real institutions and role *titles*, synthetic role
  *holders* — the sim's "corporator of Kasba" is a fictional person, never the
  real officeholder; `mint_person` refuses real names.
- **Evaluation harness:** calibration targets (trip distributions vs studies,
  pendency vs NJDG, crime vs NCRB, time-use vs India TUS) with acceptance bands;
  drift dashboards.
- **Observer-effect control:** monitor incident-rate divergence between watched
  and unwatched populations; prompt-level bias correction so watching a family
  doesn't turn their life into a soap opera.

---

## 8. Stack

Python 3.12+ / uv · SQLite (WAL, JSON1, FTS5) + GeoPackage · numpy/Philox ·
shapely 2 + STRtree · python-igraph · pyrosm/osmium · gtfs-kit · rasterio ·
pydantic · FastAPI + MapLibre GL JS + PMTiles · prompt_toolkit · openai +
anthropic SDKs (thin adapter) · optional SUMO/TraCI · optional local
embeddings (bge-m3). Single process, no servers, Windows-friendly.

---

## 9. Second-pass audit (2026-07-31) — findings and rulings

A six-agent audit fleet (identity architect, collective-dynamics architect,
data verifier, coherence critic, build-order critic, cost auditor) reviewed the
suite. New blueprints: [subsystems/08-identity.md](subsystems/08-identity.md)
and [subsystems/09-collective-dynamics.md](subsystems/09-collective-dynamics.md);
cost model and build order revised in §5/§6 above. The rulings below are
binding on implementation; subsystem docs not yet rewritten to match carry them
as errata (where an older doc contradicts a ruling, the ruling wins).

### 9.1 Blocker rulings

1. **WorldDelta is not yet a superset.** Law 3's enumeration cannot carry a T1
   `day_plan`, scene `messages`, `new_commitments`, or `mood_deltas`; 04-events
   §5, 06-inference's SceneOut, and 03-cognition §2.3 each specify a different
   shape. Ruling: WorldDelta := the literal union (add day_plan/schedule_ops,
   messages/info_ops, commitments, mood_deltas; keep world_ops); one pydantic
   file; T1/micro/reflection outputs are declared subsets. First V0 deliverable.
2. **One time substrate.** The docs carry two units (seconds vs minutes), two
   epochs (2026-06-01 vs 2026-01-01), and four cadences (300s/1s, 15-min,
   hourly, 288-tick). Ruling: canonical time = int64 **seconds** since
   **2026-01-01 IST**; the kernel owns the clock; "288 ticks/day" = 300-second
   frames; EVENTS' 15-min and INSTITUTIONS' hourly cadences become timers on
   the single queue; canon timestamps minute-grain or finer.
3. **Identity policy.** Resolved — 08-identity supersedes 02-population §6,
   03-cognition §8, and the QC blocklist parts of 06-inference §7.

### 9.2 Major rulings

4. **Async scenes vs determinism:** async execution with deterministic commit
   at trigger_tick + fixed k per tier; physically-coupled adjudications resolve
   ahead of the play head; strict barriers only under attention. (Adopts the
   dropped 04/07 red-team fixes; without this, branch `diff` compares
   wall-latency noise.)
5. **One cost authority:** 06-inference's ModelSpec-derived table as corrected
   in §5. 03-cognition §7 is deleted as a cost source; 07-interface's CI cost
   ceilings regenerate from the cost tool, never hand-set constants.
6. **Budget never vetoes physics:** cascade budgets throttle discretionary /
   social children only; R0 deterministic physical consequences (death stage
   transitions, scheduled process steps) are never budget-gated. (Fixes the
   commit-order bug where a spent cascade budget absorbed a death.)
7. **Per-role capacity:** procedure transitions declare effort per role;
   causelist slots derive from the judge budget alone. (Role-blind
   capacity-hours silently break the flagship NJDG-calibrated pendency claim.)
8. **Ruling-4 enforcement:** org-anchored kinds/templates (court_case,
   ward_election, wedding, …) are struck from 04-events' process registry;
   EVENTS keeps non-org arcs + festival city-phases; the Ganeshotsav umbrella
   is an EVENTS city-phase while all org activity (mandals, bandobast, permits)
   is INSTITUTIONS cases; calibration constants live in exactly one anchor file
   (04 shipped adjournment p=0.55, 05 shipped p=0.62 — one number, one file).
9. **One life-event sampler:** POPULATION's cohort ledger is the sole sampler
   of births/deaths/marriages; EVENTS' CalendarScheduler only schedules
   muhurat-weighted ceremony dates for already-emitted `life.marriage` events;
   attribution decrements the residual ledger cell in the same transaction.
   (Otherwise interfaith couples get statistically double-married.)
10. **One exposure engine:** EVENTS owns the only exposure/percept store and
    delivery model (reach curves + eager tier); MINDS §6.2's parallel
    propagation and duplicate exposure table are deleted; percepts deliver to
    MINDS as the sole E5 emitter (EVENTS never calls resolve_reaction on
    persons); claim genesis = a notability function on EVENTS ClassDefs.
11. **Finances mapping:** INSTITUTIONS' ledger gains household obligation
    schedules (creditor may be person or org); MINDS §1.3 tables are
    re-declared as views with a published column↔ledger mapping; scene
    `new_commitments` compile to open_case/transfer intents validated by
    INSTITUTIONS.
12. **Institutions write through the log:** case_/case_event/document become
    deterministic projections of orchestrator-log events; the interpreter emits
    rather than writes. ("These tables ARE the institutional canon of record"
    violated Law 1; replay/branching cannot fold state outside the log.)
13. **Call classes:** Segment A is keyed by (call_class, schema_name); the
    class list gains `digest` (reflection/bio/catchup) and `compiler`
    (injection/NL-query), covering the calls the five-class contract missed.
14. **One re-optimizer:** the kernel+WORLD deterministic choice layer is the
    only routine re-optimizer; MINDS' E9 merely fires it and escalates residual
    conflicts; INFERENCE's "template migration" is retired as a mechanism
    (kept only as the budgeted thaw wave).
15. **README alignment:** tier bullet gains the routine-bypass gate; milestone
    list replaced by §6's vertical slices. (Applied 2026-07-31.)
16. **Disposition table owed:** ~28 [major] red-team findings were dropped by
    the first synthesis without disposition (among them: the flood-sill units
    bug, the retroactive `learned_at` behavioral floor, narrative staleness
    invalidation on fact supersede, pin-consistent bridge sampling, claim-key
    canonicalization, the anchor-manifest hash missing from the determinism
    hash, AttentionField damping). Rule: every [critical]/[major] finding gets
    folded / deferred-with-milestone / rejected-with-reason **before its owning
    subsystem is implemented**; silence is not a disposition. Tracking file:
    `docs/red-team-dispositions.md`, created per slice.
17. **RNG:** one kernel `keyed_rng()` with Law 4's six-tuple. The PCG64
    mentions in 02/04/05 and the divergent key tuples in 01/07 are superseded;
    a lint gate bans `PCG64`/`default_rng` imports.
18. **Prefix-cache:** 06-inference §6 is the normative segment spec; POPULATION
    serves byte-stable A/B/C segments (hard cards only in C); anything
    retrieval-scored (context_pack steps 2–3, memories, persona deltas) is tail
    by definition; 03-cognition's three-segment variant is superseded.
19. **One tier gate:** `needs_scene` and B1 selection are re-specified as pure
    functions of AttentionField score + BudgetPressure; staleness joins the
    AttentionField definition in Law 5; E9 becomes normative in 03 §3.1 (the
    taxonomy is 8+1 everywhere).

### 9.3 Data corrections (verified against live sources, 2026-07-31)

- **OSM:** no Geofabrik "Maharashtra extract" exists — use the India **Western
  Zone** .pbf (~209 MB), clipped with osmium. The old-city core has ~2.9k
  building footprints (recent mapping surge) but ~99% are untyped: derive
  building use from POI density + Google Open Buildings / Microsoft footprints,
  not OSM `building=*` tags.
- **Wards — three incompatible geographies in the wild:** 2011 census wards
  (the demographics' 151-row CSV, via the OpenCity mirror), DataMeet's 2012
  electoral wards, and bharatlas's likely-2022-draft 58-ward layer. Build a
  one-time crosswalk; for the 4 starting peths, georeference the District
  Census Handbook ward maps by hand. Politics: the **Jan 2026 PMC election
  already happened** (41 prabhags, ~165 corporators) — the "upcoming election /
  162 corporators" framing is stale, and the election end-to-end test becomes a
  counterfactual replay (§6 V4).
- **PMPML GTFS:** the live feed is **494 routes / 6,203 stops / 10,728 trips**
  (not 366/5,624/21,804); it is an unofficial, regenerated scrape — vendor a
  hash-pinned zip into `data/anchors/`.
- **Census identity data:** C-1 religion is **town-level** (confirmed — no ward
  religion table exists); ward PCA gives SC/ST; no jati data since 1931 (SECC
  2011 unreleased). Peth-level priors are editorial estimates grounded in
  citable sources: Gadgil's *Poona: A Socio-Economic Survey* (1945/52,
  peth-by-peth), OSM places-of-worship density, Susewind's published
  booth-level estimates — see 08-identity §1; never scrape electoral rolls.
- **NCRB:** city tables give total rioting only; the communal/political/caste
  split exists at **state** level — use Maharashtra rates × Pune exposure share
  (`provenance=estimate`), sanity-checked against documented events (2014
  Hadapsar / 2018 Bhima Koregaon; 1894 Pune & 2009 Miraj as festival-trigger
  templates).
- **Pricing:** `deepseek-chat` retired 2026-07-24; V4-Pro/V4-Flash are current;
  the off-peak discount is gone (2× peak surcharge instead). §5 carries the
  corrected model; 06-inference's ModelSpec constants and scheduler logic need
  re-pointing.

### 9.4 Owner ruling (2026-07-31): the novelty ladder — dynamism guarantee

Motivating probe: `inject "the District Collector shot dead in broad daylight
outside Council Hall"`. Owner requirement: the world must be *dynamic* — no
scenario may require engine code, and no injection may fail for want of
authored content. Binding guarantees:

1. **Scenarios are never code.** Engine code knows shapes, budgets, fields,
   timers, menus, and deltas — never scenarios (04-events' stance, now binding
   suite-wide). Authored content is *categories* (event classes, org types,
   procedures, claim families), not situations; scenarios are parameterizations
   and compositions. The assassination is `crime.murder` (already required by
   NCRB calibration) + a public-figure victim + an org role — all existing
   machinery.
2. **Every injection lands.** The compiler resolves to the deepest registry
   support available, in order: (a) instance of an existing ClassDef;
   (b) inline LLM-synthesized **draft** ClassDef (04-events §3 source 5 — the
   ban in 09 applies only to the communal-unrest family); (c) generic
   acute/ambient/informational event with adjudicated consequences. It never
   errors out; it degrades in institutional depth, and the injection preview
   states which level it hit and what content is missing.
3. **The world grows at play time.** When an injection or scene references an
   institution, role, or long process with no registry entry, the compiler
   drafts one (org from a generic OrgType, role, minimal procedure) as
   `provenance='draft'` data shown in the injection preview; accepted drafts
   run immediately and are promotable to curated registry content later.
   `mint_person`'s philosophy extended to orgs and procedures: the thing the
   story needed retroactively always existed.
4. **Adjudication is the improviser.** Where rules don't cover an outcome,
   R1/R2 adjudication writes it as a WorldDelta within `allowed_effects` — the
   LLM composes consequences nobody authored, through the gate that keeps them
   canonical, persistent, and replayable. Dynamic ≠ unconstrained: the menus
   and the fact gate are precisely what make improvised consequences *stick*
   (the shopkeeper interviewed three sim-months later still knows).
5. **Acceptance trace (V2/V3 exit test):** the DM-assassination probe runs
   end-to-end with zero engine changes and at most draft-content minting:
   murder event + witnesses cast by presence sampling + death (budget-exempt) +
   FIR/investigation + MLC + city-wide news/rumor cascade with claim mutations
   + fear/`unrest:civic` field response + shop-close/gawker behavior menus +
   bandobast redeployment draining capacity elsewhere + succession via
   `rebind_position` on a drafted procedure + multi-year court tail +
   condolence meetings / bandh call as ephemeral orgs.
6. **Acknowledged ceiling:** the modeled world tops out at city institutions.
   State/national actors (CM, Home Ministry, Governor) are WORLD stubs
   reachable by notify/transfer effects; their decisions arrive as drafted
   procedures or injected events, not simulated politics. Full state-level
   machinery is deliberate backlog, not a broken promise.

Known content gaps the probe exposes today (registry data, not code): the
Collectorate/DM org type is unauthored; no succession/appointment procedure
exists; `crime.murder` must ship in the NCRB-calibrated base ClassDef set.
