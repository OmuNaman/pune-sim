# 30-day re-soak — runs/soak2/events.db

```
uv run punesim run --days 30 --scenes --inject data/scenarios/soak_30d.json \
  --db runs/soak2/events.db --follow hh:000 --seed 108
```

Seed 108, 80 households, 306 people, random hazards on, the Chavan family
(hh:000) pinned on camera for the whole month so the V1 continuity criterion is
actually testable — the first soak "followed" them only by an ascending-id
tie-break, and lost them on day 6.

43,515 events over 30 days, 233 scenes, 0 skipped. Determinism hash
`f0e339c2fbbbc688…`. Code as of commit `5ad30be`; commits after that are
additive and unit-tested but not exercised here (see *Caveats*).

## Verdict

| Criterion | First soak | This run |
|---|---|---|
| **Mechanical audit** (`scripts/audit_run.py`) | **10 FAIL**, 11 WARN | **0 FAIL**, 8 WARN — exit 0 |
| **Continuity on the followed family** (V1 exit) | **FAIL** — 4 contradictions | **FAIL** — clean through day 11, then a cluster on days 14-19 |
| Cost | $0.0025/sim-day | $0.0050/sim-day |
| Rumour lifecycle | pass | pass — nothing alive in the last 3 days, max reach under 90% |

**The mechanical gate passes and the narrative gate does not.** Every defect the
first soak's forensics identified is gone, and one that was hidden underneath
them is now visible.

## What the audit says, probe by probe

| Probe | First soak | This run |
|---|---|---|
| Verbatim memory duplications (consecutive days) | 11 | **0** |
| Verbatim message duplications | 9 | **0** |
| Texts a person's later scene reproduced | 20 | **0** |
| References to people who do not exist | 7 | **0** |
| Messages to an unnamed child outside the household | 4 | **0** |
| Financial re-crossings (3+) | 1 | **0** |
| Occupation classes with >50% financially crossed | 1 (domestic_worker, 13/13) | **0** |
| Ambulances dispatched to non-casualty hazards | 2 | **0** |
| Hazards nobody perceived | 1 of 5 | **0 of 5** |
| Scenes skipped | 3 of 161 (1.9%) | **0 of 233** |
| Rumour echoes (a story convincing its own teller) | 151 of 1,229 | **0 of 1,017** |
| Longest spotlight streak, unfollowed households | 15 days | **2 days** |
| Distinct households on camera, last 10 days | 5 | **41** |
| Households on camera at least once | 27 of 80 | **80 of 80** |

Remaining warnings, none of them failures:

- **ACTIVITY-VOCAB (51 of 694)** — scene-authored activity strings that look
  like an absence ("stays home, calls the school") but are not in
  `ABSENT_ACTIVITIES`, so the ledger pays for the day. The known-open item:
  absence should be detected by presence, not by string.
- **TEMPORAL-DRIFT (34)** — the same cluster the continuity read found, seen
  through a cruder lens.
- **SCENE-COPY-PASTE (5)** — a scene handing three or more members an identical
  memory. Lazy, not contradictory.
- **ID-REJECTED (9)** — the canon gate catching invented ids. Working as
  designed; nine attempts over a month, none committed.
- **ID-HONORIFIC (18)**, **INFO-WITNESS-HEARSAY (39)** — heuristics and
  opportunity counts, not defects.
- **TALK-COVERAGE (0)** — this run predates the street-talk lane.

## Continuity: what actually failed

Days 0-11 are clean. The judge's own words: *"consistent with the roster, no
misdated canon events, no identity, repeat, or state contradictions."* Nothing
resembling the first soak's failures — no invented colleague, no copied memory
blocks, no witnessed fire moved to nighttime.

Then days 14-19 cluster on **one mechanism**, and it is the one thing the fixes
did not cover: **a memory written in relative time stays in the digest and is
re-read as if still true.**

The power cut is canon at day 14, 21:29, witnessed by the family. The day-15
scene correctly says "last night" — and writes that phrase into a memory.
Day 16 reads the memory and says "kal raatri". So does day 17. So does day 18,
which goes further and narrates a restoration on the wrong evening. The same
mechanism re-lives a lost eraser and a mislaid geometry box across three
mornings.

The digest line carries the absolute date; the sentence inside it does not, and
the sentence is what gets copied. Dating the *container* was not enough.

**Fixed after this run** (commit following this report), mechanically rather
than by prompt:

1. `absolutize()` rewrites relative time to the day it means at the moment a
   memory is written — "last night" → "on Thu 15 Jan night", "kal raatri" →
   "Thu 15 Jan chya ratri". A memory that will be read for weeks may not
   contain a word that is true for one day.
2. The digest now carries **at most one memory from the last two days** per
   person. What just happened is already in RECENT EVENTS; stacking it in the
   digest as well is what made a household re-live one small incident three
   mornings running.

The audit now measures the mechanism directly: **160 of 520 memories in this
run carried a relative time word**, so 31% of everything the block remembered
was a dated falsehood waiting to be copied. After the fix a five-day live run
produced 39 memories and none relative.

Three further corrections came out of reviewing that fix rather than running it:
the repeat gate compared incoming text against *stored* text (which absolutize
had already rewritten), so a re-emitted sentence would slip through and be
committed pinned to the wrong day; the rewrite vocabulary is open while the
table is not, so `MEMORY-RELATIVE-TIME` now reports what the table missed; and
the digest keeps at most one memory from the last two days.

Neither fix is verified over 30 days yet. **soak3 is running** with all of it in
place — same protocol, `runs/soak3/events.db`. The two commands that decide it:

```bash
uv run python scripts/audit_run.py --db runs/soak3/events.db --seed 108 --follow hh:000
uv run python scripts/continuity_read.py --db runs/soak3/events.db --household hh:000 --seed 108
```

## Cost

$0.0050/sim-day, up from $0.0025 — the prompts carry more (dated lines, named
ids, witnessed facts, memory digest) and the followed family adds a scene a day
on top of k=5. Still two-and-a-half orders of magnitude under the $1/sim-day
exit bar.

## Caveats

- **The run tests commit `5ad30be`.** Later commits — canonical id
  normalization, one-line-per-thing witnessed facts, the curfew absence set, the
  street-talk lane, the log time-bounds — are additive and unit-tested but were
  not exercised over 30 days here.
- **The audit had two probe bugs, found by this run and fixed before the final
  numbers above.** `SPOTLIGHT-STREAK` flagged the followed household for doing
  exactly what `--follow` asks; `SELF-ECHO` flagged one scene handing five
  witnesses of the same power cut the same sentence. Both corrected; `run.meta`
  now records who was followed so the first can never recur.
- **Two of this run's seven continuity findings were the reader's fault, not
  the simulation's**, and are corrected in the script rather than argued away:
  it was feeding the judge scene-authored `message.sent` events as ground truth
  (one cited "canon" line was a scene's own output), and showing whole-run canon
  in every batch, so a day-19 scene was faulted against a day-26 event — which
  is not a contradiction, it is the future. The days 14-18 majors stand either
  way, so the verdict does not move.
- **The continuity judge is a model.** It cites canon for every finding and is
  told a false positive is worse than a miss, but it is not a proof. Its
  baseline read of the first soak independently reproduced the human auditor's
  findings and caught one they missed, which is the evidence for trusting it.
