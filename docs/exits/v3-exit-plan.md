# V3 exit test — the plan

*2026-08-10. V3's exit is two clauses: "<$2/sim-day background" (met at
$0.0031, docs/architecture.md) and **"V0's scenario re-runs unchanged on 4 real
peths / 12k households"**, which had never been run. This is the spec for that
second half — what has to be fixed first, what the four scenario files become on
oldcity, the exact run sequence, and what decides each clause.*

*Produced by a five-agent read-only survey of the repo; every load-bearing claim
carries a file:line or a measurement. Results go in `v3-exit-result.md`.*

---


---

# V3 EXIT TEST — CONCRETE PLAN

**Verified myself** (ran read-only probes; citations below are from the files as they stand at `a8113ca`, working tree clean): place-id identity across blocks; person-identity divergence at oldcity/12000; `world_card` size; hazard realize-days; reaction/procedure timings and `caused_by` wiring; `continuity_read`/`audit_run`/`branch`/`interview`/`follow` block handling; `Injection.parse` non-validation; `json_extract` works on the payload BLOB; `p_financial` arithmetic for candidate households.
**Taken on trust** (Report D, not re-measured): full-log scan timings (18.6 s / 36.3 s), `request_id` divergence proving ~100% cassette miss on oldcity, `diff.py` per-Event byte cost, runtime injection-apply behaviour (nothing was run).
**Corrections I am carrying into the plan**: the briefing's "hardcoded place ids refer to nothing" is **false** — all four resolve identically on both blocks (measured: 0 of kasba's 124 named places missing from oldcity's 438, 0 with differing name/kind). Report A's "V2-b is COVERED / oldcity-ready" is **false** — see BLOCKER 3.

---

## 1. BLOCKERS

### B1 — `punesim interview` is hard-wired to kasba/80 **and writes into the log** — WRONG ANSWER + CONTAMINATION
`src/punesim/cli.py:124-125`
```python
block = Block.load()                 # -> data/anchors/kasba_places.geojson, max_homes=400, name="kasba"
_, people = synthesize(run_seed, block)   # -> n_households default 80  (population/synth.py:124)
```
No `--block` / `--households` option exists on the command (`cli.py:104-111`).
**How I know**: `Block.load`'s defaults are literal at `world/block.py:184-187`; `synthesize`'s default `n_households=80` at `population/synth.py:124`. I measured that the roster it would build is a *different* world: `person:000.2` is Dnyaneshwar Chavan **age 10, school RCM** on kasba and **age 11, school Rasta Peth Education Society** on oldcity. And `minds/interview.py:77-101` commits `conversation.held` + `memory.formed` into whatever db it was pointed at, `provenance="user"` — plus `cli.py:130` attaches the log to the Gateway, so an `llm.response` lands too. It is the only read-side command that mutates the log.
**Consequence**: clause V0-d cannot be executed at all, and attempting it corrupts the run log and moves its determinism hash.
**Fix**: add `--block`/`--households`, or better read them from the db it was already handed — `branch_mod.read_meta(log)` already exists (`branch.py:21-24`) and returns `{"seed","households","days","follow","block"?}`. Then `load_for(hh, meta.get("block","kasba"))` + `synthesize(seed, block, n_households=hh)`. Mirror `audit_run.py:1050-1057`'s refuse-on-mismatch.

### B2 — `punesim follow` same defect, read-only — WRONG ANSWER
`src/punesim/cli.py:155-156`, identical two lines. Prints a kasba person card over oldcity events; `place_name` (`cli.py:163-165`) falls through to raw ids for every oldcity-only place; every id above `person:079.x` prints "unknown person" — 49,272 of 49,578 residents unreachable. Same fix. Lower severity than B1 only because it does not write.

### B3 — `minds/compiler.py:97-107` `world_card` emits one line per person — V2-b CANNOT EXECUTE
**Measured, not estimated**:

| block | people | world_card |
|---|---|---|
| kasba@80 | 306 | 20,649 chars / 433 lines (~5.2k tok) |
| oldcity@12000 | 49,578 | **2,326,466 chars / 50,019 lines (~582k tok)** |

Consumed by `compile_injection` (`compiler.py:207`), i.e. `punesim compile` (`cli.py:263`), `punesim branch "<what-if>"` (`cli.py:334`), viewer `/api/compile`. A 2.3 MB user message is not a request any configured model will take.
**Do not work around it with `--households 80 --block oldcity`** — that is the third-population footgun (`load_for(80,"oldcity")` → `max_homes=max(400,80)=400`, a fourth world matching neither the pin nor the run), and it would ground the compiled ids against the wrong roster.
**Fix (minimal, kasba-byte-identical)**: keep the places section unconditionally — measured at **438 lines / 23,184 chars (~5.8k tok)** on oldcity, entirely affordable — and gate the people directory on a cap, e.g. `PEOPLE_CARD_MAX = 400`; above it emit one line saying the directory is omitted and that `participants` may be left empty or given as an exact id. At kasba's 306 people the card is byte-identical, so the V2 compile cassettes still hit and no hash moves. `_validate` (`compiler.py:125-128`) already checks participants against the full `people` dict and does not need them in the prompt.

### B4 — `scripts/continuity_read.py` never reads `run.meta` — WRONG ANSWER on the V1 exit instrument
`continuity_read.py:257-258` defaults `--households 80`, `--block kasba`; `:271` is `block = load_for(args.households, getattr(args, 'block', 'kasba'))`. There is **no `read_meta` call in the file**, despite `:258`'s help text saying "normally taken from run.meta". Verified by reading the whole file.
`hh:000` exists in both worlds (`f"hh:{i:03d}"` is a *minimum*-width format, `synth.py:145`), so a forgotten flag pair does not error — it assembles a kasba family as CANON, pulls oldcity scenes for the same id, and asks a premium judge to find contradictions. It will find many, all artifacts. This is the exact failure that cost a whole soak in `audit_run.py` (`docs/soaks/v3-scale-soak.md:60-66`), fixed there at `audit_run.py:1043-1057` and never ported here.
**Fix**: read `run.meta` from the db, take `seed`/`households`/`block` from it, and exit 2 on a mismatch with explicit flags — a direct transcription of `audit_run.py:1047-1057`.

### B5 — `continuity_read.py` cannot judge the interview at all — the CHECK for V0-d does not exist
Not a run-blocker; a *judgeability* blocker. `build_scenes` collects only `scene.morning`/`scene.reaction` (`:205`), so the interview answer is never judged; and `build_canon`'s skip-set (`:171-173`) does **not** exclude `conversation.held`, so the interview answer is fed to the judge **as canon**. Backwards for this clause.
**Fix (2 edits)**: add `"conversation.held"` to the `skip` set at `:171-173`; add a `conversation.held` branch to `build_scenes` (`:204-213`) that renders `question`/`answer` as one more judged block. Key on `conversation.held` with `payload.with == "journalist"` — `interview.answered` is a dead type (referenced at `viewer/server.py:233`, emitted nowhere).

### B6 — `continuity_read.py` opens the log read-WRITE — SIDE EFFECT
`:278` `EventLog(args.db)`; `kernel/log.py:78-80` connects read-write, sets `PRAGMA journal_mode=WAL`, and runs `executescript(_SCHEMA)` which contains `CREATE INDEX IF NOT EXISTS ev_branch_type` (`log.py:43`). On a fresh 12k log the index exists (it is created at run time), so this is benign *for this test* — but it means the tool cannot be pointed at an archived log safely. Contrast `audit_run.py:920,958` which use `file:{db}?mode=ro`. **Fix if convenient; not on the critical path.**

### Adjacent, real, but NOT on this test's path — do not rank as blockers
- `branch.py:101-106` calls `run_simulation(...)` with **no `block_name=`**, so it takes `DEFAULT_BLOCK` (`engine/loop.py:38`) and, because `loop.py:75-76` records `block` only when non-default, a branch of an oldcity run writes a `run.meta` that implicitly claims kasba. Verified by reading both. One-keyword fix: `block_name=block.name`. **The run sequence below uses neither `branch` nor `diff`.**
- `kernel/diff.py:89-90` `list(log_a.events())` + `list(log_b.events())`, no `TooBig` guard; called unconditionally at the end of `punesim branch` (`cli.py:360`). Merely fatal-at-scale, not wrong.
- `audit_run.py:1070` computes `n_days` over the *stitched* set, and `:983-988` re-attaches `run.meta` at day 0 to any window with `since_day` — so a windowed audit reports the full run length. This inflates the denominator in `COST` (`:808`), `SCENE-EVERY-DAY` (`:607`), `TALK-COVERAGE` (`:780`) and un-skips `SPOTLIGHT-STREAK` (`:358`). **Phase 1 runs unwindowed and dodges it entirely; Phase 2 must treat those four as known artifacts** — §4 gives an SQL replacement for COST.
- `audit_run.py:815` hardcodes the FAIL limit at `$1.00` (V1's bar) against V3's `$2` (`docs/architecture.md:274`). Stricter, therefore safe. **Do not "correct" it.**
- The gate *reason* never reaches the log — `state.gate_marks` is cleared unlogged at `loop.py:137`, and `scene.morning`'s payload is `{household, narration, transcript}` (`minds/scene/apply.py:52-56`). "A *money* scene fired" is therefore only ever provable as "a scene the day after a `p_financial` crossing". Stated in §4, not fixed.

### Regression gate after any fix
```powershell
uv run pytest tests/test_scale_guard.py -q      # SOAKED_HASH f4d83a2c… must not move (tests/test_scale_guard.py:28,47-53)
uv run pytest -q
```

---

## 2. PORTED SCENARIOS

Design rules applied, each backed by a measurement:

- **Place ids: keep all but one.** oldcity's bbox strictly contains kasba's (`scripts/fetch_osm_block.py:58` vs `:64`); I measured 0 kasba named places missing from oldcity and 0 with a differing name or kind. `place:node/3681735096` = RCM Gujarati High School, `3337848241` = Shaniwar Peth Police Chowki, `11430153883` = Nilkantheshwar, `10172994194` = Tulshibaug Mandir — identical on both. Only `soak_30d`'s mandal rumour moves, to a real mandal that exists only on oldcity.
- **Participants: re-ground only where a *relation* breaks.** The crash victim must actually attend the anchor school. Rumour seeds do not need proximity — `_seed_rumor` (`engine/info_pass.py:217-240`) seeds first-hearers from `inj.participants` with no distance term — so they only need to exist and be adults, which I verified they are.
- **The crash victim is chosen by four measured constraints**, not by taste: `occupation=="student"`, `work_id == place:node/3681735096`, `age==10`, household has ≥2 adults ≥18 (needed for the school-call `message.sent` at `reactions.py:86-107` and the FIR complainant `min(adults)` at `catalog.py:80-89`), **and** the household can actually cross `p_financial`. That last one is the plan's most important finding — see the note under `oldcity_soak_30d.json`.
- **Time stays 07:20.** `docs/architecture.md:193` says 08:10; the kasba file says 07:20. V3's exit is "*re-runs unchanged*", so the file wins and the doc drift is noted, not fixed.
- The top-level `"comment"` key is inert: `Injection.parse` (`engine/injection.py:17-27`) reads only `day/time/type/place/participants/severity/payload`. **Do not put comments inside `payload`** — `loop.py:162-167` splats `payload` straight into the committed event. **Do not `punesim compile --save` into these files** — `cli.py:275-283` rewrites with a fixed key set and would drop the comments.

### `data/scenarios/oldcity_school_bus_crash.json`
```json
[
  {
    "comment": "V0 exit scenario re-grounded for --block oldcity --households 12000 --seed 108. PLACE UNCHANGED: place:node/3681735096 is the same 'Ratanben Chunilal Mehta (RCM) Gujarati High School' on both blocks (measured: kasba's 124 named places are a strict subset of oldcity's 438; 0 missing, 0 with a differing name or kind). It is also far more central here: 1,162 of 7,008 homes take it as their nearest school. PARTICIPANT CHANGED: on oldcity person:000.2 is Dnyaneshwar Chavan, age 11, whose work_id is place:way/282102070 (Rasta Peth Education Society), 688 m away - the original id commits a school-gate collision at a school the victim never walks to, and its note would commit a false age. person:1160.3 = Suhas Thorat, 10, student, work_id == place:node/3681735096; household hh:1160 (Thorat, nuclear_kids, 5) has 2 adults so the school-call message.sent and the FIR complainant both have someone to land on, and p_financial 0.544 -> 0.761 on the hospital bill so the V2 chain can fire. TIME UNCHANGED at 07:20 (docs/architecture.md:193 says 08:10; V3's exit says 're-runs unchanged', so the file wins).",
    "day": 1,
    "time": "07:20",
    "type": "hazard.road.collision",
    "place": "place:node/3681735096",
    "participants": ["person:1160.3"],
    "severity": 0.6,
    "payload": {
      "mechanism": "school van clipped by a reversing truck at the school gate",
      "note": "V0 exam scenario: Suhas Thorat (10) injured on the morning school run"
    }
  }
]
```

### `data/scenarios/oldcity_v1_exam.json`
```json
[
  {
    "comment": "PLACE UNCHANGED: place:node/10172994194 is 'Tulshibaug Mandir' on both blocks, so the claim key cl:tulshibaug_water still matches the sentence minds/info.py:154-156 renders from block.get(subject).name. Its low catchment rank on oldcity is irrelevant - _seed_rumor (engine/info_pass.py:205-240) seeds from participants only, with no proximity term; the place is used solely as claim.subject. PARTICIPANTS UNCHANGED: on oldcity person:001.1 = Sharvari Sathe, 31, shopkeeper and person:002.3 = Meena Bafna, 34, tailor - different people from kasba (hh:002 is Bafna/Jain here, not Tamboli/Muslim) but both exist and are adults, which is all this injection requires. Credence 0.85 clears the water topic's store_water threshold of 0.6 (minds/info.py:43), so both seeds act on day 0 and the honest liveness test is a NON-seed hearer acting by day 3.",
    "day": 0,
    "time": "18:30",
    "type": "info.rumor",
    "place": "place:node/10172994194",
    "participants": ["person:001.1", "person:002.3"],
    "payload": {
      "credence": 0.85,
      "claim": {
        "key": "cl:tulshibaug_water",
        "subject": "place:node/10172994194",
        "predicate": "contaminated",
        "topics": ["water", "health"],
        "charge": 0.8,
        "specificity": 0.5,
        "veracity": "false",
        "valence": -0.7
      }
    }
  },
  {
    "comment": "Same re-grounding as oldcity_school_bus_crash.json: place kept, participant moved to the child who actually attends this school on oldcity.",
    "day": 1,
    "time": "07:20",
    "type": "hazard.road.collision",
    "place": "place:node/3681735096",
    "participants": ["person:1160.3"],
    "severity": 0.6
  }
]
```

### `data/scenarios/oldcity_soak_30d.json`
```json
[
  {
    "comment": "PLACE UNCHANGED: place:node/11430153883 is 'Nilkantheshwar' on both blocks. Unlike the rumour path this one IS proximity-driven (world/hazards.py:149-186, 220 m radius, +/-30 min presence) and it holds up: rank 3 of 61 temples on oldcity, 395 homes within 220 m, so a collision here is still 'in the dense residential core'. PARTICIPANT CHANGED, and this is the single most consequential edit in the port. The original person:002.2 exists on oldcity as Sanjay Bafna, 38, nurse - but hh:002 (Bafna, joint, 6) holds liquid 103,900 against monthly costs 35,700, giving p_financial 0.120. A 21,200 hospital bill leaves it at 0.120, so pressure.crossed NEVER FIRES and the V2 exit chain silently no-shows while every probe passes. Measured, not assumed. person:1160.3 (Suhas Thorat, 10, hh:1160, liquid 22,400 / costs 30,500 / p_financial 0.544) crosses: severity 0.55 gives stay=3 -> discharge day 8, bill 22,500, p_financial 0.544 -> 0.811, i.e. below P_THRESHOLD 0.6 before and above it after, which is what engine/pressure.py:60 requires. Keeping the same household as the V0 run also means one --follow serves V0-b, V1-c and V2-a.",
    "day": 5,
    "time": "11:40",
    "type": "hazard.road.collision",
    "place": "place:node/11430153883",
    "participants": ["person:1160.3"],
    "severity": 0.55
  },
  {
    "comment": "PLACE AND SUBJECT CHANGED TOGETHER - the only place-id edit in the whole port. cl:mandal_funds was pinned to a temple on kasba only because the kasba extract holds no mandal. oldcity holds a real one: place:way/666675662 = 'Prakash Navajawan Mandal', engine kind 'temple' (so nothing mechanical shifts), rank 2 of 61 by catchment, 334 homes within 220 m. place and claim.subject are the same node string in this file and MUST stay equal, or minds/info.py:154-156 renders a different place name than the event carries. TYPO HAZARD: place:node/3337848240 (one digit off 3337848241) is a real, different oldcity place, 'Samarth Police Station' - a single-character slip lands on a valid id and fails silently. PARTICIPANTS UNCHANGED: person:014.0 = Vaishali Kale, 22, student and person:035.3 = Gauri Joshi, 37, shopkeeper on oldcity; both exist and are adults. Noted drift: person:035.3 was a 19-year-old student on kasba, so the rumour now starts in a shop rather than a student network. Semantic, not structural - fraud maps to stop_patronage at threshold 0.7 (minds/info.py:47) and credence 0.8 clears it either way.",
    "day": 12,
    "time": "17:45",
    "type": "info.rumor",
    "place": "place:way/666675662",
    "payload": {
      "credence": 0.8,
      "claim": {
        "key": "cl:mandal_funds",
        "subject": "place:way/666675662",
        "predicate": "misappropriated",
        "topics": ["fraud"],
        "quantity": 200000,
        "unit": "rupees",
        "charge": 0.75,
        "specificity": 0.45,
        "veracity": "unknown",
        "valence": -0.6
      }
    },
    "participants": ["person:014.0", "person:035.3"]
  }
]
```

### `data/scenarios/oldcity_dm_test.json`
```json
[
  {
    "comment": "UNCHANGED in every field. place:node/3337848241 is 'Shaniwar Peth Police Chowki' on both blocks, and participants is already empty so there is nothing to re-ground - this is the one file that is literally identical. Deliberately peripheral on both: 0-home nearest-police catchment and 10 homes within 220 m on kasba, rank 10 of 11 and 111 homes within 220 m on oldcity, 757 m from the home centroid. Re-centring it would silently make the DM test a louder event than V0's. This file is the ARTIFACT of a compile, not the V2-b test itself: V2-b is decided by running punesim compile fresh (step 8 in the run sequence) and comparing its preview to this shape.",
    "day": 2,
    "time": "12:00",
    "type": "hazard.violence.attack",
    "place": "place:node/3337848241",
    "participants": [],
    "severity": 0.8,
    "payload": {}
  }
]
```

---

## 3. THE RUN SEQUENCE

PowerShell (the primary shell here; `&&` is unavailable, `$env:` is how you set a variable). All runs are `--seed 108 --households 12000 --block oldcity`. `punesim run` deletes its target db and `-wal`/`-shm` first (`cli.py:50-53`), so every run needs its own path.

Budget model: **62 s/sim-day** clockwork at 12k (`docs/soaks/v3-scale-soak.md:144`, clean-room; 76–92 s/day when the machine is shared). Scenes add ~8–10 LLM calls/day — derived from the recorded $0.0031/sim-day at 12k (`docs/architecture.md:336`) against deepseek-v4-flash's rate card (`audit_run.py:45`) — i.e. ~60–80 s/day of sequential latency. Call a scened sim-day **~130 s**.

Cassettes are effectively empty for oldcity (Report D: ~10 of 1,291 rows carry an oldcity-only place id, all sim day 0–1), so every scene call in step 2 and step 9 is a fresh recording.

### Phase 0 — fixes and regression (~3 min, $0)
```powershell
uv run punesim doctor
uv run pytest tests/test_scale_guard.py -q
uv run pytest -q
```
Apply B1–B5. Re-run both pytest lines. `SOAKED_HASH` must be unchanged.

### Phase 1 — the cheap clauses, fail-fast (~33 min, < $0.10)

**1. V0 record run — 5 days.** Days 0–4. Crash day 1; FIR day 2; discharge + bill day 3 (stay=2, computed from `keyed_rng(108,"hospital","person:1160.3",1,"stay")`); `pressure.crossed` day 3; money scene day 4. Sampled `hazard.fire.small` also lands on day 1 (see below).
```powershell
uv run punesim run --days 5 --block oldcity --households 12000 --seed 108 `
  --scenes --k 5 --follow hh:1160 --hazards `
  --inject data/scenarios/oldcity_school_bus_crash.json `
  --db runs/v3exit-v0/events.db
```
*~11 min, ~$0.02. Record the printed `determinism hash`.*

**2. V0-e replay — same 5 days, different db, no network.**
```powershell
$env:PUNESIM_LLM='replay'
uv run punesim run --days 5 --block oldcity --households 12000 --seed 108 `
  --scenes --k 5 --follow hh:1160 --hazards `
  --inject data/scenarios/oldcity_school_bus_crash.json `
  --db runs/v3exit-v0-replay/events.db
$env:PUNESIM_LLM='record'
```
*~6 min, $0. A replay miss is a hard `CassetteMiss` (`llm/gateway.py:194-197`), so "zero API calls" is enforced by the mode, not by inspection.*

**3. Audit run 1 — unwindowed.** 5 days at 12k ≈ 1.14M events (the reference soak runs 225k–241k/day), under `MAX_EVENTS_UNBOUNDED = 1_500_000` (`audit_run.py:906`). Unwindowed means `n_days` is correct and the four windowing artifacts do not apply.
```powershell
uv run python scripts/audit_run.py --db runs/v3exit-v0/events.db --seed 108 --households 12000 --details 20
```
*~1 min.*

**4. V0-a / V0-c / V1-b — SQL over the consequence cone.** See §4 for the three queries. *~10 s.*

**5. V0-b — collision reference in the family's scenes.** SQL in §4. *~5 s.*

**6. V0-d — the interview (must run AFTER step 2, it writes).**
```powershell
uv run punesim interview person:1160.0 "Kaay zaala tya divshi shaaleh javar? Tumchya mulaa baddal saanga." `
  --db runs/v3exit-v0/events.db --block oldcity --households 12000 --seed 108
```
*~15 s, < $0.01. Requires B1. `interview.py:42-43` sets `last_t = max(sim_time)`, so the answer is spoken from the END of the log — on a 5-day run this is a day-4 interview, not a day-3 one, and there is no flag to move it. Stated as a limit, not fixed.*

**7. V0-b / V0-d — continuity read on the crash family.**
```powershell
uv run python scripts/continuity_read.py --db runs/v3exit-v0/events.db --household hh:1160 `
  --block oldcity --households 12000 --seed 108 --out runs/v3exit-v0/continuity.json
```
*~2 min, ~$0.02. Pass the flags explicitly even after B4 — belt and braces. B5 is what makes this decide V0-d.*

**8. V2-b — free-text compile (needs B3).**
```powershell
uv run python -c "import pathlib; pathlib.Path('runs/v3exit-compile').mkdir(parents=True, exist_ok=True)"
uv run punesim compile "the city DM was killed in broad daylight near Shaniwar Peth Police Chowki on day 2 at noon" `
  --block oldcity --households 12000 --seed 108 --day 2 `
  --save runs/v3exit-compile/dm.json
```
*~15 s, < $0.01. `--save` to a scratch path, never into `data/scenarios/`.*

**9. V1-a — the rumour exam, 4 days.** Rumour day 0, +3 sim-days = day 3.
```powershell
uv run punesim run --days 4 --block oldcity --households 12000 --seed 108 `
  --scenes --k 5 --follow hh:1160 --hazards `
  --inject data/scenarios/oldcity_v1_exam.json `
  --db runs/v3exit-v1/events.db
uv run python scripts/audit_run.py --db runs/v3exit-v1/events.db --seed 108 --households 12000 --details 20
```
*~9 min + 1 min, ~$0.015.*

**10. V0-f — refusal probe (model-facing; no block involved).**
```powershell
uv run python scripts/refusal_probe.py
```
*~2 min, ~$0.02.*

### Phase 2 — the 30-day clause. **This alone is ~77 min and busts the 90-min budget; it is last on purpose.**

**11. The soak.**
```powershell
uv run punesim run --days 30 --block oldcity --households 12000 --seed 108 `
  --scenes --k 5 --follow hh:1160 --hazards `
  --inject data/scenarios/oldcity_soak_30d.json `
  --db runs/v3exit-soak/events.db
```
*~66 min (30 × ~130 s), ~$0.10. ~6.8M events, ~1.3 GB.*

**12. Five audit windows.** ≤6 days each: the worst 6-day window of the reference soak is 1,401,835 events (under the 1.5M cap); 7 days is 1,632,319 (over). The guard only fires on *unwindowed* loads (`audit_run.py:960`), so nothing will stop you exceeding it — keep to 6.
```powershell
foreach ($w in @(@(0,5),@(6,11),@(12,17),@(18,23),@(24,29))) {
  uv run python scripts/audit_run.py --db runs/v3exit-soak/events.db --seed 108 --households 12000 `
    --since-day $w[0] --until-day $w[1] --details 12
}
```
*~6 min. Ignore `COST`, `SCENE-EVERY-DAY`, `TALK-COVERAGE`, `SPOTLIGHT-STREAK` in these windows — `n_days` is inflated to 30 by the `run.meta` row re-attached at day 0 (`audit_run.py:983-988` feeding `:1070`). I confirmed the mechanism by reading; Report D confirmed it empirically (a days-27–29 window printed "| 30 days |").*

**13. V1-c — the continuity read.**
```powershell
uv run python scripts/continuity_read.py --db runs/v3exit-soak/events.db --household hh:1160 `
  --block oldcity --households 12000 --seed 108 --batch 6 --out runs/v3exit-soak/continuity.json
```
*~5 min (1 + 2B full-log scans, B ≈ 5), ~$0.05.*

**14. V1-d / V2-a — SQL.** §4. *~30 s.*

**Totals** — Phase 0+1 ≈ **36 min, < $0.10**. Phase 2 ≈ **77 min, ~$0.15**. Grand total ≈ **113 min**, above the 90-minute target by the 30-day run alone, exactly as the task anticipated.

---

## 4. PASS CRITERIA

`$DB` below is the run's db. All SQL is `sqlite3`-shaped; run it read-only, e.g. `uv run python -c "import sqlite3;con=sqlite3.connect('file:runs/v3exit-v0/events.db?mode=ro',uri=True);[print(r) for r in con.execute('''…''')]"`. I verified `json_extract` works on the payload BLOB (SQLite 3.50.4, `audit_run.py:923` relies on it).

### V0-a — consequences fire on schedule (`arch.md:193-194`)
**Command**
```sql
WITH inj AS (SELECT seq, sim_time FROM event
             WHERE branch_id=0 AND provenance='user' AND caused_by IS NULL AND type LIKE 'hazard.%')
SELECT e.type, e.sim_time - inj.sim_time AS dt
FROM event e JOIN inj ON e.caused_by = inj.seq WHERE e.branch_id=0 ORDER BY dt;
```
**Look for exactly these four deltas, in seconds** — I read the constants out of `engine/reactions.py`:

| child | dt | source |
|---|---|---|
| `condition.set` (kind=injury, stage=er) | **300** | `reactions.py:79` |
| `ambulance.dispatched` | **480** | `reactions.py:64` |
| `message.sent` (school calls home) | **1200** | `reactions.py:97` |
| `hospital.admitted` | **1500** | `reactions.py:74` |

Plus, off the *same* parent seq (procedures bind `caused_by = matched_event.seq`, `interpreter.py:89-93`):
`police.fir.registered` at `(inj_day+1)*86400 + 39600` (day+1, 11:00 — `catalog.py:96,107`); `fir.update` at `(inj_day+8)*86400 + 43200` (`catalog.py:97,110`).
And off the `hospital.admitted` seq: `hospital.discharged` at `(inj_day+stay)*86400 + 36000` (`catalog.py:29,48`).
**FAIL** if any of the four is absent or off by more than 0 s (they are arithmetic, not draws), or if the FIR is missing (severity 0.6 clears `FIR_SEVERITY_MIN = 0.4`, `catalog.py:11,65`).
*Existing tooling only proves these fired, never when — `probe_hazards` (`audit_run.py:469-500`) never reads `sim_time` deltas along the cone.*

### V0-b — the family's scenes reference it for days (`arch.md:194`)
**Command**
```sql
SELECT sim_time/86400 AS d, count(*) FROM event
WHERE branch_id=0 AND type IN ('scene.morning','scene.reaction')
  AND json_extract(payload,'$.household')='hh:1160'
  AND (cast(payload AS text) REGEXP '(?i)\b(accident|collision|apghat|apghaat)\b')
GROUP BY d;
```
(SQLite has no built-in `REGEXP`; run the same predicate in Python with `audit_run.TOPIC_RE["collision"]` — `audit_run.py:89` — over `narration || transcript`. The regex is already tuned for Marathi/Hindi.)
**PASS** = ≥1 hit on ≥2 distinct days in `d ∈ {1,2,3,4}`. **FAIL** = hits on ≤1 day.
Plus **step 7's `continuity_read.py` must exit 0** (no surviving canon contradictions). Note the asymmetry: continuity_read only catches a family that references it *wrongly*; a family that never mentions it scores a clean PASS. The SQL above is the liveness half, the read is the consistency half. Both required.

### V0-c — a gossip hop reaches neighbours (`arch.md:194-195`)
**Command**
```sql
WITH inj AS (SELECT seq FROM event WHERE branch_id=0 AND provenance='user' AND caused_by IS NULL AND type LIKE 'hazard.%'),
     ck AS (SELECT DISTINCT json_extract(payload,'$.claim_key') k FROM event
            WHERE branch_id=0 AND type='info.heard' AND caused_by IN (SELECT seq FROM inj))
SELECT json_extract(payload,'$.channel') ch,
       max(json_extract(payload,'$.claim.hop')) max_hop,
       count(DISTINCT json_extract(payload,'$.person')) people
FROM event WHERE branch_id=0 AND type='info.heard'
  AND json_extract(payload,'$.claim_key') IN (SELECT k FROM ck)
GROUP BY ch;
```
**PASS** = at least one row with `ch != 'witness'` (i.e. `f2f`, `phone`, or `household`), `max_hop >= 1`, and at least one such hearer whose household ≠ `hh:1160`. **FAIL** otherwise.
*Every existing INFO probe is a pathology test (`INFO-ECHO`, `RUMOR-IMMORTAL`, `RUMOR-SATURATION`); `audit_run.py:505` SKIPs with "no rumours in this run", which is a non-verdict, not a pass.*

### V0-d — the interview matches canon (`arch.md:195`)
**Command**: step 7, `scripts/continuity_read.py` **after B5 is applied**.
**PASS** = exit 0 with the `conversation.held` block visibly among the judged scenes and no surviving canon finding attributable to it. **FAIL** = a surviving `canon`-scoped finding citing the interview answer.
Key on `type='conversation.held' AND json_extract(payload,'$.with')='journalist'` — `interview.answered` is emitted nowhere.
**Honest limits stated up front**: (a) this is the one clause decided by a model judge, not a mechanical predicate — there is no mechanical check for "matches canon" and inventing a proxy would be worse than saying so; the two-pass judge + skeptic (`continuity_read.py:302-362`) is the mitigation. (b) It is a **day-4** interview, not a day-3 one (see step 6).

### V0-e — replay hash-identical, zero API calls (`arch.md:195-196`)
**Command**: compare the `determinism hash :` lines printed by steps 1 and 2 (`cli.py:77`).
**PASS** = byte-identical strings, and step 2 completed without a `CassetteMiss`. **FAIL** = any difference, or a `CassetteMiss` traceback.
There is no pinned oldcity hash — `SOAKED_HASH` (`tests/test_scale_guard.py:28`) is kasba-80-3-day only — so run-twice-compare *is* the operational form of this clause. `determinism_hash` (`kernel/log.py:190-205`) covers every field but `wall_meta` and `seq`, including the `llm.response` payloads, which replay reproduces byte-for-byte from the cassette.
**Ordering is load-bearing**: step 6 (interview) commits `conversation.held` + `memory.formed` + `llm.response` into `runs/v3exit-v0/events.db` and *moves the hash*. Compare hashes before running it.

### V0-f — refusal behaviour on identity-salient content (`arch.md:196-198`)
**Command**: step 10, plus `SCENE-SKIP-RATE` in step 3's audit.
**PASS** = `runs/refusal_probe.csv` shows 0 `refused` for the workhorse across the battery, and `SCENE-SKIP-RATE` is PASS (`audit_run.py:594-605`: FAIL above 10%, WARN above 1%). Block-independent; the probe takes `--models`, not `--block`.

### V1-a — rumour propagates, mutates, changes behaviour in 3 days (`arch.md:202-204`)
**Command** (on `runs/v3exit-v1/events.db`)
```sql
SELECT count(*) hearings,
       count(DISTINCT json_extract(payload,'$.person')) people,
       count(DISTINCT json_extract(payload,'$.claim.text')) variants,
       max(json_extract(payload,'$.claim.hop')) max_hop
FROM event WHERE branch_id=0 AND type='info.heard'
  AND json_extract(payload,'$.claim_key')='cl:tulshibaug_water' AND sim_time < 4*86400;

SELECT count(DISTINCT json_extract(payload,'$.person')) actors, min(sim_time)/86400 first_day
FROM event WHERE branch_id=0 AND type='belief.action'
  AND json_extract(payload,'$.claim_key')='cl:tulshibaug_water' AND sim_time < 4*86400
  AND json_extract(payload,'$.person') NOT IN ('person:001.1','person:002.3');
```
**PASS** = `people >= 5` (the 2 seeds + ≥3 reached), `variants > 1` **or** any `info.heard` with a non-empty `claim.ops` (mutation, `minds/info.py:217-235`), `max_hop >= 1`, and `actors >= 1` with `first_day <= 3`.
**The non-seed exclusion is not optional**: injected credence is 0.85 and the `water` topic's `store_water` threshold is 0.6 (`minds/info.py:43`), so both seeds fire a `belief.action` on day 0 by construction. Counting them would make the clause vacuous.
**Do not look for `plan.avoided`** — `store_water` is not in `AVOIDING_ACTIONS` (`minds/info.py:67`), so this claim never produces one.
*The existing probes fire on the opposite failure: `BELIEF-ACTION-SCALE` (`audit_run.py:612-640`) is WARN-only for a claim moving >25% of the block, and `audit_run.py:622` SKIPs with "nobody acted on a rumour in this run".*

### V1-b — a *random* hazard produces an un-injected ripple (`arch.md:203`)
**Command**
```sql
SELECT provenance, type, sim_time/86400 d, seq FROM event
WHERE branch_id=0 AND type LIKE 'hazard.%' ORDER BY seq;
SELECT count(*) FROM event WHERE branch_id=0 AND type='info.heard'
  AND caused_by = <the clockwork hazard's seq>;
```
**PASS** = ≥1 row with `provenance='clockwork'`, and its percept count > 0. **FAIL** = zero clockwork-provenanced hazards in a `--hazards` run.
**This is guaranteed at seed 108 and I verified it two independent ways.** The realize gate is `keyed_rng(seed,"hazard",cls,day,"realize").random() < p_per_day` (`world/hazards.py:106-107`) and depends on nothing but seed, class and day. Computed for seed 108: it opens on **days 1, 7, 9, 14** and nowhere else in 30. Cross-checked against the existing 12k oldcity soak (`runs/v3soak/events.db`, `run.meta` = `{block: oldcity, households: 12000, seed: 108, days: 30}`): its four hazards are `hazard.fire.small` **day 1**, `hazard.road.collision` day 7, `hazard.water.supply_cut` day 9, `hazard.power.outage` day 14 — an exact match, which also proves the day-1 fire clears `MIN_AUDIENCE = 3` on this block. So a 5-day run contains one.
*Residual uncertainty I will not paper over*: with `--scenes` on, day-1 morning-scene plan overrides feed `pre_intervals` (`loop.py:141-151`) before `sample_day`, so the fire's *venue* may differ from the clockwork soak's. The realize draw cannot move; only the venue and participants can.
**"Believable" has no mechanical check.** Saying so is the honest answer. It is adjudicable only if the rippled household happens to be `hh:1160`, in which case step 7's read covers it; otherwise it is not judged.

### V1-c — 30 days, zero canon contradictions (`arch.md:203-204`)
**Command**: step 13.
**PASS** = exit **0** and the header reads `VERDICT: PASS`. **FAIL** = exit 1, or `VERDICT: PARTIAL` (batches the judge could not read are printed at `continuity_read.py:369-373` and are explicitly *not* a pass for those days).
**Precondition, non-negotiable**: B4 must be applied, or `--block oldcity --households 12000` must be on the command line. Without both, `continuity_read.py:271` builds an 80-household kasba world, finds a *different* `hh:1160`, and returns a confident verdict in either direction.
Supporting, not deciding: `MEMORY-RELATIVE-TIME` (`audit_run.py:656`) and `ID-INVENTED-REF` (`:279`) must be PASS in step 12's windows. `TEMPORAL-DRIFT` (`:848`) is WARN-only and blind to time-of-day by its own docstring (`:818-821`) — it does not decide anything.

### V1-d / V3 cost — `<$1/sim-day` (V1) and `<$2/sim-day` (V3, `arch.md:274`)
**Do not use the windowed `COST` probe** — its `n_days` is inflated to 30 in every window, understating $/sim-day by up to 6×. Use SQL over the whole log:
```sql
SELECT sum(coalesce(json_extract(payload,'$.usage.cost'),0)) AS reported,
       count(*) AS calls,
       sum(CASE WHEN json_extract(payload,'$.usage') IS NULL THEN 1 ELSE 0 END) AS no_usage,
       (max(sim_time)/86400)+1 AS days
FROM event WHERE branch_id=0 AND type='llm.response';
```
**PASS** = `reported / days < 1.00`. `no_usage > 0` biases the total low and must be reported alongside (`audit_run.py:813-814` does the same). If `reported` is 0 (no provider cost), fall back to the pinned rate card at `audit_run.py:44-48`.
Phase 1's audits (steps 3 and 9) *are* unwindowed, so their `COST` probe is trustworthy and gives an early read.

### V2-a — crash → FIR + bill → p_financial → money scene (`arch.md:210-212`)
**Command** (on `runs/v3exit-soak/events.db`), four ordered lookups:
```sql
-- 1. FIR off the injection, next morning
SELECT seq, sim_time/86400 d, json_extract(payload,'$.victim'), json_extract(payload,'$.complainant')
FROM event WHERE branch_id=0 AND type='police.fir.registered';
-- 2. discharge with a bill, off the admission
SELECT sim_time/86400 d, json_extract(payload,'$.bill'), json_extract(payload,'$.household')
FROM event WHERE branch_id=0 AND type='hospital.discharged';
-- 3. the money actually moving
SELECT sim_time/86400 d, json_extract(payload,'$.amount') FROM event
WHERE branch_id=0 AND type='money.paid' AND json_extract(payload,'$.household')='hh:1160';
-- 4. the crossing, then a scene the next day
SELECT sim_time/86400 d, json_extract(payload,'$.person'), json_extract(payload,'$.value')
FROM event WHERE branch_id=0 AND type='pressure.crossed'
  AND json_extract(payload,'$.pressure')='p_financial'
  AND json_extract(payload,'$.person') IN ('person:1160.0','person:1160.1');
SELECT sim_time/86400 d FROM event WHERE branch_id=0 AND type='scene.morning'
  AND json_extract(payload,'$.household')='hh:1160';
```
**PASS** = FIR on day 6 with `victim = person:1160.3` and `complainant = person:1160.0`; `hospital.discharged` on day 8 with `bill > 0` and `household = hh:1160`; a `money.paid` for `hh:1160` on day 8; a `p_financial` crossing for a `hh:1160` adult on day 8 (predicted value ~0.81, from 0.544); a `scene.morning` for `hh:1160` on day 9.
**Two limits stated plainly.** (a) The gate *reason* is never logged — `state.gate_marks` is cleared unlogged at `loop.py:137` and `scene.morning`'s payload is `{household, narration, transcript}` (`minds/scene/apply.py:52-56`). "Money scene" is provable only as "a scene the day after a `p_financial` crossing", never as "a scene gated *because of* money". (b) "Weeks later" is not enforced anywhere, including in the existing unit test (`tests/test_procedures.py:131-143` asserts a crossing over 21 days, not a 14-day lag). The chain, in order, is what this decides.
**Precondition**: the `oldcity_soak_30d.json` participant re-grounding above. With the original `person:002.2` this clause silently no-shows — hh:002's `p_financial` is 0.120 before the bill and 0.120 after.

### V2-b — free-text injection compiles, zero new code (`arch.md:212`)
**Command**: step 8.
**PASS** = `punesim compile` prints a `compiled injection` preview and writes `runs/v3exit-compile/dm.json` with a `place` that resolves in oldcity and, if `participants` is non-empty, ids that exist in the 49,578-person roster (`_validate`, `compiler.py:110-138`). **FAIL** = a `CompileError` list, or a provider rejection on prompt size.
**Precondition**: B3. Unfixed, this command sends a 2,326,466-character user message.
"Zero new code" is structural, not a runtime assertion: the run path takes any scenario file uniformly (`cli.py:59-61`), which the ported `oldcity_dm_test.json` demonstrates.

---

## 5. WHAT THIS TEST CANNOT SHOW

1. **"V0's scenario re-runs *unchanged*" is not literally what gets tested.** Places carry over unchanged and I measured that. The victim does not: `person:000.2` is a different child on oldcity attending a different school 688 m from the crash site. The port is the honest reading of the exit, but it is a port, and the exit's own word is "unchanged."

2. **"Believable" (V1-b) and "matches canon" (V0-d) are model judgements, not measurements.** There is no mechanical check for either, and every proxy I could invent would be worse than the admission. Unless the sampled hazard happens to touch `hh:1160`, its ripple is *seen* but not *judged* by anything.

3. **One household, of 12,000.** V0-b, V1-c and V2-a are all decided on `hh:1160`. Nothing here says the other 11,999 households are coherent — and at `k=5` spotlight, ~14 of 12,000 are on camera on a given day (`docs/architecture.md:336`), so most of the city has no prose to be incoherent in. `SPOTLIGHT-COVERAGE` (`audit_run.py:394`) reports the breadth; it does not judge it.

4. **Nothing tests the branch/diff half of V2.** `punesim branch` writes a `run.meta` that implicitly claims kasba (`branch.py:101-106` omits `block_name`), and `punesim diff` materializes both logs whole (`kernel/diff.py:89-90`, no guard) — Report D put a 30-day 12k pair at ~9.7 GB before the auxiliary structures. The plan routes around both rather than fixing them, so "branch-lite works at V3 scale" remains unknown after this test passes.

5. **The 30-day audit is five slices, not a whole.** `MAX_EVENTS_UNBOUNDED` (`audit_run.py:906`) forbids a single pass, and four probes are unreliable in a window because `n_days` is inflated (`:1070` + `:983-988`). Cross-window pathologies — a rumour that dies in window 3 and revives in window 5 — are visible only through `claim_reach()`'s whole-run aggregate (`:909-944`) and nothing else.

6. **The cost figure is a *scened-spotlight* figure, not a ceiling.** The `<$2/sim-day` exit is already met at $0.0031 with 14 of 12,000 households on camera. This test does not probe `SCENE_GATE_MODE=all`, and says nothing about what the same world costs with the camera everywhere.

7. **A green run is not a determinism *pin*.** Step 2 proves this run replays to itself. There is no committed oldcity hash equivalent to `SOAKED_HASH`, so nothing stops the next commit from silently changing oldcity's behaviour. Pinning one would be a separate, deliberate act.

8. **Hazard rates are not calibrated to Pune.** `p_per_day` is absolute, not per-capita (`data/classdefs/hazards.json`), so the four-peth city draws the same ~0.25 hazards/day as an 80-household block — 1.84 per 1,000 people per year at 49,578. The exit tests that the ripple machinery works, never that the world's incident rate is plausible.

9. **Unmeasured wall-clock risk.** The 62 s/sim-day figure is clockwork-only and clean-room; the scene-latency term (~70 s/day) is derived from a cost figure, not timed. If provider latency runs long, Phase 2 could reach two hours. Phase 1 is ordered so that every clause except V1-c and V2-a has already returned a verdict before that risk is taken.

10. **Read-only session**: nothing above was applied and nothing was run. The plan prescribes; it does not execute.