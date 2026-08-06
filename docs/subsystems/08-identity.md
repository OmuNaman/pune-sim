# 8. Identity & disclosure

## Summary

This subsystem replaces the sensitive-attribute firewall (02-population §6, 03-cognition §8) with **identity as a first-class layer under graduated disclosure**. Religion, community, and (latent) jati-cluster codes condition the city's structure always — names, the marriage market, peth micro-geography, festival calendars, diet, language, membership networks — exactly as before, but prompts are no longer blanket-blind: scenes carry a `disclosure_tier` (0 = derived facts only, today's behavior; 1 = identity-salient, explicit identity context from canon; 2 = communal-conflict, premium model + authored framing + 100% QC + review queue). Character attitudes become canon state (`att.stance` rows with provenance), so identity-based positions persist and supersede cleanly instead of being unstatable. Model refusals become a detected, rerouted, first-class outcome instead of silent clockwork fallback. The narrator/character two-voice rule is enforced structurally (blocking lint on narrator fields; slur hard-block everywhere; judge audit on dialogue) rather than by one classifier asked to intuit the difference between depicting prejudice and endorsing it. Mass identity-differentiated behavior (riots, bandhs) stays clockwork over codes and fields — see [09-collective-dynamics.md](09-collective-dynamics.md).

Provenance: designed by the second planning fleet (2026-07-31) after the owner rejected the firewall as a core flaw; the first fleet's own red teams had already shown it broken (see §0).

## Design

### 0. Why the firewall was replaced (flaw record)

The original design stored religion/community as coded fields conditioning structure, but stripped them from **all** LLM prompts (02-population.md context_pack step 4, §6; 03-cognition.md §8) and lint-blocked caste terms in all output. This failed in both directions:

1. **Constitutionally unsatisfiable.** architecture.md §7 required "characters may voice identity-based positions only when those are recorded canon state" — but no predicate existed to record such a position, and even recorded, the firewall would strip it from every prompt. The rule could never fire.
2. **Pre-blanded canon.** The docs' own traces showed the inter-religious-marriage arc generated with its defining dimension "surgically absent" (02-population.md Trace 2), the D2 lint "an unpassable constraint" (03-cognition.md Trace B), and R1 adjudication on communal dynamics producing "stereotype-driven outcomes or sanitized instant-acceptance" (04-events.md).
3. **Names leak anyway.** Names are deterministic functions of religion/mother-tongue pools. Any model sees "Ayesha Shaikh" vs "Aditya Deshpande" and infers community — so identity-charged generation happened regardless, driven by *unvetted model priors* instead of governed canon facts. The firewall firewalled nothing; it only removed governance.
4. **Refusals were invisible.** A cheap-provider refusal on communal content presented as a parse failure, walked the repair ladder, and landed on clockwork fallback — "systematic refusals silently turn the whole arc into clockwork with no detection" (06-inference.md B5).
5. **The highest-risk lane had the least QC.** Gossip about an interfaith couple ran through `micro_update`: cheapest model, temp 0.7, no lexicon gate, judge sampling undefined (06-inference.md B4).
6. **Inexplicable segregation.** Marriage homophily and peth settlement priors were applied "never verbalized" — the sim exhibits endogamy and residential clustering whose cause no interview or `explain()` could ever state.

### 1. Representation model

**Fields on `person` (D0 + canon):**

| field | values | sensitivity | provenance |
|---|---|---|---|
| `religion` u8 | hindu, muslim, buddhist_navayana, jain, christian, sikh, parsi, other_none | `contextual` | Census C-1 town-level shares (`anchor`) × per-peth locality priors (`estimate`, gazetteer table below) |
| `community` u8 | General / OBC / SC / ST / NT-DNT / VJNT (constitutional categories) | `contextual` (tier-2 preferred) | ward PCA SC/ST (`anchor`); state urban estimates for the rest (`estimate`) |
| `jati_cluster` u8 | ~25 editorial clusters for urban Maharashtra (Deshastha, Kokanastha, CKP, Maratha-Kunbi, Mali, Dhangar, Vanjari, Teli, Sonar, Mahar-descended Navayana Buddhist, Matang, Chambhar, Koli, Muslim biradari clusters, …) | **`latent`** — never disclosed, never narrated | `estimate`; no public jati data since 1931 (SECC 2011 unreleased); calibrated from scholarly literature + surname-frequency evidence |
| `mother_tongue` u8 | per Census C-16 | `public` | `anchor` |
| `origin` u8 | pune_native, rural_mh, hindi_belt, south, northeast_other, intl | `public` | D-series migration (`anchor`/`estimate`) |
| `religiosity` | existing trait float — personal observance intensity, separate axis from membership | `public` (derived facts) | synthesis |

**Sensitivity scale** (replaces the binary `firewalled` in the predicate registry):

| level | prompt exposure |
|---|---|
| `public` | any scene |
| `contextual` | only when scene `disclosure_tier ≥ 1`, restricted to the ClassDef `discloses:` list |
| `latent` | never as a label; conditions structured derivations only |
| `private` | unchanged (non-identity personal facts) |

**`jati_cluster` exists only to drive three derivations** — (i) surname pools, (ii) the marriage-matching kernel, (iii) peth settlement priors — and its effects surface only as concrete facts (the surname itself, the specific mandal, who a matchmaker shortlists). This is the defensible line: jati-level *structure* (which Maharashtra marriage networks and surnames genuinely have) without jati-level *labeling* (which no data can support per-person and which narration must never do).

**Peth-level priors are auditable content, not code.** New anchor-adjacent table:

```sql
CREATE TABLE peth_composition (peth_id TEXT, group_key TEXT,  -- 'religion:muslim' | 'jati:deshastha' ...
  share REAL, uncertainty REAL, provenance TEXT, source_note TEXT,
  PRIMARY KEY (peth_id, group_key));
```

Citable sources (verified 2026-07-31): **Gadgil, *Poona: A Socio-Economic Survey* (Gokhale Institute, Part I 1945 / Part II 1952)** — peth-by-peth surveys, full text free on archive.org / GIPE dspace; documented settlement history (Sadashiv/Narayan/Shaniwar Brahmin concentrations, Raviwar merchant/Bohri, pre-Maratha Muslim peth names — Shahapur→Somwar, Mohiyabad→Budhwar); **OSM places of worship** (old-city core: 57 Hindu, 9 Muslim, 2 Jain, 1 Sikh, 1 Jewish — reproducible via ohsome API); **Susewind's published booth-level religion estimates** (Maharashtra 2014, ODbL — use published aggregates only; never scrape electoral rolls). Everything carries `provenance=estimate` with a source note per row.

### 2. Structural derivations (always on, no disclosure needed)

All clockwork/statistical; this is where identity does ~99% of its work:

1. **Names:** `name_pool` re-keyed to `(religion, jati_cluster, mother_tongue, sex, birth_decade)`; surname inheritance unchanged. Fixes sociologically impossible surname/community pairs.
2. **Marriage market:** matching kernel `P(a,b) ∝ exp(θ1·same_jati_cluster + θ2·same_religion + θ3·same_community + θ4·edu_distance + θ5·geo)` with calibrated exogamy leak (inter-religious ~1–2%, inter-jati within religion ~5–10% urban; IHDS/NFHS-informed, config-exposed). Arranged-match lane samples the kernel; the love-match lane (E2 edge-threshold romance) samples co-presence + edge dynamics, producing the exogamous tail *endogenously*. Every committed marriage decrements the cohort ledger (POPULATION backflow fix).
3. **Micro-geography:** `peth_composition` drives building-level household placement at synthesis.
4. **Festival calendar:** participation = f(religion, religiosity, affiliation); the written fact (`id.observance`) is public.
5. **Diet, language mix:** derived public facts, unchanged.
6. **Occupational clustering:** occupation priors conditioned on (edu, community, origin), `estimate` provenance, never narrated as causation.
7. **Membership networks:** mandal/mosque/vihara/church/dargah-committee affiliations sampled by (religion, locality, sex, age) — the identity-network topology that gossip, weddings, and collective dynamics traverse.
8. **Administrative facts:** reservation category on school admission, scholarships, ration cards — ordinary canon where the real rule is identity-aware (statute-indexed allowlist; see 05-institutions amendment in §7).

### 3. Graduated disclosure — the tier rule

`disclosure_tier ∈ {0,1,2}`, computed per scene at assembly time by a pure function, stored on the scene job (replay-deterministic):

```
tier = max(
  classdef.identity_class,           # 1: authored on the event/scene ClassDef YAML
  attitude_trigger(participants),    # 2: any participant pair with active |att.stance| ≥ 0.4
                                     #    targeting the other's group/union, or open identity-tagged Process
  condition_trigger(ward, scene),    # 3: scene ward's communal_tension field > θ_tension
  user_flag                          # 4: injection compiler / god console
)
```

- **Tier 0 (default, ~99.9% of scenes):** exactly the old behavior — derived facts only.
- **Tier 1 (identity-salient):** `context_pack` appends an `IDENTITY CONTEXT` block: participants' `religion` (and `community` only if the ClassDef discloses it), relational identity facts computed from codes ("inter-religious match: bride's family Hindu, groom's family Muslim"), and recorded `att.stance` rows. Canonically ordered, rendered in the volatile tail (prefix-cache-safe).
- **Tier 2 (communal-conflict):** tier 1 + hand-authored scenario framing sheet per event family (grounding notes, outcome priors, tone constraints) + mandatory premium model + 100% QC + review queue.

`identity_class: none|salient|communal` and `discloses: [...]` are new ClassDef YAML fields (04-events §2). `life.marriage` variants where the matching draw crossed religion/community → `salient`; `matchmaking.*`, `discrimination.*`, `housing.application_rejected(basis=identity)` → `salient`; `unrest.communal.*`, `communal.boycott` → `communal`.

**Two hard invariants:** attention/salience never *raises* disclosure (watching a family doesn't out them), and budget pressure never *lowers* it — identity-salient scenes are non-demotable (07-interface significance floor): under pressure they **DEFER** (clockwork consequences proceed, narration queued), never template.

### 4. Content-tier routing and refusal detection

**Routing:** tier 1 → premium-lite/premium regardless of attention (content-driven escalation, fixing 04-events' attention-only path); tier 2 → premium always, lower temperature, hand-authored outcome priors keyed to *family-level variables* (`traditionalism`, `intergroup_exposure`) — never to group identity. `micro_update` items whose claim carries `identity_tags` get the slop lexicon gate + elevated judge sampling (30%, not 5%). Worst-case volume is tens of scenes/day — cents.

**Refusal detection — new ladder rung 2b** (06-inference §5, between parse and local repair):

```
detect_refusal(raw) -> bool:
  (i)   refusal-lexicon match (en/hi/mr: "I can't assist", "as an AI", policy boilerplate)
  (ii)  length anomaly: output < 30% of budget AND no parseable JSON
  (iii) sanitization check on VALID parses: empty/one-line dialogue where schema requires
        dialogue, or all-null-op outcomes on a conflict ClassDef
```

On detection: `llm_results.status='refused'` (new terminal status, distinct from `parse_fail`/`fallback_used`); **skip** repair rungs; reroute once to premium with the tier-appropriate preamble; if premium also refuses → `deferred_review`, human review queue, clockwork consequences proceed with narration deferred. **Never** silent rung-6 templating for `identity_class ≥ salient`. Telemetry: refusal rate by (model, call_class, identity_class); alarm if tier-0 refusal rate > 1% (identity content leaking past routing).

### 5. Guardrails that remain

1. **Fictional individuals only** — `mint_person` refuses real names; real institutions, synthetic officeholders (constitutional, unchanged).
2. **Two-voice rule, enforced asymmetrically.** Narrator voice (narration/summary/vignette fields) may never attribute traits or behavior to identity — blocking `narrator_lint` (regex + pattern gate) on narrator fields only. Character voice (dialogue) may express *recorded* prejudice — audited, not blocked. Depicting prejudice-in-world ≠ endorsing it; the enforcement machinery now encodes that distinction structurally.
3. **Slur lexicon hard-block everywhere, including dialogue.** Curated Marathi/Hindi/English epithet list. Prejudice is expressible without epithets; this is a bright line and cheap.
4. **QC for tier ≥ 1:** 100% async judge coverage scoring {narrator-endorsement, caricature, slur-miss, specificity}; failures → regen + review queue. **Silent post-editing is disabled for tier ≥ 1** (replaces the "prose bends" rule that gutted scenes): regenerate, escalate tier, or surface to user.
5. **Narratability tiers in the ClassDef schema** (promoted from backlog): `narratability: full|abstract|numeric`. Riot *ambience* (families sheltering, shuttered shops, police columns) = full; specific acts of communal violence = abstract (one-line summary, no scene); deaths = countable canon facts, abstract narration; sexual violence, harm to children, suicide = numeric only. The renderer refuses to open a scene on a `numeric` event regardless of attention — the console shows the count and the causal chain, not prose.
6. **Review queue as a UI surface** (07-interface): all tier-2 scenes and judge-flagged tier-1 scenes; the sim never blocks on review (canon commits; flagged scenes marked and regen-able).
7. **Hazard rates stay identity-blind** (04-events guardrail stands): religion/caste never modifies victimization/crime/accident propensity. Written explicitly: **identity may condition response behavior and network topology; it may never condition misfortune rates.** Communal-riot events are never Poisson background — they enter only by user injection or by an authored escalation Process gated on the tension field (see 09). The sim cannot spontaneously decide a pogrom.

### 6. Attitudes and tension as canon

**`att.stance` — the identity-attitude predicate** (the object §0-flaw-1 said was missing):

```
predicate: att.stance     cardinality: multi (one live row per target)
mutability: scene_gated   # NEW class: writable by tier ≥ 1 scenes and by events always;
                          # tier-0 scenes cannot create prejudice
sensitivity: contextual
object_json: {
  target_kind: 'group'|'person'|'union'|'org',    # union = a specific match/marriage
  target: 'religion:muslim' | 'person:10233' | 'union:10233x18734',
  stance: -1..1, intensity: 0..1,
  basis: ['religious','caste','family_honor','political','personal','economic'],
  origin_event_id, last_expressed_event_id }
```

The father's opposition is canon: `att.stance{target:'union:…', stance:-0.8, basis:['religious','family_honor']}`, superseded (not deleted) when he softens. Tier-1 prompts render these rows — positions are consistent across scenes and months, satisfying architecture §7 for the first time. Group-targeted stances are allowed but **event-originated only** (a riot, a betrayal), never synthesized at population genesis: the base population ships with *structure* (endogamy, clustering) but zero pre-authored bigotry; every recorded prejudice has a causal history `explain()` can show.

**Derived clockwork scalars:** `traditionalism` (religiosity, age cohort, edu) and `intergroup_exposure` (share of ego-network edges crossing religion/community, computed from the actual graph). These parameterize outcome priors and policy tables so clockwork behavior differentiates by *measured individual variables*, never raw group identity.

**INFO module:** `claim_json` gains `identity_tags: [group_keys]`; identity-tagged claims route to the QC'd micro lane and feed the `communal_tension` field (owned by WORLD's field registry, spec in [09-collective-dynamics.md](09-collective-dynamics.md)).

### 7. Migration checklist (edits owed to other docs)

- **02-population.md:** §6 → this doc's tiers; add `jati_cluster` to §1.1 draws and the D0 schema; re-key `name_pool`; context_pack step 4 → disclosure rule; D2 prompt "never mention caste/religion" → two-voice instruction (households in identity-salient arcs get tier-1 sketches); canon linter → `narrator_lint` + dialogue judge split; marriage kernel jati-level + exogamy config + ledger backflow; rewrite the firewall key decision.
- **03-cognition.md:** §8 → pointer here + two-voice policy; add `traditionalism` scalar, `att.stance` to canon writes, attitude-edge feed into E4 stake and the E2 romance lane; rewrite the firewall key decision.
- **04-events.md:** ClassDef gains `identity_class`, `narratability`, `discloses`; `identity_class ≥ salient` overrides `resolution.default` to premium; riot-never-hazard-sampled rule.
- **05-institutions.md:** §10 amended — procedures never read identity for eligibility/fees/jurisdiction *except* where the real rule is identity-aware by statute (statute-indexed allowlist: reservation admin facts, SMA vs personal-law routing); bandobast procedures may read ward composition *aggregates* + tension field (real bandobast is communal-geography-aware).
- **06-inference.md:** §5 rung 2b + `refused` status + premium reroute; §7 delete the hard post-filter blocklist in favor of the two-voice split; lexicon gate + elevated judge on identity-tagged micro; refusal-rate dashboard.
- **07-interface.md:** injection compiler sets `identity_class`; significance floor formalized as `narratability`/`identity_class` non-demotability; review queue in the god console.
- **architecture.md §7:** representation policy re-pointed here (done).

## Key decisions

- **Replace the blanket firewall with graduated disclosure (tiers 0/1/2): identity conditions structure always; prompts see identity labels only in identity-salient scenes, as governed canon facts.** — The first fleet's own traces prove the firewall fails both ways: it starves the one scene class that is *about* identity into slop, while names leak identity into every prompt anyway, shifting identity-charged generation from governed facts to unvetted model priors. Disclosure-by-tier makes the risky path the *most* controlled path.
  - Rejected: keeping the firewall (pre-blanded canon, unwritable core arcs, deniability-not-prevention); always-disclose with "be respectful" instructions (leaks stereotype pressure into 99.9% of routine scenes for zero benefit).
- **Two-track caste: constitutional category (`contextual`, the narratable ceiling) + ~25 latent editorial jati clusters (drive names, marriage kernel, peth geography; never disclosed or narrated).** — Surnames and marriage networks in Maharashtra are jati-structured; 5-bucket modeling produces visibly wrong names and a wrong marriage graph. But no per-person jati data exists since 1931, so jati stays a latent statistical field whose effects surface only as concrete facts. Structure without labeling.
  - Rejected: no jati modeling (wrong names/marriages — the texture the owner wants most); narratable jati (indefensible per-person provenance; per-jati narration is where caricature risk concentrates).
- **Disclosure tier = max(ClassDef flag, canon attitude trigger, ward tension field, user flag); attention never raises it, budget never lowers it.** — Salience is decided by *what the scene is*, not who is watching or what it costs. All four sources are data — no new code paths.
  - Rejected: LLM-judged salience per scene (a classification call per scene, failing precisely on subtle cases); ClassDef-only flagging (misses emergent salience — a routine kirana scene between parties to a live communal dispute must see the dispute).
- **Attitudes as canon (`att.stance`, `scene_gated`, event-originated): base population ships with structure but zero pre-authored prejudice.** — Positions persist, supersede cleanly, and render into prompts; event-only genesis means every recorded prejudice has an explainable causal history.
  - Rejected: free-text persona traits (unqueryable drift, no supersede semantics); synthesizing population-wide attitude distributions at genesis (turns editorial priors into 50k canonical bigotries with no evidentiary basis — unnecessary, since structural homophily already produces the aggregate patterns).
- **Refusal as a first-class ladder outcome: detect, mark, skip repair, reroute to premium once, then defer-to-review — never silent clockwork fallback on identity-salient content.** — Refusals-as-parse-failures silently convert the owner's most-wanted arcs into statistics with no alarm. Detection is cheap; reroute volume is cents; telemetry doubles as a routing-leak detector.
  - Rejected: treating refusals as retries (burns budget re-asking a model that said no); pre-filtering topics off the cheap lane entirely (tiering already does this for known-salient classes; detection catches the unknown ones).
- **Split enforcement by voice: blocking narrator lint + universal slur hard-block + 100% judge audit on tier ≥ 1 dialogue; silent post-editing disabled for tier ≥ 1.** — One classifier cannot distinguish depicted from endorsed prejudice; splitting by output field makes the distinction structural.
  - Rejected: single stereotype classifier over all output (over-blocks the interfaith arc, under-blocks euphemism, no training data for Marathi-English code-mix); no dialogue audit (caricature would accumulate as permanent canon).
- **Mass identity-differentiated behavior is clockwork over codes + fields + individual-variable policy tables; LLM renders only sampled tier-2 scenes; violence narration capped by narratability tiers.** — Differentiated mass response must scale to wards, which only clockwork can do; conditioning policy tables on measured individual variables keeps behavior differentiated without hard-coding "group X does Y". Full spec: [09-collective-dynamics.md](09-collective-dynamics.md).
  - Rejected: LLM-adjudicated riot behavior per household (cost-insane, maximum-risk content on the cheap lane); riots as hazard-sampled background (the sim must never spontaneously generate a pogrom).

## Interfaces

- `context_pack(subjects, scene_desc, token_budget, disclosure_tier)` — POPULATION; renders the identity block iff tier ≥ 1, restricted to the ClassDef `discloses:` list.
- `compute_disclosure_tier(classdef, participants, ward, user_flag) -> int` — kernel pure function; result stored on the scene job.
- `assert_facts` accepts `att.stance` under `scene_gated` mutability (writer must be a tier ≥ 1 scene or an event).
- `detect_refusal(raw) -> bool` + `status='refused'` — INFERENCE ladder rung 2b.
- `narrator_lint(fields) -> violations` (blocking) / `dialogue_judge(scene) -> flags` (async, 100% at tier ≥ 1) — INFERENCE QC.
- `peth_composition` table — POPULATION anchor data, owner-curated, versioned like rate tables.

## Open questions

- Final jati-cluster list (~25) and the θ vector for the marriage kernel — needs a focused calibration pass against IHDS/NFHS intermarriage tables and the Gadgil surveys; wide-uncertainty flags until then.
- Review-queue ergonomics: batch UI vs inline console; retention policy for flagged transcripts.
- Whether `att.stance` group-targets need decay (prejudice softening absent reinforcing events) or only event-driven supersession.
- Premium-model choice for tier 2: the workhorse-family top model vs Sonnet-class — refusal behavior on communal fiction differs by provider and needs an empirical probe in V0 (see architecture §6 revised build order).
