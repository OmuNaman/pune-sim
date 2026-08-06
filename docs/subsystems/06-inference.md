# 6. Inference & cost

## Summary

The Inference & Cost subsystem is a provider-agnostic LLM operations layer for Pune Sim built around five generic call classes (household_plan, scene, micro_update, focal_turn, judge/repair) that every simulation event maps onto — no event type ever gets its own inference path. The workhorse is a DeepSeek-class OpenAI-compatible API for T1/T2/micro (parameterized at $0.87/M output, $0.28/M input-miss, $0.028/M input-hit), with a Claude Sonnet-class premium model ($3/$15/M, verified current pricing) reserved for the single user-facing T3 focal stream; the local GPU earns its place only for embeddings (bge-m3, canon retrieval + contradiction candidates) and an optional Qwen3-4B QC judge, never for generation. All work flows through a crash-resumable SQLite job queue with deterministic request-ID idempotency, asyncio workers with per-provider token-bucket rate limiting, priority lanes (interactive preempts background), and cache-aware ward-sorted scheduling. Prompt-cache engineering uses a four-segment prefix architecture (city/role preamble 2,500 tok → ward-day 800 tok → household canon slice ≤1,200 tok → volatile tail), achieving h≈0.67 input cache-hit and halving cheap-lane cost. The key design challenge to prior planning: T1 is NOT one call per household per morning but a salience-ranked global call budget — naive per-household T1 at 3.5M pop costs ~$5,700/mo while the attention-budgeted design costs ~$267/mo before off-peak discounts (~$175/mo after), keeping 50k-pop operation at ~$80–110/mo and full-Pune under $300/mo including T3. Structured output is enforced by a five-rung ladder (json_object mode → pydantic → json-repair → one repair call → one regen → deterministic clockwork fallback) so the simulation never blocks on LLM failure. QC combines an n-gram slop lexicon, embedding near-dup detection, sampled LLM-judge audits (routed through Anthropic's 50%-off Batches API), in-band canon-fact assertion checked against the Canon DB, and register-conditioned Marathi/Hindi/English code-mixing with anti-caricature rules baked into the cached prefix.

## Design

# Pune Sim — Inference & Cost Subsystem Design

## 0. Design principles

1. **Five call classes, zero event special-cases.** Every LLM invocation in the entire simulation is one of: `household_plan` (T1), `scene` (T2), `micro_update` (T2-lite, k-packed), `focal_turn` (T3), `qc` (judge/repair). Events, arcs, institutions, and festivals differ only in *prompt content and job volume*, never in code path.
2. **The sim never blocks on an LLM.** Every job class has a deterministic clockwork fallback; the queue is durable; results are idempotent.
3. **Attention is the budget unit, not population.** LLM spend scales with the user's attention radius and event load, not with city size. This is the load-bearing cost decision (see §9, and Key Decision #1 — it replaces the "one T1 call per household per morning" hypothesis).
4. **Cache-first prompt architecture.** Prompts are assembled from versioned, byte-stable segments ordered longest-shared-first; anything volatile lives in the tail.
5. **Boring tech**: SQLite, asyncio, pydantic, official SDKs. No Redis, no Celery, no litellm.

---

## 1. Tier-to-model mapping

| Tier | Call class | Model (logical role) | Concrete default | Temp | Why |
|---|---|---|---|---|---|
| T0 | — | none | — | — | Clockwork: schedules, traffic, hazard draws, ledger math. $0. |
| T1 | `household_plan` | `cheap` | DeepSeek-class chat via OpenAI-compatible API | 0.8 | One JSON day-plan per *selected* household (salience-ranked budget, not per-household-per-day). |
| T2 | `scene` | `cheap` | same | 0.9 | Non-routine interactions: dialogue + outcome JSON + asserted facts. |
| T2-lite | `micro_update` | `cheap` | same | 0.7 | k-packed array calls: gossip mutation hops, opinion nudges, one-line reactions. 20 items/call. |
| T3 | `focal_turn` | `premium` | **Claude Sonnet-class** (`claude-sonnet-4-6` tier; verified $3/$15 per MTok; Sonnet 5 has an intro $2/$10 through 2026-08-31) | 1.0 | Streaming roleplay for whoever the user is watching/interviewing. Never used for background. |
| T3-b | `focal_turn` (secondary) | `premium_lite` | Claude Haiku 4.5 ($1/$5) | 1.0 | Optional: non-focal speakers *inside* a focal scene (crowd voices), keeping the focal character on Sonnet. |
| QC | `qc_judge`, `repair` | `cheap` or `local` | DeepSeek-class; optional local Qwen3-4B-Instruct | 0 | Sampled naturalness/caricature audits; JSON repair. |
| — | embeddings | `local` | bge-m3 (multilingual, handles Devanagari) via sentence-transformers on the local GPU | — | Canon retrieval, contradiction candidate search, output near-dup detection. High volume, zero marginal cost. |

**Does a tiny local model earn its place?** For *generation*: no — DeepSeek-class output at $0.87/M is so cheap that a local 4–8B model's quality loss (worse Marathi, worse instruction-following, worse JSON) isn't worth saving ~$2.6/day at 50k pop, and it competes for the GPU with embeddings. For *embeddings*: yes, mandatory (millions of vectors, free locally). For *QC judging*: optional yes — a temperature-0 Qwen3-4B classifier scoring {slop 1–5, caricature y/n, register-match y/n} is a good use of idle GPU and removes QC from the API bill; ship API-judge first, swap later behind the same `qc_judge` role.

Model roles are logical names resolved through a registry (§2), so any provider swap is config, not code.

---

## 2. Provider-agnostic client layer

**Decision: thin adapter over the official `openai` (AsyncOpenAI, `base_url` per provider) and `anthropic` (AsyncAnthropic) SDKs — not litellm.** Rationale: only two wire protocols exist in this system; litellm adds a fast-churning dependency and obscures provider-specific features we depend on (DeepSeek automatic context caching semantics; Anthropic `cache_control` breakpoints, streaming helpers, Batches API). Fallback chains are ~40 lines of our own code.

```python
# models.py — registry (TOML-configurable)
@dataclass(frozen=True)
class ModelSpec:
    role: str                 # "cheap" | "premium" | "premium_lite" | "local_judge"
    provider: str             # "deepseek" | "anthropic" | "local"
    model_id: str             # e.g. "deepseek-chat", sonnet-class id
    api: str                  # "openai" | "anthropic" | "llamacpp"
    base_url: str | None
    rpm: int; tpm: int        # rate-limit budgets we self-impose
    max_concurrency: int
    price_in_miss: float      # $/Mtok  — cost model inputs live HERE, single source
    price_in_hit: float
    price_in_write: float     # anthropic cache-write (1.25x 5m / 2x 1h); = miss for deepseek
    price_out: float
    off_peak: OffPeakWindow | None   # e.g. UTC 16:30–00:30, discount factor
    fallbacks: list[str]      # ordered logical roles to try on hard failure

REGISTRY = {
  "cheap":   ModelSpec(..., provider="deepseek", api="openai",
                       price_in_miss=0.28, price_in_hit=0.028, price_out=0.87,
                       off_peak=OffPeakWindow("16:30","00:30", 0.5),
                       fallbacks=["cheap_alt"]),          # 2nd openai-compatible vendor
  "premium": ModelSpec(..., provider="anthropic", api="anthropic",
                       price_in_miss=3.0, price_in_hit=0.30, price_in_write=3.75,
                       price_out=15.0, fallbacks=["premium_lite"]),
  ...
}
```

### Core request/response types (pydantic v2)

```python
class CacheSegment(BaseModel):
    key: str            # "city_preamble:t1:v7" | "ward:kasba:2026-07-31" | "hh:H10233:v41"
    text: str           # canonical bytes — NEVER regenerated per call
    version: int
    sha256: str

class LLMRequest(BaseModel):
    request_id: str         # deterministic — see §3
    role: str               # logical model role
    call_class: Literal["household_plan","scene","micro_update","focal_turn","qc_judge","repair"]
    segments: list[CacheSegment]     # ordered stable prefix
    tail: str                        # volatile suffix (the actual ask)
    schema_name: str | None          # registered pydantic schema for output
    max_output_tokens: int
    temperature: float
    priority: int                    # 0 = interactive, 1 = event-urgent, 2 = background
    deadline_class: Literal["interactive","hour","overnight"]
    sim_tick: int
    entity_ids: list[str]            # for canon commit + telemetry

class LLMResult(BaseModel):
    request_id: str
    status: Literal["ok","fallback_used","dead"]
    parsed: dict | None              # schema-validated JSON (None for focal_turn free text)
    raw_text: str
    model_id: str
    usage: TokenUsage                # in_miss, in_hit, in_write, out — provider-reported
    cost_usd: float
    attempts: int
    qc_flags: list[str]
```

### Client interface (what other subsystems call)

```python
class InferenceService:
    async def submit(self, req: LLMRequest) -> None            # enqueue; returns immediately
    async def submit_and_wait(self, req: LLMRequest, timeout_s: float) -> LLMResult
    def open_focal_stream(self, req: LLMRequest) -> AsyncIterator[str]   # T3 token stream
    def result(self, request_id: str) -> LLMResult | None      # replay-safe lookup
    def spend_today(self) -> SpendSnapshot                     # budget governor / UI
```

Anthropic-side note (verified against current API): T3 uses `client.messages.stream(...)` with `cache_control: {"type":"ephemeral"}` breakpoints; adaptive-thinking-era models reject `temperature` on some tiers — the adapter only passes sampling params the target model accepts, and the Sonnet-class default keeps temperature support. Errors are caught as typed exceptions (`RateLimitError`, `APIStatusError`, `APIConnectionError`) most-specific-first.

---

## 3. Job queue: durability, idempotency, crash resume

SQLite (WAL mode) is the queue, the result store, and the ledger. One writer process (the sim), N async workers in-process.

```sql
CREATE TABLE llm_jobs (
  request_id   TEXT PRIMARY KEY,      -- idempotency key
  call_class   TEXT NOT NULL,
  role         TEXT NOT NULL,
  priority     INTEGER NOT NULL,      -- 0 interactive, 1 urgent, 2 background
  deadline     TEXT NOT NULL,         -- 'interactive'|'hour'|'overnight'
  sim_tick     INTEGER NOT NULL,
  ward_id      TEXT,                  -- for cache-aware ordering
  payload      BLOB NOT NULL,         -- msgpack(LLMRequest)
  state        TEXT NOT NULL DEFAULT 'PENDING',  -- PENDING|INFLIGHT|DONE|DEAD
  lease_until  TEXT,                  -- worker lease; expired INFLIGHT -> PENDING on boot/sweep
  attempts     INTEGER DEFAULT 0,
  created_at   TEXT, updated_at TEXT
);
CREATE INDEX idx_jobs_dispatch ON llm_jobs(state, priority, ward_id, request_id);

CREATE TABLE llm_results (
  request_id  TEXT PRIMARY KEY REFERENCES llm_jobs(request_id),
  status      TEXT NOT NULL,
  parsed_json TEXT, raw_text TEXT,
  model_id    TEXT, attempts INTEGER,
  in_miss INTEGER, in_hit INTEGER, in_write INTEGER, out_tok INTEGER,
  cost_usd REAL, qc_flags TEXT, created_at TEXT
);

CREATE TABLE llm_ledger (          -- append-only cost telemetry, one row per API attempt
  id INTEGER PRIMARY KEY, ts TEXT, request_id TEXT, call_class TEXT, model_id TEXT,
  in_miss INTEGER, in_hit INTEGER, in_write INTEGER, out_tok INTEGER,
  cost_usd REAL, off_peak INTEGER, http_status INTEGER, latency_ms INTEGER
);
```

**Idempotency.** `request_id = blake2b_hex(call_class | schema_ver | sim_tick | sorted(entity_ids) | sha256(all segment hashes + tail))[:24]`. Enqueue is `INSERT OR IGNORE`; a re-run of the same sim tick (crash replay, user rewind-and-replay of a deterministic segment) produces byte-identical requests and is served from `llm_results` with zero API calls. Crashed batches resume for free: on startup, `UPDATE llm_jobs SET state='PENDING' WHERE state='INFLIGHT' AND lease_until < now()`.

**Determinism boundary.** The clockwork layer is seeded-deterministic; LLM outputs are not. Once a result is committed to `llm_results` it is canon — replay reads it back rather than re-sampling. This makes the whole sim reproducible from (seed, llm_results).

---

## 4. Concurrency, rate limits, retry, scheduling

```python
async def worker_loop(lane: Lane):            # lanes: INTERACTIVE (2 slots premium), BACKGROUND (12 slots cheap)
    while True:
        job = await claim_next(lane)          # SELECT ... WHERE state='PENDING' AND priority IN lane.prios
                                              # ORDER BY priority, ward_id, request_id LIMIT 1  (+ lease)
        spec = REGISTRY[job.role]
        await bucket(spec).acquire(rpm=1, tpm=estimate_tokens(job))   # token buckets per provider
        try:
            result = await call_with_ladder(spec, job)                # §5 validation ladder inside
        except RetryableError as e:                                   # 429 / 5xx / timeouts / conn
            await backoff(job.attempts, retry_after=e.retry_after)    # expo + full jitter, cap 60s, max 5
            requeue(job); continue
        except HardError:                                             # 400-class after ladder exhausted
            result = clockwork_fallback(job)                          # §5 rung 6
        persist(result); ledger(result); notify(job.request_id)
```

- **Rate limiting:** two token buckets per provider (requests/min, tokens/min) sized from `ModelSpec`; TPM estimate = prompt chars/3.2 + `max_output_tokens`. Honors `Retry-After` on 429.
- **Priority preemption:** the interactive lane has reserved concurrency and its own bucket share so a Ganeshotsav background flood (S8) can never add latency to the user's live conversation.
- **Cache-aware ordering:** background dispatch sorts by `(priority, ward_id, request_id)` so all households of a ward run consecutively — Segment A+B stay hot in the provider's automatic prefix cache (DeepSeek-style context caching keys on exact prefix bytes; temporal locality maximizes hit rate). This ordering is *the* mechanism behind the h≈0.67 hit rates in the cost model.
- **Off-peak scheduler:** jobs with `deadline_class != interactive` and sim-speed slack are held for the provider's discount window (DeepSeek historically ~50% off chat in UTC 16:30–00:30; treated as a config knob `off_peak.discount`, re-verified at deploy). The sim can pre-compute the next sim-day's T1/T2 background load during the window ("overnight batch" pattern).
- **Anthropic Batches API (verified: 50% discount, ≤24h, results keyed by `custom_id`):** used for the offline QC audit lane (§7) — never for anything the sim loop waits on.

---

## 5. Structured output enforcement — the six-rung ladder

Schemas are pydantic models in a versioned registry; schema text (compact, field-commented) is embedded in Segment A so it is cached. All non-T3 calls run the ladder:

```
1. Request-level: response_format={"type":"json_object"} (OpenAI-compatible), schema in cached prefix.
2. Parse: json.loads → pydantic validate.                          (~92–95% pass here)
3. Local repair: strip fences/prose, json_repair lib, retry parse.  (+3–5%)
4. Repair call: cheap model, "broken JSON + schema + pydantic errors → fixed JSON only",
   max 1, temp 0, max_output = original budget.                     (+1–2%)
5. Regenerate: original prompt + appended error feedback, temp +0.1, max 2 regens.
6. Dead-letter → clockwork_fallback(job): a deterministic default decision
   (T1: reuse household's weekly template plan; scene: outcome sampled from event's
   base-rate table + one canned narration line; micro: no-op). Logged, QC-flagged,
   eligible for overnight re-run. The sim advances regardless.
```

**Retry budget:** ≤3 generations + 1 repair call per job (expected overhead ~3–5% of cheap-lane calls, ~$0.07/day at 50k — in the cost table). Rung 6 guarantees liveness (Principle 2).

Schema design rules that cut cost and failure: terse keys (`acts`, not `planned_activities`); enums as short codes; no prose fields in T1 (`household_plan` output is pure structure ~350 tok); dialogue only in `scene`/`focal`; every scene schema carries `facts_asserted` (§7) so fact extraction needs no second pass.

```python
class HouseholdPlan(BaseModel):        # T1 output, ~350 tok
    hh: str
    date: str
    members: list[MemberDay]           # per person: acts: list[Act]
class Act(BaseModel):
    t: str          # "HH:MM"
    a: str          # activity code: WRK|SCH|SHP|TMP|MED|SOC|HOM|TRV ...
    loc: str        # place_id
    note: str = ""  # ≤12 words, optional flavor
class SceneOut(BaseModel):             # T2 output, ~700 tok
    summary: str                       # ≤60 words
    dialogue: list[Line]               # speaker_id, lang tag, text
    outcomes: list[Outcome]            # typed state changes for the event engine
    facts_asserted: list[Fact]         # subject_id, predicate, value, confidence
    mood_deltas: dict[str, int]
```

---

## 6. Prompt-cache engineering

### Segment architecture (order = share-breadth, widest first)

| Seg | Content | ~Tokens | Shared by | Changes |
|---|---|---|---|---|
| A | City preamble, sim rules, output schema, code-mix style rules + 3 register exemplars — per call_class | 2,500 | ALL calls of that class | On prompt version bump only (version-stamped `:vN`) |
| B | Ward-day context: ward stats, landmarks, ambient state (weather, festival phase, flood status), active local events digest | 800 | All calls touching that ward that sim-day | Once per sim-day at day boundary |
| C | Household/person canon slice: members, jobs, relations, active arcs, memory digest | ≤1,200 (governed) | All calls touching that household that sim-day | Day boundary only; intra-day new facts go in the tail |
| tail | Today's specifics, impinging events, retrieved memories, the ask | 400 (T1) / ~1,000 (T2) | nobody | Every call |

**Byte-stability rules (enforced by `CacheSegment` construction, unit-tested):** canonical JSON (`sort_keys`, fixed separators); no timestamps, RNG, or UUIDs in segments (sim-date lives in B where it's constant for the day); segment text rendered once per (key, version) and content-addressed by sha256; Segment C updates are deferred to the day boundary — a mid-day fact appears in the tail today and is folded into C tonight by the weekly/daily digest job. Any C edit intra-day would invalidate only C+tail (A+B survive — prefix property), but we avoid even that.

**Quantified effect (cheap lane).** T1 call: 4,900 in / 350 out. Cached fraction h = (A+B+C hit)/total = 3,300/4,900 ≈ 0.67 (C hits on the 2nd+ touch of a household in a day; first touch pays C as miss — the table below books C as miss for T1, conservative).

- Per-call T1 cost, cache working: 350×0.87 + 1,600×0.28 + 3,300×0.028 = **$0.00084**
- Per-call T1 cost, no cache: 350×0.87 + 4,900×0.28 = **$0.00168**
- → **Prefix caching halves the cheap lane.** A silent invalidator (stray timestamp, unsorted dict) doubles the monthly bill; the ledger's `in_hit/in_miss` ratio is monitored with an alert at h < 0.5.

**T3 / Anthropic mechanics (verified):** prompt caching is exact-prefix; render order tools→system→messages; breakpoints via `cache_control {"type":"ephemeral"}` (5-min TTL, write = 1.25× input; 1h TTL available at 2× write — only worth it when ≥3 reads with >5-min gaps, e.g. a persona the user revisits across a slow evening). Breakpoint placement: (1) end of system = city preamble + persona sheet (~3–4k tok, comfortably above the 1024-token Sonnet-class cache minimum); (2) last content block of the latest turn, so the growing conversation re-reads at 0.1×. Effective T3 economics per turn (8k avg context, ~80% cached): in ≈ 1,500×$3 + 500×$3.75(write) + 6,000×$0.30 ≈ $0.0081; out 500×$15 = $0.0075 → **≈$0.016/turn**. A mid-conversation model switch would cold-start the cache (caches are model-scoped) — the adapter pins a focal session to one model.

---

## 7. Quality control

**a) Slop/cliché gate (free, synchronous).** Per-language n-gram blocklist ("bustling streets", "tapestry of", "eyes widened", "heart of the city", + Hindi/Marathi equivalents like overused "arre wah" spam); regex caricature markers (phonetic-spelling mockery: "vhat", "ischool"; servile stock phrases). Hit → QC flag → regen once with the offending phrase named in the tail. Lexicon lives in a data file, grown from judge findings.

**b) Embedding near-dup detector (local GPU, synchronous, cheap).** Every scene summary/dialogue is embedded (bge-m3); cosine > 0.93 against the last 500 outputs of the same call_class ⇒ "template-itis" flag ⇒ regen with a diversity nudge. Also powers memory retrieval so the vectors are paid for anyway.

**c) Sampled LLM judge (asynchronous, off the hot path).** 5% of T1/T2 outputs scored on {naturalness 1–5, register-match, caricature y/n, canon-consistency y/n} with a cached rubric prefix. Routed through the **Anthropic Batches API at 50% off** (or the local Qwen3-4B judge at $0). Scores land in `llm_ledger`-adjacent `qc_scores`; weekly report drives prompt iteration. Judged failures don't retro-edit canon; they tune prompts and lexicons.

**d) Canon-contradiction checks (in-band, two-stage).** Stage 1 — deterministic: every `Fact` in `facts_asserted` is key-checked against Canon DB (names, ages, jobs, addresses, relationship edges, alive/dead). Exact-key conflict ⇒ regen with a `KNOWN_FACTS` block appended to the tail listing the violated facts (1 retry; then rung-6 fallback with the scene's outcomes kept but dialogue discarded). Stage 2 — fuzzy: embedding-NN retrieves the 10 nearest canon facts about the scene's entities; a cheap temp-0 NLI-style call adjudicates only when stage 1 was clean but retrieval surfaces high-similarity tension; sampled at 20% (cost is in the QC line). Prevention beats detection: the retrieved facts are *already in the tail* at generation time, so contradictions are rare by construction.

**e) Code-mixing without caricature.** Every person's canon slice carries a sociolinguistic register tag generated once from ward statistics: `{home_lang: mr|hi|ur|other, edu, occupation_register, code_mix_style: mr_dominant|balanced|en_dominant}`. Segment A contains the rules — mix at clause level, not word-salad; Marathi/Hindi lines in Devanagari with an inline English gloss in parentheses (UI can toggle); honorific correctness (अहो/तुम्ही vs तू, -राव, ताई, काका, साहेब); **banned**: phonetic-accent caricature, religion/caste-marked speech stereotypes, poverty-porn diction — plus 3 curated exemplar dialogues per register (cached, so ~600 tok of exemplars cost 0.028/M after the first call per class). Judge (c) samples specifically for caricature. Sensitive dimensions rule: caste/religion may condition *statistics* (festival participation rates, neighborhood composition) upstream in the clockwork layer, but the prompt never instructs the model to derive personality or speech from them; a hard post-filter blocklist backstops.

**Devanagari token tax (cost-relevant):** Devanagari text tokenizes ~2–3× worse per word than English on most BPE vocabularies. Mitigation: structure/JSON keys and outcome fields always in English; only dialogue lines carry Devanagari; T2 output budget gets +15% (700→~800) when the register mix is Marathi-heavy. Open question §OQ-2 tracks measuring the real inflation on the chosen model.

---

## 8. Tokens-per-simulated-day formulas

Let: `B1` = T1 call budget/day, `B2` = T2 scenes/day, `M` = micro items/day (k=20 per call), `J` = judge rate (0.05), `R` = repair rate (0.04), `F` = focal turns/day (user-driven, ~20/focal-hour).

Per-call token profiles (in_hit / in_miss / out):

| Class | in_hit | in_miss | out |
|---|---|---|---|
| household_plan | 3,300 (A+B) | 1,600 (C+tail) | 350 |
| scene | 3,300 (A+B+warm C) | 2,200 (tail+cold C amortized) | 700 |
| micro_update (per call, k=20) | 2,500 (A) | 1,200 (20×60) | 800 (20×40) |
| qc_judge | 600 | 800 | 80 |
| repair | 0 | 1,000 | 400 |
| focal_turn (premium) | 6,000 @0.1× | 1,500 + 500 write @1.25× | 500 |

**Daily cheap-lane cost** = Σ_class N_class × (in_miss×P_miss + in_hit×P_hit + out×P_out) with P = ($0.28, $0.028, $0.87)/M.
**Daily premium cost** = F × $0.016.
**Monthly** = daily × 30 × sim_speed_multiplier × (1 − off_peak_blend).

## 9. Cost tables

**Assumptions:** DeepSeek-class prices as parameterized above (the $0.87/M output figure is given; input split is the standard ~10:1 hit:miss context-caching ratio at this class — all numbers re-derivable from `ModelSpec`). 30 sim-days/month at 1× speed. 12k households per 50k residents.

### 50k population (Old City core) — baseline day

| Line | Calls/day | $/day |
|---|---|---|
| T1 household_plan (B1 = 1,800 = 15% of 12k, salience-selected) | 1,800 | 1.52 |
| T2 scene (B2 = 600) | 600 | 0.79 |
| micro_update (M = 4,000 items, 200 calls) | 200 | 0.22 |
| qc_judge (5%) | 120 | 0.04 |
| repair (4%) | 104 | 0.07 |
| **Cheap lane total** | ~2,800 | **2.64** |
| T3 focal (2 focal-hours, F=40, Sonnet-class) | 40 | 0.63 |
| **Total/day** | | **≈3.3** |

**Monthly @50k: ≈$79 cheap + ≈$19 premium ≈ $100.** With off-peak batching of ~70% of background volume at 50%: ≈$72/mo. Comfortably inside budget with 3–4× headroom for sim-speed bursts.

### 3.5M population (full Pune, 875k households)

| Policy | B1 | B2 | M | Cheap $/day | Cheap $/mo | Verdict |
|---|---|---|---|---|---|---|
| **Naive linear** (15% of 875k households daily) | 131,250 | 43,750 | 290k | ≈190 | **≈5,700** | Infeasible — kills the per-household-per-morning hypothesis |
| **Attention-budgeted (recommended)** | 6,000 | 2,000 | 15,000 | 8.9 | **267** | Fits; quality concentrated where attention/events are |
| Attention-budgeted + off-peak (70% deferred @50%) | 6,000 | 2,000 | 15,000 | ≈5.8 | **≈175** | Recommended operating point |
| Rich mode (user pays for more life) | 20,000 | 6,000 | 50,000 | ≈29 | ≈870 | Optional knob, linear |

Plus T3 ≈$19–30/mo (unchanged — it scales with the user, not the city). **Full-Pune total ≈ $200–300/mo.**

Selection under a budget: households ranked by `salience = w1·1/dist(attention) + w2·event_exposure + w3·arc_activity + w4·staleness`; top-B1 get real T1 calls, the rest run frozen weekly templates (T0) with staleness accumulating so everyone is eventually refreshed. The budget governor recomputes B1/B2/M every sim-day from `spend_today()` vs. the monthly cap and degrades gracefully: shrink radius → drop micro class → clockwork-only (sim never stops).

### Lever cost curves

| Lever | Curve | Notes |
|---|---|---|
| T1 budget B1 | $0.00084 × B1 /day — linear | Quality = radius of "alive" households around attention |
| Frozen-household fraction | complement of B1; 85%→95% frozen cuts cheap lane ~55% | Staleness term bounds divergence |
| Scene sampling B2 | $0.00132 × B2 /day — linear | Sub-sampled scenes degrade to clockwork outcome + 1-line narration ($0) |
| Cache hit rate h | input cost × [(1−h) + 0.1h]; h: 0→0.67 halves cheap lane; each +0.1 h ≈ −9% total | Guarded by ward-sorted dispatch + byte-stability tests + ledger alert |
| Output discipline | out is ~35% of cheap cost; −100 tok on T1 output ≈ −10% of T1 line | Terse schemas, enum codes |
| k-packing micro | ~2.2× cheaper per item than solo calls | Only for gossip/opinion/no-dialogue updates |
| Off-peak window | ×(1 − 0.5×deferred_share) on background | Deferred share ~0.7 achievable at 1× sim speed |
| Anthropic Batches (QC lane) | ×0.5 on judge line | Verified 50% batch discount; ≤24h turnaround acceptable for audits |
| Sim speed | × sim-days per wall-day — linear | 4× time-lapse = 4× cost; governor caps |
| T3 model choice | Sonnet $0.016/turn vs Haiku $0.004/turn | Haiku for secondary voices only; focal stays premium |

---

## 10. Interfaces, telemetry, ops

- **Budget governor:** config `{daily_cap_usd, monthly_cap_usd, reserve_interactive_usd}`; exposes `spend_today()`; emits `BudgetPressure` events the Scheduler uses to shrink B1/B2/M. Interactive T3 has a carved-out reserve so background can never starve the user's session.
- **Dashboards** (read from `llm_ledger`): $/sim-day by call_class; cache hit ratio; ladder rung distribution; QC flag rates; p50/p95 interactive latency.
- **Config:** all prices, budgets, windows in `pune_sim/config/inference.toml`; cost model is a pure function of `ModelSpec` + volume knobs, re-runnable as `python -m pune_sim.costs` to reprint §9 tables when prices drift.

## 11. Libraries (named)

`openai` (AsyncOpenAI, base_url per provider) · `anthropic` (AsyncAnthropic, streaming + cache_control + Batches) · `pydantic` v2 · `json-repair` · `aiosqlite` (+ WAL) · `tenacity` (backoff) · `aiolimiter` (token buckets) · `blake3`/`hashlib` (request ids) · `msgpack` (payloads) · `sentence-transformers` + `bge-m3` (local embeddings; `sqlite-vec` for vector storage) · optional `llama-cpp-python` + Qwen3-4B-Instruct (local judge) · `structlog` (ledger-adjacent logging). All Windows-11-friendly, no service dependencies.

## Key decisions

- **Replace the 'one T1 household call per household per simulated morning' hypothesis with a salience-ranked global T1 call budget (attention-budgeted activity), with frozen households running weekly template plans on the clockwork layer.** — Cost at full Pune: naive per-household T1/T2 is ~$190/day (~$5,700/mo) at 875k households — infeasible; a 6,000-call/day budget costs ~$8.9/day (~$267/mo) and concentrates fidelity exactly where the user's attention and active events are, which is where fidelity is observable. Staleness is a salience term, so every household is eventually refreshed.
  - Rejected: Per-household daily T1 calls (the working hypothesis) — linear in population, breaks the 'few hundred dollars/month' constraint by ~20x at city scale; also mostly wasted, since 99% of households are never observed on any given day.
- **Five generic call classes (household_plan, scene, micro_update, focal_turn, qc/repair) are the only inference paths; events/arcs/institutions differ only in prompt content and job volume.** — Guarantees generality: any new situation type is expressible as jobs of existing classes with different segment/tail content, so no code changes per scenario. Also concentrates schema, caching, and QC engineering on five stable shapes.
  - Rejected: Per-domain prompt pipelines (accident pipeline, wedding pipeline, court pipeline) — more 'tuned' per scenario but combinatorially unmaintainable and violates the generality design value.
- **Thin provider adapter over official openai + anthropic SDKs with a ModelSpec registry, instead of litellm.** — Only two wire protocols exist in the system; the design depends on provider-specific features litellm abstracts poorly (DeepSeek automatic prefix caching semantics, Anthropic cache_control breakpoints/TTLs, Anthropic Batches for QC). Fallback chains are ~40 lines. Fewer fast-churning dependencies for a solo dev.
  - Rejected: litellm router — convenient multi-provider routing and cost tracking out of the box, but adds churn, obscures cache-control placement, and its cost tracking is replaced by our ledger anyway.
- **SQLite (WAL) as job queue + result store + cost ledger, with deterministic blake2b request IDs for idempotency and lease-based crash resume.** — Boring, zero-service, transactional with the rest of the sim's persistence; INSERT OR IGNORE dedupe makes crash replay and deterministic re-runs free (results replayed from llm_results, zero API calls); expired-lease sweep resumes interrupted batches exactly.
  - Rejected: Redis+Celery or an in-memory asyncio queue — the former is operational overhead on Windows for a solo dev; the latter loses in-flight work on crash and cannot dedupe across restarts.
- **Four-segment cache architecture (class preamble / ward-day / household slice / volatile tail) with byte-stability rules, day-boundary-only canon slice updates, and cache-aware ward-sorted dispatch.** — Achieves h≈0.67 input cache hit on the cheap lane, halving its cost ($0.00168 → $0.00084 per T1 call); ward-sorted consecutive dispatch maximizes provider prefix-cache temporal locality; version-stamped segments make invalidation explicit and testable, and the ledger's hit/miss ratio alarms on silent invalidators.
  - Rejected: Freely assembled per-call prompts (retrieval directly interpolated anywhere) — more flexible wording but destroys prefix stability and doubles the cheap-lane bill.
- **Structured output via a six-rung ladder ending in a deterministic clockwork fallback; fact extraction is in-band (facts_asserted inside the scene schema), not a second pass.** — Bounded retry budget (≤3 gens + 1 repair, ~3–5% overhead) with a guaranteed-liveness floor: the sim always advances even if a model or provider is down. In-band extraction halves the number of calls a scene needs and keeps the asserted facts adjacent to the dialogue that asserted them.
  - Rejected: Separate extraction pass per scene (doubles scene call volume) and unlimited retries (unbounded cost, sim stalls on provider outage).
- **Local GPU runs embeddings (bge-m3) and optionally a Qwen3-4B temp-0 QC judge; no local generation. Premium T3 is Claude Sonnet-class with cache_control conversation caching; Haiku 4.5 optionally voices secondary characters in focal scenes.** — Embeddings are the highest-volume, quality-insensitive workload (retrieval, contradiction candidates, dup detection) — free locally. Local 4–8B generation would save only ~$2.6/day at 50k while degrading Marathi quality and JSON reliability. T3 economics with verified Sonnet-class caching (write 1.25x, read 0.1x, 5-min TTL) come to ~$0.016/turn, ~$19/mo — premium quality where the user is actually looking.
  - Rejected: Local 7B as the T1/T2 workhorse — the API is too cheap for the quality tradeoff to pay, and the GPU is better spent on embeddings; also rejected running QC judging on the premium model (unnecessary quality for a classification task).
- **Off-peak deferral of background lanes (config-driven discount window) plus Anthropic Batches API (verified 50% off) for the asynchronous QC audit lane.** — Background T1/T2 with deadline_class 'overnight' is deferrable by construction; a ~70% deferred share at a 50% window discount cuts the full-Pune cheap lane from ~$267 to ~$175/mo. QC audits tolerate 24h turnaround, so the batch discount is free money.
  - Rejected: Uniform real-time dispatch — simpler scheduler but leaves ~35% of the cheap-lane budget on the table at scale.

## Interfaces

- **Clockwork Scheduler / Simulation Core**: Calls InferenceService.submit(LLMRequest) for T1/T2/micro jobs at decision points (fire-and-forget; results consumed next tick via result(request_id)) and submit_and_wait(req, timeout_s) for event-urgent jobs. Receives LLMResult with parsed schema-validated JSON, or status='fallback_used' meaning the deterministic clockwork default was applied — the scheduler must accept both identically. Consumes BudgetPressure events to shrink its T1/T2/micro volume knobs (B1, B2, M). Provides the salience ranking that selects which households enter the T1 budget.
- **Canon DB**: Reads: get_segment(kind, key, as_of_tick) -> CacheSegment{text, version, sha256} for ward-day (B) and household-slice (C) segments — Canon DB owns canonical serialization and version bumps at day boundaries; get_related_facts(entity_ids, k) -> list[Fact] for tail-time grounding and contradiction retrieval (vectors via this subsystem's local embedder). Writes: commit_facts(request_id, facts_asserted: list[Fact]) after ladder success; find_conflicts(claims: list[Fact]) -> list[Conflict] used synchronously in the contradiction gate. Canon DB may never receive facts from a job whose result was rung-6 fallback dialogue.
- **Scene/Narrative Engine (T2/T3 presentation)**: T2: submits scene jobs (call_class='scene') with participant entity_ids and event payload in the tail; receives SceneOut{summary, dialogue[], outcomes[], facts_asserted[], mood_deltas}. T3: open_focal_stream(LLMRequest with call_class='focal_turn') -> AsyncIterator[str] of tokens for live rendering; the engine appends conversation turns and this subsystem manages Anthropic cache_control breakpoints and model pinning for the session.
- **Event Engine**: Enqueues event-triggered jobs with priority=1 (event-urgent) and deadline_class='hour'; supplies the event digest text that goes into Segment B (ward ambient) at day boundaries and into tails immediately. Receives Outcome[] objects from SceneOut for consequence propagation. Event types carry no inference-specific code — only tail content and job volume.
- **UI / Observer**: Consumes the T3 token stream (SSE/websocket passthrough of open_focal_stream); queries spend_today() -> SpendSnapshot{usd_today, usd_month, by_class, cache_hit_rate, budget_state} for the cost HUD; may set user-facing knobs (sim speed, 'rich mode' budget multiplier) which route to the budget governor.
- **Persistence / Ops**: Owns tables llm_jobs, llm_results, llm_ledger, qc_scores in the sim's SQLite database (WAL). Other subsystems read llm_results by request_id (read-only). Exposes config file pune_sim/config/inference.toml (ModelSpec registry, prices, budgets, off-peak windows) and a CLI 'python -m pune_sim.costs' that reprints the cost tables from current config for price-drift audits.

## Scenario traces

## Scenario traces — each is ordinary jobs of the five generic classes

### S1 — School-bus crash, Shivajinagar, 8:10am (acute burst)
Clockwork's hazard sampler fires the collision (T0, base rates). Event Engine enqueues a priority-1 `scene` job for the crash site (participants: father, daughter, driver, bystander sample) with the event payload in the tail; Segment B for Shivajinagar ward already carries the day's ambient state. SceneOut returns injuries as typed `outcomes` (consumed by health/institutions subsystems: ambulance to Sassoon, FIR record) and `facts_asserted` (injury severity, vehicle plate, FIR number) committed to Canon. Downstream ripples are cheaper classes: parental-panic phone calls = `micro_update` items k-packed 20/call; school-absence notations = pure T0. If the user is watching, the same moment is instead served by `focal_turn` on the premium stream — same event, different tier, chosen by attention, not by event type. The interactive lane's reserved concurrency means the background burst (jam reroutes, gossip seeds) never delays the focal stream. Total marginal cost of the whole incident: ~15 scenes + ~40 micro items ≈ $0.03.

### S2 — Temple-donation-scam rumor (informational, high-volume)
Rumor propagation is the flagship `micro_update` workload: each gossip hop is one k-packed item — input: 60-token item (current rumor text, teller/listener register tags, relationship edge), output: 40-token mutated rumor + belief delta. Mutation emerges from the LLM rewriting under each teller's register; the WhatsApp forward is just a hop with `channel=forward` and higher fan-out in the clockwork diffusion model (T0 decides *who* hears; the LLM decides *what* it becomes). 2,000 hops/day = 100 packed calls ≈ $0.11/day. When the rumor reaches a household selected into the T1 budget, it appears in that household's tail and can alter the day plan (skip the temple donation); if the user interviews a believer, the current mutated text is retrieved from Canon into the T3 tail. No rumor-specific inference code exists.

### S3 — 48-hour cloudburst flood near the Mutha (area-ambient)
Weather is T0. The flood writes itself into Segment B of affected wards at the day boundary ('ambient: flooding in low-lying lanes; PMPML diversions; PMC complaint surge') — one segment version bump, automatically shared by every call in those wards, cache-coherent by design. Salience weights spike for riverside households (event_exposure term), pulling them into the T1 budget: their day plans now route around water, file PMC complaints (outcomes), worry about disease (mood_deltas). Commute failures are clockwork. Disease worry that turns into a clinic visit is a `scene` only if non-routine. The budget governor absorbs the surge by radius-shrinking elsewhere, keeping $/day flat — an area event costs re-prioritization, not new spend.

### S5 — Job loss → debt spiral → recovery (slow personal arc, months)
The arc lives in Canon (arc state on the person), not in this subsystem — but arc_activity is a salience term, so the affected household stays inside the T1 budget for months at a low duty cycle (e.g. 2–3 T1 calls/week, not daily): each call's Segment C carries the updated arc digest (weeks 1–3: unemployed, moneylender debt ₹40k, school fees missed), and the plan JSON reflects it (job-search acts, skipped purchases). Family-tension flashpoints are ordinary `scene` jobs when the clockwork tension meter crosses threshold. The weekly canon-digest job (a `micro_update`-class summarization) compresses months of history so Segment C never exceeds its 1,200-token governor — this is what makes a 6-month arc cost the same per call as day one. Total arc cost over 6 sim-months: ~80 T1 + ~15 scenes ≈ $0.09.

### S6 — Truck driver's case in Shivajinagar district court (institutional, 3+ years)
Sparsest possible workload, and the idempotency design's showcase: the court subsystem schedules hearing-date jobs years of sim-time apart. Each hearing is one `scene` job (participants: driver, lawyer, judge persona slice; tail: case file digest retrieved from Canon; outcome: adjournment/order per BNSS-informed clockwork probabilities, with the LLM narrating and asserting facts like next-hearing dates). Deterministic request IDs mean a crash or replay across those years never re-bills a hearing; results replay from llm_results. Deadline_class='overnight' routes every hearing through the off-peak window. Three years of litigation ≈ 25 scenes ≈ $0.03 — institutional longevity is free because cost follows events, not elapsed time.

### S8 — Ganeshotsav, 10 days (mass event, peak load)
The stress test for the budget governor and lanes. Segment A is untouched; Segment B for every Peth ward carries the festival phase (day-N processions, road closures, bandobast); commerce spike and crowd inflow are clockwork. Demand for T1/T2 triples (visarjan-day processions, pandal scenes, vendor windfalls). The governor holds daily spend at the cap by: (1) keeping the interactive reserve inviolate (the user watching a dhol pathak gets full T3), (2) filling B2 with attention-proximate pandal scenes first, (3) degrading far-from-attention festival activity to clockwork + one-line narration, and (4) pushing deferrable jobs into the off-peak window each night. A 10-day festival at 'rich mode' knobs costs ~$29/day at full Pune — a user choice, not a failure mode; at default budgets it costs the same as any other day, just allocated differently.

## Generality argument

Generality holds because the subsystem is blind to event semantics by construction. (1) Closed set of call classes, open set of content: every conceivable situation — a chain-snatching, a bandh, a birth, an exam result, a PMC tender scandal, an inter-caste marriage negotiation handled respectfully — must reduce to "a household re-plans" (household_plan), "people interact non-routinely" (scene), "small state/belief/text updates propagate" (micro_update), "the user talks to someone" (focal_turn), or "output gets audited/repaired" (qc). These five cover the complete space of {plan, interact, propagate, converse, verify}; new situation types supply new tail text and new volumes, never new code. The eight probe scenarios were traced without a single scenario-specific mechanism, and the traces used every class. (2) Content-agnostic scheduling: salience (attention distance, event exposure, arc activity, staleness) and budgets are scalar policies that know nothing about *what* the event is — a flood and a festival are both just exposure gradients; a court case and a debt arc are both just arc_activity with sparse job emission. (3) Segments are typed by scope, not by topic: Segment B holds *whatever* is ambient in a ward (monsoon, election campaigning, plague scare, metro construction) with identical cache mechanics; Segment C holds *whatever* a household's canon digest says. (4) Schemas are structural, not topical: `outcomes` are typed state-changes interpreted by other subsystems, `facts_asserted` is subject-predicate-value — a hearing adjournment and a wedding invitation are the same Fact shape. (5) The failure path is general too: every class has a clockwork fallback, so an unforeseen situation that produces malformed output degrades to statistics + canned narration rather than crashing — unseen inputs are at worst boring, never fatal. The one deliberate asymmetry — attention-budgeting — is itself content-neutral: it allocates fidelity by observability, which is the only dimension the user can actually perceive, regardless of what is happening in the city.

## Open questions

- DeepSeek-class context-cache TTL/eviction behavior under low, bursty QPS is undocumented: will ward-sorted dispatch sustain h≈0.67 if a ward's batch takes >N minutes, and does the 3.5M-scale multi-hour overnight batch keep Segment A hot across the whole run? Needs an early empirical probe with the ledger's hit/miss telemetry; if hit rates disappoint, the fix is larger per-ward batch contiguity or accepting a lower h in the cost model (worst case 2x cheap lane, still within budget at 3.5M).
- Exact Devanagari token inflation on the chosen cheap model's tokenizer (assumed 2–3x per word; budgeted +15% on Marathi-heavy T2 output). Measure with count-token probes on representative code-mixed dialogue; if inflation exceeds ~3x, consider romanized Marathi in the wire format with Devanagari rendering client-side (quality/authenticity tradeoff to be evaluated by the judge lane).
- Does the cheap provider ship an OpenAI-compatible batch endpoint (50%-class discount) during the build window? Currently the design substitutes off-peak-window scheduling for background lanes and uses Anthropic Batches only for QC; a cheap-lane batch API would stack with prefix caching and roughly halve the recommended full-Pune operating point again.
- Cross-provider persona drift on fallback: if the cheap-alt provider serves a scene mid-arc, does dialogue voice shift noticeably? Mitigations to test: pin arcs/households to one provider via request-id hash affinity, or restrict fallback to structure-only classes (household_plan, micro_update) and let scenes wait out an outage on the clockwork fallback.
- Local GPU sizing: bge-m3 embedding throughput plus an optional Qwen3-4B judge on the same card — VRAM and contention unknown until the actual GPU is confirmed; the design keeps both optional and API-substitutable, but the QC-at-$0 claim depends on it.
- T1 salience-budget quality validation: does a 15% duty cycle with weekly template reuse produce visible 'frozen NPC' artifacts when the user pans quickly across wards? May need a cheap 'thaw' micro_update class (one-shot plan perturbation, ~100 tokens) between full T1 refreshes — cost is provisioned for in the micro line but the mechanism is unbuilt.
- Price drift: all tables derive from ModelSpec constants (cheap: 0.87/0.28/0.028; premium Sonnet-class verified 3/15 with 0.1x read, 1.25x/2x write, 1024-token cache minimum; Haiku 1/5; Anthropic Batches 50%). The `python -m pune_sim.costs` re-derivation exists precisely because these WILL drift — re-verify at implementation time, especially the Sonnet-5 intro pricing window ending 2026-08-31.

## Red-team critique (verdict: needs_changes)

- **[critical]** Cross-household scenes are unspecified in the four-segment cache architecture. The `scene` cost row books A+B+warm-C, but any scene whose participants span two or more households — family confrontations, shop-customer disputes, the couple and both families, and even the design's own S1 bus-crash trace (father, daughter, driver, bystanders) — has no defined C slice and possibly two candidate ward-B segments. The design's flagship trace silently glosses this, which is exactly the overfitting smell it was told to avoid: most dramatic content is multi-household, and for it the cache math, the grounding story, and the contradiction gate's coverage are all undefined.
  - Fix: Specify scene assembly explicitly: A + B(scene-location ward) + C(primary household, chosen by a deterministic rule such as event-owner) + compact 'participant sheets' (~150 tok each, canonically serialized by Canon DB) for foreign participants in the tail at miss price. Add relationship-scoped canon slices for durable dyads (couples, rivals, landlord-tenant). Extend contradiction stage 1 to foreign participants' keyed facts. Re-derive the scene cost row with +400-800 miss tokens (cheap lane +~10%, absorbed by stated headroom).
- **[critical]** No mechanism updates frozen household templates when the world changes structurally (new metro line, road closure, school shutdown). At 3.5M pop the staleness refresh cycle is ~146 days (875k households / 6,000 T1 calls per day), so for months hundreds of thousands of canon plans assert commutes the clockwork transport layer no longer runs — a mass canon-vs-clockwork contradiction that fires no LLM error, because templates are replayed as T0 and the contradiction gate only inspects facts_asserted at generation time. Principle 3 ('attention is the budget unit') is falsified for structural change: required work scales with change-affected population, not attention.
  - Fix: Store templates as structured Act lists and give clockwork a deterministic template-migration pass: on infrastructure change, rewrite TRV/loc acts through the routing engine — zero LLM cost, immediate consistency. Add a commute-graph term to event_exposure so corridor households (not just station-ward residents) gain salience. Provision a bounded one-off 'migration wave' budget line of k-packed micro thaws for behavioral texture (~350k affected households ≈ $20-40 one-time), outside the daily B1 governor so it cannot starve the attention bubble.
- **[critical]** The sensitive-arc path fails exactly where the mandate demands care. Segment A's cached rule ('never derive personality/speech from religion') directly contradicts scenario tails that require religiously-grounded motivation (a father whose objection to the match IS religious). A cheap model at temp 0.9 resolves the contradiction as generic soap-opera mush, or violates the rule and risks caricature — after which the hard post-filter blocklist flags legitimate religious speech, regen loops burn, and the canon-conflict path terminates at rung-6 'dialogue discarded'. Meanwhile community gossip about the couple flows through micro_update, the one lane with no slop lexicon, no near-dup check, and judge sampling stated only for 'T1/T2 outputs'. The QC machinery is calibrated for background texture and actively suppresses foreground drama on the highest-stakes content.
  - Fix: Mediate sensitivity through canon facts: individual characters may hold explicitly recorded views ('opposes the match; fears community standing') that prompts are permitted to voice — statistics still never imply individual traits. Add a sensitive_scene flag (set by the Event Engine on religion/caste/communal topics) that routes the scene to premium or premium_lite with 100% judge coverage instead of blocklist-regen. Run the free lexicon gate on micro_update outputs and raise judge sampling on sensitive-tagged rumor chains. Add refusal-pattern detection (short apologetic/policy text) with reroute-to-premium so cheap-provider refusals cannot silently clockwork the arc.
- **[major]** Dead-letter 'overnight re-run' breaks canon consistency and the idempotency model. The sim already advanced on the rung-6 fallback (template plan / base-rate outcome); a later re-run either produces a different result under the same request_id (silently dropped by INSERT OR IGNORE) or retro-contradicts state downstream ticks already consumed. Related inconsistency in §7d: keeping 'outcomes' from a generation whose dialogue failed the canon check preserves the very generation that was judged contaminated.
  - Fix: Add a consumed_at flag on llm_results. Re-runs are permitted only while unconsumed; once consumed, the fallback IS canon and any refresh must be a new forward-looking request at a later sim_tick with a fresh id. In §7d, on contradiction-retry failure drop the LLM outcomes too and take them from the event's base-rate table.
- **[major]** T3 economics are optimistic. $0.016/turn assumes 8k context, 80% hit, and the 5-minute TTL holding — but a slow conversation (>5-min turn gaps) pays 1.25x cache-write on the whole prefix every turn, and nothing caps context growth across a long interview. At 30-50k context, turns cost $0.05-0.15; a heavy focal user is $60-100/mo, not $19. 'T3 scales with the user' hides superlinear growth in conversation length.
  - Fix: Cap focal context at ~12k with a rolling digest of older turns (written once, cached); auto-promote to the 1h TTL when observed turn cadence exceeds 5 minutes; expose per-session T3 spend in the budget governor with a soft per-focal-hour cap that downshifts to Haiku with user notice.
- **[major]** The 70% off-peak deferral assumption conflicts with the salience model. Tomorrow's T1/T2 selection depends on live attention position and same-day events, which are unknowable during the prior night's discount window — either deferred jobs are mis-selected (wasted spend, wrong households refreshed) or the true deferrable share is far below 0.7, and the $175/mo full-Pune operating point is understated.
  - Fix: Split the T1 budget into an attention/event-driven share (dispatched same-day, full price) and a staleness-driven, attention-independent refresh share (~40-50%) that is deferrable by construction. Restate the full-Pune operating point at ~$200-220/mo and treat the difference as headroom, not plan.
- **[major]** sqlite-vec is brute-force (no ANN index). At 3.5M pop, canon facts plausibly reach 10^7 vectors; bge-m3 is 1024-dim fp32 = 4KB/vector, ~40GB of vector data, and every synchronous contradiction-candidate or memory retrieval becomes a multi-second full scan. The stage-1/tail-grounding hot path dies at scale.
  - Fix: Partition vector tables by entity_id — every retrieval in the design is already entity-scoped (get_related_facts(entity_ids), 'nearest canon facts about the scene's entities') — so each query scans a few hundred rows. Quantize to int8 (bge-m3 tolerates it, 4x smaller). Forbid global-scope KNN in the Canon DB interface contract. Keep near-dup detection on its own small rolling table.
- **[major]** 'Warm C' for scenes is contradicted by the design's own dispatch order. Sorting by (priority, ward_id, request_id) scatters same-household jobs randomly within a ward batch (request_id is a hash), so C-hits depend on provider cache TTL luck, and the scene row's 3,300 in_hit is overstated — compounding OQ-1's admitted uncertainty about DeepSeek cache eviction under bursty QPS.
  - Fix: Sort dispatch by (priority, ward_id, household_id, sim_tick) so same-household jobs are adjacent, and book C as a miss for scenes in the base cost model regardless (cheap lane +~8-10%, within headroom). Let measured ledger hit-rates upgrade the estimate rather than the reverse.
- **[major]** Regen triggers stack without a global cap. Ladder regens (max 2), slop-gate regen, near-dup regen, and canon-conflict regen are separate counters, so a single bad lexicon entry or exemplar mode-collapse can push effective generations to 5-6x per job and fleet regen rate from the budgeted 4% to 30%+ — a silent 30% cheap-lane cost blowup with no alarm defined for it.
  - Fix: Enforce one per-job generation budget (e.g. 4 total generations across all causes, then rung 6) inside call_with_ladder. Add a ledger alarm on fleet regen rate >8%. Stage lexicon updates behind a one-week shadow mode that counts hits without triggering regens.
- **[major]** Non-household strategic actors fit no call class — the Laxmi Road saree price war exposes it. HouseholdPlan is schema'd around members/acts; Segment A embeds exactly one schema per call class; `scene` requires an interaction. A shop's adaptive pricing decision (observe rival, cut margin, run promotion) either gets shoehorned into an ill-fitting schema or silently hand-coded in clockwork — conceding the generality claim for the entire commerce/institution layer.
  - Fix: Key Segment A by (call_class, schema_name) and register an entity_plan schema variant under household_plan; A re-warms once per class-day so the cache splinter is negligible. Give shops/institutions C-slices and admit them to the same salience budget (rivalry = arc_activity). Add a clockwork convergence guard (cost-floor margins) so two temp-0.8 planners cannot undercut forever.
- **[major]** The attention-pan 'thaw' is load-bearing for feel but unbuilt (admitted in OQ-6). Panning to a non-focal ward shows frozen weekly templates plus canned one-line narration; the fidelity cliff between the Sonnet focal bubble and the clockwork city is the single biggest 'dead world / LLM slop' exposure, and at 3.5M the frozen fraction is 99.3%.
  - Fix: Promote thaw from open question to specified mechanism: on attention move, fire an interactive-priority k-packed micro wave perturbing the visible set (~50 households ≈ 3 calls ≈ $0.003, sub-5s latency), with the top few households getting expedited T1. Carve a small interactive-lane reserve for it in the governor so panning is never starved by background load.
- **[minor]** The QC judge line is booked at cheap-lane DeepSeek prices ($0.04/day at 50k) while the text routes it through Anthropic Batches — Sonnet-batched is roughly 10x the booked figure (~$0.4/day at 50k, $1+/day at full Pune). Small dollars, but the cost table and the architecture disagree.
  - Fix: Book the judge at the model actually used. Default to Haiku 4.5 via Batches ($0.5/$2.5 effective) or the local Qwen judge; either keeps the line under ~$0.1/day and makes the table honest.
- **[minor]** Devanagari output inflation budgeted at +15% is likely understated: dialogue dominates scene output and tokenizes 2-3x per word, so Marathi-heavy scenes plausibly run 1.5-2x the 700-token budget. Worse, max_output_tokens sized for English will truncate Devanagari dialogue mid-line, feeding truncated JSON into the repair lane and inflating the 4% repair-rate assumption.
  - Fix: Run the OQ-2 tokenizer probe in week 1. Until measured, pad both T2 output cost and max_output_tokens by +30% for mr-dominant registers and carry the delta explicitly in the §9 tables; keep the romanized-wire-format fallback as the pressure valve.
- **[minor]** Three static exemplar dialogues per register, cached in Segment A for every call of a class, is a mode-collapse engine over long runs — and the near-dup window (last 500 same-class outputs, ~6 hours at 50k volume) cannot see day-over-day template-itis, so the city converges on the exemplars' cadence and nobody notices.
  - Fix: Rotate exemplar sets on a weekly Segment A version bump (invalidation cost: one warm-up per class per day — negligible). Extend near-dup to a per-(ward, call_class) 7-day sampled table. Auto-feed judge-flagged cliches into the lexicon data file.
- **[minor]** Voice discontinuity on promotion and replay-epoch drift: a background character's canon dialogue history is terse DeepSeek temp-0.9 output, and when the user focuses them Sonnet reinvents the voice ('same person sounds different'). Separately, the reproducibility claim breaks across prompt-version bumps — request_id embeds segment hashes, so replays after an A:v7-to-v8 bump miss llm_results, re-bill, and diverge.
  - Fix: Include 3-5 verbatim recent dialogue lines plus the register tag as style anchors in the focal persona sheet. Document that determinism is scoped to a prompt-version epoch, and keep a version-map table so replay pins the segment versions that were live at the original tick.
- **[minor]** k=20 micro packing has all-or-nothing failure: one malformed array item fails schema validation for the whole call and burns ladder retries on 19 good items — and under the current design their content is regenerated wholesale, wasting tokens and mutating already-good rumor hops.
  - Fix: Echo per-item ids in the output schema, validate items positionally after json-repair, commit the valid subset, and regen only failed indices in the next packed call.
- **[minor]** Solo-dev surface area: queue + six-rung ladder + five QC gates + budget governor + off-peak scheduler + salience coupling is 6-8 weeks of plumbing before the sim says one interesting thing. The stack choices are genuinely boring and buildable (SQLite/asyncio/pydantic is right), but the design reads as ship-everything-at-once.
  - Fix: Ship in three cuts: (1) queue + ladder + lexicon + canon stage-1 + T3 streaming; (2) cache-segment discipline + ledger alerts + governor; (3) judge, near-dup, off-peak, thaw. Each cut runs standalone; per the design's own math, cut 1 with zero caching (h=0) merely doubles $2.64/day at 50k — still comfortably inside budget while the interesting parts get built.

### Novel holdout-scenario traces

CHOICE OF HOLDOUTS. The stray-dog attack and chain-snatching are near-isomorphic to the traced S1 bus crash (priority-1 scene burst + micro ripples + optional focal, ~$0.01-0.03) and add no new stress beyond the cross-household-C gloss noted below. The wada collapse is a composition of S1 (acute burst) + S3 (ambient Segment B) + S5 (displacement arcs) and hits only already-identified gaps. The two scenarios that attack this design's actual load-bearing assumptions are the METRO STATION OPENING (falsifies "attention is the budget unit" and the frozen-template economics) and the INTER-RELIGIOUS MARRIAGE (breaks the single-household segment model and turns the QC machinery against the content). Traced below.

TRACE A — NEW METRO STATION OPENS, COMMUTE PATTERNS SHIFT.
Day 0 mechanics that work: Event Engine fires the opening; Segment B of the station ward gets a version bump at the day boundary (one bump, cache-coherent per §6); clockwork's transport graph gains edges (T0, outside this subsystem); kiosk-economy scenes and property-broker rumors are ordinary scene/micro jobs. Then it breaks, four times:
(A1) SELECTION CANNOT SEE THE AFFECTED SET. The affected population is corridor-commuter-shaped, not ward-shaped: a Kothrud household whose earner rides the new line to Kharadi has zero event_exposure under a geographic salience term and no attention proximity. Under the stated formula (attention dist + event_exposure + arc_activity + staleness) these households gain salience only via staleness — a ~146-day rotation at 3.5M (875k/6,000 per day). Silent special-case #1: a commute-graph exposure term computed by clockwork, which no interface currently provides.
(A2) FROZEN TEMPLATES BECOME MASS CANON CONTRADICTIONS WITH NO DETECTION SURFACE. Templates encode acts (t, a=TRV, loc). Clockwork reroutes aggregates immediately, so for months hundreds of thousands of canon day-plans assert bus commutes the transport layer no longer runs. No LLM error ever fires: templates replay as T0 and the contradiction gate only inspects facts_asserted at generation time. A user interviewing any frozen commuter gets a T3 tail whose retrieved plan contradicts the world the clockwork is simulating. This is canon drift by omission, invisible to every QC mechanism in §7.
(A3) EVEN CORRECT SELECTION IS BUDGET-STARVED. ~350k affected households through B1=6,000/day monopolizes the entire T1 budget for two months while the attention bubble's quality collapses. The governor's only verbs are shrink-radius/drop-class/clockwork-only; it has no concept of a bounded one-off migration wave.
(A4) THE RIGHT FIX IS MOSTLY NON-LLM, AND NOBODY OWNS IT. Deterministic rewrite of TRV/loc acts through the routing engine costs $0 and is instant; LLM work is only texture (k-packed thaw, ~$20-40 one-time). But the design defines templates as frozen LLM outputs with no mutation owner, and §10's interfaces contain no bulk-migration contract. Verdict: the five call classes survive; the refresh economics and Principle 3 do not — structural change scales cost with affected population, not attention, and the design silently needs a structural-migration pathway it never names.

TRACE B — INTER-RELIGIOUS COUPLE MARRIES AGAINST BOTH FAMILIES.
What works: the arc lives in Canon with arc_activity keeping both households in the T1 budget at low duty cycle (the S5 pattern); the Special Marriage Act 30-day notice is clockwork institutional scheduling (the S6 pattern); focal interviews run on Sonnet, the one surface that will genuinely handle this well. Then it breaks, five times:
(B1) EVERY PIVOTAL SCENE IS CROSS-HOUSEHOLD, AND §6 DEFINES EXACTLY ONE C SLICE. The couple's decision, each family confrontation, the two-family meeting — participants span two households, plausibly two wards (two candidate B segments). Which C? Which B? Unspecified; the S1 trace glossed the identical hole. Whichever household wins the prefix, the other family is grounded only via tail retrieval — asymmetric grounding, elevated contradiction odds on the tail side, and the scene row's booked warm-C in_hit is simply wrong for this workload. Special-case #1: participant sheets / dyad slices.
(B2) SHARED ARC STATE IS DUPLICATED INTO TWO INDEPENDENTLY LLM-DIGESTED C SLICES. Nightly digests of each household compress the couple's shared facts (engagement status, secrecy state, dates) separately; over a months-long arc the two copies drift, and stage 1 checks facts_asserted against Canon keys — never C-slice text against C-slice text. Special-case #2: a relationship-scoped canon entity as the single source of shared state.
(B3) INSTRUCTION COLLISION IN THE CACHED PREFIX. Segment A: "never derive personality or speech from religion." The tail: a father whose objection IS religious. A cheap model at temp 0.9 resolves this either as sanitized soap-opera mush ("society won't accept us" — LLM slop precisely where realism was mandated) or by breaking the rule with caricature risk — whereupon the hard post-filter blocklist (§7e) flags legitimate religious speech, regens burn, and the canon-conflict path terminates at rung-6 "dialogue discarded". Net: the arc's most important scenes systematically degrade to outcome codes plus a canned line. The safety machinery, calibrated for background texture, suppresses exactly the foreground drama the mandate says to handle with care. Special-case #3: sensitivity mediated through per-character canon facts the prompt may voice, plus sensitive-scene routing to premium with 100% judge coverage.
(B4) THE COMMUNITY-REACTION LAYER RUNS IN THE QC-BLIND LANE. Gossip about the couple is micro_update: 40-token rumor mutations, temp 0.7, cheapest model, no lexicon gate, no near-dup, judge sampling specified only for "T1/T2 outputs". The highest caricature/communal-stereotype risk content gets the least QC in the system. Special-case #4.
(B5) PROVIDER REFUSAL RISK HAS NO PATH. A cheap-provider refusal or heavy sanitization on communal-conflict content presents as a parse failure, walks the ladder, and lands on rung 6 — systematic refusals silently turn the whole arc into clockwork with no detection or premium reroute. Special-case #5.
Verdict: the class taxonomy holds (plan/interact/propagate/converse/verify does cover it), but the segment architecture, the QC calibration, and the sensitivity rules all need the five special-cases above — the design as written would render this arc as either mush or statistics, which is the one outcome the brief forbade.