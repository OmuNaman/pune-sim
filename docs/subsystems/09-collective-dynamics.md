# 9. Collective dynamics

## Summary

This subsystem gives the sim collective behavior — riots, bandhs, protest marches, festival processions, crowd crushes, celebrations, panic evacuations — without a riot-special-case engine. Five general mechanisms, each owned by an existing subsystem through its existing extension points: **civic fields** (unrest/tension/fear/police_presence in WORLD's generic field registry), **claim-coupled mobilization** (INFO belief credence feeding fields and mobilization), **threshold crowds** (Granovetter-style per-person participation thresholds evaluated as vectorized clockwork — "most stay home, ~200 show up" is a zero-LLM outcome), **public-order procedures** (BNSS §163, curfew, dispersal, detention, redeployment, compensation as Procedure-engine data on existing org types; the crowd itself is an ephemeral org), and **field-mediated cascades** (self-exciting phenomena re-seed through fields as fresh root events instead of fighting the depth-decayed cascade budget). All five constitutional laws hold. Identity enters only per 08-identity's rules: fields are geographic scalars, mobilization reads measured individual variables, hazard rates never key on identity, and communal events are never Poisson background — injection or authored escalation only.

Provenance: second planning fleet (2026-07-31). The break trace below documents why the layer exists; it is the fleet's walk of the owner's headline injection through the original design.

## Break trace — `inject "communal riot breaks out at [chowk] in Raviwar Peth tonight"` vs the original design

- **B1 — No ClassDef for civil unrest.** The seven shapes offer only `mass_gathering` (planned, calendar-owned, process-owned sub-budgets — Ganeshotsav semantics). Fallback is inline ClassDef synthesis by a one-shot R1 call (04-events §3.5): the *cheapest* model synthesizing the *most sensitive, most novel* content in the design, with no sensitivity flag and an `allowed_effects` menu limited to what the registry already knows.
- **B2 — No crowd entity.** `event.participants` is `{entity_id, role}` rows; statistical presence casts victims and witnesses, not collective *actors*. Nothing exists to be the agent of downstream events, the subject of police cases, or the referent of `explain()`.
- **B3 — The cascade budget extinguishes a self-exciting process.** Child cost = severity × depth multipliers [1, 1.5, 2.5, 4, 6…]; a riot is Hawkes-like — dozens of severity-0.3–0.6 events at depth 5–15 over one night, each *raising* the rate of the next. The red team already showed depth-multiplied budgets kill legitimate low-severity chains by depth 2–3; a riot is that failure at maximum stakes.
- **B4 — ~20 eager percepts cannot produce mass behavior.** 50,000 people staying home **is** the phenomenon; moving 5,000 households through eager percepts would need hundreds of events, which B3 forbids.
- **B5 — Belief crosses a threshold into a void.** Action thresholds are hand-authored per claim family; no claim family means "violence at the chowk", and no action in the vocabulary means shelter / close the shop / keep the son home / join.
- **B6 — No behavior menu, no fear substrate.** The deterministic adaptation layer covers mode/destination/shop choice only; no discrete choice over {shelter, continue, gawk, participate, flee}; WORLD has no per-edge/zone risk or fear cost for anything to write.
- **B7 — The firewall strips the communal dimension from every prompt.** Resolved by [08-identity.md](08-identity.md); without it, compiler payload, scenes, and narration lawfully reduce to "a disturbance near the chowk."
- **B8 — Cheap-model refusal degrades silently to clockwork** (06-inference B5). Resolved by 08-identity §4 (rung 2b).
- **B9 — Scene volume: misses tonight, explodes tomorrow.** The morning gate has no lane for 21:30 behavior; next morning it fires for ~all 12k affected households and the governor degrades to rules exactly when fidelity matters most.
- **B10 — AttentionField spikes with positive feedback** — 07-interface's red team names the riot case verbatim; the damping fix was never folded. No "render 5 of 5,000" sampling policy exists.
- **B11 — Police have no public-order machinery.** Procedures are fir/nc/missing-person/bandobast/mlc; bandobast is pre-planned festival deployment. No BNSS §163, no curfew, no dispersal/detention, no redeployment; the closed effect vocabulary cannot even express a movement ban on persons.
- **B12 — Economy/mobility can't respond tonight.** Shop strategy lives in a *weekly* business review; no fear term in LOD-0 pool shares; no transit-suspension owner; nobody owns issuing the edge closures the machinery could technically apply.
- **B13 — Everything decays; riots need amplification and memory.** All three decay clocks decay; no state holds residual communal tension; relationship edges are dyadic only — no group-attitude substrate, no segregation drift.
- **B14 — Hazard generation has no unrest hook.** Festival crowd fields raise pickpocketing rates, but no geographic unrest modifier exists for assault/arson/stampede — the sim can never endogenously produce the small clashes NCRB records hundreds of times a year.

Net: the injection commits, produces a road_block, ~20 reactions, some reach rows, a blanded ticker line, and by day 2 — nothing.

## Design

### 1. Civic fields — owned by WORLD (generic field registry)

Two field *kinds*, instantiated per axis as registry data:

- `unrest:<axis>` — per-zone, fast (half-life hours), written via `add_contribution` from committed events through ClassDef effect templates. The Hawkes excitation term.
- `tension:<axis>` — per-zone, slow (half-life weeks–months), **with a floor parameter that ratchets up after major episodes** — long memory; the Hawkes background rate μ.

Axes as data: `communal`, `civic` (anti-government/bandh), `labor`. Plus `fear` (hours–days; written by acute violent events; feeds discrete choice and WORLD's per-edge risk/comfort costs) and `police_presence` (written by deployment procedures; the perceived-arrest-risk input). Threshold bands auto-emit `world.field.unrest.communal` signals; values auto-merge into PlaceContext.

**Guardrail compliance:** fields are geographic scalars — never keyed to person identity. Identity enters person-side only as `identity_stake`, derived from canon network structure (share of ego edges/institutions in the affected area/community). Hazard rates key on the *field*, i.e. geography — exactly what the 04-events guardrail permits.

### 2. Rumor → mobilization coupling — owned by MINDS (INFO)

Three data-level extensions, no engine change:

1. **Claim families gain a `mobilizing` attribute** `{axis, valence, target_zone}`. "They attacked the temple at X" is an ordinary claim with `mobilizing: {axis: communal, valence: -1, target: z:14}`.
2. **Generic action vocabulary** (adopting the red team's own fix): actions derive from predicate class × traits instead of hand-authored per-family tables; new action `mobilize(menu)` — crossing credence threshold feeds the person's mechanical mobilization evaluation (§3), not an LLM scene. Beliefs cause behavior through the same E5 pipe at zero LLM.
3. **Aggregate belief writes to fields:** when ward-level credence mass in a mobilizing family crosses a band, INFO calls `add_contribution(tension:axis)`. Rumors raise tension without any physical event — how most Indian riots actually start (Wilkinson) — reusing the ward-level belief aggregation the election module already reads.

### 3. Threshold mobilization and the behavior menu — MINDS (evaluation) + kernel choice layer (execution)

The core clockwork. Per affected ward, vectorized numpy over P1 rows, each 15-min tick during an episode:

- **Threshold** `θ_i`: drawn once per (person, episode), keyed Philox `(run_seed,'mobilize',person_id,episode_id)`, from a heterogeneous distribution parameterized by packed traits (temper, credulity, risk_tol), age/sex, `identity_stake`, and grievance (p_job, p_financial, prior episode losses). Granovetter's result — outcomes hinge on the threshold *distribution's shape*, not its mean — is preserved by construction.
- **Activation** `A_i = w1·unrest(zone_i) + w2·network_exposure_i + w3·max mobilizing-claim credence + w4·visible_crowd − w5·perceived_arrest_risk` — network_exposure = fraction of ego edges already mobilized (iterated across ticks, so the cascade unfolds in sim time and a fast lathi charge can cut it mid-cascade); perceived_arrest_risk reads the `police_presence` field. Epstein 2002's net-risk term.
- **Behavior menu:** if `A_i > θ_i`, discrete choice over `{shelter, continue, gawk, participate, flee}` (trait/fear/stake-parameterized utilities, keyed RNG). Below threshold: `{shelter, continue}` driven by fear + curfew conditions.

Participation/shelter **counts are clockwork-provenance canon** (law 2); scenes read them read-only. LLM scenes are sampled (§9) purely for meaning — a family arguing about whether the son goes out receives the mechanical prior and may resolve *its own household's* choice at higher fidelity; observer-effect monitoring watches for drift. Shelter shares feed cohort-trip demand suppression; nobody needs a scene to not go to work.

### 4. Crowd as entity — owned by INSTITUTIONS

**The crowd is an ephemeral org** (`crowd` OrgType, `is_ephemeral=1`), spawned when a zone's `participate` count crosses a band, expiring on dispersal. Lifecycle is a Procedure: `forming → massed → agitated → violent → dispersing → dispersed`, transitions driven by timers, field levels, and police-action case events. State: `size` (from mobilization counts), `mood/violence` scalar, `valence` (riot −1 … procession/celebration +1), location footprint (writes `crowd_idx` + `moving_closure` disruptions exactly like processions), cohesion. Membership is statistical — a composition vector plus `presence_sample`-style deterministic individuation when a scene, casualty, or arrest needs a named member; 200 people are never materialized.

Why an org, not a new entity type: orgs already have identity, location, jurisdiction, rosters, event emission, and — decisive — Procedure-engine interoperability: police cases need a case *subject*; the crowd org is it, and `explain()` gets an actor. The original design already listed "strike committee" as an ephemeral org; a crowd is the same idea minus the treasurer.

### 5. Public-order procedures — INSTITUTIONS (procedure_defs = data, as promised)

New procedures on *existing* police/DM/PMC/hospital org types:

- `public_order_response`: states `[alert, deployed, negotiation, dispersal_ordered, force_used, containment, normalized]`; DecisionPoints for SHO/DCP with options `{monitor, deploy, lathi_charge, tear_gas, request_reinforcement, recommend_restrictions}`, calibrated defaults keyed to crowd size/violence. **Response latency and force posture are explicit parameters** — Wilkinson's decisive variable becomes an experiment knob; DecisionPoint context may include ward electoral salience (the S7 link for electoral-incentive dynamics).
- `prohibitory_order` (BNSS §163, DM/CP): a Condition on an area entity with `effects: {assembly_ban: 1}` — consumed by §3 as a participation-cost term — plus an official-channel informational event.
- `curfew`: area Condition `{movement_ban, curfew_pass_classes}`; consumed by (a) the choice layer as a go-out utility penalty with trait-conditioned compliance draws, (b) WORLD's disruption API for vehicular mode blocks, (c) shop open/close (§6). Daily review timer extends/relaxes keyed to the unrest field.
- `mass_detention` / batch `fir_bnss` (BNS §§189–197 in vars) → the existing court machinery (a riot's court tail lasting years is emergent, like S6); `redeployment` reuses bandobast resource mechanics — response to Raviwar Peth degrades FIR/witness capacity everywhere else, for free.
- Hospital `mass_casualty` triage toggle; PMC `riot_cleanup`, `compensation_claims`.

Police actions write back: successful dispersal → large negative `add_contribution(unrest)`; a sampled force-excess outcome → positive contribution to `tension` — the Epstein/Wilkinson two-edged sword, in data. **The only engine change in INSTITUTIONS: add `set_field`/`add_contribution` to the closed effect vocabulary.**

### 6. Economy & mobility coupling — kernel choice layer + INSTITUTIONS + WORLD

- **Shop shutdown as discrete choice**, evaluated hourly when zone fear/unrest crosses a band (not the weekly business review): `P(close) = logit(fear_field, fraction_of_neighbor_shops_closed, perishability, owner traits if materialized)`. The imitation term produces the empirically observed shutter-wave. Effect: temporary `venue_closed` Condition; LOD-0 pool shares reallocate mechanically.
- **Transit suspension:** PMPML depot org gets a `service_suspension` procedure triggered by field threshold signals — cancels trip instantiation on affected routes, emits `world.transit.disrupted`.
- **Edge closures:** crowd footprint and police barricades via the existing disruption API; curfew adds mode blocks. Nothing new mechanically — the gap was ownership, now procedures own it.

### 7. Casualties — EVENTS, existing machinery

ClassDefs `hazard.unrest.assault / .arson / .stampede` with rates = base × `unrest(zone)` × crowd-size exposure — the festival crowd-multiplier pattern. Casualties → KSI injury Conditions → hospital beds → MLC → FIR, all existing. Deaths are budget-exempt physical outcomes (commit-order fix, architecture §9 ruling 6). Because base rates are NCRB-anchored, small clashes occur *endogenously* at realistic frequency — the injected riot is a large forced draw, like the injected cloudburst.

### 8. The cascade-budget fix — field-mediated re-seeding

The central move: **take self-excitation out of the event DAG's depth economy entirely.**

1. Each unrest event's effect template does `add_contribution(unrest:axis, f(severity), footprint)` — cheap, budget-free (a condition-like effect, not a child event).
2. The hazard sampler (already per-tick) reads the field as a rate multiplier and spawns **new ROOT events** — depth 0, fresh impulse, `source='hazard'`.
3. Loop: event → field↑ → rate↑ → new roots → field↑… — a discretized Hawkes process where the field's decay *is* the kernel.

Explosion control moves from depth multipliers to field dynamics, where it belongs:

- **Subcritical by default:** contribution weights and decay calibrated so the branching ratio < 1 unless `tension` is high *and* police response is absent — episodes self-extinguish in hours, matching reality.
- **Fender-benders can't cascade to city collapse:** contribution weights are ClassDef data, ~zero for everything except violence/unrest classes; bounded, zone-local; a collision contributes nothing to any field.
- **Accounting/UX:** an `Episode` Process (EVENTS-owned non-org arc — the "festivals-as-city-phases" slot) groups the roots, owns a sub-budget for *discretionary social* children, and gives the UI one handle.
- **Law 1 & explainability:** field state is a pure fold of logged contributions + decay — replayable, rebuildable. New roots record `field_context` (field value + top-k contributing events) so `explain()` renders "spawned under unrest 0.82, driven by [arson at Phadke Chowk]".

### 9. Attention & cost governance under mass events — owned by INTERFACE

- **Scene sampling:** when a trigger class fires for > N households inside an episode footprint, the scheduler stratifies by (ward × behavior choice × household type) and renders **k representative scenes** (default 5) at T2, one T3 if focal; everyone else gets facts-only percepts + template memory rollups. Hard per-episode, per-tick scene cap; episode events' attention contribution discounted. This is the answer to 07's spike problem: the spike still ranks attention, but tier assignment saturates at the sample budget, not the wallet.
- **Sensitivity routing:** the unrest ClassDef family is **hand-authored registry content — inline synthesis is banned for this family**. Flags per 08-identity: `identity_class: communal`, premium regardless of attention, significance floor = DEFER-not-degrade, explicit refusal path. The injection compiler maps free text onto this authored family + parameters (which chowk, which axis, initial intensity, seed claim).
- **Event-day budget reserve:** a pre-authorized per-event reserve outside the daily governor (~$20 at Old City scale, ~$50 at full Pune), triggered by episode declaration — the governor's degrade/shrink/drop verbs apply only beyond it. (Cost audit: a well-simulated Old City riot day ≈ $15–18; see architecture §5 revised.)

### 10. Aftermath

Curfew-day Conditions with stage schedules and review timers; mass trials through existing pendency machinery; `compensation_claims` (documents + WORLD→household transfers + asset-layer damage records); **peace committees as ephemeral orgs** (mohalla-committee model) spawned by a police-procedure effect, whose meetings are scheduled_social events contributing small negative tension. Long memory: the tension floor ratchet (§1); event-derived household records ("shop burned in the June episode", bounded `intergroup_wariness` scalar — event-derived only, never sampled from identity priors) shift scene texture and, later, a relocation-choice term for segregation drift (dependent on the housing-market backlog item).

### 11. Calibration anchors

- **Granovetter 1978** — heterogeneous threshold distributions; preserve outcome instability (same shock, different outcome across seeds — a feature, not a bug to tune away).
- **Epstein 2002 (civil violence ABM)** — activation = grievance × (1 − risk_aversion·arrest_probability); expect punctuated equilibrium in long runs.
- **Wilkinson, *Votes and Violence*** — police response speed as the decisive variable; persistent town-level heterogeneity → per-zone tension floors seeded from history.
- **NCRB rioting offences** (BNS ch. XI; order of a few hundred registered cases/yr for a Pune-sized city, overwhelmingly small clashes) anchor `hazard.unrest.*` base rates. Note (data audit 2026-07-31): the communal/political/caste riot split exists at **state** level only — use Maharashtra rates × Pune exposure share, `provenance=estimate`, sanity-checked against documented events: **2014 Hadapsar (Mohsin Shaikh)**, **2018 Bhima Koregaon violence + Maharashtra bandh**, 1894 Pune procession riots / 2009 Miraj as festival-trigger structural templates.
- Harness targets: heavy-tailed episode sizes; most episodes zero-casualty, dispersed within hours; city-scale conflagration unreachable without high tension + delayed response.

## Ownership table

| Piece | Owner | Mechanism |
|---|---|---|
| `unrest/tension/fear/police_presence` fields | WORLD | generic field registry; PlaceContext merge |
| Field contributions from events | EVENTS | ClassDef effect templates; `add_contribution` effect |
| `hazard.unrest.*` ClassDefs, Episode process, field-mediated root spawning | EVENTS | hazard sampler field-multiplier hook |
| Mobilizing claim families, generic action vocabulary, belief→field writes | MINDS (INFO) | claim schema + action-vocabulary fix |
| Threshold draw, activation, behavior-menu choice | MINDS (evaluation) + kernel choice layer (execution) | vectorized, keyed Philox, zero LLM |
| Crowd org + lifecycle procedure | INSTITUTIONS | ephemeral org |
| Public-order/curfew/§163/detention/redeployment/compensation procedures; `set_field` effect | INSTITUTIONS | procedure_defs (data) + one vocabulary addition |
| Shop-close & transit-suspension choices | kernel choice layer + INSTITUTIONS | discrete choice + depot procedure |
| Scene sampling, sensitivity routing, authored-family injection compiling, event reserve | INTERFACE | AttentionField (law 5) + budget governor |

## Key decisions

- **Field-mediated re-seeding instead of depth-multiplied cascades for self-exciting phenomena.** — Matches the physics (Hawkes); keeps the anti-explosion guarantee (bounded, class-gated, zone-local contributions; subcritical defaults); preserves law 1 and replay.
  - Rejected: raising impulse/depth constants for riot classes (concedes the budget model is wrong and special-cases it); unlimited cascades with probabilistic decay (no hard guarantee); one giant riot Process scripting its own children (a riot engine — banned).
- **Crowd = ephemeral org + lifecycle Procedure.** — Free identity, location, roster, case-subject status, event emission; procession/mandal precedent; "strike committee" already listed.
  - Rejected: new engine entity type (duplicates timers/lifecycle, violates generality); pure field with no entity (police cases and `explain()` need an actor; interviewing an arrested member needs individuation).
- **Mechanical Granovetter thresholds + discrete choice; LLM only for sampled meaning-scenes.** — "Clockwork is for behavior mass" is already constitutional; 50k decisions/tick is a numpy expression; determinism and branch-diff ("same riot, faster police") come free via keyed draws.
  - Rejected: LLM per-household decisions (cost-insane, non-replayable); ward-aggregate-only model (loses trait heterogeneity, participant casting, and interview consistency).
- **Tension as registered WORLD fields — multiple axes as data, two timescales, ratcheting floor.** — The registry was built for exactly this ("a registration, not a schema migration"); auto-signals and PlaceContext merge come free; floor = long memory.
  - Rejected: area Conditions (stage/decay semantics wrong for a continuously driven quantity); per-person emotion aggregation in MINDS (O(n) where a scalar field suffices; identity-adjacent state on persons is what the guardrail minimizes).
- **Hand-authored unrest ClassDef family with premium routing; inline synthesis banned for this family.** — The highest-sensitivity content must not be authored by the least-capable component at injection time; authored classes carry sensitivity flags, narratability tiers, calibrated rates.
  - Rejected: one-shot R1 ClassDef synthesis for civil unrest (that is break B1).

## Same machinery, other instantiations

**Bandh.** A party/union org emits the bandh *call* — an informational event, claim family `mobilizing: {axis: civic, scheduled_date}`. Tension:civic rises with credence; mobilization is calendar-anchored rather than shock-driven; the main choosers are **shops** (close-choice dominated by the imitation term plus an enforcement-fear term from small crowd orgs patrolling markets) plus individuals choosing shelter/continue. Police run `public_order_response` at low force posture plus preventive detention of organizers; transit suspends via the depot procedure; unrest contributions stay near zero, so the episode decays by evening. No new mechanism — only parameters.

**Festival procession (and crush).** Calendar-spawned (existing S8): crowd org with `valence=+1`, planned route as `moving_closure`, bandobast pre-allocated. `hazard.unrest.stampede` keys on crowd density × chokepoint capacity — crowd crush needs zero extra code. And the coupling history demands falls out mechanically: `hazard.unrest.clash` rate = f(crowd size × `tension:communal(zone)`) — a visarjan procession routed through a high-tension zone is the classic riot trigger via the field, not a scripted link.

**Celebration** (cricket win) = positive-valence mobilization off a joy claim. **Panic evacuation** = the same menu with `flee` dominant after an acute hazard spikes the fear field.

## Build note

Slots into the reference phases without reordering: fields + choice-layer hooks with WORLD-dynamic, unrest ClassDefs + re-seeding with EVENTS, procedures with INSTITUTIONS, mobilization with MINDS, sampling/routing with INTERFACE. Engine-level changes anywhere in the system: the `set_field/add_contribution` effect-vocabulary entry, the hazard sampler's field-multiplier hook (already half-built for festivals), and the scene-sampling policy in the scheduler. Everything else is registry rows, procedure JSON, claim families, and one vectorized evaluator — the design keeping its own promise: data, not code. In the vertical-slice plan (architecture §6 revised), a *minimal* instance (one fear field, shelter/continue choice, hand-authored `unrest.communal` ClassDef, scripted police response) is testable at V2 scale to de-risk refusal behavior and scene quality early.

## Open questions

- Threshold-distribution parameterization: how much heterogeneity vs how much identity_stake weight — needs sensitivity analysis against the "most episodes fizzle" target.
- Crowd org spatial dynamics: single footprint vs splitting into multiple crowd orgs when police cut a mass in two.
- Whether `intergroup_wariness` (household, event-derived) should decay to zero or to a floor; interaction with the marriage kernel's exogamy leak in long runs.
- Media/INFO amplification loop calibration: identity-tagged claims both feed and are fed by the tension field — verify the closed loop stays subcritical under a hostile-rumor injection with no physical event.
