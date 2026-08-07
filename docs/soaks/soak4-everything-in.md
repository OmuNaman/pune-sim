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
| Continuity: canon contradictions | 4 | 9 | 6 | **0** |
| Distinct mechanisms behind them | 3 | 2 | 1 | **none left** |
| Scenes skipped | 3/161 | 0/233 | 45/233 | **0/151** |
| Cost / sim-day | $0.0025 | $0.0050 | $0.0044 | **$0.0029** |

**The V1 continuity exit is met.** Thirty sim-days, the Chavan family on camera
every one of them, thirty scenes, and **zero canon contradictions** — nothing in
the prose that the event log rules out. Three *texture* nits remain: a scene
disagreeing with another scene about something the log never recorded (a lost
notebook, a tiffin). Those are worth knowing and they are printed, but the log
is silent on them, so nothing has been overruled.

Two things make that claim worth anything rather than a number I tuned my way to.

## Why the PASS is worth anything

**First: every finding has to survive a skeptic.** By this run the simulation's
error rate had dropped below the first-pass reader's false-positive rate, which
makes an unverified count meaningless in both directions. Its three "canon
contradictions" on this run were: *"power went out 9:30, back at 12"* against a
canon of 21:29→23:56 (which is the same thing, said as a person says it); *"I
plugged the charger in at 12:30"* against power returning at 23:56 (not a
contradiction at all — its own reasoning said so); and *"it went out on the 15th
and came back at night"* against 21:29→23:56 on 15 Jan (correct on both counts).
Each finding now gets an independent call whose job is to REFUTE it, told to
refute when unsure. Only survivors count.

**Second: the gate still fails a bad run.** The verified reader was pointed back
at soak1 and returns **FAIL, exit 1**, with the fire-at-night contradiction
surviving verification — Madhura says the fire broke out at night when canon has
it at 14:10 and her among the witnesses. Same script, same prompt, both
reproducible (temperature 0, cassette-backed). A gate that passes everything is
not a gate; this one passes soak4 and fails soak1.

**What "canon contradiction" means here** is a choice I made mid-stream, and it
should be visible rather than buried: the V1 criterion says *canon*
contradictions, so the gate keys on prose the event log RULES OUT. Scene-to-scene
drift about things the log never recorded is reported separately as texture. A
character misremembering by a day which morning the notebook went missing is a
household being human; a family that never saw a fire they witnessed is a broken
world.

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
