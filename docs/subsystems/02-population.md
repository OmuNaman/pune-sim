# 2. Population & canon

## Summary

Population & Canon is designed as two coupled layers: a deterministic, regenerable statistical population (D0) that exists as a pure function of a world seed plus calibration tables, and a touch-activated canon database (SQLite) that permanently records every fact ever surfaced about a person, household, or place. Synthesis uses a generative "household grammar" (conditional sampling that can only produce structurally valid Indian urban households) calibrated to ward Census-2011 marginals via IPF reweighting — chosen over raw IPF/IPU (no ward-level seed microdata for India; integerization breaks household coherence), Gibbs (slow, hard to validate, overkill at 12k households), and copulas (built for continuous marginals, not relational categorical structure). Workplaces/schools/shops are assigned with doubly-constrained gravity models over OSM places, producing the affiliation graph that gossip, commuting, and institutions all consume. Demographic dynamics run as aggregate ward-cohort Poisson draws (births, deaths, marriages, migration, job separation) that are lazily attributed to individuals; the identical rate tables run *backwards* ("retro-history sampling") to generate a statistically consistent life skeleton the moment attention first reaches a person, so LLM biography calls decorate structure rather than invent it. Canon is a bitemporal subject-predicate-object fact store governed by a predicate registry (cardinality, value schema, mutability, sensitivity); LLM output enters canon only through assert_facts(), which enforces supersede-via-event rules, referential integrity, temporal validity, and entity resolution, with a one-retry repair loop. Frozen households are the default state; rehydration replays aggregate hazards over the gap subject to "pins" (any fact about them created mid-gap), unifying LOD-over-time with lazy generation under one mechanism. Sensitive attributes (religion, caste-category, community) live behind a firewall: they condition structured facts (names, festivals observed, language) but are never emitted into LLM prose prompts, and the canon linter rejects outputs containing caste terms. Everything other subsystems need arrives through six APIs: get_person, ensure_detail, context_pack, assert_facts, mint_person/resolve_entity, and query_population, plus a demography.tick event feed.

## Design

# POPULATION & CANON — Detailed Design

## 0. Core principle: Lazy Realization with Pinning

The whole subsystem reduces to one invariant:

> **A person's untouched past and untouched present are always *sampled retroactively at first touch*, conditioned on (a) aggregate cohort trajectories and (b) any pins — facts about them already in canon. Once sampled, a fact is canon forever.**

This single mechanism covers: initial biography generation, frozen-household catch-up, minting strangers named by an LLM, and scaling from 50k to 3.5M people (untouched people cost zero storage and zero LLM tokens; they are a deterministic function of the world seed).

Two layers:

- **D0 statistical layer**: columnar arrays (parquet via pyarrow, loaded as numpy struct-of-arrays). ~50k persons / 12k households at start; scales to 3.5M because rows are 40–60 bytes and fully regenerable from `world_seed`. No SQLite rows exist for a person until first touch.
- **Canon layer**: SQLite (WAL mode) storing facts, narratives, relationships, provenance for *touched* entities only. Canon is append-mostly; facts are superseded, never deleted.

Determinism: all randomness uses `numpy.random.Generator(PCG64)` with derived seeds: `seed(entity, purpose) = blake2b(world_seed || entity_id || purpose)[:8]`. Re-running synthesis or retro-history with the same seed and same pins yields identical output — this makes D0 regenerable and bugs reproducible.

---

## 1. Synthetic population generation

### 1.1 Method choice: IPF-calibrated generative household grammar

**Rejected — raw IPF/IPU on a seed sample:** IPF/IPU require representative seed microdata at or near the target geography. India publishes Census tables, not microdata; IHDS-II (2011-12 urban Maharashtra, ~n=2k urban HH) is the only public option and is too sparse to seed 12k households in 4 specific Peths without massive cloning artifacts (identical households repeated). IPF integerization (rounding fractional weights) also produces structurally broken households (a 6-year-old household head, a wife 40 years older than listed children) unless heavily post-processed.

**Rejected — Gibbs sampling / simulated annealing (combinatorial optimization):** converges slowly, validation is opaque, and its advantage (matching many cross-tabulated controls simultaneously) is moot because Indian ward-level data only gives univariate marginals anyway.

**Rejected — copulas:** designed to couple *continuous* marginals through a dependence structure; our variables are categorical, hierarchical (relation-to-head depends on household type), and *relational* (members of one household constrain each other). Copulas have no natural way to emit a coherent household.

**Chosen — generative household grammar + IPF reweighting:**

1. A conditional sampler that can only emit *valid* households: draw `household_type` (nuclear / joint-patrilocal / single / couple-elderly / single-parent / unrelated-adults-mess / …, ~14 types with urban-Maharashtra priors from IHDS-II + NFHS-5 tabulations), then size, then members top-down: head(age,sex) → spouse(age gap distribution) → children(count from parity tables given mother age, ages from birth spacing) → coresident parents/siblings. Structural validity is guaranteed by construction.
2. Generate an oversized pool (~3× target) of candidate households per ward.
3. **IPF (`ipfn` package)** reweights the pool to match ward marginals simultaneously on: household-size distribution, age×sex pyramid (5-yr bands), SC/ST share, literacy by sex, worker/non-worker by sex, 0–6 population. Integerize with the truncate-replicate-sample method — safe here because units are whole *valid households*, so integerization can't break structure.
4. Post-fit conditional draws for attributes absent from ward marginals: religion (town-level Census C-1 shares adjusted by documented locality priors per Peth, provenance=`estimate`), mother tongue (C-16), community (coarse: General/OBC/SC/ST/NT-DNT with state-level urban estimates, wide-uncertainty flag), household income band (7 bands, fit to consumption distribution NSS urban Maharashtra, correlated with education/occupation of head via ordered logit), ration card type, vehicle ownership.

**2011→2026 scaling:** multiply ward marginals by ward growth factors (PMC ELU/DP projections; Old City Peths are near-flat or declining — factor table `ward_growth(ward_id, factor, provenance)`), and age the pyramid forward 15 years with a Leslie-matrix cohort projection before fitting, so the 2026 pyramid is demographically plausible rather than a stretched 2011 one.

**Upgrade path:** if IHDS/NSS microdata is later licensed and cleaned, swap step 1–3 for PopulationSim (ActivitySim ecosystem, list-balancing IPU) without changing any downstream contract — the output schema is identical.

### 1.2 Output schema (D0 columnar, parquet)

```
persons.parquet    : person_id u32 | household_id u32 | age u8 | sex u8 | rel_to_head u8
                     | marital u8 | religion u8 | community u8 | mother_tongue u8
                     | edu_level u8 | occ_class u8 | worker_type u8 | frailty f16
                     | big5 4×u8 (packed) | flags u16
households.parquet : household_id u32 | ward_id u16 | building_id u32 | hh_type u8
                     | income_band u8 | ration_card u8 | vehicles u8 | tenure u8
```
Names are NOT stored at D0 — they are a deterministic function of `(seed(person_id,'name'), religion, mother_tongue, sex, birth_decade, father_surname)` over frequency-weighted name pools (`name_pool(pool_key, given_name, weight)` — Marathi Hindu, Muslim, Navayana Buddhist, Jain, Christian, North-Indian-migrant pools; surname inheritance follows dominant patrilineal convention with a configurable exception rate). Generating a name does not require an LLM and is reproducible, so "everyone has a name" is true without storing 50k strings.

### 1.3 Geographic & institutional assignment (gravity models)

Jobs inventory: OSM POIs + building footprints → `place` records with `jobs_capacity` (floor-area heuristics per land-use class), scaled so ward totals match worker counts. Then a **doubly-constrained gravity model**:

```
T_ij ∝ O_i · D_j · exp(-β · t_ij)     # t_ij = network travel time (from Mobility subsystem's skim matrix)
```
balanced with standard Furness iterations; β calibrated to a target mean commute (~25 min Old City). Individual assignment: sample workplace j for each worker from row T_i·, filtered by occupation-class ↔ place-kind compatibility matrix.

Schools: same form, steeper β, hard capacity constraints (greedy fill by distance rank), age→level mapping, PMC-vs-private split as logit on income band. Shops: each household draws a **habitual choice set** (kirana, vegetable vendor, milk booth, medical store, ration shop [determined by card], temple/mosque/vihara attended) via distance-decayed multinomial. These affiliations ARE the social topology: gossip, queues, and festival participation all traverse them.

All assignments land in the `affiliation` table (canon layer, but bulk-written at synthesis with provenance=`synthesis`) — this is the one D0 dataset that does live in SQLite from day one, because every subsystem joins on it. At 3.5M scale it moves to its own parquet + SQLite index of touched rows; same API.

---

## 2. Canon database (SQLite, WAL, single writer)

**Why SQLite over Postgres:** solo dev on Windows, zero admin, backup = file copy, canon is sparse (facts accrue only for attended entities — thousands, not millions of rows/month), FTS5 built in, sqlite-vec extension for embeddings. Single-writer discipline: all writes go through the Canon service object; the sim is one process. Documented escape hatch to Postgres if multi-process ever needed.

### 2.1 DDL (core tables)

```sql
CREATE TABLE person (            -- row exists only once touched (detail >= D1)
  person_id INTEGER PRIMARY KEY, household_id INTEGER NOT NULL,
  detail_level INTEGER NOT NULL DEFAULT 1,          -- 1..3
  given_name TEXT, surname TEXT, born_date TEXT, sex TEXT,
  frozen_since TEXT, archived INTEGER DEFAULT 0);   -- archived: emigrated/deceased

CREATE TABLE household (
  household_id INTEGER PRIMARY KEY, ward_id INTEGER, building_id INTEGER,
  detail_level INTEGER DEFAULT 0, frozen_since TEXT, snapshot_json TEXT);

CREATE TABLE place (
  place_id INTEGER PRIMARY KEY, osm_ref TEXT, kind TEXT, name TEXT,
  ward_id INTEGER, geom_wkb BLOB, jobs_capacity INTEGER, meta_json TEXT,
  is_anchor INTEGER DEFAULT 0);                     -- anchor rows immutable

CREATE TABLE affiliation (
  person_or_hh_id INTEGER, subject_type TEXT,       -- 'person'|'household'
  place_or_org_id INTEGER, role TEXT,               -- 'employee','student','regular_customer','member','patient','party_in_case',...
  start_date TEXT, end_date TEXT, meta_json TEXT, provenance TEXT);

CREATE TABLE relationship (
  a_id INTEGER, b_id INTEGER, kind TEXT,            -- 'kin','neighbor','coworker','friend','rival','acquaintance'
  subtype TEXT,                                     -- 'father','mother-in-law','landlord',...
  strength REAL, start_date TEXT, end_date TEXT, provenance TEXT);

CREATE TABLE fact (                                  -- THE canon store
  fact_id INTEGER PRIMARY KEY,
  subject_type TEXT, subject_id INTEGER,
  predicate TEXT NOT NULL,                           -- namespaced, see registry
  object_json TEXT NOT NULL,                         -- pydantic-validated per predicate
  valid_from TEXT NOT NULL, valid_to TEXT,           -- sim-time validity
  created_tick INTEGER NOT NULL,                     -- transaction time (bitemporal-lite)
  provenance TEXT NOT NULL,                          -- see 2.3
  source_event_id INTEGER, source_call_id TEXT,
  supersedes INTEGER REFERENCES fact(fact_id),
  disputed INTEGER DEFAULT 0);
CREATE INDEX fact_subj ON fact(subject_type, subject_id, predicate, valid_from);

CREATE TABLE predicate_registry (
  predicate TEXT PRIMARY KEY,                        -- e.g. 'bio.occupation','state.injury','life.married_to','asset.debt'
  cardinality TEXT,                                  -- 'single'|'multi'
  value_schema TEXT,                                 -- pydantic model name
  mutability TEXT,                                   -- 'immutable'|'event_only'|'free'
  sensitivity TEXT,                                  -- 'public'|'private'|'firewalled'
  importance REAL, decay_class TEXT);                -- retrieval ranking inputs

CREATE TABLE narrative (                             -- prose derived FROM facts; regenerable
  narrative_id INTEGER PRIMARY KEY, subject_type TEXT, subject_id INTEGER,
  kind TEXT,                                         -- 'bio_sketch','memory','voice_sheet','period_summary'
  text TEXT, embedding BLOB, derived_from TEXT,      -- JSON list of fact_ids
  created_tick INTEGER, model TEXT);
CREATE VIRTUAL TABLE narrative_fts USING fts5(text, content=narrative);

CREATE TABLE alias (name_text TEXT, subject_id INTEGER, scope TEXT, weight REAL);
CREATE TABLE canon_log (tick INTEGER, actor TEXT, op TEXT, payload_json TEXT); -- append-only audit; enables replay
CREATE TABLE rate_table (domain TEXT, key_json TEXT, rate REAL, unit TEXT, source TEXT);
CREATE TABLE ward_marginals (ward_id INTEGER, variable TEXT, category TEXT, value REAL, provenance TEXT);
```

### 2.2 The predicate registry is the generality mechanism

New life domains (a court case status, a chit-fund membership, a chronic illness, an election candidacy) are added by registering predicates + pydantic value schemas + rates — **never new tables, never scenario code**. Examples:

| predicate | card. | mutability | example object_json |
|---|---|---|---|
| `bio.occupation` | single | event_only | `{"title":"tempo driver","occ_class":7,"employer_place":8812}` |
| `state.health_condition` | multi | event_only | `{"cond":"fracture_arm","severity":2,"onset":"2026-08-14"}` |
| `life.married_to` | single | event_only | `{"person_id":10233,"since":"2009-05-21"}` |
| `asset.debt` | multi | event_only | `{"creditor":"moneylender","principal":80000,"rate_pm":0.03}` |
| `legal.case_role` | multi | event_only | `{"case_id":"CC/812/2026","court":"Shivajinagar","role":"accused"}` |
| `pers.trait` | multi | free | `{"trait":"soft-spoken, anxious about money"}` |

`mutability='event_only'` is the load-bearing rule: prose cannot change the world. An LLM scene may *propose* "Ramesh lost his job," but the extractor converts it into a candidate **event**, the Event subsystem validates/schedules it, and only its committed outcome writes the fact. `free` predicates (personality color, tastes) can be asserted directly from bio/scene calls.

### 2.3 Provenance & precedence

`anchor` (real-world data; immutable) > `user_injection` > `simulation` (clockwork/demography outcomes) > `simulation.catchup` > `llm_scene(event_id,call_id)` > `llm_bio` > `synthesis` (statistical; silently supersedable until first touch) > `estimate`. On conflict, higher precedence wins; lower is marked `disputed` and logged. Anchor facts can never be superseded — validation rejects any candidate contradicting one.

### 2.4 assert_facts() — the only write path for generated content

```python
def assert_facts(candidates: list[CandidateFact], provenance, now_tick) -> AssertResult:
    accepted, rejected, proposed_events = [], [], []
    for c in candidates:
        reg = registry[c.predicate]                     # unknown predicate -> HARD reject
        v = pydantic_validate(reg.value_schema, c.object)          # type/shape
        resolve_refs(v)                                 # every person/place id must exist or resolve via alias/mint policy
        if reg.mutability == 'immutable' and exists(c): HARD
        if reg.mutability == 'event_only' and provenance.startswith('llm'):
            proposed_events.append(to_event_proposal(c)); continue   # reroute, don't write
        if reg.cardinality == 'single' and overlaps_valid(c):
            if precedence(provenance) > precedence(existing): supersede(existing, c)
            else: HARD
        run_domain_validators(c)     # registered per predicate-group: age bounds, timeline sanity,
                                     # gestation >= ~8.5 months before a birth, death terminates all open facts, etc.
                                     # each returns HARD | SOFT(flag disputed) | OK
        accepted.append(write(c))
    return AssertResult(accepted, rejected, proposed_events)
```

**Repair loop** (owned by LLM Gateway but specified here): on HARD rejections from a scene call, re-prompt once with `VIOLATIONS: [...]` appended; if still violating, the offending span is post-edited out of the prose (canon wins, prose bends) and the fact is dropped with a `canon_log` entry.

**Entity resolution** (`resolve_entity(name, ctx)`): scene roster → 1-hop relationships/affiliations of roster → household → FTS over ward aliases → fail. On fail: if the mention carries facts that outlive the scene, call `mint_person()`; if it's scenery ("a rickshaw driver waved"), it stays an unpersisted role. `mint_person(constraints, ward_hint)` samples a *real D0 person* matching the constraints (age band, sex, occupation, location at that hour via affiliation joins) rather than fabricating — so the stranger the LLM invented was, retroactively, always a real resident. Only if no D0 match exists (constraint too exotic) is a new person created and back-fitted into a household, provenance=`llm_scene`, flagged for marginal-drift accounting.

### 2.5 context_pack() — retrieval into prompts

```python
def context_pack(subjects, scene_desc, token_budget) -> PromptBlock:
    # 1. HARD CARD per subject (always included, canonically ordered keys -> prefix-cache friendly):
    #    name, age, sex, household roster w/ names+ages, occupation+employer, home lane,
    #    open state.* facts (injuries, debts), language register hint
    # 2. RELEVANT FACTS: score = registry.importance * recency_decay(decay_class) * predicate_match(scene_desc.tags)
    #    fill by score until 60% of remaining budget
    # 3. NARRATIVE: sqlite-vec cosine top-k of narrative chunks vs scene_desc embedding, dedup vs (2), fill rest
    # 4. SENSITIVITY FIREWALL: strip any predicate with sensitivity='firewalled' (religion code, community);
    #    replace with derived public facts already materialized (festivals_observed, diet_flags?, deity, language)
    return stable_serialize(...)   # identical inputs -> byte-identical prefix
```
Hard cards are cached per person and invalidated on fact writes touching card predicates — this is what makes T1/T2 calls cheap under prefix caching.

---

## 3. Lazy biography generation

**Detail ladder:** D0 statistical row → D1 named (deterministic, no LLM: name, workplace/school resolved, appearance one-liner from templates) → D2 sketch (ONE cheap-model call per household) → D3 dossier (premium model, focal only).

**Triggers:** user zoom → D3 target + D2 household + D1 one-hop contacts; cast in a T2 scene → D2; named in accepted output → D1; demography event participant (marriage/death) → D2 if scene will render, else facts only. Aggregate processes never trigger detail.

**Retro-history sampling (before any LLM call):** run the *same* hazard rate tables backwards over the person's lifespan with `seed(person_id,'retro')`: sampled marriage year (from age-at-marriage distributions given cohort/community), children's births (must match the actual coresident children — pins!), migration-to-Pune year if born elsewhere (D0 migrant flag), parental deaths, past job changes, one or two salient stochastic events (accident, windfall) at NCRB/insurance base rates. Output: a dated **life skeleton** written as `synthesis`-provenance facts. Pins: everything already in D0/canon (current age, spouse's actual age, existing kids) constrains the sampler — rejection-sample until consistent (cheap; constraints are loose).

**D2 prompt design (household sketch, ~800 out-tokens, cheap model, JSON schema output):**
```
SYSTEM: You write grounded, dignified character sketches for a Pune simulation.
Never mention caste. Never attribute traits to religion or community. No stereotypes;
individuality comes from circumstances and temperament. Marathi/Hindi/English code-mixing
per the language hints. Output ONLY the JSON schema provided.
USER: HOUSEHOLD SKELETON: {hard cards + life skeletons + home lane + income band label
+ festivals observed + habitual shops}. WARD COLOR: {2-line ward gazetteer}.
Produce per person: 3-5 formative memories (dated, consistent w/ skeleton),
current_concerns (2-3), speech_style, pers.trait facts (2-4), 
household_dynamics (who defers to whom, standing tensions), one concrete daily-life detail each.
```
Returned JSON → `assert_facts` (provenance `llm_bio`; memories become `narrative(kind='memory')` + embedding). A **canon linter** (regex + small classifier) rejects outputs containing caste terms or stereotype patterns before assertion; one repair retry.

---

## 4. Demographic dynamics over sim-time

Monthly **ward-cohort ledger** (Leslie-style) is ground truth for totals: births (ASFR NFHS-5 urban Maharashtra), deaths (SRS abridged life tables urban MH by age×sex, frailty-tilted), marriages (age/sex-specific nuptiality), in/out-migration (D-series-derived urban churn rates). Daily tick draws Poisson counts per (ward × cohort × event) and **attributes events lazily**: individuals are sampled at attribution time from eligible D0 rows (weighted by frailty for deaths, by eligibility-and-matching priors for marriages — religion/community homophily applied statistically with configurable exogamy rates, never verbalized). Each attributed event is emitted to the Event Bus as a normal canonical event (`life.birth`, `life.death`, `life.marriage`, `life.migration_in/out`), which the Event subsystem may elaborate into scenes (a wedding = S4 machinery firing on a demography-emitted event — no special case). Marriages create/merge households; migration-in instantiates new D0 households at vacant capacity; migration-out freezes then archives. Untouched people's events may remain unattributed until someone is touched — retro-history then draws from the *residual* cohort ledger so totals still balance.

```python
def demography_tick(date):
    for ward, cohort, ev in rate_table.active():
        n = rng(ward,cohort,ev,date).poisson(rate * cohort_count(ward,cohort) / period)
        for _ in range(n):
            p = sample_eligible(ward, cohort, ev)          # may defer if ward is fully frozen
            emit_event(ev, p, date)                        # Event Bus; outcome writes facts
    reconcile_ledger(ward)                                 # keep macro totals exact
```

---

## 5. LOD-over-time: freeze & rehydrate

**States:** ACTIVE (daily clockwork) / FROZEN (default; only cohort-level dynamics) / ARCHIVED. Freeze after N sim-days without attention and no open arc/event linkage; freezing writes `household.snapshot_json` (finances band, employment digest, health digest, relationship digest, mood-of-house one-liner).

**Rehydrate(household, until):**
1. Collect **pins**: all facts/events referencing members with `valid_from` in the gap (gossip that named them, a court summons, a customer interaction recorded by a shop scene).
2. Replay monthly hazards over the gap with `seed(hh,'catchup',gap)`: job separation/finding (occupation-class-specific monthly hazards), health events, school outcomes, price/expense drift on the household budget bands, any deferred cohort events assigned to them. Rejection-sample the event sequence until consistent with all pins (gestation timing, employment continuity, "was seen selling vegetables on Aug 3").
3. Write reconciliation facts (provenance `simulation.catchup`) + dated micro-events into the event log.
4. If narrative is needed (user is about to talk to them): one cheap-model call — "summarize this household's last k months from these events" → `narrative(kind='period_summary')`.

**Shallow touch:** if a frozen person is merely referenced (a rumor names them, S2), no rehydration — the referencing fact is written and becomes a pin for the eventual catch-up. This is what keeps S2-style information cascades cheap.

---

## 6. Sensitive-attribute firewall (religion, caste, community)

- Stored as coded categoricals, `sensitivity='firewalled'` in the registry.
- They condition ONLY structured derivations: name pool, festivals observed, place-of-worship affiliation, language mix, marriage-matching priors, reservation-category where legally relevant (school admission category as an administrative fact).
- `context_pack` strips firewalled predicates; prompts see the derived facts (festivals, deity, language) — so generated text is culturally specific without ever labeling anyone.
- Canon linter blocks caste terms and stereotype patterns in all LLM output; violations logged for review.
- Distributions themselves are real (Census C-1/C-16, state estimates) with provenance recorded, so the *structure* of the city is honest while the *text* stays dignified.

---

## 7. Libraries & files

- **pandas / numpy / pyarrow** (D0 columnar), **ipfn** (IPF), **pydantic v2** (fact schemas, API DTOs)
- **geopandas + shapely + pyrosm** (OSM ingest), buildings/POIs preprocessed once to `places.parquet`
- **sqlite3** stdlib + **FTS5**; **sqlite-vec** for narrative embeddings; **sentence-transformers** (bge-small, local GPU) or API embeddings — pluggable
- Upgrade paths named, not built: PopulationSim (synthesis), Postgres (canon)
- Layout: `D:\Coding_Workspace\pune-sim\popcanon\{synthesis/, canon/, demography/, retro/, api.py}`; data in `data\anchors\` (read-only), `data\d0\`, `canon.db`

## 8. Cost/scale sanity

50k persons D0 ≈ 3 MB. D2 sketches only on demand: even 3,000 households sketched in a month ≈ 3k × 800 out-tokens ≈ 2.4M tokens ≈ ~$2 on the workhorse model. Canon fact writes are I/O-trivial. At 3.5M people nothing changes structurally: D0 grows to ~200 MB parquet, canon still only holds the touched few thousand.

## Key decisions

- **Population synthesis via a generative 'household grammar' (structurally-valid conditional sampling) calibrated with IPF reweighting over an oversampled pool, rather than seed-based IPF/IPU, Gibbs, or copulas** — India publishes ward-level marginal tables but no ward-level microdata seed; grammar guarantees valid household structure by construction, and IPF over whole-household units integerizes safely; ipfn is boring proven tech a solo dev can debug
  - Rejected: IPU via PopulationSim on an IHDS-II seed (too sparse at ward level, cloning artifacts, broken households after integerization — kept as documented upgrade path); Gibbs (slow, opaque validation, overkill for univariate controls); copulas (built for continuous marginals, cannot emit relational household structure)
- **Two-layer existence: deterministic regenerable D0 columnar population (parquet/numpy, seeded PCG64) + SQLite canon rows created only on first touch ('canon on first touch')** — Scales to 3.5M people at near-zero storage/compute for untouched residents; determinism makes the whole population 'exist' as a function of world_seed; canon stays small and fast because facts are sparse
  - Rejected: Materializing all 3.5M people as DB rows up front (storage, migration pain, and it forecloses lazy retro-history); Postgres from day one (server admin burden for a solo Windows dev, no current multi-process need)
- **One unifying mechanism — 'lazy realization with pinning': retro-history sampling runs the same forward hazard rate tables backwards, constrained by pins (existing canon facts), for initial biographies, frozen-household catch-up, and minted strangers** — One code path instead of three; guarantees statistical consistency between biographies and the living simulation because they share rate tables; pins make lazy generation provably non-contradictory
  - Rejected: Free-form LLM biography invention (statistically wrong life structures, uncontrolled canon growth) and separate bespoke catch-up logic per situation type (scenario special-casing)
- **Canon as a bitemporal SPO fact store governed by a predicate_registry (cardinality, pydantic value schema, mutability, sensitivity) with 'event_only' mutability as the write-discipline rule: prose can propose events but never directly mutate world state** — New life domains are added as registry rows + rate entries, never new tables or code — this is the generality engine; event_only routing makes LLM output structurally incapable of corrupting causality; supersede-not-delete plus provenance precedence gives deterministic conflict resolution and full replayability via canon_log
  - Rejected: Wide typed tables per domain (schema migration for every new situation type = special-casing); free-text memory blobs with LLM-judged consistency (undetectable drift, no provenance, unqueryable)
- **mint_person() resolves LLM-invented strangers to real existing D0 residents matching the constraints, fabricating a new person only when no match exists** — Keeps ward marginals honest under heavy narrative activity, and makes the city feel closed and coherent — the rickshaw driver a scene names was always a real resident with a home and family reachable by zoom
  - Rejected: Always fabricating new people on mention (population drifts above census totals, orphan characters with no household or history)
- **Sensitive-attribute firewall: religion/caste/community stored as coded fields that condition structured derivations (names, festivals, language, matching priors) but are stripped from all LLM prompts; canon linter rejects caste terms in output** — Distributions stay statistically real (structure of the city is honest) while generated text cannot stereotype — cultural specificity is carried by derived facts like festivals observed and language mix
  - Rejected: Passing demographic labels into prompts with instructions to be respectful (LLMs leak and stereotype under pressure; one bad output is a canon fact forever)
- **Demographic dynamics as monthly ward-cohort ledgers (Leslie-style ground truth) with Poisson event counts attributed lazily to individuals; life events emitted as ordinary Event Bus events** — Macro trajectories stay exactly right regardless of which individuals are touched; weddings/funerals/migrations become normal events the Event subsystem can elaborate — S4 is demography output, not a feature; per-person daily dice at 3.5M scale would be wasteful
  - Rejected: Per-person Bernoulli trials every tick (O(N) daily work for rare events, macro drift from integerization noise)

## Interfaces

- **Event/Simulation Engine (clockwork layer + Event Bus)**: Consumes: query_population(filter_expr, at_time, sample_n) -> [person_id] for casting participants (joins D0 attributes + affiliations + schedules, e.g. 'students routed through Shivajinagar at 08:10'); assert_facts(candidates, provenance='simulation', event_id) for committing event outcomes; ensure_detail(id, level, reason). Emits to it: demography_tick(date) -> [LifeEvent{type, person_ids, ward, date}] on the Event Bus; proposed_events rerouted from assert_facts when LLM prose implies a state change.
- **Minds/Cognition (T0-T3 tiers)**: context_pack(subject_ids, scene_descriptor{tags, premise, embedding}, token_budget) -> PromptBlock{hard_cards, facts, narrative} with stable serialization for prefix caching; get_person(person_id, min_detail) -> PersonCard; after scenes, Minds submits extracted CandidateFacts via assert_facts(provenance='llm_scene') and receives AssertResult{accepted, rejected+violations, proposed_events} to drive the one-retry repair loop.
- **Information/Gossip**: resolve_entity(name_text, context_scope) -> person_id|None for grounding rumor subjects; shallow_touch(person_id, referencing_fact) registers pins on frozen people without rehydration; reads relationship + affiliation tables (habitual shops, workplaces, temples) as the diffusion topology: edges(person_id) -> [(peer_id, channel, strength)]. Beliefs live in Information's store and reference canon fact_ids; canon stores only objective truth.
- **Institutions (police, courts, hospitals, PMC, schools)**: Institutions write administrative facts through assert_facts using registered predicates (legal.case_role, health.admission, civic.complaint, edu.enrollment_status) with provenance='simulation'; read context_pack for any person appearing in a proceeding; affiliation(role='party_in_case'|'patient'|'student') rows are created/closed by institutional events; mint_person(constraints) supplies statistically-correct staff/officials on first need.
- **Geography/Mobility**: Consumes their travel-time skim matrix t_ij at synthesis time for gravity assignment; provides households->building_id, persons->workplace/school place_ids, and habitual-shop choice sets that Mobility turns into daily trip chains; place table mirrors their anchor place registry (is_anchor=1 rows are read-only).
- **UI/Attention Manager**: Attention signals drive LOD: on_focus(person_id) -> ensure_detail(D3 target, D2 household, D1 one-hop) and rehydrate(household, now); on_blur schedules freeze after N idle days; interview mode reads context_pack with elevated budget; user event injection writes facts with provenance='user_injection' (second-highest precedence).
- **LLM Gateway**: Population enqueues generation jobs: BioJob{household_id, level, skeleton_pack, output_schema, budget_class} and CatchupSummaryJob{household_id, events}; Gateway returns schema-validated JSON which Population routes through the canon linter then assert_facts; Gateway calls back with repair(violations) at most once per job.

## Scenario traces

## S1 — school bus crash, Shivajinagar, 08:10 (acute physical)
Event subsystem casts victims via `query_population("age in 5..16 AND school_route passes Shivajinagar at 08:05-08:15", sample)` — a pure join over D0 attributes + `affiliation(role='student')` + Mobility's route assignment; the father is on board because `relationship(kind='kin')` + his workplace assignment put him on the same route (queryable, not scripted). All are D0 strangers until now: `ensure_detail(D2)` fires retro-history (their pasts sampled consistent with current ages/households) then one household sketch call each. Hospital admission, FIR, injuries are ordinary registry predicates: `health.admission{place:sassoon}`, `state.health_condition{fracture}`, `legal.case_role{role:complainant}` written by Institutions with provenance=`simulation`. School absence is not special code — the injury fact changes the child's availability, which the clockwork layer reads. Parental panic scenes get `context_pack` whose hard cards now include the open `state.*` injury facts automatically.

## S2 — temple donation scam rumor (informational)
The rumor names "the trustee Joshi" — Information calls `resolve_entity("Joshi", scope=temple_affiliates)`; canon resolves against `affiliation(place=temple, role='member'|'trustee')` aliases, or `mint_person(constraints={adult, affiliated with that temple})` selects a real D0 resident who becomes, retroactively, the trustee (fact `org.role{trustee}` written; his household stays FROZEN — a **shallow touch** records the referencing fact as a pin). Diffusion runs over the affiliation/relationship topology canon provides (kirana queues, temple congregations, coworkers). Weeks later, if the user zooms to the trustee, `rehydrate` must produce a past consistent with the pin: retro-catch-up rejection-samples his months to include awareness of the rumor. Canon stores only objective truth (whether a scam actually occurred is an event-subsystem fact); every believer's version lives in Information's belief store referencing canon fact_ids — mutation of the rumor never corrupts canon.

## S5 — job loss spiral (slow personal arc)
A `bio.occupation` end arrives as an economic event (firm closure drawn from job-separation hazards in `rate_table`, or arc-injected). The fact write cascades through ordinary predicates over months: `asset.debt` accrues via household budget catch-up rules, `edu.enrollment_status{fees_overdue}` via the school's institutional check, `pers.trait`/tension via periodic D2 refresh. Crucially the household is FROZEN most of this time: monthly catch-up hazards (job-finding hazard conditioned on occ_class and age, debt growth) advance the arc statistically; when the user returns after four months, `rehydrate` replays the gap under pins (a gossip mention that he was seen at the labor naka is honored), writes dated `simulation.catchup` facts, and one cheap summary call produces the period narrative. Recovery vs spiral is just which branch the hazard draws took — both are canon-consistent.

## S6 — truck driver's court case over 3+ years (institutional long-horizon)
The driver was minted at S1 (`mint_person(constraints={adult male, occ_class=driver, on that road at 08:10})` → an existing D0 resident of a different ward). `legal.case_role{accused}` and each hearing/adjournment are multi-valued facts written by the Courts institution on its own slow clock. The driver's household freezes between hearings; each hearing is an attention touch → `rehydrate(until=hearing_date)` catches up 4–8 months of his life with the open case as a standing pin (catch-up sampler cannot, e.g., emigrate him while under trial — a domain validator on `legal.case_role` blocks `life.migration_out`). Three years of proceedings cost ~10 rehydrations and a handful of scene calls, yet his biography remains gap-free and queryable at any past date via `valid_from/valid_to`.

## S7 — 2026 PMC ward election (city-scale process)
Candidates are minted from constraints (ward resident, age, party affiliation as `org.role` facts); the electorate is never individually simulated — `query_population` over D0 supplies ward-level aggregates (age structure, issue exposure proxies: households with `civic.complaint` facts, flood-affected buildings) that the civic subsystem turns into vote-share models. Only canvassing scenes touch individuals (D1/D2 on demand). The losing corporator is a normal person whose `org.role{corporator}` fact gets `valid_to` closed — his subsequent life runs on the same freeze/rehydrate machinery as S5. No election-specific population code exists: candidacies, party membership, and office are registry predicates.

## S8 — Ganeshotsav (mass event)
Mandal membership is pre-seeded at synthesis as `affiliation(role='member', place=mandal)` drawn with locality-weighted probabilities; festival participation propensity is a derived fact from the firewalled religion code (never verbalized as identity, expressed as 'observes Ganeshotsav, family follows X mandal'). Crowd inflow = temporary migration events instantiating visitor cohorts at D0 (regenerable, discarded after). Commerce spike is a rate_table modifier window, not code. Any individual the user zooms to mid-procession is an ordinary lazy touch.

**Common pattern across all traces:** cast via query over D0+affiliations → touch via ensure_detail/retro-history → world changes only through events writing registry predicates via assert_facts → absence simulated by freeze/pins/rehydrate. No trace required a mechanism the others didn't use.

## Generality argument

Every life situation, seen from Population & Canon's side, decomposes into exactly four operations, and all eight probes (plus unseen ones) reduce to them: (1) CASTING — find who is involved, which is always a relational query over D0 attributes + affiliations + relationships (query_population/resolve_entity/mint_person); (2) REALIZATION — give them exactly as much history as the moment needs, which is always retro-history sampling from the same rate tables that drive the forward sim, constrained by pins (ensure_detail); (3) STATE CHANGE — record what became true, which is always a registry predicate written through assert_facts, with the event_only rule guaranteeing prose can never bypass causality; (4) ABSENCE — advance the untouched, which is always cohort-ledger dynamics plus pinned catch-up (freeze/rehydrate). New situation types — a kidney sale racket, a metro land acquisition dispute, an interfaith elopement, a startup boom — require only: new predicates in predicate_registry (rows + pydantic schemas), new entries in rate_table, and possibly new affiliation roles. None require schema migration, new tables, or code paths, because the fact store is schema-per-predicate, casting is expression-based rather than enumerated, and biography/catch-up are parameterized by the same rate tables the new domain adds. The two places domain knowledge concentrates — the household grammar and the domain validators — are themselves data-driven (type priors, per-predicate-group rules) and additive. The strongest evidence of generality is that the probe scenarios share mechanisms pairwise in nonobvious ways: S4's wedding is S1's casting plus demography's marriage event; S6's court case is S5's freeze/rehydrate with an institutional pin; S2's rumor subject and S1's truck driver are the same mint_person call. Special-casing was avoided precisely where it is most tempting (weddings, elections, festivals) by making each an emergent composition: demography emits, registry records, affiliations connect, attention realizes.

## Open questions

- Seed-data licensing and quality: is IHDS-II (or NSS 68th round) microdata worth acquiring to upgrade the household grammar's conditional tables, and can its urban-Maharashtra subsample be used at Peth granularity without cloning artifacts?
- Ward-level religion composition: Census C-1 is town-level; the per-Peth locality priors are editorial estimates — what provenance/uncertainty labeling and review process does the user want for these, given the sensitivity firewall depends on the underlying distributions being defensible?
- Belief-vs-canon boundary with the Information subsystem: this design assumes canon stores only objective truth and beliefs live in Information's store referencing fact_ids — needs joint sign-off, especially for 'facts' no one objectively verified (did the temple scam actually happen?).
- Repair-loop budget: one retry then post-edit is assumed; should high-tier (T3 focal) scenes get a second retry or human-visible flagging instead of silent post-editing of prose?
- Embedding stack: local sentence-transformers (bge-small on the user's GPU) vs API embeddings for narrative retrieval — cost is negligible either way, but local adds a Windows/CUDA maintenance surface; needs a benchmark on Marathi-English code-mixed text.
- Concurrency contract: design assumes a single sim process and single canon writer; if the UI later runs interviews concurrently with the clockwork tick, does the Canon service need a write queue with tick-stamped ordering, or does the orchestrator guarantee alternation?
- Marginal drift accounting: mint_person fabrication (when no D0 match exists) and user injections slowly distort ward marginals — is a monthly reconciliation report sufficient, or should the cohort ledger actively absorb fabricated people by retiring statistical equivalents?
- Frailty and health realism: SRS life tables give mortality, but morbidity (chronic condition prevalence driving S3 disease-worry and Sassoon load) needs a source — NFHS-5 self-reported chronic illness vs GBD India estimates; which does the Health/Institutions subsystem prefer to co-own?
- 2026 back-projection legitimacy: Census 2021 never happened; the 2011→2026 growth-factor plus Leslie-aging approach should be validated against PMC electoral-roll counts per ward — acceptable as an anchor-with-estimate-provenance?
- Retention/compaction policy for canon_log and superseded facts at multi-year sim horizons: keep forever (replayability) vs periodic cold-storage export of pre-epoch history?

## Red-team critique (verdict: needs_changes)

- **[critical]** Casting reads stale D0 for exactly the people who matter. query_population is described as 'a pure join over D0 attributes + affiliations', but D0 columns (occ_class, marital, age-derived eligibility, worker_type, household membership) become wrong the moment canon supersedes them — S5's job-loser still shows as employed, a canon-divorced person still shows married, a dead-but-touched person is still castable. After a sim-year, every casting query silently returns wrong results for the touched population, which is precisely the population narrative gravitates to. The design never specifies a canon-overlay for D0 queries.
  - Fix: Declare explicitly which D0 columns are projections of which registry predicates (occ_class ← bio.occupation, marital ← life.married_to, building_id ← life.residence, alive ← life.death). Maintain a materialized 'current_view' table in SQLite for touched persons, updated inside assert_facts when a projected predicate changes. query_population executes as: D0 scan EXCEPT touched_ids UNION current_view, and this contract goes in the API doc so the Event subsystem can rely on it.
- **[critical]** The sensitivity firewall fails exactly on identity-salient narratives — and the design's own generality argument claims 'an interfaith elopement' needs only new predicates, which the trace disproves. With religion stripped from context_pack, the model writing the family-opposition scenes either (a) infers religion from names (Ayesha Shaikh / Aditya Deshpande scream community membership — the firewall is deniability, not prevention, and the inference is un-vetted stereotyping, the worst outcome) or (b) produces vague 'family honor' mush — pure LLM slop in the one arc that most demands specificity and care. Meanwhile the linter's stereotype classifier will HARD-reject scene after scene of an arc that is legitimately about religion, and the 'post-edit the span, prose bends' repair rule silently guts the emotional core of scenes.
  - Fix: Replace blanket strip with a disclosure policy: firewalled attributes may enter prompts only when an arc/event is flagged identity_salient=true (set by Event subsystem or user), which simultaneously (1) escalates to the premium model, (2) switches the linter from block-mode to flag-for-human-review mode for religion terms while still blocking caste slurs, (3) disables silent post-editing in favor of regenerate-or-surface-to-user. Also widen the derived cultural fingerprint (mandal/mosque/vihara affiliation, food practice, neighborhood institutions, wedding-custom hints) so non-salient scenes get texture without labels.
- **[critical]** The city is structurally frozen at synthesis. Places come from a one-time OSM preprocess; there is no place-creation path (mint_person exists, mint_place does not), no place closure, no dynamic gravity, and no capacity-occupancy tracking. Worse, D0 affiliations are deterministic functions of world_seed + the synthesis-time skim matrix, so any infrastructure change (metro), commercial change (a shop gaining customers), or firm closure either can't propagate to the 99% untouched population or, if you regenerate, breaks the determinism invariant and contradicts existing pins. The metro and price-war holdouts both break here; so does the design's own claimed-easy 'metro land acquisition dispute'.
  - Fix: (1) Add place lifecycle events (place.opened/closed/modified) with an assert path. (2) Version the deterministic layer: D0 = f(world_seed, epoch), where an epoch bundles skim matrix, place inventory, and choice-set parameters; untouched entities re-derive under the current epoch, touched entities change only via events, and retro-history queries the epoch that was current at each past date (epoch table keyed by valid_from). (3) Model adoption as fractional re-assignment: per affected OD pair, a switching probability applied as a bulk affiliation-update event, so commute/footfall shifts are gradual and demographically skewed rather than an overnight re-solve.
- **[major]** Ledger reconciliation is one-directional. The design specifies aggregate→individual attribution and retro draws from the residual ledger, but never the reverse: narrative/exogenous life events (a wada collapse killing five, an arc-driven interfaith marriage, a scene-committed job loss) do not decrement the (ward, cohort, event) residuals. The demography tick will then draw its full Poisson counts on top — a ward that just buried five collapse victims also gets its full background deaths; the interfaith couple gets statistically double-married. Macro totals drift in exactly the wards where the story happens.
  - Fix: Every committed life.* event with an attributed individual writes a decrement to the corresponding residual ledger cell inside the same transaction; reconcile_ledger clamps negatives (disaster > background rate) to zero, carries the deficit forward, and logs. Add an invariant test: sum(attributed + residual) == ledger total, run nightly.
- **[major]** Job-finding has no workplace-assignment path. Retro-history and catch-up hazards produce 'found a job' outcomes, but bio.occupation requires an employer_place, and the gravity machinery is described only as a synthesis-time batch. Nothing tracks jobs_capacity occupancy over time, so dynamic assignment (even in a static city) is undefined — every rehydration of a job-finder needs machinery that doesn't exist yet.
  - Fix: Refactor the gravity model into a callable service: sample_workplace(person, at_date) draws from the person's T_i· row filtered by the occupation-compatibility matrix and a live vacancy ledger (capacity minus current affiliation(role='employee') count, maintained incrementally). Retro/catch-up job-finding calls it with the epoch current at the sampled date.
- **[major]** Rejection-sampling 'until consistent with all pins' is claimed cheap because 'constraints are loose' — false for the design's own flagship cases. The S6 driver accrues pins for 3+ years (open case, hearing appearances, gossip sightings); a rumor-pinned trustee accrues Information-subsystem pins for weeks. The probability of a joint monthly-hazard sequence satisfying many dated pins decays multiplicatively; rejection sampling either loops unboundedly or the dev caps attempts and ships silent contradictions.
  - Fix: Replace naive rejection with constructive bridge sampling: sort pins by date, sample each inter-pin segment forward conditioned on the segment's endpoint constraints (employment continuity, location, open-case validators), which turns a joint rejection problem into k small local ones. Cap attempts per segment; on exhaustion, relax the lowest-provenance soft pin, mark it disputed, and write a canon_log entry — never silently violate.
- **[major]** Narratives go stale with no invalidation, which is the primary long-run canon-drift vector. Memories, voice sheets, and period summaries are stored with derived_from fact_ids, but nothing marks them stale when an underlying fact is superseded. Two sim-years later, vector retrieval happily injects 'he works at the mill' into a prompt long after the mill closed — the LLM then reasserts it, and the repair loop fights the retrieval layer forever.
  - Fix: On supersede(fact), flag every narrative whose derived_from contains that fact_id as stale (single indexed join). context_pack excludes stale narratives; regeneration is lazy on next attention (one cheap call re-summarizing from current facts). Add stale-narrative count to the monthly reconciliation report.
- **[major]** pers.trait is mutability='free', multi-cardinality, assertable from every scene — traits accrete without bound over years, accumulate contradictions ('soft-spoken' and 'quick-tempered' both live), and a character's personality becomes a landfill of one-off scene impressions. This is slow personality drift, and it reads as slop.
  - Fix: Cap open pers.trait facts per person (say 6). When the cap is hit, next attention triggers a consolidation pass: one cheap call merges traits into a superseding set (old ones get valid_to closed, not deleted), anchored to the facts that evidenced them. Enforce decay_class in retrieval scoring so stale scene-impressions lose to consolidated traits.
- **[major]** There is no residence-change pathway. household.building_id is a bare D0/SQLite column with no predicate behind it — the wada-collapse holdout needs displacement (families to relatives, PMC transit camps, eventual rehousing), the interfaith couple needs new-household formation in a rental, and ordinary tenancy churn needs it too. Migration-in 'instantiates at vacant capacity', proving a vacancy notion exists, but intra-city moves have no mechanism at all.
  - Fix: Add life.residence (event_only, single-cardinality per household) with building_id as its projection in current_view; reuse the migration-in vacancy machinery for destination sampling; add a 'displaced/temporary' residence subtype so transit camps and doubling-up-with-relatives are representable without a housing-market model.
- **[major]** The entity ontology is closed over person/household/place, and it leaks: affiliation.place_or_org_id references an org concept with no org table in the DDL (mandals, firms, courts, shop-as-business-with-owner-and-finances are all conflated with their premises), and non-human actors don't exist at all — the stray-dog holdout has no referent for a recurring neighborhood animal that gets reported, caught by PMC, or attacks again. resolve_entity('that brown dog near the school gate') can only fail or mis-mint a person.
  - Fix: Add an org table (org_id, kind, name, premises place_id nullable, meta_json) and a lightweight generic entity table (entity_id, kind='animal'|'vehicle'|'object', description, ward, status) that fact/alias/affiliation subject_type can reference. mint_entity mirrors mint_person minus retro-history. The scenery-vs-persistent rule already in resolve_entity decides when a dog graduates from unpersisted role to entity row.
- **[major]** Cheap-model D2 sketches will homogenize at scale. One 800-token call inventing '3–5 formative memories' per person, thousands of times, converges on the cheap model's template attractors (the exam, the wedding, the accident, the monsoon). The city will feel like one person wearing 50,000 masks — the single biggest 'LLM slop' risk after the firewall blandness.
  - Fix: Force memories to anchor on the retro-history skeleton: the prompt must reference the person's actual dated skeleton events (their specific migration year, job change, parent's death) and the ward gazetteer line, and the schema requires each memory to cite a skeleton event id. Add a batch-level embedding-similarity dedup: reject a sketch whose memories are >0.9 cosine to any of the last N household sketches and retry with an injected style/topic rotation.
- **[major]** The observed-equals-pinned rule is implied but not universal, so calibration fixes silently rewrite surfaced history. Names are deterministic functions of seed + name-pool tables; 'named in accepted output → D1' pins names, but resolve_entity matches against deterministic names of untouched people (the trustee stays FROZEN with only a pin), and query_population exposes D0 attributes into scenes without any promotion. Fix a name-pool weighting bug or a rate table and every previously-surfaced-but-unpinned value changes under the user's feet.
  - Fix: Make it a hard rule: any deterministic D0 value that crosses the subsystem boundary (a resolve_entity hit, an attribute embedded in a cast list or prompt) is promoted to canon at that moment with provenance='synthesis'. Stamp world header with grammar_version/name_pool_version/rate_table_version; any version bump requires an epoch bump so regeneration differences are auditable rather than silent.
- **[minor]** mint_person as perpetrator-assigner (chain-snatching, scams) has two unhandled edges: the sampled 'real resident' may have existing canon that makes the retcon absurd (a pinned, devout schoolteacher sampled because age/sex/location matched), and demand-driven crime minting inflates crime prevalence among the touched population with no ledger accounting — narrative attention manufactures a crime wave.
  - Fix: Add a no-contradiction filter to mint constraints (candidate must have zero canon facts inconsistent with the role; weight by a crime-propensity prior derived from NCRB rates), fall back to fabrication when no clean candidate exists, and count minted crime roles against an NCRB-calibrated ward crime ledger in the monthly drift report.
- **[minor]** The 3.5M affiliation story is a hand-wave ('moves to parquet + SQLite index of touched rows; same API'). ~20–25M affiliation rows need bidirectional access (edges(person) for gossip, members(place, hour) for casting) plus merge semantics between the parquet base layer and canon-modified rows for touched people — none of which is specified, and the gossip subsystem's performance depends on it. Relatedly, 'periodic D2 refresh' in the S5 trace contradicts 'aggregate processes never trigger detail': the touched set only grows, so any scheduled refresh is a monotonically growing cost.
  - Fix: Specify now: CSR index arrays both directions (person→affil, place→affil) built at synthesis, ~400MB mmap-able; touched-row overlay resolved identically to the query_population current_view mechanism. Strike 'periodic refresh' — D2 refresh happens only on next attention, never on schedule.
- **[minor]** Solo-dev risk concentrates in three research-flavored components the plan treats as line items: the pin-consistent retro sampler, the open-ended domain-validator library (death-terminates-facts, gestation, custody, tenancy… discovered by whack-a-mole), and the canon linter's 'small classifier' for caste/stereotype detection in Marathi-English code-mixed text, which has no training data and will both over-block (interfaith arc) and under-block (euphemisms). The rest — grammar, IPF, gravity, SQLite registry — is genuinely boring and buildable.
  - Fix: Stage it: v0 ships with forward-conditioned bridge sampling only between at most 2 pins (covers 95% of touches), a validator library seeded from ~15 rules and grown from canon_log replay failures, and a linter that is a curated bilingual lexicon + an LLM-judge sampled over 10% of accepted outputs feeding a human review queue — accept imperfection, log everything, defer the classifier until real violation data exists.

### Novel holdout-scenario traces

CHOICE OF HOLDOUTS: The two most stressing scenarios for THIS design are (1) the new metro station and (2) the inter-religious marriage, because each attacks a load-bearing named mechanism rather than exercising the well-trodden S1/S2 path. The dog attack reduces to an entity-ontology gap (issue: no non-person entities — real but a bounded fix); the wada collapse is S1-casting plus two gaps already itemized (no residence-change pathway; no event→ledger backflow for disaster deaths); the chain-snatching is the least stressing — it runs almost entirely on S1+S2 machinery, with the perpetrator-minting edge cases noted in the issues. The metro and the marriage, by contrast, break invariants the design brags about — and tellingly, BOTH appear in the design's own generality_argument as things requiring 'only new predicates and rate entries.' The traces below show that claim is false for both, which is the clearest overfitting evidence: the four probe scenarios never required the city's structure to change or the firewall to be interrogated, so those mechanisms were never stress-tested.

=== TRACE 1: A NEW METRO STATION OPENS AND SHIFTS COMMUTE PATTERNS ===

Step 1 — The station must exist as a place. BREAK #1: places are a one-time OSM preprocess ('preprocessed once to places.parquet'); there is no place-creation path. mint_person exists; mint_place does not. The station, its new kiosks, the chai stall cluster that springs up outside it — none can be born. First silent special-case: someone hand-inserts rows and hopes downstream consumers (gravity O_i/D_j totals, choice-set samplers) notice.

Step 2 — Travel times change. The skim matrix t_ij was consumed 'at synthesis time' per the Geography/Mobility contract. Mobility updates its skim; Population has no listener and no recompute path. Mode/route shifts are Mobility's job, fine — but Population owns WHERE people work and shop, and that's what a metro actually shifts over months.

Step 3 — Existing workers. Correctly, most people keep their jobs (no break — realism preserved by inertia). But job-CHANGERS should now accept farther workplaces because commute cost fell. BREAK #2: job-finding hazards produce 'found a job' with no workplace-assignment machinery — gravity exists only as a synthesis batch, there is no vacancy/occupancy tracking, and even in a static city every rehydrated job-finder hits this undefined path. With a metro, the T_i· rows they'd sample from are stale by construction.

Step 4 — Habitual choice sets and footfall. Shops near the station gain customers; this is the social topology gossip runs on. The untouched 99% have choice sets that are deterministic functions of world_seed + the OLD distance-decay. BREAK #3 (the deep one): to shift them you either (a) regenerate D0 affiliations under the new skim — which violates 'D0 is a pure function of world_seed', silently rewrites the affiliations of everyone untouched, and contradicts pins referencing old affiliations ('was seen at his usual kirana'), or (b) emit per-household change events — ~100k events, absurd. The invariant 'a person is a deterministic function of the world seed' is only true while the city is static. The design needs epoch-versioned determinism: D0 = f(seed, epoch), untouched people re-derive under the current epoch, touched people move only via events — and retro-history must query the epoch current at each PAST date (someone first touched in 2028 shopped at the pre-metro kirana in 2026), so epochs must be layered and historically queryable. No concept of this exists anywhere in the design.

Step 5 — Second-order effects. Rents rise near the station (rate_table modifier window — actually generalizes fine); migration is attracted (ward growth factors are a static 2011→2026 table — needs a manual rate edit, acceptable as scenario authoring); land-acquisition disputes are registry predicates (fine). The parts of the design that are pure fact-recording absorb the scenario; every part that touches the deterministic statistical layer breaks.

Verdict on trace 1: needs place lifecycle events, gravity-as-a-service with a live vacancy ledger, epoch-versioned D0 with historical epoch lookup in retro-history, and a fractional-switching bulk re-affiliation mechanism. Four new mechanisms, one touching the core invariant. 'Only new predicates and rate entries' is false.

=== TRACE 2: AN INTER-RELIGIOUS COUPLE MARRIES AGAINST BOTH FAMILIES' WISHES ===

Step 1 — Genesis. Best case for the design: nuptiality machinery has 'configurable exogamy rates', so an interfaith pairing is in-distribution for the demography draw, or the arc is user/event-driven. Casting the couple via affiliation overlap (coworkers, college) works. The matching priors condition on firewalled codes without verbalizing them — as designed. No break yet.

Step 2 — Retro-history for both families. Works: skeletons, D2 sketches, household dynamics. Note, though, that the D2 prompt was linted to 'never attribute traits to religion or community' — so the sketches of two families whose defining upcoming conflict is communal have been generated with that dimension surgically absent. The canon entering this arc is pre-blanded.

Step 3 — The opposition scenes. This is where it breaks. context_pack strips religion/community codes and supplies derived facts: names, festivals, deity, language mix. The scene premise is 'the families oppose the marriage.' The cheap model has exactly two options. (a) Infer religion from names — Ayesha Shaikh and Aditya Deshpande are unambiguous to any model — and write the real objection. The firewall is thereby revealed as deniability, not prevention: identity-charged text is generated from UNGOVERNED inference rather than governed facts, which is the maximally risky configuration for caricature. (b) Not infer, and write vague 'log kya kahenge / family honor' mush — precisely the LLM slop the review is asked to detect, in the scenario demanding the most specificity. BREAK: the firewall's contract ('sensitive attributes are never emitted into LLM prose prompts') makes the one story that is ABOUT identity unwritable honestly. No predicate addition fixes prompt-level information starvation.

Step 4 — The linter compounds it. It blocks 'caste terms and stereotype patterns.' An honest scene in this arc contains religion terms and depictions of prejudice (a character voicing bigotry is realism, not stereotype — but a pattern-classifier can't tell). Every scene risks HARD rejection → one retry → then 'the offending span is post-edited out; canon wins, prose bends.' Silent post-editing of the emotional core of an interfaith-conflict scene produces incoherent, gutted prose — and doubles token cost via constant repair churn. The design's repair policy is exactly wrong for identity-salient content: it needs escalation and human review, not silent excision.

Step 5 — Mechanics that DO work. Special Marriage Act 30-day notice = institutional predicate on the courts' slow clock (works, and elegantly). life.married_to is event_only, so the scene proposes and the Event subsystem commits (works). New household formation: 'marriages create/merge households' — but the default machinery implies patrilocal merge; an estranged couple forms a new nuclear household in a rental, hitting the missing residence-assignment pathway (same gap as wada displacement; the migration-in 'vacant capacity' machinery exists and can be reused — small extension, but today it's a special case). Family rupture as relationship-strength changes and gossip diffusion: works.

Step 6 — Ledger. If arc-driven rather than demography-drawn, the marriage never decrements the nuptiality residuals for either cohort or the exogamy accounting — the one-directional reconciliation gap, concretely: the ward statistically marries this couple twice.

Verdict on trace 2: the skeleton (events, predicates, institutions, households) absorbs the scenario; the firewall + linter + cheap-model + silent-repair stack fails at its center. Required special-casing: an identity_salient arc flag that scope-discloses firewalled attributes into prompts, escalates to the premium model, switches the linter to flag-for-review, disables silent post-editing, and backflows the marriage into the cohort ledger. Handling 'with care and realism' is achievable — but only by adding a disclosure mechanism the design currently defines itself against.