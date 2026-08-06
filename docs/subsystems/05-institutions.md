# 5. Institutions & economy

## Summary

The Institutions & Economy subsystem is one data-driven machine: an Organization is a row instantiated from an OrgType template (roles, capacity, resources, operating hours, jurisdiction, fee schedule), and everything an organization does is a Case advancing through a declaratively-defined Procedure state machine interpreted by a single ~400-line engine. Police stations, courts, hospitals, schools, PMC offices, newsrooms, temple trusts, shops, employers, and even ephemeral collectives (a wedding, a Ganeshotsav mandal, an election) are all configurations of this one machine — no institution has bespoke code. Money is a strict double-entry ledger with a single RestOfWorld boundary account, so wages, fees, fines, purchases, donations, and loans are all conserved transfers; at scale, unwatched commerce runs as zone-level aggregate flows that are backfilled into itemized canon when attention promotes a shop or household to detail. LLMs never drive the machine: procedures expose typed DecisionPoints with enumerated options, and the Minds subsystem may substitute a role-holder's LLM choice for the calibrated default sampling only when a case is focal — the engine validates the choice and applies all effects itself, so canon and conservation cannot be broken by a model. Realistic multi-year court pendency, BNSS timelines, hospital triage, and election phases are emergent from the same primitives: capacity-limited work slots, sim-time timers, calibrated stochastic outcome distributions, and cause-list overflow mechanics, calibrated against NJDG, NCRB, and PMC public data. Cross-institution cascades (hospital MLC auto-intimating police, election results rebinding PMC corporator positions and budget weights, bandobast draining court witness availability) are declared effects in procedure JSON, so they compose without special-casing. Storage is SQLite (WAL) with Pydantic-validated JSON templates, shapely/STRtree for jurisdiction geometry, and numpy PCG64 streams keyed per-case for deterministic replay. The tick loop is O(due timers + active cases), so dormant organizations cost nothing and the design scales from the 2-3 km2 Old City core to full Pune unchanged.

## Design

# Institutions & Economy Subsystem — "One Org Machine"

## 0. Design thesis

Every institution is the same machine with different data. The machine has exactly five primitives:

1. **Organization** — instantiated from an **OrgType template** (roles, resources, capacity, hours, jurisdiction, fees, funding, procedures offered).
2. **Case** — the universal work item (an FIR, a court matter, a hospital admission, a PMC complaint, a loan, a permit, an election contest, a news story, an overdue school fee). Cases are the *only* way anything happens inside an org.
3. **Procedure** — a declarative state machine (JSON, Pydantic-validated) that a Case executes: states, transitions, guards, effort costs, timers, calibrated stochastic outcome distributions, effects, and DecisionPoints.
4. **Ledger** — double-entry accounts and transfers; all value flows in the sim pass through it; money is conserved with one explicit `WORLD` boundary account.
5. **Router** — a data-driven service registry mapping `(need_kind, location, attributes) → [candidate org + procedure]` using jurisdiction rules.

LLMs (owned by Minds/Scenes subsystem) appear only at **DecisionPoints** (choose among machine-enumerated options) and **scene hooks** (roleplay dialogue whose mechanical outcome is still a validated option). The machine runs on rules + calibrated RNG at T0 cost.

## 1. Storage & libraries

- **SQLite** (stdlib `sqlite3`, WAL mode, single file `institutions.db`, foreign keys on). Boring, Windows-friendly, transactional. Write volume at Old City scale is trivial (<10k rows/sim-day); scales to full Pune because dormant orgs write nothing.
- **Pydantic v2** — validates OrgType templates and Procedure definitions at load; fail-fast on bad content authored by AI assistants.
- **shapely 2.x + STRtree** — point-in-polygon jurisdiction lookup, built once at boot from PMC ward GeoJSON + police-station boundary polygons (digitized approximations are acceptable; read-only reality anchor).
- **numpy** — `numpy.random.Generator(PCG64(seed=stable_hash(case_id, seq)))` per-case RNG streams → deterministic replay; distribution sampling (lognormal dwell times, categorical outcomes).
- **No ORM, no `transitions` library** — a custom ~400-line interpreter is required anyway for persistence, timers, resource guards, effort budgets, and RNG streams; a generic FSM lib would fight all of that.

## 2. Schema (SQLite DDL, abridged to load-bearing columns)

```sql
-- Templates (data, not code)
CREATE TABLE org_type   (org_type_id TEXT PRIMARY KEY, template_json TEXT NOT NULL);
CREATE TABLE procedure_def (procedure_id TEXT, org_type_id TEXT, version INT, def_json TEXT NOT NULL,
                            PRIMARY KEY (procedure_id, version));

-- Instances
CREATE TABLE org (
  org_id TEXT PRIMARY KEY, org_type_id TEXT NOT NULL REFERENCES org_type,
  name TEXT NOT NULL, parent_org_id TEXT,            -- chowky→station, ward office→PMC
  location_ref TEXT,                                  -- OSM node/way id (Geography subsystem)
  jurisdiction_id TEXT, hours_json TEXT NOT NULL,     -- weekly template + calendar overrides
  status TEXT NOT NULL DEFAULT 'active',              -- active|dormant|closed
  is_ephemeral INTEGER DEFAULT 0, expires_at INTEGER, -- weddings, mandals, campaign offices
  detail_level INTEGER NOT NULL DEFAULT 0,            -- 0=statistical 1=staffed 2=focal
  params_json TEXT);                                  -- per-org overrides: capacity, fees, priors

CREATE TABLE jurisdiction (jurisdiction_id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,          -- 'polygon'|'ward_set'|'citywide'|'nearest_k'|'assigned'
  geom_ref TEXT);              -- geometry blob / ward-id list

-- Staffing (persons come from Population subsystem)
CREATE TABLE position (position_id TEXT PRIMARY KEY, org_id TEXT, role_id TEXT, seat_no INT);
CREATE TABLE employment (
  employment_id TEXT PRIMARY KEY, position_id TEXT REFERENCES position,
  person_id TEXT NOT NULL, start_day INT, end_day INT,
  wage_paise INTEGER, wage_period TEXT,               -- monthly|daily|piece
  status TEXT NOT NULL);                              -- active|leave|suspended|ended
CREATE TABLE vacancy (vacancy_id TEXT PRIMARY KEY, org_id TEXT, role_id TEXT,
  wage_paise INTEGER, skill_req_json TEXT, posted_at INT, filled_by TEXT);

-- The universal work item
CREATE TABLE case_ (
  case_id TEXT PRIMARY KEY, org_id TEXT NOT NULL, procedure_id TEXT NOT NULL,
  state TEXT NOT NULL, priority INTEGER NOT NULL DEFAULT 5,
  opened_at INTEGER NOT NULL, closed_at INTEGER,
  subject_json TEXT NOT NULL,     -- {persons:[], assets:[], upstream_cases:[], location}
  vars_json TEXT NOT NULL,        -- procedure-scoped variables (hearing_count, bed_id, ...)
  next_timer_at INTEGER);         -- denormalized scheduler index
CREATE INDEX ix_case_due ON case_(next_timer_at) WHERE closed_at IS NULL;
CREATE INDEX ix_case_org ON case_(org_id, state)  WHERE closed_at IS NULL;

CREATE TABLE case_event (case_id TEXT, seq INT, at INTEGER, transition_id TEXT,
  outcome TEXT, cause TEXT, actor_ref TEXT, delta_json TEXT,
  PRIMARY KEY (case_id, seq));    -- append-only; this IS canon history
CREATE TABLE timer (timer_id TEXT PRIMARY KEY, case_id TEXT, fire_at INTEGER, transition_id TEXT);
CREATE TABLE document (doc_id TEXT PRIMARY KEY, case_id TEXT, kind TEXT, at INTEGER,
  canon_facts_json TEXT, prose_ref TEXT);  -- FIR, chargesheet, summons, permit, invoice,
                                           -- prescription, judgment, election result, news story

-- Economy: strict double-entry
CREATE TABLE account (account_id TEXT PRIMARY KEY,
  owner_kind TEXT NOT NULL,      -- household|person|org|world|pool
  owner_id TEXT, balance_paise INTEGER NOT NULL);
CREATE TABLE transfer (txn_id TEXT PRIMARY KEY, at INTEGER NOT NULL,
  debit_account TEXT NOT NULL, credit_account TEXT NOT NULL,
  amount_paise INTEGER NOT NULL CHECK (amount_paise > 0),
  memo_kind TEXT NOT NULL,       -- wage|purchase|fee|fine|donation|loan_disbursal|emi|rent|tax|grant|remittance|dowry_gift|refund
  case_id TEXT, item_json TEXT);
CREATE TABLE inventory (org_id TEXT, sku TEXT, qty REAL, PRIMARY KEY(org_id, sku));
CREATE TABLE price (scope TEXT, sku TEXT, base_paise INTEGER, surge_mult REAL DEFAULT 1.0,
  PRIMARY KEY (scope, sku));     -- scope = org_id or zone_id (zone rows are the LOD-0 price book)

-- Router registry (data-driven service discovery)
CREATE TABLE service_registry (need_kind TEXT, org_type_id TEXT, procedure_id TEXT,
  jurisdiction_rule TEXT,        -- containing_polygon|nearest_k|assigned|singleton|parent_escalation
  mandatory INTEGER, rank INT);
```

**Conservation invariant** (nightly audit job): `SELECT SUM(balance_paise) FROM account` is constant forever; every mutation of `account.balance` occurs inside the same SQLite transaction as its `transfer` row. The `WORLD` account is the explicit boundary (state salaries in, GST out, remittances, wholesale restocking) — local conservation with an honest edge, not fake closure.

## 3. OrgType template (example: police station)

```json
{"org_type_id": "police_station",
 "roles": [
   {"role_id":"sho","title":"Sr. Police Inspector","count":1,
    "decision_points":["fir_accept","station_bail","bandobast_plan"]},
   {"role_id":"io","title":"PSI/API (Investigating Officer)","count":6,"decision_points":["arrest_now"]},
   {"role_id":"constable","count":40,"shift":"3x8"}],
 "resources":[{"kind":"vehicle","count":4},{"kind":"lockup_cell","count":2}],
 "capacity":{"io_case_hours_per_shift":6,"counter_intakes_per_hour":4},
 "procedures":["fir_bnss","nc_complaint","missing_person","bandobast","mlc_intimation"],
 "hours":{"public":"24x7","admin":"Mon-Sat 10:00-18:00"},
 "funding":{"payroll_source":"world","period":"monthly"},
 "jurisdiction_default":"polygon"}
```

The same shape configures `jmfc_court`, `govt_hospital` (Sassoon: `resources:[{"kind":"bed","count":1300},{"kind":"icu_bed","count":90}]`), `private_clinic` (same template, 10 beds, real fees), `school`, `pmc_ward_office`, `pmc_hq`, `newsroom`, `temple_trust`, `kirana_shop`, `employer_generic`, `moneylender`, `event_org` (weddings/mandals, `is_ephemeral:1`), `sec_maharashtra` (election commission, `singleton`, `citywide`).

## 4. Procedure definition (example: criminal trial at JMFC Shivajinagar)

```json
{"procedure_id":"criminal_trial_jmfc","org_type_id":"jmfc_court",
 "calendar":"pune_district_court",
 "priority_policy":"custody_first_then_case_age",
 "states":["filed","cognizance","charges_framed","prosecution_evidence",
           "defence_evidence","final_arguments","judgment_reserved","closed"],
 "vars":{"hearing_count":0,"witnesses_total":0,"witnesses_examined":0,"accused_in_custody":false},
 "transitions":[
  {"id":"admit","from":"filed","to":"cognizance","trigger":"work_slot","effort":1,
   "guards":["doc_exists:chargesheet"],
   "effects":[{"emit":"case_admitted"},{"doc":"summons","serve_to":"subject.accused"},
              {"timer":{"in_days":{"dist":"lognorm","median":21,"sigma":0.4},"transition":"hearing"}}]},
  {"id":"hearing","trigger":"timer","requires":["role_present:judge","causelist_slot"],
   "decision_point":{"id":"judge_hearing","role":"judge",
                     "options":["adjourn","proceed","dispose"],"default":"sample"},
   "outcome_dist":{
     "adjourned":{"p":0.62,"cause_dist":{"lawyer_absent":0.30,"witness_absent":0.25,
                  "causelist_overflow":0.20,"judge_on_leave":0.10,"other":0.15}},
     "progress":{"p":0.35},"disposed":{"p":0.03}},
   "effects_by_outcome":{
     "adjourned":[{"inc":"hearing_count"},{"emit":"hearing_adjourned"},
                  {"timer":{"in_days":{"dist":"lognorm","median":35,"sigma":0.5},"transition":"hearing"}}],
     "progress":[{"advance_stage":true},{"scene_hook":"court_hearing"},
                 {"inc":"witnesses_examined","when":"state=prosecution_evidence"},
                 {"timer":{"in_days":{"dist":"lognorm","median":30,"sigma":0.5},"transition":"hearing"}}]}},
  {"id":"judgment","from":"judgment_reserved","to":"closed","trigger":"timer","effort":3,
   "decision_point":{"id":"verdict","role":"judge","options":["convict","acquit"],
                     "default":{"convict":0.27}},
   "effects":[{"doc":"judgment"},{"emit":"verdict"},
              {"spawn_case_if":{"cond":"outcome=convict","org_ref":"world_stub:yerawada_jail",
                                "procedure":"sentence_custody"}}]}]}
```

**Cause-list mechanic (emergent pendency).** Each court sitting day, the org pulls all cases with hearings due, sorts by `priority_policy`, and grants `causelist_effective_slots` (calibrated ~12-18 effective hearings/judge/day out of 60-90 listed) eligibility for `progress`; the remainder auto-adjourn with cause `causelist_overflow`. Judge leave, court vacations (May, Diwali week), and police-witness unavailability during bandobast all shrink effective slots. Three-plus-year pendency is *emergent* from these numbers (calibrated to NJDG Pune district figures), never scripted.

**BNSS fidelity.** Statutory clocks are just timers with guard effects: chargesheet deadline (60/90 days per BNSS 187/193) sets a timer whose expiry emits `default_bail_eligible`; FIR must-register (BNSS 173) makes `fir_accept` decision default heavily toward accept with `refused` emitting a grievance-escalation case at the parent org (SP office stub). Law changes = data edits.

## 5. The interpreter (core loop)

```python
def institutions_tick(slot: SimSlot):                       # called hourly by Clockwork
    for t in Timer.due(slot):                               # ix_case_due
        fire(t.case_id, t.transition_id, trigger="timer", slot=slot)

    for org in Org.open_with_work(slot):                    # operating hours ∩ active cases; dormant orgs skipped
        budget = capacity_hours(org, slot)                  # Σ present role-holders (Population duty schedule) × slot
        for case in org.queue_by_priority():
            if budget <= 0: break
            tr = eligible_transition(case, org, slot)       # guards: docs, resources, role presence, calendar
            if tr is None: continue
            budget -= tr.effort
            fire(case.case_id, tr.id, trigger="work_slot", slot=slot)

def fire(case_id, transition_id, trigger, slot):
    case, tr = load(case_id, transition_id)
    if tr.decision_point and needs_llm(case):               # focal / director-flagged / high-stakes score
        minds.enqueue(DecisionRequest(case_id, tr.decision_point, options, deadline=slot+Δ))
        return                                              # case waits; on timeout → default sampling
    rng = pcg64(stable_hash(case_id, case.seq))
    outcome = sample(tr.outcome_dist, rng) if tr.outcome_dist else "done"
    with db.transaction():                                  # atomicity = canon + conservation safety
        apply_effects(case, tr, outcome)                    # advance state, inc vars, write docs,
                                                            # ledger transfers, acquire/release resources,
                                                            # set timers, spawn linked cases, emit events
        append(case_event); update(case.next_timer_at)
```

Complexity: O(due timers + active cases in currently-open orgs). Old City steady state ≈ 2-5k open cases → milliseconds per tick. Full Pune scales linearly in *activity*, not in org count.

**Effect vocabulary** (closed, validated set — this is where all cross-subsystem coupling lives):
`advance_stage, set_var, inc, doc, serve_to (→ Information subsystem delivers summons/notice), transfer (ledger), acquire_resource / release_resource, timer, cancel_timer, spawn_case / spawn_case_if (cross-org cascades), emit (world event bus), scene_hook (Minds), notify_org (router-resolved intimations, e.g. hospital→police MLC), rebind_position (elections, transfers, suspensions), set_param (org params, e.g. budget weights), price_surge (zone/sku), close_case`.

## 6. Router (service discovery)

```python
def route(need: Need) -> list[OrgOption]:
    # Need = {kind, location, severity, attrs, subject_refs}
    rows = service_registry.lookup(need.kind)               # ranked
    opts = []
    for r in rows:
        orgs = resolve_jurisdiction(r, need.location)       # STRtree polygon | nearest_k by travel_time
        for o in orgs:
            opts.append(OrgOption(o.org_id, r.procedure_id,
                eta=traffic.travel_time(need.location, o.location),
                cost=fee_estimate(o, r.procedure_id),
                queue=o.current_queue_len(), quality=o.params.quality_prior))
    return opts     # chooser (Minds for focal, utility rule for background) picks one
```

`crime_report` → containing-polygon police station (mandatory). `medical_emergency` → nearest-k hospitals with casualty capability; the Sassoon-vs-private choice is the agent's (cost/queue/quality trade), not the machine's. `permit:pandal` → PMC ward office + police NOC as a *linked* spawned case (single-window = one parent case, two child cases). Adding "RTO licence" or "ration card" someday = registry rows + a procedure def; zero engine code.

## 7. Economy

**Payroll**: each employer org has a monthly `payroll` procedure — one timer, N transfers (org account → person/household accounts). Government payroll debits `WORLD`. PMC is funded by property-tax cases (annual invoice per household, delinquency emergent) + WORLD grants; its `params.budget_weights` (per-ward, per-head: roads/drainage/water) gate the capacity of PMC works procedures — which is exactly what the 2026 election perturbs.

**Consumption at LOD-0 (statistical commerce).** Unwatched commerce must not generate per-purchase rows. Per sim-day, per zone: one batched job takes each household's daily spend vector (budget model owned by Population; priced from `price[zone]`), executes `household → zone_retail_pool` transfers (batched, one row per household per day), then splits the pool to member shop accounts by footfall weight and runs shop payroll/restock (`shop → WORLD` wholesale). Conservation holds exactly; cost is O(households + shops) cheap rows, no LLM.

**Promotion/demotion (canon backfill).** When attention touches a shop/employer (user zooms; an event names it; a wedding buys from it), it is promoted to `detail_level ≥ 1`: staff are lazily generated via Population (`ensure_person(role, ward_stats)`), and its recent books are *backfilled* itemized consistent with its pool-share history — canonical thereafter. On attention decay it returns to aggregate flow; its canon facts persist and constrain any later backfill.

**Labor market**: `vacancy` rows + `labor_market.search(profile) → ranked vacancies` (skill match, wage, commute time via Traffic). Hiring/firing are procedures on the employer org (`hire`, `layoff` — with notice timers, final-settlement transfer). Informal work is `wage_period='daily'` employment with same-day payroll — the naka labor market is a `nearest_k` registry entry.

**Credit**: `moneylender` and `bank_branch` org types run a `loan` procedure: disbursal transfer → EMI timers → `missed_emi` outcomes → `default` state → effects: collection scene hooks, collateral events. Interest is modeled as extra EMI transfers to the lender (conserved: borrower pays it from income; no money creation in v1 — WORLD stands in for the banking system's balance sheet).

**Prices**: real Pune price book seeded per sku/zone; `price_surge` effect (bounded multiplier, auto-decaying timer) models wedding/festival demand spikes. No general-equilibrium model in v1 — surge rules + conservation give believable local dynamics at near-zero cost.

## 8. LLM boundary (DecisionPoints & scene hooks)

```python
@dataclass
class DecisionRequest:
    case_id: str; decision_id: str; org_id: str
    position_id: str                      # → employment → person_id → that person's mind
    options: list[OptionSpec]             # each: id, plain-language consequence summary, default_prob
    context_refs: list[CanonRef]          # case docket, subject facts, org norms
    deadline_slot: int                    # unanswered → engine samples the default distribution
# Minds returns Choice(option_id, rationale_text); engine validates option_id ∈ options,
# stores rationale as canon color, applies effects itself.
```

Properties: (a) the LLM cannot invent outcomes — it selects among machine-enumerated options, so canon, statute, and conservation are engine-enforced; (b) cost control is structural — background cases *never* generate DecisionRequests (defaults sample calibrated distributions), so institutional LLM spend is proportional to user attention, not city size; (c) personality flows in naturally — the SHO's mind, biased by mood/relationships/gossip beliefs, picks among the same lawful options a fair one would, producing corruption-shaped or diligence-shaped trajectories without any special mechanism. `scene_hook` effects hand a scene brief (participants, setting, stakes, allowed mechanical outcomes) to Minds; the scene's mechanical result re-enters via `submit_action(case_id, action, actor, payload)` — same validation.

## 9. Ephemeral organizations (the generality workhorse)

A wedding, a Ganeshotsav mandal, a campaign office, a strike committee, a chit fund — any temporary purposeful collective — is an org with `is_ephemeral=1`, an expiry, an account, roles (host, treasurer, priest, volunteers), and procedures (procure, permit, conduct_event, settle). It hires short-term labor through the same labor market, buys through the same shops, files permits as ordinary PMC/police cases, and emits the same events (road_closure, crowd_load, noise). When it expires, its account settles to its funder and its canon persists. This one feature covers S4, S8, and unforeseen collective situations (a building society fighting a builder, a relief committee after S3 floods) with zero new code.

## 10. Sensitive-dimension policy

Procedures and the router never read religion/caste attributes; jurisdiction is geographic, fees are income-tested only where the real rule is (Sassoon's nominal charges). Ward-level statistical seeding (owned by Population) sets employment/wage priors; institution text surfaces (documents, scene briefs) carry occupational and procedural facts only. Temple-trust affiliation keys on deity/locality, not on simulated persons' identities.

## 11. Calibration anchors (read-only)

NJDG Pune district: pendency/dwell distributions, adjournment rates → court `outcome_dist`. NCRB + Pune Police crime review: FIR volumes per station, chargesheet rates, conviction rates. Sassoon: ~1,296 beds, casualty throughput. PMC citizen charter: complaint SLA targets (used as the *promise*; delivery rates set worse, per audit reports). PMC budget docs: per-ward works spending → `budget_weights` priors. Court calendar: real vacation/holiday list. All loaded as data files under `D:\Coding_Workspace\pune-sim\data\anchors\`.

## Key decisions

- **One declarative Procedure interpreter (JSON state machines + closed effect vocabulary) instead of per-institution Python code** — Generality is the prime directive: a new institution or process is authored as data (org_type row + procedure_def rows + registry rows), validated by Pydantic, and runs on the same tested ~400-line engine; canon persistence, timers, RNG streams, and effort budgets are implemented once. Also ideal for a solo dev with AI assistance — authoring JSON templates is safer than authoring stateful code.
  - Rejected: Hardcoded classes per institution (PoliceStation, Court, ...) or the `transitions` FSM library — both would fragment persistence/replay logic, invite special-casing, and make cross-institution effects (spawn_case, notify_org) ad hoc.
- **Strict double-entry ledger with a single explicit WORLD boundary account** — Money conservation becomes a checkable invariant (sum of balances is constant; nightly audit) rather than a hope; every wage, fee, fine, purchase, donation, EMI is the same transfer primitive; external flows (state salaries, wholesale, remittances) are honest boundary crossings instead of hidden money printing.
  - Rejected: Per-agent wallets with ad hoc credits/debits — unauditable, drifts, and makes S5-style debt arcs incoherent.
- **Statistical commerce at LOD-0 via zone retail pools, with canon backfill on attention promotion** — Simulating every kirana purchase for 12k (later 1.2M) households is pointless cost; batched household→pool→shops flows preserve exact conservation at O(households+shops) rows/day, while promotion backfills itemized, canon-consistent books only when attention arrives — same philosophy as lazy person generation.
  - Rejected: Full per-purchase simulation (unaffordable at city scale) or pure narration without ledger effects (breaks conservation and S5 debt arithmetic).
- **LLMs act only through option-constrained DecisionPoints and scene hooks; engine applies all effects** — The model can never contradict canon, statute, or the ledger because it only selects among machine-enumerated options whose effects the engine executes; background cases sample calibrated defaults with zero LLM cost, so institutional inference spend scales with user attention, not city size; role-holder personality still matters because the mind biases the choice.
  - Rejected: Free-form LLM adjudication of case outcomes — canon-unsafe, unreproducible, and cost scales with case volume.
- **Court pendency is emergent from cause-list slot scarcity + calibrated adjournment distributions + real vacation calendar, not scripted delays** — Produces NJDG-realistic 3+ year arcs mechanically, and creates free cross-couplings (judge leave, Ganeshotsav bandobast pulling police witnesses, custody-first priority) that a scripted delay table could never generate.
  - Rejected: Sampling a total case duration up front and back-filling hearings — no sensitivity to world state, breaks when the user perturbs the system.
- **Ephemeral organizations (is_ephemeral flag + expiry + settle-out) for weddings, mandals, campaign offices, relief committees** — Temporary purposeful collectives are the long tail of life situations; making them first-class orgs means they get accounts, permits, hiring, purchases, and event emission through the exact same machinery — S4 and S8 need zero new code.
  - Rejected: A separate 'social event' system parallel to organizations — duplicated ledger/permit/labor logic and a standing source of special cases.
- **The 2026 election is a Procedure on a singleton election-commission org whose terminal effects are rebind_position and set_param(budget_weights)** — Proves the abstraction at the largest institutional scale: phases are states, nominations are child cases, turnout/vote-share are calibrated outcome distributions modulated by the sim's own complaint-SLA and salience data, and the political consequence is an ordinary parameter change that downstream PMC procedures already read.
  - Rejected: A bespoke election module — would duplicate timers/phases/events and would not feed consequences back through general channels.
- **SQLite + Pydantic + shapely + numpy; no Postgres, no ORM, no workflow engine** — Solo dev on Windows, deterministic replay requirements, modest write rates (O(active cases)); SQLite WAL in one file is debuggable and fast; Temporal/Celery-class workflow engines solve distributed-systems problems this sim does not have and would wreck sim-time determinism.
  - Rejected: Postgres (ops burden, no benefit at this scale), Temporal/Prefect (wall-clock oriented, non-deterministic, heavy).

## Interfaces

- **World Event Bus / Events subsystem**: Consumes: on_world_event(EventEnvelope{kind, at, location, severity, subject_refs}) → router.route() → open_case(...) (e.g., accident → ambulance dispatch case + hospital admission case; flood → PMC complaint cases). Emits: InstitutionEvent{kind ∈ chargesheet_filed, hearing_scheduled, hearing_adjourned, verdict, admitted, discharged, permit_granted, road_closure, layoff, payday, result_declared, fee_overdue, ...; case_id, org_id, subject_refs, payload} for propagation to gossip, news, traffic, and minds.
- **Population / Persons**: Calls: ensure_person(role_constraints, ward) → person_id (lazy staffing); apply_treatment(person_id, treatment_spec) → HealthOutcome (hospital procedures never own health state); household_daily_spend(household_id) → spend vector for LOD-0 commerce. Provides: post_vacancy/hire/end_employment; labor_market.search(profile) → ranked vacancies; duty_blocks(person_id, day) → [TimeBlock] exported to the Clockwork schedule layer (role-holders are AT their org); payroll transfers land on household accounts.
- **Minds / Scenes**: Emits: DecisionRequest{case_id, decision_id, position_id, options[OptionSpec], context_refs, deadline_slot}; expects Choice{option_id ∈ options, rationale} (validated; timeout → engine samples default). Emits: scene_hook(case_id, scene_type, participants, setting_ref, stakes, allowed_outcomes); scene results return via submit_action(case_id, action_id, actor_person_id, payload) which the engine validates and applies.
- **Canon DB**: Writes: case_event rows and document rows registered as immutable canon facts keyed by entity refs (person, org, case). Reads: facts_for(entity_ref) to assemble DecisionRequest/scene context. case_ + case_event + document tables ARE the institutional canon of record; retrieval layer indexes them.
- **Information / Gossip / Media content**: Emits: publishable InformationItem{source_org, credibility, reach, topic, facts} on news publish, notice, result declaration; serve_to effect requests delivery of summons/notices to persons (Information decides how/when they learn). Reads: salience(topic, zone) → float and belief_level(claim, zone) → float, used as modifiers (temple donation demand under S2 rumor; election issue-salience term).
- **Traffic / Geography**: Reads: travel_time(a, b, mode) for router ETAs and ambulance/commute estimates; location_ref resolution to OSM nodes. Emits: road_closure(way_ids, window, cause_case_id) (pandal permits, processions, court-ordered works) and crowd_load(zone, magnitude, window) (bandobast, festivals, rallies) for the clockwork traffic layer.
- **Clockwork scheduler / Calendar**: Provides institutions_tick(slot) called hourly; registers calendars (court vacations, school terms, festival closures, election dates) via calendar_register(calendar_id, rules); exposes is_open(org_id, slot). Deterministic: identical inputs + seeds → identical tick outputs.
- **Economy consumers (all subsystems)**: transfer(debit_acct, credit_acct, amount_paise, memo_kind, case_id?, item?) → txn_id (atomic, conserving — the only way money moves anywhere in the sim); price(sku, zone_or_org) → paise; balance(account_id); account lookup by owner ref.
- **UI / Director**: inspect_org(org_id) → OrgView{staff, queues, ledger summary, today's causelist/OPD}; inspect_case(case_id) → CaseView{state, docket, next dates, history}; director injections use the public open_case/submit_action APIs with a director flag (no privileged mutation path, so injected events obey the same physics); attention signals drive detail_level promotion/demotion.

## Scenario traces

## S1 — Bus crash, 8:10am Shivajinagar (hospital admission on the general engine)
Events subsystem emits `EventEnvelope{kind: road_accident, severity, subjects:[father, daughter, driver, bus...], location}`. Router: `medical_emergency` → nearest-k hospitals with casualty capability → Sassoon (govt fees, 2.1 km); `crime_report` → containing-polygon → Shivajinagar PS (mandatory). Cases opened: (a) `ambulance_dispatch` on Sassoon's ambulance sub-org — vehicle resource acquired, ETA from Traffic (jam raises it — visible consequence); (b) per-casualty `emergency_admission` cases: triage transition sets priority from the Persons injury model → casualty treatment (doctor role capacity; morning OPD load competes for the same budget — realistic Sassoon crowding is emergent) → `acquire_resource: bed` → daily-round timers calling `apply_treatment` → discharge with a nominal-fee transfer. The admission procedure's declared effect `notify_org(police, mlc_intimation)` — a statutory coupling in data, not code — spawns the MLC case; (c) Shivajinagar PS opens `fir_bnss`: FIR document (canon), IO assigned (capacity slot), truck driver arrested (`lockup_cell` resource), BNSS 187 chargesheet timer set. School absence and parental panic are downstream of the emitted `admitted` events reaching Minds/Information — my subsystem just publishes facts. Every step used: router, cases, resources, capacity, timers, effects, transfers.

## S6 — The truck driver's case, 3+ years at Shivajinagar JMFC (same engine, slow parameters)
The S1 FIR case runs `fir_bnss`: investigation work-slots consume IO capacity over weeks; witness-statement effects create scene hooks only if focal; the 60/90-day BNSS timer either sees `doc: chargesheet` filed (calibrated ~78% for accident cases) or fires `default_bail_eligible`. Chargesheet effect: `spawn_case(org: jmfc_shivajinagar, procedure: criminal_trial_jmfc, upstream: fir_case)`. The court org then simply ticks: `admit` → summons served (Information delivers; accused's mind learns a hearing date — it enters his schedule). Each hearing is a timer + causelist-slot draw: with ~62% adjournment and ~12-18 effective slots per judge-day against 60-90 listed, the case accrues 25-40 hearings; May vacation and Diwali weeks skip sittings (calendar guard); during S8's Ganeshotsav bandobast the IO-witness is on duty posts, so `witness_absent` probability rises that fortnight — cross-scenario coupling with zero special code. Stage advances (charges framed → 4 prosecution witnesses examined one hearing at a time → arguments) until `judgment`: a DecisionPoint — background default samples calibrated conviction odds; if the user has been following the driver, the judge's mind (T3) chooses among {convict, acquit} with the docket as context. Verdict document is canon; `spawn_case_if convicted` → jail stub; the family's 3-year ordeal is just the emitted hearing events reaching their minds. Pendency was never scripted — it fell out of slot scarcity.

## S7 — 2026 PMC ward election (largest scale, same five primitives)
Singleton org `sec_maharashtra` (citywide jurisdiction) opens master case `ulb_general_2026` + one `ward_contest` child case per electoral ward. States: announced → nominations (each candidate files a `nomination_scrutiny` case — some corporators seeking re-election are current role-holders in the PMC org) → campaign: work-slots emit rally/padyatra events (Traffic gets crowd_load; Minds get canvassing scenes; candidates' duty_blocks change) while the Model Code of Conduct is a `set_param` effect that guards PMC discretionary-works procedures (visible: pothole complaints stall — voters notice via Information) → polling: per-ward turnout draw (base prior × weather × salience) with booths as temporary resources of ward offices → counting: vote share = softmax(party_prior_ward + incumbency_term × unresolved_complaint_index + candidate_attrs + campaign_intensity + shock). The `unresolved_complaint_index` is computed from MY OWN complaint-case SLA data — if S3's flood complaints rotted in queues, the incumbent's number is worse, mechanically. Terminal effects: `rebind_position(pmc, corporator, ward_k, winner_person_id)` and `set_param(pmc, budget_weights, from_manifesto_mix)`. The losing corporator (probe requirement) is one draw; the "civic priorities shift" is that PMC works procedures thereafter read different budget_weights — drainage cases in flood-hit prabhags get more capacity. Election = procedure + timers + outcome_dists + two ordinary effects.

## S5 — Job loss and debt spiral (pure economy primitives)
Employer org (a Raviwar Peth wholesale trader, promoted to detail when the arc starts) runs `layoff`: notice timer, final-settlement transfer, `end_employment` — emitting `layoff` events (gossip fodder). The household's monthly inflow stops (payroll simply no longer targets it — no special state). Population's budget model cuts the daily spend vector; LOD-0 commerce automatically shows the kirana shop extending credit only if promoted (scene). School org's recurring `fee_invoice` case hits `overdue` → dunning-notice effect → parent-teacher scene hook (Minds). The worker uses `labor_market.search` weekly (naka daily-wage vacancies match; wage_period='daily' payroll pays same-day). A `moneylender` org's `loan` case: disbursal transfer, EMI timers, `missed_emi` outcomes escalating to `default` → collection scene hooks (family-tension raw material for Minds). Recovery = a vacancy match at a rebuilding wedding-catering ephemeral org (S4 spillover) or a new permanent employment row; spiral = default effects + asset events. Every beat is a transfer, timer, or case outcome; months of arc cost zero LLM except focal scenes.

## S4/S8 compressed — ephemeral orgs prove the long tail
Wedding (S4): `event_org` instantiated with account funded by the household; `procure` transitions execute real purchases at Old City shops (price_surge effect bumps the zone's flower/catering skus, decaying by timer); `permit` spawns a PMC pandal case whose grant effect emits `road_closure(lane, 3 days)` to Traffic; short-term catering employments hire through the labor market; org expires and settles. Ganeshotsav (S8): dozens of mandal `event_org`s file the same permit/NOC case pairs; police stations run `bandobast` — a procedure whose effect is `acquire_resource: constable-shifts` for duty posts, which *reduces* FIR-processing and court-witness capacity for 10 days (felt in S6); crowd_load and closure events drive Traffic; temple/mandal donation transfers spike on the festival calendar. Neither scenario has any dedicated code — both are template instantiations plus standard cases.

## Generality argument

The subsystem is general because its extension points are all data, and its primitives were chosen to be situation-agnostic. (1) Any organization — permanent or ad hoc, governmental or commercial or religious — is an OrgType template: roles, capacity, resources, hours, jurisdiction, fees, procedures. An RTO, a ration shop, a housing society, a chit fund, a coaching class, or a disaster-relief committee is a new template row, not new code; the ephemeral-org flag covers temporary collectives (weddings, mandals, strike committees) that other designs would special-case. (2) Any bureaucratic, medical, legal, commercial, or political process is a Procedure: states, guarded transitions, effort costs, sim-time timers, calibrated outcome distributions, and a closed effect vocabulary. Because effects include spawn_case, notify_org, emit, rebind_position, and set_param, arbitrary cross-institution cascades (hospital→police MLC, chargesheet→court, election→budget weights→works capacity) compose declaratively — unforeseen couplings are authored, not engineered. (3) Any value flow is a conserved double-entry transfer, so novel economic situations (dowry, hafta, crowdfunding a surgery, election spending) already have a primitive with an audit invariant. (4) Any "where do I go for X" question routes through the registry + jurisdiction rules, so new needs are rows. (5) The LLM boundary is uniform: every discretionary moment in any institution is a DecisionPoint with enumerated options and a calibrated default, so a corrupt clerk, a lenient judge, or a crusading editor are the same mechanism under different minds — and cost scales with attention, not with city size or scenario count. The probe set spans acute (S1), slow (S5), institutional (S6), political (S7), and mass (S8) time-scales, and all reduce to the same five primitives with different parameters; a scenario nobody predicted (say, a building collapse triggering PMC audits, arrests, court cases, and a compensation fight) decomposes the same way: events route to orgs, orgs open cases, cases follow procedures, procedures move money and emit consequences.

## Open questions

- Household consumption ownership boundary: the design assumes Population owns the budget/spend-vector model and Institutions owns prices and execution — the exact API split (who decides to skip a meal vs. who prices it) needs joint sign-off with the Population subsystem designer.
- Credit realism depth: v1 treats banks/moneylenders as conserving intermediaries with WORLD as the banking system; is emergent local credit (shopkeeper udhaar ledgers as micro-loan cases everywhere) worth the row volume, or should informal credit stay a promoted-detail-only feature?
- Appeals and out-of-area escalation: Sessions Court, High Court, MAT, and Yerawada jail are 'world stub' orgs (cases enter, sampled outcomes return after calibrated delays). Where exactly to draw the stub boundary as the sim area grows toward full Pune?
- Adjournment cause calibration: NJDG gives pendency and disposal aggregates but not cause-of-adjournment distributions; the 62%/cause-mix numbers are informed estimates from legal-empirics literature (DAKSH studies) and need a documented calibration pass with sensitivity checks.
- Corruption representation: the DecisionPoint mechanism lets biased minds pick lawful-but-slanted options, but explicit illegal transfers (bribes, hafta) are representable as ledger transfers with a memo_kind — should they exist in v1, and under what guardrails for respectful, non-defamatory generation about real institution types?
- Statutory fidelity budget: which BNSS/BNSS-adjacent clocks beyond FIR/chargesheet/bail (e.g., BNSS 193 supplementary chargesheets, plea bargaining chapter, Lok Adalat settlement days) are worth encoding in v1 vs. leaving as future procedure versions?
- Price dynamics ceiling: surge multipliers + fixed base prices dodge macro-inflation entirely; over a multi-year sim (S6 spans 3+ years) should a slow exogenous CPI drift be applied to the price book and wage revisions, and who owns that clock?
- Election vote-share model validation: the softmax feature set (party prior, incumbency × complaint index, campaign intensity) is plausible but uncalibrated — 2017 PMC results by ward exist as public data and could anchor the priors; needs a data-collection task.

## Red-team critique (verdict: needs_changes)

- **[critical]** The economy has no demand side. LOD-0 splits the zone retail pool by STATIC footfall weight; household spend vectors are priced from the ZONE price book, not per-org prices; the only price mutation is `price_surge` (bounded, auto-decaying, built for demand spikes). Consequently a shop that cuts prices gains zero customers, a shop that loses its footfall (metro opens) keeps its pool share forever, and no org can even decide to change a price because DecisionPoints only exist inside Cases and shops have no standing case. Two of the six holdouts (saree price war, metro commute shift) are unrepresentable at ANY detail level — this is an engine gap, not a data row, directly contradicting the generality thesis.
  - Fix: Add (1) durable per-org prices with a `set_price` effect; (2) a standing `business_review` recurring procedure (weekly timer) for every detail_level>=1 commercial org, whose DecisionPoints cover price/promotion/hiring/close, with competitor price rows and own P&L allowed as context_refs; (3) replace static footfall weights with a cheap logit share recomputed weekly per zone over (price ratio, distance/access from Traffic, quality prior) — the metro then shifts shares mechanically via changed access times; (4) an insolvency watchdog: org balance negative beyond a float for N periods spawns a `business_distress` case (borrow / cut staff / close). All O(shops) per week, no LLM at background.
- **[critical]** No property/asset layer. subject_json carries `assets:[]` refs with no home table; the effect vocabulary can move money and rebind positions but cannot transfer ownership, create a tenancy, or attach a lien. The moneylender procedure's 'collateral events' are therefore fictional; the wada-collapse compensation fight (centrally about pagdi tenancy and who gets the rebuilt flats), chain-snatching (a stolen gold chain as recoverable property/muddemal), dowry, and any civil property suit all silently break. Additionally LOD-0 rent is unspecified: household daily spend goes to the zone RETAIL pool, but rent to a private landlord is not retail, so landlord-tenant relationships are never canonized and must be invented post-hoc under heavy constraints exactly when a collapse makes them load-bearing.
  - Fix: Add `asset` (kind, ref, condition, location) and `asset_interest` (owner / pagdi-tenant / lien / attachment, holder_ref, terms) tables owned by this subsystem (courts, PMC, lenders, and police muddemal all act on them), plus `transfer_asset`, `set_interest`, and `seize_asset` effects in the closed vocabulary. Route LOD-0 rent as explicit household→landlord-account transfers (monthly, one row — cheap) so tenancy is canon from day one. Seed Old City wada ownership/tenancy at boot from ward statistics the same way persons are lazily generated.
- **[critical]** Transfer-row volume at full Pune: 'one row per household per day' is ~1.2M rows/day, ~440M rows/year of pure LOD-0 noise in a single SQLite file — tens of GB per sim-year, degrading the nightly audit, backups, replay, and every index, for rows nobody will ever read.
  - Fix: Coarsen LOD-0 cadence to one household→pool transfer per WEEK (or month), keeping daily granularity only for detail_level>=1 households; add a monthly compaction job that rolls cold transfers into summary rows carrying a conservation checksum, preserving itemized history only for promoted entities and case-linked transfers. Conservation invariant still checks exactly.
- **[major]** The sensitive-dimension policy ('procedures and the router never read religion/caste') is at the wrong altitude and breaks the inter-religious marriage holdout: the actual legal fork — Special Marriage Act 30-day public notice + objection window vs personal-law marriage — is religion-indexed BY STATUTE. The machine cannot route the couple to the SMA registrar, run the objection window, or represent a temple trust's refusal, without reading the attribute, so the scenario forces either a policy violation or hollow special-casing where Minds smuggles the logic past guards that cannot check it.
  - Fix: Replace the blanket ban with an audited allowlist: attribute reads are permitted only where the real statute or institutional rule is explicitly indexed on them (marriage-law routing, legally mandated admission categories), declared per-procedure in the template, logged on every read, and reviewed. Keep the hard ban for discretionary DecisionPoints (bail, triage, FIR acceptance) so bias can only enter through Minds, where it is a character trait, not a mechanic.
- **[major]** detail_level conflates canon fidelity with LLM focality (level 2 = focal), inviting attention cascades and cost blowups: zooming into Ganeshotsav promotes dozens of mandal event_orgs, whose permit/procure cases all start generating DecisionRequests and scene hooks; focal cases spawn child cases whose focality is undefined; nothing caps concurrent focal work.
  - Fix: Separate `focal` from detail_level. Focality is a budgeted resource: a global cap on concurrent focal cases and a per-sim-day token budget; promotion beyond the cap falls back to calibrated defaults (which are always available by design); child cases inherit focality only when the same subjects remain on-screen. Log spend per case for tuning.
- **[major]** Backfill is an unacknowledged constraint-satisfaction problem that gets harder forever: promoted-shop itemized books must be consistent with (a) already-canonical household→pool aggregate transfers, (b) all prior canon facts from earlier promote/demote cycles, and (c) inventory. Attributing a Rs 3,000 saree purchase to a household whose canonical pool transfer that day was Rs 400 is a contradiction the nightly SUM audit will never catch; after years of oscillation, feasible backfills stop existing.
  - Fix: Backfill at counterparty-CLASS granularity only (walk-in retail, wholesale, named-event) with named-household line items created solely when a specific event requires one; hold a slack sub-share in every pool split reserved for retroactive attribution; store backfill provenance so re-promotion extends rather than re-derives; add a consistency check to the nightly audit that reconciles itemized books against pool-share history.
- **[major]** Pooled capacity budget is role-blind: `budget = capacity_hours(org, slot)` sums all present role-holders, so clerk-hours can be spent doing judge work. Since 3-year pendency is claimed to be EMERGENT from judge-slot scarcity, this single bug silently destroys the subsystem's flagship calibration (NJDG realism) while the numbers look plausible in aggregate.
  - Fix: Per-role effort budgets: transitions declare `effort: {role: hours}`; the tick maintains one budget per (org, role); causelist_effective_slots derives from the judge budget alone. Calibrate against NJDG after the fix, not before.
- **[major]** Hourly tick vs minute-scale reality: wada rescue, triage, ambulance runs, and a chain-snatch all unfold sub-hour, but timers and `at` are slot-grained, so canon records 'collapse 21:00, rescued 21:00, admitted 21:00, declared dead 21:00' with no orderable sequence — visible slop the moment a user inspects any acute event, and a contradiction generator for scene prose that narrates minutes.
  - Fix: Make canonical timestamps minute-grained integers and allow minute-resolution timers, while keeping the hourly batch tick for work-slot allocation; within a tick, apply_effects assigns monotonically increasing minutes from the event chain. Cheap change now, schema migration later.
- **[major]** No migration story for in-flight cases across procedure_def versions. Multi-year cases (the entire point of S6) are guaranteed to be alive when the solo dev edits criminal_trial_jmfc; a renamed state or removed transition strands them silently.
  - Fix: Pin every case to (procedure_id, version) at open (add version to case_); the loader keeps old versions live; a linter verifies every pinned version still reaches a terminal state; authoring a new version requires either compatibility or an explicit state-mapping migration script run in a transaction.
- **[major]** The deterministic-replay claim is false for any timeline touched by an LLM: PCG64 streams make defaults reproducible, but Choice results and scene submit_actions are external nondeterminism, and 'identical inputs + seeds → identical tick outputs' (Clockwork contract) doesn't hold.
  - Fix: Journal every DecisionRequest→Choice and every submit_action in an append-only decision log; replay mode injects from the journal instead of calling Minds. This also gives free regression testing: replay a season, diff case_event streams.
- **[major]** Statistical/narrative whiplash on attention decay: a focal hearing scene where the judge visibly shreds the prosecution, followed (once the user looks away) by a default-sampled conviction at the flat 0.27 prior. Long arcs that drift in and out of focus — the design's normal mode — will feel incoherent, which is exactly the LLM-slop failure the sim must avoid.
  - Fix: Let scene outcomes write bounded modifier vars on the case (e.g., prosecution_strength, judicial_disposition) that shift subsequent outcome_dists via declared multipliers in the procedure JSON. Engine-enforced bounds keep canon safe; narratively-established facts then bend the statistics instead of being overwritten by them.
- **[major]** No liveness guarantee and no semantic validation of authored procedures: a case with no pending timer and a guard that can never be satisfied (doc never arrives, resource never freed) stalls forever silently; Pydantic validates shape, not reachability, deadlock, or timer leaks — and the dev is authoring dozens of these JSONs with AI assistance, the exact recipe for subtle dead machines.
  - Fix: Build (1) a static procedure linter: terminal reachability from every state, no orphan timers on close, every guard's doc/resource kinds producible by some effect in scope; (2) a nightly liveness audit: open case with no timer and no eligible transition for N sim-days raises a flag; (3) property-based tests per procedure (random event storms; assert conservation + eventual termination or bounded pendency).
- **[minor]** '~400-line engine' and 'no institution has bespoke code' are already false in the document itself: the cause-list mechanic, triage priority, and `priority_policy` are per-org-type scheduling algorithms, and guards/conds ('state=prosecution_evidence', when-clauses, spawn_case_if conds) are an untyped expression DSL needing its own parser and tests. Underscoping this misleads the build plan — real engine size is 3-5k lines plus a test harness, and the JSON authoring + calibration effort dominates the schedule.
  - Fix: Own it: formalize scheduling policies as a small declared plugin interface (name → registered Python strategy, ~5 of them) instead of pretending they are data; spec the guard DSL grammar explicitly (a dozen typed predicate forms, no eval); re-plan the solo-dev schedule around procedure authoring + calibration as the critical path with golden-scenario tests per procedure.
- **[minor]** case_.next_timer_at is a denormalized min over multiple concurrent timers (chargesheet deadline AND next hearing coexist on one case); every timer set/cancel must maintain it correctly or timers fire late/never — a classic silent-drift bug.
  - Fix: Drive the due-scan from the timer table directly with an index on (fire_at) and drop next_timer_at, or maintain it via a SQLite trigger on timer insert/delete so it cannot desynchronize.
- **[minor]** Non-person, non-org actors have no subject primitive: subject_json holds persons/assets/cases, so a stray dog (holdout), a hazardous tree, an open drain, or a persistent dog pack behind the school has nothing to reference — the PMC dog-catching case and the recurring-menace texture need an entity to attach condition and history to.
  - Fix: Fold hazards/animals into the new asset/entity registry from the property fix (kind: animal|hazard, location, condition, history via case links); PMC survey and abatement procedures then reference them like any subject. One table serves both gaps.
- **[minor]** Negative balances are semantically undefined: no constraint or policy says which owner_kinds may go negative or what happens when they do, so shops, households, and even orgs can drift arbitrarily negative while payroll and restock keep firing — unauditable un-realism that also masks the missing insolvency loop.
  - Fix: Declare per-owner-kind overdraft policy in data (households: none — spend vector must clamp; orgs: float up to X months payroll then business_distress case; WORLD: unbounded by definition); enforce in the transfer primitive and alert in the nightly audit.

### Novel holdout-scenario traces

HOLDOUT 1 — PRICE WAR BETWEEN TWO LAXMI ROAD SAREE SHOPS (chosen because it attacks the economy's core LOD-0 mechanism head-on).
Trace: Both shops are members of the Laxmi Road zone retail pool. Step 1, initiation: shop A's owner decides to undercut shop B before wedding season. WHERE does this decision live? DecisionPoints exist only inside Procedures attached to Cases, and a shop has no standing case — there is no org-initiated strategy loop anywhere in the design. Already stuck: the design must special-case a recurring "business review" case per shop, which exists nowhere. Step 2, the act: the only price primitive is `price_surge` — a bounded multiplier with an AUTO-DECAYING timer, built for demand spikes. A sustained competitive cut has no effect in the closed vocabulary (`set_price` doesn't exist), and any cut expressed as negative surge reverts by itself. Step 3, competitor response: shop B must observe A's price. Guards and context_refs reference the case's own docket/vars/resources; there is no primitive for org A reading org B's price row. Step 4, the fatal one — demand: at LOD-0 the pool splits by STATIC footfall weight with no price term, so A's cut gains zero customers; even at detail_level 2, household spend vectors are "priced from price[zone]" — the zone book, not per-org prices — so watched shops don't get price-sensitive demand either. The price war is mechanically irrational and invisible at every LOD. Step 5, consequences: margin compression should produce losses, layoffs, and possibly one shop's death — but shop accounts can go negative without bound, nothing watches viability, and org closure has no driver (`status='closed'` exists with no procedure that sets it). The social texture (trader gossip, the merchants' association brokering a truce as an ephemeral org, a Sakal story) routes fine through Information and event_orgs — the machine breaks precisely in the economic core the subsystem owns. Verdict: unrepresentable without engine changes (durable per-org prices + set_price effect, standing strategy procedure, competitor observation in decision context, price-elastic pool shares, insolvency loop). This is not "a new template row"; it falsifies the generality claim for the whole commercial half of the design, and the metro-station holdout breaks on the identical static-footfall gap.

HOLDOUT 2 — DECREPIT WADA PARTIALLY COLLAPSES IN THE RAIN WITH FAMILIES INSIDE (chosen because the design's own generality argument explicitly claims building collapse decomposes cleanly — calling that bluff).
Trace: Events emits building_collapse{location, subjects}. Step 1, rescue: fire brigade is an authorable OrgType (fine), but the rescue unfolds over minutes-to-hours while timers and canon timestamps are hour-grained — trapped, rescued, admitted, and declared dead all stamp the same slot, unorderable; the scene layer narrates minutes the canon cannot hold. Step 2, casualties: per-casualty emergency_admission at Sassoon, MLC auto-intimation, FIR against the landlord — all route beautifully on the S1 pattern; this part genuinely validates the machine. Step 3, the landlord — who IS the landlord? Residential property ownership, pagdi tenancies, and building condition exist in NO table of this subsystem; `rent` is a memo_kind but LOD-0 household spend goes to the zone RETAIL pool, and rent to a private landlord is not retail, so tenancy relationships were never canonized. At collapse time the sim must retroactively invent the owner, decades of frozen-rent tenancies, and prior repair disputes under whatever constraints existing canon imposes — a worst-case backfill. Step 4, the PMC arc that makes it feel real: Pune's actual pre-monsoon dangerous-structure notices (the news line "PMC had served notice in 2023" is the whole story) require building-stock entities with condition state that PMC survey cases reference — authorable procedures, but no entity to attach them to; subject_json's assets:[] are refs into a table that doesn't exist. Step 5, displacement and relief: PMC transit camp and a relief committee as ephemeral orgs, CM-fund WORLD→household transfers — the ephemeral-org primitive covers this well. Step 6, the compensation fight — the probe's heart: tenants vs landlord litigation over tenancy restoration and rebuilt-flat rights. The civil suit is an authorable court procedure, but its SUBJECT is property interests, and its terminal effects need to move ownership/tenancy — the closed effect vocabulary can move money and rebind positions but has no transfer_asset/set_interest, so the judgment cannot execute its own remedy. Verdict: routes ~70% impressively (rescue→hospital→FIR→relief→litigation scaffolding), then hard-breaks on the missing property/asset/tenancy layer, LOD-0 rent flows, hour-grained acute timelines, and effect-vocabulary asset operations — four silent special-casings, two of them (assets table + asset effects) engine-level.