# 3. Minds — decisions, memory, information

## Summary

MINDS is the cognition layer of Pune Sim: it owns person state (traits, pressures, goals, finances, health), household morning scenes, the decision-escalation framework, memory, relationships, and information as a simulated object. The core design principle is "numeric integrators + threshold-gated LLM scenes": every life force (debt, illness, rumor exposure, court pressure) is a cheap clockwork-updated scalar or structured record, and LLM calls fire only when an appraisal function detects that rules cannot resolve a situation faithfully — expectation violations, threshold crossings with hysteresis, goal conflicts, high-stakes encounters, belief-impacting information, institutional summonses, hazards, or user attention. I revise the working hypothesis of one LLM household scene per household per morning to a routine-bypass gate (cached schedule templates replay for ~92-95% of households daily), which cuts baseline cost to roughly $1-2/day for the 50k-person start area while preserving full fidelity wherever anything non-routine is happening or the user is watching. Memory is not a per-person free-text stream: the shared append-only event log is the substrate, personal memory is a view over it plus subjective entries emitted as structured fields of every scene's JSON output (no separate summarizer calls), with local-GPU embeddings and Stanford-style recency/salience/relevance retrieval only for materialized (P2+) people. Relationships are directed typed edges in SQLite with sentiment, trust, and running summaries, lazily materialized from kinship, spatial adjacency, and organization rosters; groups (households, WhatsApp groups, temple committees) are first-class nodes that double as information channels. Information items carry structured claims with lineage: transmission is a clockwork probability over co-presence windows and group broadcasts, mutation is mechanical distortion ops (exaggerate, reattribute, moralize) with LLM rewrites only above a prominence threshold, and belief updating uses trust-weighted, lineage-discounted evidence with a confirmation-bias term — so a rumor's drift is measurable and auditable. Months-long arcs like job loss compress into ~6-10 scenes because pressures integrate daily without LLM involvement and each scene sets commitments that change the trajectory slope. Everything is Pydantic-validated JSON over SQLite + sqlite-vec, DeepSeek-class workhorse with prefix caching, premium model only for the attention bubble.

## Design

# MINDS Subsystem — Detailed Design

## 0. Position in the architecture

MINDS sits between the **Clockwork** layer (schedules, traffic, hazards — deterministic/stochastic, no LLM) and the **LLM Gateway**. Clockwork advances everyone; MINDS decides *who thinks today, about what, at what fidelity*, and turns the results back into clockwork-executable schedule actions and canon facts. One invariant governs everything: **state lives in numbers and structured records; LLMs are invoked only to resolve situations rules cannot, and every LLM output is structured JSON that mutates state**. Prose is a rendering of state, never the state itself.

## 1. Person model

### 1.1 Materialization tiers (lazy generation)

| Tier | Trigger | Contents | Cost |
|---|---|---|---|
| P0 | none (statistical mass) | no row; exists in ward demographic pools | 0 |
| P1 skeleton | household materialized, or referenced by an event | name, demographics, household link, occupation slot, schedule template, trait vector, pressure scalars, kin edges | procedural, 0 LLM |
| P2 persona | first LLM scene involving them, or attention within 2 hops | persona card (~300 tok), memory stream active, non-kin edges materialized | 1 T1 call, cached forever |
| P3 deep | user attention (focal or interlocutor) | full retrieval, T3 roleplay, itemized ledger | premium calls while focal |

Promotion is monotone (never demote below P1; deep people fall back to P2 processing when attention leaves, keeping all state). **Retro-consistency rule:** the event log records everything that ever touched a person even at P1, so P2 promotion backfills a biography *constrained by* their logged history (the persona-generation prompt receives their event digest and must not contradict it; Canon DB write is rejected on contradiction check failure).

### 1.2 Core tables (SQLite; SQLAlchemy Core; all times = sim-epoch minutes)

```sql
CREATE TABLE person (
  id INTEGER PRIMARY KEY, tier INT NOT NULL DEFAULT 1,
  household_id INT NOT NULL REFERENCES household(id),
  name TEXT, gender TEXT, birth_year INT,
  ward_id TEXT, home_place TEXT, work_place TEXT,       -- place refs into Geography subsystem
  occupation_code TEXT,                                  -- NCO-2015 code; org_id if employed
  employer_org_id INT, schedule_template_id TEXT,
  religion_code TEXT, mother_tongue TEXT, community_code TEXT,  -- statistical only; see §8 guardrail
  traits BLOB,        -- packed float32[12]: O,C,E,A,N, risk_tol, religiosity, sociability, thrift, ambition, temper, credulity ∈[0,1]
  -- pressure integrators, all ∈[0,1]; the S5 mechanism
  p_financial REAL DEFAULT .2, p_health REAL DEFAULT .1, p_family REAL DEFAULT .1,
  p_job REAL DEFAULT .1, p_social REAL DEFAULT .1, p_legal REAL DEFAULT 0,
  mood REAL DEFAULT 0,            -- fast-decay [-1,1], set by scenes, decays τ≈2 days
  hysteresis BLOB,                -- per-pressure next-fire thresholds (see §4.3)
  alive INT DEFAULT 1, updated_t INT);

CREATE TABLE persona (            -- P2+; written once, versioned on reflection
  person_id INT PRIMARY KEY, card_json TEXT,  -- {values:[tags], quirks:[..], speech_style, fears, aspirations}
  self_narrative TEXT,            -- 3-5 sentences, updated by nightly reflection only
  life_goals_json TEXT,           -- [{goal, why, horizon}]
  version INT, created_t INT);

CREATE TABLE project (            -- active structured goals; the unit of "trying to do something over time"
  id INTEGER PRIMARY KEY, person_id INT, kind TEXT,     -- e.g. save_for, repay, find_job, organize_event, litigate
  spec_json TEXT,                 -- {target, deadline_t, progress, blockers:[event refs], stake:0..1}
  status TEXT CHECK(status IN ('active','stalled','done','abandoned')), updated_t INT);
```

**Personality is hybrid.** The float trait vector parameterizes clockwork policy tables (share-gossip probability, default transport choice under delay, compliance with a summons, savings rate) so background behavior varies by person with zero LLM cost. The persona card exists only for prompts. Both are generated together at P2; P1 people get traits sampled from ward-conditioned distributions with household correlation.

### 1.3 Finances (state, not simulation)

```sql
CREATE TABLE account (owner_kind TEXT, owner_id INT, liquid REAL, monthly_income REAL, income_reliability REAL, PRIMARY KEY(owner_kind,owner_id));
CREATE TABLE obligation (
  id INTEGER PRIMARY KEY, debtor_kind TEXT, debtor_id INT,
  creditor_ref TEXT,              -- 'person:123' | 'org:HDFC' | 'org:moneylender_88' | 'inst:school_44'
  kind TEXT,                      -- rent|emi|school_fees|informal_loan|chit_fund|utility
  monthly REAL, principal REAL, rate REAL, due_dom INT,
  status TEXT DEFAULT 'current',  -- current|late|defaulted|restructured|closed
  missed_count INT DEFAULT 0);
CREATE TABLE txn (id INTEGER PRIMARY KEY, t INT, owner_kind TEXT, owner_id INT, amount REAL, kind TEXT, ref TEXT);
```

Background households get **monthly rollup txns** only; itemized daily txns switch on inside the attention bubble. `p_financial` is recomputed daily: `sigmoid(k1·(monthly_obligations−monthly_income)/monthly_income + k2·(1 − liquid/(3·monthly_obligations)) + k3·missed_count_total)`. A missed EMI is a clockwork event (due date passes, liquid insufficient) that increments `missed_count`, bumps `p_financial`, drips `p_family` (+0.005/day while any obligation late), and may spawn a creditor info item/visit — all without LLM.

### 1.4 Health

```sql
CREATE TABLE health_condition (
  id INTEGER PRIMARY KEY, person_id INT, kind TEXT,      -- injury_fracture|dengue|hypertension|...
  severity REAL, onset_t INT, course TEXT,               -- acute|recovering|chronic|terminal
  expected_end_t INT, treatment_place TEXT,              -- e.g. 'inst:sassoon'
  daily_cost REAL, work_capacity REAL);                  -- 0..1 multiplier on ability to work
```

Conditions are created by Clockwork hazards (base rates from NCRB/health data), scenes, or area events (S3 flood → water-borne disease rate multiplier for exposed lanes). They mechanically feed `p_health`, drain `account.liquid` via `daily_cost`, and gate schedule templates (`work_capacity < 0.5` ⇒ absence events). Recovery is a deterministic course unless a scene changes treatment.

## 2. Household morning scene (T1)

### 2.1 The routine-bypass gate (revision of hypothesis)

Literally one call per household per morning = ~12k calls/day ≈ $180+/month on morning scenes alone, spent mostly re-deriving unchanged routines. Instead each household has a **compiled routine**: per-member weekly schedule template (workdays, school runs, market days, temple visits) produced by its *first* morning scene and replayed by Clockwork. A morning scene fires only when `needs_scene(household)`:

```
needs_scene(hh) = any member has decision_queue items with tier ≥ T1 due today
               OR any member crossed a pressure hysteresis threshold since last scene
               OR calendar exception (festival, invitation, summons, school holiday affecting hh)
               OR active area-event overlaps hh (flood zone, road closure on commute)
               OR hh ∈ attention bubble
               OR staleness refresh (uniform hash-sample, ~every 20 sim-days per hh, for texture drift)
```

Expected fire rate 5-8%/day baseline (≈700-950 scenes), spiking during festivals/area events — which is exactly when fidelity matters.

### 2.2 Prompt assembly (prefix-cache-optimized)

```
[STATIC PREFIX, cached across all households]   sim rules, output JSON schema, style guardrails (§8)
[HOUSEHOLD PREFIX, cached per household]        household card + member persona cards (stable between reflections)
[VOLATILE SUFFIX]                               date/weather/calendar; deltas since last scene (pressure changes,
                                                ledger events, health changes); pending decision items with options;
                                                retrieved memories (top-k per member, rendered as one-liners);
                                                relevant beliefs (claims above credence 0.5 touching today);
                                                incoming messages/invitations
```

### 2.3 Output schema (Pydantic model `HouseholdSceneOut`, hard-validated, one retry on failure then rule-fallback)

```json
{"household_id": 4211, "vignette": "≤2 sentences of scene color for UI",
 "members": [{
   "person_id": 18734, "mood": -0.2,
   "day_plan": [{"t0":"07:40","t1":"08:25","action":"commute","place":"place:shivajinagar_school_3",
                 "mode":"pmpml:155","companions":[18736],"purpose":"drop daughter",
                 "contingencies":[{"if":"delay>15min","then":{"mode":"auto"}}]}],
   "decision_resolutions": [{"decision_id":991,"choice":"attend_hearing","rationale":"lawyer warned of warrant"}],
   "new_commitments": [{"kind":"obligation","spec":{...}}, {"kind":"project","spec":{...}}],
   "messages": [{"channel":"wa:g88","claim_key":"...","text":"..."}],
   "memory_entries": [{"text":"Argued with Sunita about pawning bangles","salience":0.7,"tags":["money","family"]}],
   "relationship_deltas": [{"other":18735,"warmth":-0.1,"trust":0,"note":"fee argument"}],
   "belief_updates": [{"claim_key":"cl:temple_scam_v3","credence":0.8}]}]}
```

### 2.4 Plan compiler

`compile(day_plan) -> [ScheduleAction]`: resolves place refs against Geography (`validate_place`), checks feasibility (`travel_time(a,b,mode,t)` from routing/GTFS), enforces time monotonicity, clamps to opening hours, attaches contingencies as clockwork-evaluable predicates. Repair policy on infeasible steps: shift ≤30min → substitute default mode → drop step + log `plan_deviation` event (which may itself escalate). The compiler is the firewall that keeps LLM hallucination out of the deterministic layer.

## 3. Decision-point taxonomy (general escalation framework)

### 3.1 Trigger classes (exhaustive by construction — each is a *relationship between state and expectation*, not a scenario)

| Class | Definition | Emitting subsystem |
|---|---|---|
| E1 expectation violation | executed action's outcome deviates from plan beyond tolerance (blocked road, absent counterparty, failed purchase, no-show) | Clockwork |
| E2 threshold crossing | any pressure scalar crosses its hysteresis band | MINDS daily tick |
| E3 commitment conflict | two active projects/obligations become jointly unsatisfiable (detected by compiler or tick: time, money, or place conflicts) | MINDS |
| E4 stakes encounter | co-presence with a relation where `edge_stake × info_salience` exceeds θ (creditor met at market; rival at wedding) | Clockwork co-presence |
| E5 information receipt | incoming InfoItem with `relevance × credibility × surprise` above belief-impact threshold | Information (§6) |
| E6 institutional demand | summons/notice/order requiring a response by deadline | Institutions |
| E7 hazard realization | stochastic event struck this person/household | Clockwork |
| E8 attention | user watches/interviews | UI |

### 3.2 Salience appraisal (pure function, no LLM)

```
impact      = Σ_p |predicted Δpressure_p|  +  Σ_projects stake·blocked?
S = clamp( w1·impact + w2·irreversibility + w3·novelty(vs memory/beliefs)
         + w4·social_scope(#edges touched) ) × attention_mult(1|1.5|3)
```

### 3.3 Tier policy (budget-governed)

| S | Resolution |
|---|---|
| < θ0 (0.30) | **rule table**: choice = argmax over option utilities parameterized by traits (e.g., delay→auto iff `risk_tol·urgency > thrift·fare_ratio`); logged as event, may create memory rollup |
| θ0–θ1 (0.60) | **deferred**: queued into next household scene, or into a **micro-decision batch** (one LLM call resolving 10-20 independent small decisions across unrelated people, each a 5-line context card, JSON array out) if deadline precedes next scene |
| θ1–θ2 (0.85) | **T2 scene now**: participants = trigger parties + co-present high-stake edges; same output contract as §2.3 minus day_plan |
| ≥ θ2 or E8 | **T3 premium scene** |

A **budget governor** tracks daily token spend; over budget it raises θ0/θ1 smoothly (graceful degradation to rules) but never gates the attention bubble or E6 deadlines. `hysteresis`: after a scene consumes an E2 trigger at threshold x, the next fire point moves to x+0.15 (decaying back over 20 days) — this is what makes S5 produce ~8 scenes over months instead of daily thrash.

```python
def escalate(trigger) -> None:
    s = appraise(trigger)
    if s < th0(person): resolve_by_rule(trigger)            # writes event + edge/pressure deltas
    elif s < th1(person) and trigger.deadline > next_scene_t(person): queue_for_scene(trigger)
    elif s < th1(person): queue_micro_batch(trigger)
    elif s < th2 and person not in attention: schedule_t2_scene(trigger)
    else: schedule_t3_scene(trigger)
```

## 4. Memory architecture

### 4.1 Substrate: the event log IS shared memory

Every clockwork and scene outcome appends to the canon `event` table (owned by Canon DB, co-designed): `(id, t, kind, actor_ids, place, payload_json, salience)`. Personal memory = **a view over events involving me** + **subjective entries** (the `memory_entries` emitted by scenes — feelings, interpretations, things numbers can't hold). This removes the Stanford per-observation text stream entirely for background people, guarantees cross-person consistency (two witnesses recall the same crash because it's one event row), and enables retro-biography at promotion.

```sql
CREATE TABLE memory_entry (        -- P2+ only
  id INTEGER PRIMARY KEY, person_id INT, t INT,
  kind TEXT CHECK(kind IN ('episodic','reflection','routine_rollup')),
  text TEXT, salience REAL, tags TEXT, event_id INT NULL,
  emb BLOB NULL);                  -- 384-d f16, computed lazily on first retrieval need, local GPU
```

### 4.2 Write paths (zero dedicated summarization calls)

1. **Scene outputs** carry `memory_entries` per participant (§2.3) — free, part of the same call.
2. **Rule resolutions** above salience 0.4 write a template-rendered entry ("Bus 155 never came; took an auto, ₹80").
3. **Routine rollups**: nightly mechanical pass collapses the day's sub-threshold events into counters ("worked, commuted, market ×1") — one row, salience 0.05.

### 4.3 Nightly consolidation (two lanes)

- **Mechanical (everyone P2+, vectorized)**: salience decay `s ← s·0.98^days`, rollups, prune entries with `s<0.02` older than 90 days *unless* tagged to an active project or edge summary.
- **Reflection (gated + batched)**: only persons with `Σ day salience > θ_reflect` OR in attention set; batched 20-25 persons per T1 call (each gets a compact digest in, and out comes: updated `self_narrative`, promoted long-term memories, project status changes, goal revisions, mood reset). ~40-60 calls/night for the start area.

### 4.4 Retrieval

`score = 0.35·exp(-Δdays/30) + 0.30·salience + 0.25·cos(emb, query) + 0.10·participant_overlap(present interlocutors)`; top-k (k=8 for T1/T2, 15 for T3), rendered as dated one-liners. Embeddings: `BAAI/bge-m3` (multilingual — handles Marathi names/terms) via `sentence-transformers` on the local GPU; index in `sqlite-vec`. Query text = trigger description + participants + topic tags. Zero API cost.

## 5. Relationship graph

```sql
CREATE TABLE edge (                -- directed; reverse edge materialized on first divergence, else mirrored
  src INT, dst INT, type TEXT,     -- kin:{spouse,parent,child,sibling,inlaw}|neighbor|coworker|employer|friend|
                                   -- shop_regular|creditor|rival|acquaintance|member_of(org edge uses dst=org)
  closeness REAL, trust REAL, warmth REAL,   -- [0,1],[0,1],[-1,1]
  power REAL,                      -- asymmetry [-1,1] (employer→worker positive)
  freq REAL, last_t INT, stake REAL,          -- stake: how much src has riding on dst
  summary TEXT,                    -- running 1-2 sentence history, updated by scenes only
  history_json TEXT,               -- ring buffer, last 8 notable event refs
  PRIMARY KEY (src,dst,type));
CREATE TABLE grp (id INTEGER PRIMARY KEY, kind TEXT,   -- household|wa_group|workplace|temple_committee|shg|housing_society|school_class|mandal
  name TEXT, place TEXT, admin_id INT);
CREATE TABLE grp_member (grp_id INT, person_id INT, role TEXT, joined_t INT,
  read_prob REAL, post_prob REAL, PRIMARY KEY(grp_id,person_id));
```

**Lazy materialization:** kin/household edges at P1 (procedural from household composition); neighbor edges generated from spatial adjacency (same lane/building, sampled count by density) when first needed; coworker from org roster; shop_regular from schedule-template shop assignments. Mechanical drift: `warmth` decays toward 0 at low `freq`; co-presence without incident nudges `closeness` +ε. Scenes apply `relationship_deltas` and rewrite `summary`. Scale: ~50k × ~15 edges = 750k rows — trivial for SQLite; `networkx` loaded on demand for analytics (invite-list generation, gossip percolation preview, faction detection in S7).

## 6. Information as a first-class object

### 6.1 Schema

```sql
CREATE TABLE info_item (
  id INTEGER PRIMARY KEY, claim_key TEXT,      -- canonical claim family, e.g. 'cl:temple_scam'
  parent_id INT NULL,                          -- variant lineage
  origin TEXT,                                 -- 'event:5512' | 'fabricated:person:881' | 'media:outlet_3'
  veracity TEXT CHECK(veracity IN ('true','distorted','false')),
  claim_json TEXT,                             -- {subject_refs, predicate, quantity, valence, specificity 0..1}
  text TEXT, charge REAL, topics TEXT,          -- charge = emotional arousal 0..1
  distortion_ops TEXT, created_t INT);
CREATE TABLE exposure (info_id INT, person_id INT, t INT,
  channel TEXT,                                -- f2f|wa:grp|media:outlet|overheard|official_notice
  source_ref TEXT, PRIMARY KEY(info_id,person_id));
CREATE TABLE belief (person_id INT, claim_key TEXT, credence REAL,
  exposures INT, lineage_mask INT, top_source TEXT, first_t INT, last_t INT,
  PRIMARY KEY(person_id,claim_key));
```

### 6.2 Propagation (clockwork, no LLM)

- **Face-to-face**: during co-presence windows, for each held item novel to the other party: `p_share = σ(a·sharer.sociability + b·charge + c·relevance(receiver) + d·closeness − e·risk(item, receiver))`.
- **WhatsApp groups**: holders with `post_prob·charge·novelty > u` post; members receive with per-member `read_prob` and lag ~ lognormal(2h). Forwards across groups model S2's citywide jump.
- **Media**: fictional outlets (profiles: language, credibility, sensationalism) subscribe to the event log via a newsworthiness score; publishing creates one item with statistical mass exposure (sampled readers per ward).
- **Ambient**: notice boards, temple loudspeakers, chowk conversations = place-attached broadcast items hitting visitors.

### 6.3 Mutation (mechanical, LLM only at prominence)

Per hop, with `p_mutate ∝ (1−specificity)·hops·(1−sharer.conscientiousness)`, apply structured ops on `claim_json`: `EXAGGERATE` (×1.5-3 on quantities), `GENERALIZE` (drop hedges), `SPECIFY` (inject a plausible local noun from canon), `REATTRIBUTE` (shift subject to a more prominent nearby entity), `MORALIZE` (add blame frame). Text is re-rendered from templates until the variant's exposure count crosses a prominence threshold or enters the attention bubble — then one T1 call writes a natural rendering. Because ops are structured, the UI can show exact drift: origin claim vs. current variant, hop by hop.

### 6.4 Belief updating (mechanical)

```
trust_w  = edge.trust (f2f) | outlet.credibility (media) | 0.9 (official)
novelty  = 1 / (1 + same_lineage_exposures)        # correlated sources discounted
align    = 1 + κ·value_match(topics, persona.values) − κ·value_conflict   # confirmation bias
credence' = σ( logit(credence) + λ·trust_w·novelty·align·item.confidence )
```

Crossing an action threshold (per claim family: e.g., `stop_donating` at 0.7, `warn_others` at 0.6, `report_to_police` at 0.85 modulated by traits) emits an E5 decision trigger — beliefs cause behavior through the same escalation pipeline as everything else.

## 7. Cost model (start area, 12k households, DeepSeek-class @ ~$0.87/M out, cached input)

| Item | Volume/day | Out tok | $/day |
|---|---|---|---|
| Morning scenes (6% gate) | ~750 | 650 | 0.42 |
| T2 scenes | 150-400 | 500 | 0.07-0.17 |
| Micro-decision batches | ~25 calls | 900 | 0.02 |
| Nightly reflection batches | ~50 calls | 1200 | 0.05 |
| Info renders + persona gens | ~100 | 400 | 0.03 |
| **Baseline total** | | | **~$0.6-0.8** (+input ≈ ×1.3) ⇒ **~$25-35/mo** |

Festival/disaster peaks ×3-4 (governor caps at configured ceiling). Leaves $100+/mo headroom for T3 premium focal play (~$1-2 per heavy session-day at Sonnet-class pricing). Scaling to 3.5M: gate rate stays constant per household; attention bubble stays constant-size; only mechanical lanes grow linearly (numpy-vectorized ticks over columns handle millions of rows).

## 8. Sensitivity guardrail (religion/caste/community)

Codes exist in `person` because demographic realism requires them (festival participation rates, marriage-network priors, diet defaults are conditioned statistically). **Prompt firewall:** the static prefix instructs models to never attribute behavior or character to community identity; the context assembler passes only *derived neutral facts* ("family observes vegetarian diet", "family attends the Kasba Ganpati mandal") — raw codes never enter prompts. A post-generation lint (keyword + pattern check) rejects text violating the rule and retries with a stronger instruction. Statistics shape structure; individuals are individuals in prose.

## 9. Core loops (pseudocode)

```python
def sim_day(date):
    events = clockwork.advance_until("06:00", date)           # overnight
    minds.ingest(events)                                       # → decision_queue via escalate()
    for hh in households_where(needs_scene):                   # §2.1 gate
        job = build_morning_job(hh)                            # prefix-cached prompt
        out = gateway.submit(job, schema=HouseholdSceneOut)    # async, batched
        apply_scene(out)                                       # ledger/edges/beliefs/memory/projects
        clockwork.load(compile_plans(out))                     # §2.4
    clockwork.replay_templates(other_households)
    while t < "24:00":
        events = clockwork.advance(step)                       # traffic, co-presence, hazards, deadlines
        minds.ingest(events)                                   # E1..E7 appraisal
        run_due_scenes(); run_micro_batches()
        info.propagate(co_presence_windows, group_posts)       # §6.2, pure clockwork
    daily_tick()                                               # vectorized pressure integrators; E2 triggers
    nightly_consolidate()                                      # §4.3 mechanical + gated reflection batches
```

## 10. Libraries / stack

Python 3.12; **SQLite** (WAL) + **SQLAlchemy Core** (no ORM overhead) + **sqlite-vec**; **Pydantic v2** for every LLM I/O schema; **numpy** for vectorized ticks; **networkx** (on-demand analytics); **sentence-transformers** + `BAAI/bge-m3` on local GPU; **openai** client pointed at DeepSeek endpoint (OpenAI-compatible, prefix caching) with **tenacity** retries; **orjson**; **msgpack** for packed blobs. No services to run, all Windows-friendly, all boring.

## Key decisions

- **Routine-bypass gate on household morning scenes: cached schedule templates replay daily; LLM morning scene fires only on decision-queue items, pressure threshold crossings, calendar exceptions, area events, attention, or ~20-day staleness refresh (revises the working hypothesis of one call per household per morning).** — Literal per-household daily calls cost ~$180+/month re-deriving unchanged routines; the gate cuts to ~$25-35/month while concentrating fidelity exactly where anything non-routine happens — and fires universally more during festivals/disasters when it matters.
  - Rejected: One T1 call per household per simulated morning as hypothesized — rejected on cost/信息 ratio; also rejected pure procedural planning with no scenes (loses family texture and joint decisions).
- **Hybrid personality: packed float trait vector (Big Five + risk/religiosity/sociability/thrift/ambition/temper/credulity) parameterizing clockwork policy tables, plus an LLM-facing persona text card generated once at P2.** — Clockwork must make per-person-varying choices (gossip sharing, transport under delay, compliance) with zero LLM cost, which requires numbers; LLM roleplay requires text. Generating both together keeps them consistent.
  - Rejected: Pure natural-language personas (Stanford-style) — background behavior would need LLM calls or be person-invariant.
- **The shared append-only event log is the memory substrate; personal memory is a view over events involving the person plus subjective entries emitted as structured fields of every scene's JSON output — no per-observation text stream, no dedicated summarization calls.** — Guarantees cross-witness consistency (one crash = one event row), enables lazy retro-biography at promotion (P1 people accumulate history without memory cost), and eliminates an entire class of LLM calls since scene outputs already carry memory/relationship/belief deltas for free.
  - Rejected: Stanford generative-agents per-person observation streams with separate importance-scoring and summarization calls — 100-1000x the token cost and prone to inter-agent contradiction.
- **Months-long pressure via numeric integrators with hysteresis: p_financial/p_health/p_family/p_job/p_social/p_legal updated by vectorized daily ticks from structured records (obligations, conditions, edges); LLM scenes fire only at threshold crossings, and each fired threshold rises by 0.15 (decaying over 20 days).** — S5-class arcs compress to ~6-10 scenes over months; each scene sets commitments (new loan, pawned asset, job search project) that change the integrator slope, so the trajectory is genuinely path-dependent without daily cognition.
  - Rejected: Periodic LLM check-ins for everyone (cost-prohibitive) or pure scripted arc stages (kills generality and player-visible causality).
- **Information mutation is mechanical structured ops (EXAGGERATE, GENERALIZE, SPECIFY, REATTRIBUTE, MORALIZE) on claim_json with template re-rendering; LLM rewrites variant text only above an exposure-prominence threshold or inside the attention bubble.** — Rumor drift becomes cheap, measurable, and auditable (UI can diff origin vs variant hop-by-hop); LLM prose is spent only on variants people will actually read.
  - Rejected: LLM rewrite per transmission hop — unaffordable at gossip volumes and untrackable for the veracity/lineage UI.
- **Belief updating is a closed-form rule: logit-space update weighted by source trust, lineage-discounted novelty (correlated sources count less), and a confirmation-bias alignment term from persona values; action thresholds convert credence into E5 decision triggers.** — Belief must update on every exposure for thousands of people; the lineage discount prevents echo-chamber double-counting (a WhatsApp forward seen thrice ≠ three independent witnesses), and thresholds route belief into the same general escalation pipeline as physical events.
  - Rejected: LLM-adjudicated belief updates (cost), or naive exposure counting (rumors would become certain after any viral forward).
- **SQLite + sqlite-vec + SQLAlchemy Core for everything including the 750k-row relationship graph and vector retrieval; networkx loaded on demand for analytics; embeddings computed locally with bge-m3.** — Solo dev on Windows, boring proven tech, zero services to operate; the scale (even 3.5M people × 15 edges) is comfortably within SQLite with WAL; local embeddings make retrieval free.
  - Rejected: Neo4j/graph DB and hosted vector DB — operational burden and cost with no capability the queries actually need.
- **Decision escalation is an 8-class trigger taxonomy (expectation violation, threshold crossing, commitment conflict, stakes encounter, information receipt, institutional demand, hazard, attention) + a single salience appraisal function + a budget-governed tier policy with deferred/batched lanes (queue-to-next-scene, micro-decision batch calls).** — The classes are relations between state and expectation, not scenarios, so any novel situation maps onto them; deferral and batching are the main cost levers; the budget governor degrades to rules gracefully under load while protecting attention and legal deadlines.
  - Rejected: Enumerated situation lists (violates the generality mandate) or escalating everything non-routine immediately (cost spikes, no batching).
- **Fictional media outlets with realistic profiles (language, credibility, sensationalism) instead of real named newspapers publishing generated stories.** — Attributing fabricated stories to real outlets crosses the reality-anchor line the same way real named people would; fictional outlets keyed to the real media landscape preserve realism without misattribution.
  - Rejected: Using real outlet names for generated articles.
- **Community/religion codes stored statistically but firewalled from prompts: only derived neutral facts (diet, festival attendance, network membership) enter context; static prefix bans identity-attributed characterization; post-generation lint retries violations.** — Demographic realism requires the structure (marriage networks, festival participation) but the generality/respect mandate requires it never surfaces as stereotype in prose.
  - Rejected: Omitting the dimensions entirely (breaks realism of S4 weddings, S8 mandals) or passing raw codes to the LLM (stereotype risk).

## Interfaces

- **Clockwork Scheduler**: MINDS→Clockwork: compile_plans(HouseholdSceneOut) -> list[ScheduleAction{person_id, t0, t1, action, place, mode, companions, contingency_predicates}] loaded via clockwork.load(); clockwork.replay_templates(hh_ids) for gated households. Clockwork→MINDS: minds.ingest(events: list[SimEvent{id,t,kind,actor_ids,place,payload,salience}]) covering expectation_violation, hazard_realization, co_presence_window, deadline_passed, txn_due — MINDS returns list[ScheduleMutation] for immediate replanning.
- **Canon DB / Event Log**: Shared append-only event table (canon.append(Event) -> event_id, canon.events_for(person_id, since)); MINDS writes persona cards, memory_entries, edges, beliefs, projects via canon.put(fact, contradiction_check=True) which rejects writes conflicting with existing canon; reads via canon.get_digest(person_id) for promotion backfill.
- **LLM Gateway**: gateway.submit(PromptJob{tier: T1|T2|T3, prefix_key: str (cache identity), messages, schema: PydanticModel, priority, deadline_t}) -> Future[ValidatedOutput]; gateway handles batching, prefix caching, schema validation with one retry, and reports token spend to MINDS' budget governor via gateway.spend_today() -> TokenStats.
- **Attention / UI**: UI→MINDS: set_attention(person_ids | area_bbox) (drives P3 promotion and tier multipliers); interview(person_id, user_text, mode: 'in_world'|'omniscient') -> Reply (in_world runs T3 with persona+retrieval+trust-gated candor toward a stranger edge; omniscient returns state directly); inspect(person_id) -> StateReport{pressures, projects, ledger, beliefs, top_memories}; explain_last_decision(person_id) -> DecisionTrace{trigger, salience_terms, tier, choice, rationale}. MINDS→UI: info feed get_feed(scope) -> list[InfoItem+lineage_diff].
- **Institutions (police, courts, PMC, hospitals, schools, employers)**: Institutions→MINDS: E6 events {kind: summons|notice|order|bill|admission|termination, person_id, deadline_t, payload}; MINDS→Institutions: ComplianceDecision{comply|delay|contest|ignore, rationale} and Statement{person_id, event_id, claim_json, candor} (testimony/FIR content derived from the person's memory view and beliefs, so accounts can honestly diverge); employment events (quit, apply, hired) as canon events institutions consume.
- **Geography / Mobility**: MINDS calls validate_place(ref) -> PlaceInfo{exists, hours, capacity} and travel_time(a, b, mode, t) -> minutes during plan compilation; receives place-attached broadcast surfaces (notice boards, loudspeaker ranges) for ambient info channels.
- **Economy / Organizations**: Orgs→MINDS: payroll/price/hiring events into ledgers (txn stream); MINDS→Orgs: labor-supply decisions and consumption pattern changes as events (e.g., household austerity flag reduces discretionary spend multiplier used by commerce simulation).
- **Information consumers (police intelligence, election module)**: subscribe(topic_filter) -> stream[InfoItem exposures aggregated by ward]; e.g., police receive report-type items crossing credence-action thresholds; the S7 election module reads ward-level belief distributions over civic-issue claim families as sentiment input.

## Scenario traces

# Scenario traces (each traced as instances of the general mechanisms — no scenario-specific code anywhere)

## S1 — Bus crash, 8:10am Shivajinagar (acute physical)
Clockwork's hazard sampler (NCRB road-accident base rate × traffic density at that segment × time-of-day) realizes a collision event involving the bus's occupant manifest. **E7** triggers fire for father and daughter (both P1 → auto-promoted to P2 with retro-biography constrained by their event digests). Injury outcomes are hazard-table rolls creating `health_condition` rows (father: fracture, severity 0.55, treatment_place inst:sassoon, work_capacity 0.2). The father's appraisal: impact spans p_health, p_financial (daily_cost + work loss), p_family; irreversibility high → S≈0.9 → **T2 scene** at the crash site (participants: father, a co-present bystander with high-stakes edge — none, so minimal cast). Scene output: memory entries ("the truck came from nowhere"), a Statement object consumed by Institutions when police arrive (FIR under BNS from *his memory view*, which may differ from the truck driver's), messages (call wife → she receives an E5 info item with trust_w=spouse≈0.95, credence→0.9 instantly, her own appraisal → T2 panic-and-respond scene → she abandons her day-plan; compiler emits plan_deviation events). School absence: clockwork detects daughter's expectation violation at attendance (**E1** at school side); classmates' parents get a wa:school_class group item ("accident on the school route!") whose claim mutates via EXAGGERATE across hops ("three children hurt"). Mother's `p_family` and father's `p_financial` integrators now carry the arc forward for months with no further LLM until thresholds cross (medical bills, missed work → potentially spawning an S5-like sub-arc) — and the truck driver's E6 stream begins (S6).

## S2 — Temple donation scam rumor (informational)
Origin: either a real canon event (a treasurer's irregularity clockwork-sampled from fraud base rates → veracity 'true') or fabrication (a person with high temper + low conscientiousness after a dispute → veracity 'false'); either way one `info_item` with claim_json {subject: org:temple_trust, predicate: misappropriated, quantity: ₹2L, specificity 0.4}. Propagation is pure clockwork: f2f shares in co-presence windows at the temple and market (p_share high — charge 0.8, high local relevance); a devotee posts to wa:mandal_group; a cross-group forward jumps neighborhoods. Per-hop mechanical mutation: EXAGGERATE (₹2L→₹10L), REATTRIBUTE (treasurer→head priest), MORALIZE ("this is why you never trust..."). Exposure count crosses prominence → one T1 call renders the dominant variant naturally; a fictional Marathi daily's newsworthiness score triggers a published item (credibility 0.7, mass exposure). Each exposed person's belief updates mechanically — lineage discounting means ten forwards of the same variant move credence far less than one trusted f2f account; high-religiosity personas get a *negative* alignment term (motivated disbelief). Individuals crossing action thresholds emit **E5** decisions: stop_donating (rule-resolvable), confront_committee (T2 scene at the temple), report (Statement to police → institutions). The UI can show the exact lineage diff from origin claim to street version.

## S5 — Job loss spiral (slow personal arc, the no-daily-LLM proof)
Employer org emits termination event → **E6**. T2 scene: shock, memory entries, new project {kind: find_job, stake 0.9}, relationship_deltas (edge to employer: warmth −0.6). From then on, **zero scheduled LLM calls**: daily tick recomputes p_financial from the ledger (income stream removed; obligations unchanged); due-date passes → missed EMI event → missed_count++, p_financial slope steepens, p_family drips +0.005/day. Crossing 0.6 → **E2** → household morning scene fires (gate condition): family argues, commitments out: pawn jewelry (txn +liquid, p_financial dips — slope changes), cut discretionary spend (austerity flag → economy interface). Hysteresis raises next threshold to 0.75. Weeks later: school-fee obligation defaults → school institution emits notice (**E6**) → scene → informal loan from moneylender (new obligation, rate 3%/mo, new creditor edge with power 0.8, stake encounter risk **E4** whenever co-present at the market). The find_job project's progress is clockwork (application events at base rates modulated by ambition trait and labor market state); an offer event → scene → recovery slope, or repeated failures push p_family toward a 0.85 crossing (marital-crisis scene) — spiral and recovery emerge from the same integrator arithmetic. Months ≈ 6-10 scenes total; the interview tool at any point retrieves the accumulated memory entries, so the man can *tell you* his whole descent coherently.

## S7 — 2026 PMC ward election (city-scale process)
The election module (Institutions) runs the process; MINDS supplies the human substance through general mechanisms. Candidates are P2/P3 persons with projects {kind: contest_ward_seat, stake 1.0}. Campaigning = scheduled canvassing actions (day-plans from the candidates' scenes) creating co-presence windows → **E4/E5** at scale: campaign claims are info_items (veracity mixed) propagating through wa_groups and mandal networks with the standard belief pipeline; ward-level credence distributions over civic-issue claim families ("corporator ignored the flooding on our lane" — possibly seeded by S3's canon events, since PMC complaint events are in the log) are the sentiment the election module reads. Voting is a rule-table decision for nearly everyone (traits × beliefs × edge to candidate networks — no LLM), with T1 micro-batches only for cross-pressured voters the user zooms into. The loss emits E6/E7-class events for the corporator (a job loss — literally re-entering the S5 mechanism with p_social prominent) and belief/priority shifts propagate as the new corporator's actions become events.

## S8 — Ganeshotsav (mass event)
The real calendar anchor flips a 10-day regime: mandal groups (grp kind already exists) get elevated post rates; processions are clockwork area events creating road closures → mass **E1** expectation violations, resolved overwhelmingly by rule tables (reroute/delay per traits) with only high-stakes conflicts escalating; commerce spike via consumption multipliers; bandobast is the police institution's schedule. The morning-scene gate fires broadly (calendar exception) early in the festival — households plan visits, volunteering, spending (a thrift-strained S5 family's scene naturally weighs mandal contribution against EMIs, because the same integrators sit in the same prompt context). Crowd inflow = temporarily materialized P1 visitors. Budget governor raises rule-resolution thresholds under load; the attention bubble keeps full T3 fidelity wherever the user stands in the procession.

## S4 (compact) — Kasba Peth wedding
A project {kind: organize_event} on the host household → scenes produce invitation messages (info channel) → invitees' E5/calendar exceptions gate their morning scenes; pandal lane-block is a place event → neighbors' E1s; shopping surge = txns + shop_regular edge traffic; new edges (in-law materialization) post-event.

## S6 (compact) — Truck driver's case
Pure E6 cadence: court institution emits hearing/adjournment events over years; each is appraised — most resolve by rule (attend, pay lawyer txn) with p_legal ticking upward between scenes; only status changes (charge framed under BNS, warrant risk, settlement offer) cross thresholds into scenes. Three years ≈ a dozen scenes, a slowly grinding p_legal/p_financial couple, and a memory stream that makes the driver bitter and articulate about it in interviews.

## Generality argument

Every probe — and any unseen situation — traverses one pipeline: (1) something becomes an event in the shared log (clockwork hazard, institutional act, scene outcome, or information exposure); (2) events mutate structured state (ledger rows, health conditions, edges, beliefs, project blockers) through domain tables, not scenario code; (3) state changes are appraised by the single salience function against the 8 trigger classes, which are defined as *relations between state and expectation* (violation, threshold, conflict, stakes, information, demand, hazard, attention) rather than situation types — so a flood, a wedding invitation, a court summons, and a viral rumor are all just different emitters hitting the same appraisal; (4) whatever escalates resolves through the same tiered scene machinery with one output contract (day-plan steps, commitments, memory entries, relationship deltas, belief updates, messages), and those outputs feed back into (1). Adding a new life domain (say, a landlord dispute, a pilgrimage, an exam season) requires only new event kinds and base rates in clockwork plus possibly new obligation/project/condition *rows* — never new MINDS code paths, because pressures are domain-agnostic integrators, projects are a generic try-something-over-time record, information is a generic claim object with generic transmission/mutation/belief rules, and relationships are generic typed edges. The probe set demonstrates coverage across every axis the design distinguishes: acute vs. slow (S1 vs S5/S6), physical vs. informational (S1/S3 vs S2), individual vs. area vs. city-scale (S5 vs S3 vs S7/S8), spontaneous vs. planned (S1 vs S4), and each trace above uses only the general mechanisms — the traces contain no if-scenario branches, only parameter values. The strongest evidence is compositional: S1 spawns an S5-shaped financial arc and the S6 legal arc automatically, and S3's flood complaints become S7's election issues, because consequences propagate through shared state rather than scripted links.

## Open questions

- Ownership boundary with the Economy subsystem: MINDS holds household ledgers and labor decisions, but who simulates small-business revenue (a shopkeeper's daily takings feed his household income)? Proposed: Economy owns org-side flows and posts income events; needs joint schema sign-off on txn/obligation tables.
- Tuning the appraisal constants (θ0/θ1/θ2, pressure slopes, hysteresis step): the design assumes ~6% morning-scene fire rate and ~8 scenes per months-long arc — these need calibration runs with logged distributions and a replayable seed harness before the numbers can be trusted.
- Language texture: should T3 focal dialogue be English with Marathi/Hindi code-switching flavor (cheap, readable) or genuinely bilingual with translation on demand (more authentic, more tokens, harder to lint)? Affects the style prefix and the sensitivity lint.
- Retro-biography contradiction checking at P2 promotion is described as a canon-DB reject-on-conflict, but fuzzy contradictions (a generated memory that merely strains a logged event) need either an embedding-similarity heuristic or an LLM audit pass — cost vs. consistency trade-off unresolved.
- Interview candor model: how much should an in-world interviewee reveal to the user-avatar (a stranger)? Current design gates on a default low-trust edge, but repeated interviews should build that edge — does the user avatar persist as a canon person, and does their presence contaminate the sim (people gossiping about the curious outsider)? Deliberate design choice needed: observer effect as feature or bug.
- WhatsApp group topology bootstrap: real group structures (family groups, society groups, mandal groups) must be procedurally generated from the relationship graph at materialization — the generation priors (group count per person, size distributions) need grounding data or defensible guesses.
- Budget governor fairness under sustained load (e.g., all 10 Ganeshotsav days): raising thresholds globally may starve slow-burning arcs (S5 threshold crossings deferred repeatedly). May need per-person starvation credits so deferred triggers gain salience over time.
- Premium-model choice and caching strategy for T3: whether the premium tier can share the T1/T2 prompt prefix format (enabling fallback) or needs its own richer scene format, and whether local-GPU serving of a mid-size model could replace the cheap API tier for micro-batches.

## Red-team critique (verdict: needs_changes)

- **[critical]** The 8-class trigger taxonomy is negativity-complete but opportunity-blind, and there is no mechanical re-optimizer for compiled routines. Positive structural changes (new metro, a cheaper shop, a better job option) fire NOTHING: no expectation is violated (the old bus still comes), no pressure moves, no demand arrives. E5 dead-ends because belief action-thresholds are hand-authored per claim family (stop_donating 0.7, report 0.85...) with no action that mutates a schedule template. 'Exhaustive by construction' is false, and the per-claim-family action tables are themselves scenario code smuggled past the generality mandate — every new life domain needs authored thresholds and an authored action vocabulary.
  - Fix: Add an E9 'affordance delta' trigger, still defined as a state-vs-expectation relation: expectation = 'my compiled template is near-optimal'; fire when a mechanical re-evaluation of cached template legs against the changed environment (new travel_time, new price, new capacity) shows improvement beyond a trait-scaled tolerance. Resolve ~99% by the existing trait-parameterized mode/choice rule tables (zero LLM), escalating to a scene only when the change interacts with household logistics above salience. Add a generic `replan(domain)` entry to the belief action vocabulary so information receipt can trigger mechanical re-optimization, and derive per-family action thresholds from (claim predicate class x traits) instead of hand-writing them per family.
- **[critical]** The §8 identity firewall structurally breaks any scene where identity IS the load-bearing conflict (inter-religious marriage, housing discrimination, communal tension). The context assembler cannot express 'both families object because the couple is inter-community' using only derived neutral facts, and a keyword+pattern lint will reject exactly the realistic output the scenario requires (characters voicing communal objections), retrying into evasive mush or burning calls on an unpassable constraint. The design's own guardrail manufactures LLM slop precisely where the user asked for care and realism.
  - Fix: Split the rule into two levels: (N) narrator level — the simulation's own voice never attributes traits/behavior to community identity (keep enforcement here); (C) character level — characters may hold and voice identity-based positions when those positions are canon state (a structured `objection` record on the edge/project with a reason field). Allow relational identity facts ('this is an inter-community match; both families oppose it') into prompts as mediated context, with a care-style instruction (individuals not archetypes, specificity, no slurs, no group generalizations in narration). Replace the keyword lint with a cheap T1 classifier checking narration-level violations only, capped at 2 retries then human-review queue.
- **[critical]** No household lifecycle exists: person.household_id is NOT NULL, and there are no operations for household formation, split, merge, or dissolution. Marriage (the sim's S4 probe included!), elopement, widowhood, migration, and disaster displacement (wada collapse) all mutate household composition — accounts, obligations, schedule templates, morning-scene identity, and prefix-cache keys are all household-keyed, so this gap blocks multiple holdout scenarios and core life events, not one.
  - Fix: Add household lifecycle events (spawn/split/merge/dissolve) as first-class canon events with explicit migration rules: account partitioning, obligation reassignment (who keeps the EMI), template recompilation for affected members, edge rewrites (new in-law/kin edges), and a household membership history table so retro-biography and the event-log view stay coherent across moves.
- **[critical]** The §7 scaling claim is self-contradictory: 'gate rate stays constant per household' means scene volume grows LINEARLY with households, yet the text claims 'only mechanical lanes grow linearly'. At 3.5M people (~875k households), 6% gate = ~52k morning scenes/day plus proportional T2/reflection — roughly $1.5-2k/month, a 50-70x blowup over the advertised $25-35/month, before festival spikes.
  - Fix: Add a spatial/narrative LOD for scene eligibility, symmetric to the P0-P3 person tiers: full gate only inside active regions (attention bubble + households carrying live arcs/projects + event epicenters); elsewhere, non-routine triggers resolve by enriched rule tables with event logging only, and P2-style scene backfill happens on later attention (the retro-consistency machinery already supports this). Make total daily scene budget the control variable and LOD radius the actuator, so cost is constant by construction at any city size.
- **[major]** Event-to-info-item genesis is unspecified: every trace hand-waves it ('classmates' parents get a wa:school_class item') but no rule says which witnessed events spawn gossip items, with what claim_json, held by whom. This breaks secrecy/discovery dynamics (who fabricates 'Priya was seen with that boy'?), fear propagation after a chain-snatching, and crash rumors alike — the info system models transmission/mutation/belief of items that already exist, but item birth from observed world-state is a missing general mechanism.
  - Fix: Define a notability function over events: p_spawn = f(event salience, charge of event kind, witness's novelty-vs-expectation, witness traits [sociability, credulity]); witnesses passing the roll become origin holders of an item whose claim_json is template-derived from the event kind (subject_refs from actor_ids, quantity from payload). One rule serves the aunty sighting, the crash WA post, and dusk-crime fear.
- **[major]** The scene output contract (day_plan, resolutions, commitments, messages, memory, relationship_deltas, belief_updates) cannot express physical incidents. A confrontation scene cannot output 'the brother struck him' -> health_condition; a scene at a collapsing wada cannot injure anyone. Violence, accidents-during-scenes, and sudden physical outcomes have no typed path from LLM output into the hazard/health tables.
  - Fix: Add a validated `incidents` field to the contract: the scene declares a typed act (assault, fall, collapse_exposure) and the same clockwork hazard-outcome tables roll severity/consequences — the LLM never sets severities directly, preserving the firewall principle that prose renders state but numbers own outcomes.
- **[major]** Async timing semantics are unspecified: the sim loop advances clockwork while gateway futures resolve, so a T2 scene's output can arrive after the sim time it was supposed to mutate (a day_plan for a morning already simulated). No barrier, ordering, or pending-cognition state is defined; this is the class of bug that silently corrupts causality and will consume weeks of solo-dev debugging.
  - Fix: Introduce a `cognizing` person state: participants freeze at current place (clockwork holds them, low detail) until the future resolves or its sim-time deadline passes (then rule-fallback fires). Wire the existing PromptJob.deadline_t into this contract and make apply_scene reject outputs whose sim-time window has closed, logging a deviation instead.
- **[major]** The festival calendar exception fires the morning gate for essentially ALL households (Ganeshotsav 'affects hh' for everyone) — 12k scenes on day 1 at the start area, 875k at city scale. The budget governor then raises thresholds, i.e., the design degrades to rules exactly when it claims 'fidelity matters most'. The two claims contradict each other.
  - Fix: Calendar exceptions should trigger a mechanical template swap to pre-compiled festival-mode schedule variants (visits, spending multipliers, mandal duty) — zero LLM. LLM scenes fire only where the festival intersects live pressure or pending decisions (the S5 family weighing mandal contribution vs EMI already fires via E2/E3, not the calendar). This preserves the beautiful festival-vs-debt scene while removing the blanket gate.
- **[major]** Per-person exposure and belief rows explode at scale: one viral item with citywide reach writes millions of exposure rows, and dozens of viral items per month make the exposure table the largest object in the DB — mostly for P0/P1 people who will never be asked about it.
  - Fix: Statistical exposure for P0/P1: ward x item x channel counters with sampled realization into individual rows only at P2 promotion (same lazy pattern as biography backfill). Individual exposure/belief rows only for P2+. Cap concurrently-active claim families with LRU archival to a compressed history table.
- **[major]** Co-presence processing is O(n^2) per venue window: E4 stake checks and f2f share rolls 'for each held item novel to the other party' over a market or procession with thousands co-present is intractable at 3.5M and already hot at 50k during festivals.
  - Fix: Sample encounters: Poisson number of meaningful dyads per venue-tick scaled by venue size, weighted toward pairs with existing edges and high-stake edges (E4 candidates enumerated from the edge index, not the crowd). Exact pairwise only for venues under ~30 occupants or containing attention/arc persons. Restrict f2f item propagation to the top-N currently 'hot' items per ward.
- **[major]** claim_key canonicalization is unowned: scenes emit messages with LLM-chosen claim_keys, so the same rumor fragments into cl:temple_scam / cl:temple_fraud / cl:mandir_money, silently breaking lineage discounting (the anti-echo-chamber mechanism), belief merging, and the drift-audit UI.
  - Fix: Never trust LLM-provided keys: a canonicalizer embeds the claim's structured fields + text, matches against existing families above a cosine threshold (bge-m3 is already local and free), assigns the canonical key or mints a new family. All claim_key fields in scene output become free-text descriptions that pass through this service.
- **[major]** Edge `summary` prose is iteratively rewritten by scenes from partial context — a telephone game over hundreds of scenes per long-lived edge that will drift away from the actual event history (canon-consistency decay in exactly the records that make relationships feel real).
  - Fix: Make the summary a structured list of dated facts appended by scenes (bounded, oldest compressed), rendered to prose on demand at prompt time; or regenerate periodically from history_json ring buffer + event-log query, never from the previous summary alone.
- **[major]** No schema-evolution/migration story. A solo dev will iterate on schemas weekly; without migrations, every change orphans long-running saves — and this sim's whole value proposition is months-long continuous runs.
  - Fix: Alembic migrations from day one, plus lean on the architecture's own strength: the append-only event log is the source of truth, so ship a 'rebuild derived state from log' path (pressures, edges, beliefs recomputed) as the escape hatch when a migration is too painful. Version the Pydantic scene schemas explicitly and keep old parsers.
- **[major]** Relationships are pure consequence-recorders — edge scalars never cause anything (except E4 which needs pre-existing stake). No trigger fires from edge-state evolution, so endogenous relationship transitions (friendship deepening into romance, a business partnership forming, a rivalry igniting) cannot initiate; the inter-religious-marriage scenario cannot begin without special-casing, and the marriage-network priors would only ever produce assortative matches.
  - Fix: Extend E2 threshold-crossing to edge scalars: closeness/warmth/trust crossing bands on qualifying edge types emits an escalation candidate (with the same hysteresis machinery), letting a clockwork 'relationship transition' lane propose typed transitions (friend->romantic, acquaintance->partner) at trait- and context-conditioned base rates. Love matches against network priors then emerge from co-presence + edge dynamics rather than being sampled from priors that suppress them.
- **[major]** Systemic negativity bias in the escalation design: cognition fires almost exclusively on trouble (violations, debts, summonses, hazards), so the simulated texture trends toward misery porn — a top 'feels like real life' failure mode. Real lives are also promotions, births, passed exams, and relief when the last EMI clears.
  - Fix: Make E2 fire on downward crossings too (relief/celebration scenes when p_financial drops below its band); emit achievement events when projects reach 'done' with stake above threshold; add positive event kinds to hazard tables (job offer, prize, good harvest of respect) at calibrated base rates. Same machinery, sign-symmetric.
- **[minor]** Mirror-until-divergence edge storage is a write-path bug farm: every relationship_delta application must detect 'first divergence' and materialize the reverse row mid-transaction; a missed check silently corrupts both parties' views.
  - Fix: Materialize both directed rows at creation. 2x rows on a 750k-row (or even 52M-row) table is trivial for SQLite and deletes an entire class of bugs.
- **[minor]** Scene -> infeasible plan -> plan_deviation -> escalation -> scene loop: the compiler's drop-step policy emits events that 'may itself escalate', so a model that keeps emitting infeasible plans can ping-pong with the escalator, burning budget.
  - Fix: Damping rule: repair-generated deviations from a person's own scene output cannot escalate above rule-tier for the rest of that sim-day; track a per-person daily repair-escalation counter.
- **[minor]** Daily vectorized write-back of pressure columns for 3.5M person rows into SQLite is minutes of wall time per tick, and trait/pressure reads for appraisal hit the same hot columns.
  - Fix: Keep hot scalar state (pressures, mood, traits, hysteresis) in memory-mapped numpy arrays indexed by person_id, checkpointed to disk; SQLite keeps relational and durable records. This is also simpler code than staged bulk UPDATEs.
- **[minor]** Cost model assumes cached input, but the per-household prefix gets ~zero cache hits: each gated household fires at most one scene per day and provider prefix-cache TTLs are minutes-to-hours, so household prefixes are cold. Micro-decision batches mixing 10-20 strangers also risk JSON misalignment/context bleed on a cheap model.
  - Fix: Recompute the cost table with only the static prefix cached (input roughly 2-3x the stated multiplier — still cheap at 50k, but the number should be honest since it feeds the scaling decision). For micro-batches: strict per-item IDs echoed in output, per-item schema validation, discard-and-singleton-retry for misaligned items.
- **[minor]** Budget-governor starvation of slow arcs under sustained load is acknowledged in open_questions but undesigned; a 10-day festival deferring an S5 crossing repeatedly, then releasing a thundering herd of stale scenes, is both a fidelity and a spike problem.
  - Fix: Salience aging: deferred triggers gain +delta salience per deferred day (bounded), and the governor drains the deferred queue at a fixed trickle rate rather than releasing on threshold restoration.

### Novel holdout-scenario traces

HOLDOUT CHOICE: The dog attack, wada collapse, and chain-snatching are all E7-hazard variants of the already-traced S1 bus crash (acute event, injuries, FIR, fear-rumor) — the design absorbs them with parameter changes (the wada collapse additionally hits the household-displacement gap, noted in issues). The saree price war mostly lives in the Economy subsystem. The two scenarios that stress mechanisms the probe set never exercised are the METRO OPENING (positive structural change) and the INTER-RELIGIOUS MARRIAGE (identity-load-bearing conflict + endogenous relationship formation). Traced below.

=== TRACE A: New metro station opens, shifts commute patterns ===
Step 1 — emission: Geography updates its transport graph. The MINDS-Geography contract is only validate_place/travel_time/broadcast surfaces; there is NO interface for 'mobility graph changed'. First break: the event cannot even reach MINDS as a typed signal.
Step 2 — trigger walk (this is where the taxonomy fails): E1 expectation violation — no fire: existing plans still execute within tolerance; the old bus still comes. E2 — no pressure scalar moves. E3 — no project conflict. E4 — no encounter. E5 — partially fires: a 'metro open' info item propagates via media/ambient/WA and credence rises... but E5's consequence path is belief -> hand-authored per-claim-family action thresholds (stop_donating/warn_others/report), and NO action in that vocabulary mutates a schedule template. Belief in 'metro open' is dutifully recorded and changes nothing. E6/E7 — no. E8 — only if the user stares at the station. NET: for ~everyone, the design produces zero behavior change. The 'exhaustive by construction' claim is falsified by any positive-affordance event; the same hole swallows the saree price war (customers can't notice a price cut), a new school, a better job market.
Step 3 — even with a trigger, the resolution path is wrong-cost: compiled routines are produced only by LLM morning scenes and replayed by clockwork; there is no mechanical template re-optimizer. Either you fire scenes for every catchment household (3-8k at 50k pop; low hundreds of thousands at 3.5M — an absurd LLM spend to decide 'take the metro instead of bus 155', which is a rule-table decision the trait vector already parameterizes for the E1-delay case) or you write special-case template-patching code — exactly the scenario code the mandate bans. The 20-day staleness refresh eventually catches it, meaning Pune ignores its new metro for up to three weeks, then pays LLM prices to notice it.
Step 4 — what WOULD work after the fix: with an E9 affordance-delta trigger (mechanically re-evaluate cached template legs against the new graph, fire on trait-scaled improvement) + info-gated awareness, adoption emerges realistically: word-of-mouth item spreads, high-novelty/risk_tol people trial first, a positive travel-time surprise updates the template via a new 'adopt improvement' arm in the repair policy (currently repairs only shift/substitute/drop — it can fix a broken plan but never keep a better one). Downstream, the design already shines: shop_regular edges shift with recompiled templates, rickshaw drivers at the dead bus stop lose income and enter the S5 machinery via Economy events, E4 encounters relocate to the metro concourse.
BREAK SUMMARY: missing Geography delta interface; missing opportunity trigger class; missing mechanical template re-optimization; belief action vocabulary can't express 'replan'; repair policy has no adopt-improvement arm.

=== TRACE B: Inter-religious couple marries against both families' wishes ===
Step 1 — genesis: the scenario cannot begin. Edge types include no romantic category; edge dynamics are warmth-decay + closeness+epsilon on co-presence; and NOTHING escalates from edge state — walk the taxonomy: no violation, no pressure crossing (closeness is not a pressure; there is no p_romance), no project conflict (no project exists yet), E4 needs pre-existing stake, no info/demand/hazard, no attention. So two classmates can reach closeness 0.95 and no scene ever fires to create the project {kind: marry}. Worse, marriages otherwise come from 'marriage-network priors' — statistically assortative sampling that structurally suppresses exactly this tail. Without a new mechanism (edge-scalar thresholds feeding E2 + a relationship-transition lane), the scenario is impossible rather than rare — silent special-casing guaranteed.
Step 2 — secrecy/discovery: assume courtship exists. Their meetings are clockwork co-presence windows. Personal memory as 'events involving me' correctly keeps the father ignorant by default — good. But discovery requires a witness to turn an observed pattern into gossip ('seen with that boy at the chowpatty'), and the info system has NO event-to-item genesis rule: items originate from 'event:id' (who decides? never specified), vague fabrication, or media. The S1 trace hand-waved this same gap ('parents get a wa: group item'). Here it is load-bearing in both directions: drama needs the aunty's sighting to exist; concealment needs it to be probabilistic and trait-driven. Needs the notability-function fix.
Step 3 — the decision and family conflict: 'marry against both families' wishes' is a normative conflict; E3 only detects time/money/place unsatisfiability, so it cannot represent it. The arc actually routes via E5 (parents receive the item, high charge -> T2 confrontation scene) and then the S5-style integrator machinery (p_family/p_social on five people, hysteresis-spaced scenes: ultimatum, brother's intervention, mother's grief, elopement decision as a commitment changing slopes) — this part genuinely works and is the design at its best. But note it works by accident of routing, not because the taxonomy represents normative conflict.
Step 4 — the §8 firewall collision (hard break): the confrontation scene's prompt must convey WHY the families object. The context assembler passes only derived neutral facts — 'family observes vegetarian diet' cannot encode 'they oppose the match because the boy is from the other community'; raw codes are banned from prompts, so the prompt cannot state the conflict at all. If smuggled in, the OUTPUT — parents voicing communal objections, which is what care-and-realism requires — trips the keyword lint, gets rejected, and retries 'with a stronger instruction' against an unpassable constraint: the guardrail manufactures either evasive slop or an infinite retry loop, precisely on the scenario demanding the most human treatment. Needs the narrator/character two-level policy: narration never attributes behavior to identity; characters may hold identity-based positions stored as structured canon (objection records with reasons), rendered as individual beliefs.
Step 5 — mechanics of the act: Special Marriage Act 30-day public notice maps beautifully onto E6 + the notice-board ambient channel (the notice leaking to families via the info system is chillingly realistic — once genesis exists). But two contract gaps: (a) a confrontation scene cannot output a physical incident — the output schema has no path from 'the brother struck him' to a health_condition; (b) elopement/marriage itself has NO state mutation path: household_id is NOT NULL, and there are no household split/formation operations — the couple literally cannot leave home or form a new household. Accounts, obligations, templates, and prefix-cache keys are all household-keyed, so this is a schema-level dead end (which the wada-collapse displacement holdout would also hit).
Step 6 — aftermath: estrangement as kin edges at warmth -1 with grp_member expulsions (mandal, family WA group) — works; reconciliation via an E4 funeral encounter — works; a child's birth — blocked again on the missing household/person lifecycle.
BREAK SUMMARY: no endogenous relationship-transition mechanism (scenario cannot initiate); no event-to-info genesis (secrecy/discovery undefined); §8 firewall structurally incompatible with identity-load-bearing conflict; scene contract cannot express physical incidents; no household lifecycle for elopement/marriage/birth. Steps 3's escalation-and-integrator phase is the only leg that traverses cleanly.