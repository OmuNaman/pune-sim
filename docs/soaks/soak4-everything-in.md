# 30-day soak 4 — runs/soak4/events.db

```
uv run punesim run --days 30 --scenes --inject data/scenarios/soak_30d.json \
  --db runs/soak4/events.db --follow hh:000 --seed 108
```

The first run with everything: transport retry, absence-based work accounting,
the complete relative-time table, relief percepts, the gate ceiling, the
belief-action fix, street talk. 43,694 events, 151 scenes, **0 skipped**.
Determinism hash `9fe08159adbf8dd1…`.

## Verdict

| Criterion | soak1 | soak2 | soak3 | **soak4** |
|---|---|---|---|---|
| Mechanical audit | 10 FAIL | 0 FAIL | 1 FAIL (network) | **0 FAIL, exit 0** |
| Continuity findings | 4 | 9 | 6 | **1** |
| Distinct mechanisms behind them | 3 | 2 | 1 | **1, weak** |
| Scenes skipped | 3/161 | 0/233 | 45/233 | **0/151** |
| Cost / sim-day | $0.0025 | $0.0050 | $0.0044 | **$0.0029** |

**The V1 continuity exit is still not met.** It asks for *zero* contradictions
on a followed family across 30 days, and this run has one. But it is one, it is
minor in substance, and the judge's own citation for it points at the wrong
canon line — it faults a notebook left in a classroom and cites the power
restoration. Everything that made the earlier runs fail is gone.

## The measuring instrument was re-checked, not tuned

Two prompt rules were added to the judge after this run's first read: *canon
being silent is not a contradiction* (two ten-year-old siblings called "the
twins" is a reading, not an error) and *rounding an exact time as a person would
is not a contradiction* ("9:30" for 21:29). Both remove genuine false positives,
but a gate that gets easier is worth suspecting.

So the sharpened reader was pointed back at soak1, the known-bad run. It still
returns FAIL with the fire-at-night and the yesterday's-fire majors at full
severity. The instrument still cuts; the 6 → 1 drop is the simulation.

## What the audit says

Every FAIL from the first soak is zero, and the two probes added since are
clean:

| Probe | soak1 | soak4 |
|---|---|---|
| Memories carrying a relative time word | (not measured; 160 in soak2) | **0** |
| Belief-actions, and claims moving >25% of the block | 255-person load-shed avoidance | **5 actions, 0 mass events** |
| Verbatim memory / message duplications | 11 / 9 | 0 / 0 |
| Texts a person's later scene reproduced | 20 | 0 |
| References to people who do not exist | 7 | 0 |
| Rumour echoes | (unmeasurable — no lineage) | 0 of 1,443 |
| Households on camera at least once | 27 of 80 | **80 of 80** |
| Cross-household conversations | 0 in 30 days | **12 on 12 days, 12 distinct pairs** |
| Relief that reached people | 0 | **393 hearings** |

Remaining warnings, all heuristics or known-open items: ACTIVITY-VOCAB (12
scene-authored strings that look like an absence and are paid as work),
TEMPORAL-DRIFT 8 (was 35), ID-HONORIFIC 9, ID-REJECTED 2 (the canon gate
catching invented ids — working), INFO-WITNESS-HEARSAY 38 (opportunities, not
corruption), PROMPT-COVERAGE 1 (`run.meta`, correctly silent).

## What each soak actually taught

- **soak1** — three mechanisms: scenes reading their own output, bare ids
  inviting invented people, undated events drifting.
- **soak2** — two: memories written in relative time, and fresh incidents
  re-dramatized for days.
- **soak3** — one: place-scoped events reach nobody, so the world fixed the
  power and told no one.
- **soak4** — one, weak, and mis-cited.

Each round the failures collapsed into fewer and deeper causes. That
convergence, not any single number, is the result.
