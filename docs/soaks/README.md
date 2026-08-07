# Soak reports

Thirty sim-days is the V1 exit horizon, and each soak is kept here in full —
including the ones that failed, which are the useful ones.

| Run | Mechanical audit | Continuity on the followed family |
|---|---|---|
| [soak1](soak1-first-30-days.md) — the first 30 days | 10 FAIL | FAIL: 4 contradictions |
| [soak2](soak2-after-the-fixes.md) — after the fixes | **0 FAIL** | FAIL: 9 contradictions, all of two mechanisms |
| [soak3](soak3-audit.txt) — after the memory-time fix | 1 FAIL (network) | FAIL: 6 findings, all one cause |
| [soak4](soak4-everything-in.md) — everything in | **0 FAIL** | **PASS** — 0 canon contradictions (3 texture nits) |

The gate is two commands, and both are in the repo so any run can be judged the
same way:

```bash
uv run python scripts/audit_run.py       --db runs/<run>/events.db --seed 108 --follow hh:000
uv run python scripts/continuity_read.py --db runs/<run>/events.db --household hh:000 --seed 108
```

The `.txt` and `.json` files beside each report are the raw outputs those
commands produced, so the numbers in the prose can be checked rather than
trusted. The event logs themselves stay under `runs/` and out of git — they are
40 MB each and regenerable from the seed plus the cassette.

Both gates are reproducible: temperature 0 and cassette-backed, so the same run
judged twice gives the same verdict. The continuity reader verifies every canon
finding with an independent skeptic call before counting it, and the standing
control is that it must still FAIL soak1 — which it does, on the fire a family
watched at 14:10 and later called a night fire.
