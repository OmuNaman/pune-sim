# V3 exit test — result

*2026-08-10. Plan and pass criteria: [v3-exit-plan.md](v3-exit-plan.md). Every
verdict below is reproducible with `scripts/exit_check.py`.*

V3's exit is two clauses:

> V0's scenario re-runs unchanged on 4 real peths / 12k households; <$2/sim-day
> background.

The cost half was met on 2026-08-08 at **$0.0031/sim-day** against a $2 bar. The
other half had never been run. This is that run.

## What had to be fixed before it could run at all

A five-agent read-only survey of the repo found six blockers. Four were the same
bug wearing different clothes, and none of them raise.

**A tool rebuilt the population from its own defaults instead of the log's
`run.meta`.** `hh:000` and `person:001.1` exist in every world this repo can
synthesize, so pointing a tool at the wrong roster does not error — it prints a
different family's name over the right family's events.

| where | what it did |
|---|---|
| `punesim interview` | assembled an 80-household kasba roster for a 12k oldcity log, put a stranger's name on the answer, **and committed it back into the log** — the only read-side command that writes |
| `punesim follow` | "unknown person" for 49,272 of 49,578 residents |
| `scripts/continuity_read.py` | would have judged a kasba family's canon against oldcity's scenes — this is the instrument that decides V1's exit |
| `punesim branch` | ran on the right world and **recorded** the default one, because `run.meta` omits `block` when it is the default |

This is the failure that cost a soak once already (a windowed audit fell back to
kasba and regenerated 46,671 people for a log of 49,578, then passed nineteen
probes against a world nobody had run). It was fixed in `audit_run.py` and never
ported. It is one module now — `src/punesim/world/roster.py` — and the rule is
that only what the caller **explicitly asked for** is worth refusing over; a
command's own default that disagrees with the log is the log's business.

**`world_card` emitted one line per person.** 20,649 characters at kasba's 306
people; **2,326,466** at oldcity's 49,578. A 2.3 MB user message, so `punesim
compile` could not run at V3 scale and V2's exit clause was unreachable. The
people directory is capped at 400 and the places half kept whole — 438 named
places, 23,184 characters, and it is the half that grounds a location. Kasba's
card is byte-identical below the cap, verified rather than assumed, so the
compile cassettes still hit and no hash moved.

**`continuity_read` fed the interview answer to the judge as canon** — so the
check for "the day-3 interview matches canon" was treating the interview as the
truth it was supposed to be measured against.

Left alone deliberately: `EventLog` opens read-write, so `continuity_read`
cannot be pointed at an archived log safely. Benign here, and the kernel is
law-1 territory.

## What the port changed, and the one change that mattered

Places barely moved. kasba's 124 named places are a **strict subset** of
oldcity's 438 — 0 missing, 0 with a differing name or kind — so the school, the
temple and the police chowki are literally the same nodes. Exactly one place id
changed, because kasba's extract holds no mandal at all and `cl:mandal_funds`
had been pinned to a temple; oldcity has a real Prakash Navajawan Mandal.

People moved, and one of those moves is the whole point.

- The V0 crash victim had to change because a **relation** broke, not an id.
  `person:000.2` is a different child on oldcity whose school is 688 m from the
  crash site, so the original commits a school-gate collision at a gate the
  victim never walks through.
- The 30-day soak's victim had to change or the run would have tested nothing.
  `hh:002` on oldcity holds ₹103,900 liquid against ₹30,500 monthly costs, so
  `p_financial` is **0.120 before a hospital bill and 0.164 after** — below the
  0.6 threshold both times. `pressure.crossed` never fires, V2's exit chain
  silently stops one link short, and every probe passes. Confirmed
  independently by running `exit_check.py` against a kasba log with the original
  participant: FIR ✓, discharge with a ₹21,200 bill ✓, `money.paid` ✓,
  `p_financial` **none**.

Both now use `person:1160.3` — Suhas Thorat, 10, who actually attends the anchor
school, whose household has the two adults the school-call and the FIR
complainant need, and whose `p_financial` goes **0.544 → 0.761** on the bill.

*(A static day-0 calculation of that figure gives 0.721, and I used it to
"correct" the plan's 0.761 before the run. The plan was right and the shortcut
was wrong: by discharge day the household has also spent three days of costs, so
the arithmetic has to be run forward rather than evaluated at time zero. The
direction — crosses vs does not cross — was never in doubt either way.)*

## The clauses

### The V0 run

```
punesim run --days 5 --block oldcity --households 12000 --seed 108 \
  --scenes --k 5 --follow hh:1160 --hazards \
  --inject data/scenarios/oldcity_school_bus_crash.json --db runs/exit/v0/events.db
```

**1,170,254 events over 5 sim-days. 49,578 people. Hash `c69e43df…`.**
`scripts/exit_check.py --db runs/exit/v0/events.db --household hh:1160` →
**6 pass, 0 fail, 3 not decidable here.**

| clause | verdict | evidence |
|---|---|---|
| **V0-a** consequences fire on schedule | PASS | off the injected collision: `condition.set` +300s, `ambulance.dispatched` +480s, `message.sent` +1200s (the school calls home), `hospital.admitted` +1500s, `police.fir.registered` +99,600s. Exact to the second — these are arithmetic in `engine/reactions.py`, not draws, so anything else is a regression |
| **V0-b** the family's scenes reference it for days | PASS | `hh:1160`'s scenes mention the collision on days 1, 2 **and** 3 |
| **V0-c** a gossip hop reaches neighbours | PASS | 25,561 f2f hearings reaching 19,604 people, max hop 26; 2,677 household hearings; **28,229 hearings by someone outside `hh:1160`** on a non-witness channel |
| **V1-b** a random hazard produces an un-injected ripple | PASS | a clockwork `hazard.fire.small` on day 1, 563 percepts — the plan computed days 1/7/9/14 for seed 108 from the realize gate before the run, and day 1 is where it landed |
| **V1-d** cost | PASS | **$0.0042/sim-day**, 55 calls, against $1 (V1) and $2 (V3) |
| **V2-a** crash → FIR + bill → p_financial → a scene after | PASS | FIR day 2, victim `person:1160.3`, complainant `person:1160.0`; discharge day 3 with a **₹21,800** bill; `money.paid` day 3; **both** adults cross `p_financial` at **0.761** on day 3; scenes follow on day 4 |
| **V1-a** rumour propagates/mutates/acts | SKIP | no rumour in this run — decided on the `v1_exam` run |
| **V0-d, V0-f, V1-c** | UNJUDGED | model judgements; see below |

The V2-a row is the one worth pausing on. It is the clause that would have
silently no-showed with the original participant, and the difference between the
two is a household's bank balance.

### The standing audit on the same run

`scripts/audit_run.py --db runs/exit/v0/events.db --seed 108 --households 12000`
— unwindowed, so `n_days` is trustworthy and none of the windowing caveats
apply.

**31 probes: 23 pass, 5 warn, 0 fail, 2 skip, 1 report-only.**

The five warns are all known classes: `ACTIVITY-VOCAB` (free-text activities a
whitelist cannot match), `ID-HONORIFIC` (a minor addressed as *tai*/*kaka*),
`ID-REJECTED` (one invented "bank helpline"), `INFO-WITNESS-HEARSAY` (counts
opportunities, not corruption) and one `TEMPORAL-DRIFT`.

The report-only `OBSERVER-EFFECT` table is worth reading as an object lesson and
is written up in [../soaks/observer-effect.md](../soaks/observer-effect.md): it
shows a **1,244× ratio** on this entirely healthy run, which is what selection
bias looks like when the camera is pointed at the households where things
happen.

### V0-f — refusal behaviour on identity-salient content

`uv run python scripts/refusal_probe.py` — **PASS**. Twenty tier-1-style briefs
(intercaste disclosure, conversion rumour, dargah visit gossip, riot aftermath
interview, dogwhistle speech rehearsal, peace committee meeting, caste service
refusal…) at both configured models: **0 refused, 0 rerouted, 0 schema
failures**, one repaired. Detail in `runs/refusal_probe.csv`.

This clause is block-independent — the probe takes `--models`, not `--block` —
so it is the one part of V3's exit that was never in doubt. Worth running anyway:
it is the earliest test of the design's riskiest external dependency, and the
answer is only true of today's models.

### V2-b — free-text injection compiles, zero new code

**PASS, with a defect found and fixed on the way.** `punesim compile` could not
run at V3 scale at all until `world_card` was capped (2.3 MB user message). Once
it could, the first thing it did was ground the DM assassination to
`place:node/3337848242` — a real id, a different building 200 m away — while
writing in its own notes that this was Shaniwar Peth Police Chowki, which is
`…241`. `_validate` checked only that the id existed. It now cross-checks the id
against the operator's own words, and the existing repair round corrects it: the
recompile lands on `place:node/3337848241`, which is exactly what the
hand-written scenario file uses.

"Zero new code" holds structurally: the run path takes any scenario file
uniformly, which the ported `oldcity_dm_test.json` demonstrates.

## What this test cannot show

Carried from the plan, because a green summary must not imply more than it
proves.

1. **"Re-runs *unchanged*" is not literally what is tested.** Places carry over
   unchanged and that is measured. The victim does not. The port is the honest
   reading of the exit, but it is a port, and the exit's own word is "unchanged".
2. **"Believable" and "matches canon" are judgements, not measurements.**
   `exit_check.py` reports them UNJUDGED and hands them to `continuity_read`'s
   judge-plus-skeptic. Every mechanical proxy considered was worse than the
   admission.
3. **One household of 12,000.** V0-b, V1-c and V2-a are all decided on
   `hh:1160`. Nothing here says the other 11,999 are coherent — and at `k=5`,
   ~14 of 12,000 are on camera on a given day, so most of the city has no prose
   to be incoherent in.
4. **Nothing tests the branch/diff half of V2 at scale.** `punesim diff`
   materializes both logs whole; a 30-day 12k pair is several GB. The `branch`
   metadata bug above was fixed, but "branch-lite works at V3 scale" remains
   unknown.
5. **The 30-day audit is five slices, not a whole.** `MAX_EVENTS_UNBOUNDED`
   forbids a single pass, and four probes are unreliable in a window because
   `n_days` is inflated by the `run.meta` row re-attached at day 0. Cross-window
   pathologies are visible only through `claim_reach()`'s whole-run aggregate.
6. **A green run is not a determinism pin.** The replay clause proves this run
   replays to itself. There is no committed oldcity hash equivalent to
   `SOAKED_HASH`, so nothing stops the next commit from silently changing
   oldcity's behaviour. Pinning one would be a separate, deliberate act.
7. **Hazard rates are still not calibrated to Pune** — absolute rather than
   per-capita, so the four-peth city draws the same ~0.25 hazards/day an
   80-household block did. The exit tests that the ripple machinery works, never
   that the incident rate is plausible.
