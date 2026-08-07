# Soak reports

Thirty sim-days is the V1 exit horizon, and each soak is kept here in full —
including the ones that failed, which are the useful ones.

| Run | Mechanical audit | Continuity on the followed family |
|---|---|---|
| [soak1](soak1-first-30-days.md) — the first 30 days | 10 FAIL | FAIL: 4 contradictions |
| [soak2](soak2-after-the-fixes.md) — after the fixes | **0 FAIL** | FAIL: clean to day 11, one residual mechanism after |
| soak3 — after the memory-time fix | running | running |

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
