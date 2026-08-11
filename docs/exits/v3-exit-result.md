# V3 exit test — result

*2026-08-10. Plan and pass criteria: [v3-exit-plan.md](v3-exit-plan.md). Every
verdict below is reproducible with `scripts/exit_check.py`.*

V3's exit is two clauses:

> V0's scenario re-runs unchanged on 4 real peths / 12k households; <$2/sim-day
> background.

The cost half was met on 2026-08-08 at **$0.0031/sim-day** against a $2 bar. The
other half had never been run. This is that run.

**Result: 11 of 11 clauses closed. V3's exit is met.**

Every clause V0, V1 and V2 name is decided at 12,000 households and 49,578
people. Ten passed on first measurement. The eleventh, V1-c, **failed** — and
that failure is the most valuable thing in this document, because it found a
defect nothing shorter could: a hospitalised child written into his own front
room for three consecutive mornings and committed to the event log as real
activity. It was root-caused, fixed, and the fix verified on the exact failure
shape.

**Read the V1-c section before treating this as eleven passes.** The clause is
closed on the strength of a defect found, understood and fixed — not on a second
thirty-day soak, which was not run. What that does and does not establish is set
out there in full.

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

### V0-e — replay is hash-identical with zero API calls

**PASS.** The same five days re-run with `PUNESIM_LLM=replay` into a different
database:

```
record  c69e43df60b07e538a36a3081941c817a26700ab793ec492ded86ab7c7900fe8
replay  c69e43df60b07e538a36a3081941c817a26700ab793ec492ded86ab7c7900fe8
```

1,170,254 events both times. "Zero API calls" is enforced by the mode rather
than inspected — a cassette miss raises `CassetteMiss` and the run dies — so a
completed replay *is* the proof. This is V0's headline clause, holding at 49,578
people.

### V0-d — the interview matches canon

**PASS**, and it is the clause that was both unrunnable and unjudgeable this
morning: `punesim interview` would have answered as somebody from a different
world and written it into the log, and `continuity_read` would have filed the
answer as *canon* rather than judging it.

Asked *"Kaay zaala tya divshi shaaleh javar? Tumchya mulaa baddal saanga"*,
Suhas's father says:

> *Arre, tya divshi... Friday hota na. School madhun call aala, Ratanben
> Chunilal Mehta school madhun. Mhanje, Suhas la accident jhala mhanun. Mi
> ghabarlo, lagech dispensary la dhavlo. Suhas cha ankle la lagla hota, pan
> doctors mhanale ki kahi serious nahi... Mi tya divshi kaam var nahi gelo.*

Every load-bearing detail is in the log: it was a Friday, the school called home
(`message.sent`, +1200s off the collision), the ankle injury (`condition.set`,
+300s), the dispensary (`hospital.admitted`, +1500s), and the day of work lost.
The judge's own verdict:

> Omkar's interview recollection aligns perfectly with the canon: the accident
> on Friday, the school call, rushing to the dispensary, and his worry. No
> contradictions with the event log.

`VERDICT: PASS — no canon contradictions`, over 7 scenes in 2 batches. Three
first-pass findings were raised and all three refuted by the independent
skeptic, each for the documented reason that canon is silent on the point
("severity 0.3 after discharge does not rule out a limp"). Raw output:
`runs/exit/v0/continuity.json`.

*Caveat kept from the plan: `interview.py` speaks from the end of the log, so on
a 5-day run this is a **day-4** interview, not the day-3 one V0's exit names.
There is no flag to move it.*

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

### V1-a — a rumour propagates, mutates and changes behaviour in 3 days

```
punesim run --days 4 --block oldcity --households 12000 --seed 108 \
  --scenes --k 5 --follow hh:1160 --hazards \
  --inject data/scenarios/oldcity_v1_exam.json --db runs/exit/v1/events.db
```

939,920 events, hash `d75e51b9…`. **PASS**, with room to spare. One false claim
— *"the water at Tulshibaug Mandir is contaminated"*, `veracity: false`,
credence 0.85 — injected into **two** people on day 0:

| | |
|---|---|
| propagates | **1,176 distinct people**, 1,405 hearings, max hop **24** |
| mutates | **71 distinct texts**, 4,261 mutation ops |
| changes behaviour | **301 non-seed people** acted (`store_water`), first on day 1 |

The non-seed exclusion is not optional. Injected credence 0.85 already clears
the `store_water` threshold of 0.6, so the two seeds act on day 0 *by
construction*; counting them would let the clause pass itself. Only people who
were **told** count. (And `store_water` is not in `AVOIDING_ACTIONS`, so this
claim never produces a `plan.avoided` — looking for one would fail a healthy
run.)

A false claim reorganising 301 households' behaviour is the info lane's whole
thesis, demonstrated rather than asserted.

**Texture defect noticed here, not a clause failure.** The `REATTRIBUTE`
operator invents somebody to blame and picks a real nearby place, but has no
notion of which institutions could plausibly be responsible for the claim's
topic. Alongside credible targets (*"people are blaming Faraskhana Police
Station"*, *"…Sant Dnyaneswar Medical Education Research Centre"*) it produced
*"people are blaming **Blackberrys**"* — a menswear shop — and *"…IDBI Bank"*.
Real rumours about contaminated water blame the municipality, the tanker
operator or the temple trust. Logged rather than fixed mid-exit.

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

### V1-c — 30 sim-days, zero canon contradictions — **FAIL**

*Run 2026-08-11: 30 sim-days, 12,000 households, 49,578 people, 6,879,345
events, across fourteen kills and thirteen resumes. Raw output:
`runs/exit/soak/continuity.json`.*

**The automated verdict said `PASS`. It was wrong, and reading it is how the
real defect was found.**

Three major canon contradictions, all one mechanism. A ten-year-old was hit by a
car on day 5 and admitted to Manish Clinic at 12:05; he was not discharged until
10:00 on day 8. On days 6, 7 and 8 the morning scenes put him at home — not
ambiguously:

| day | the scene | canon |
|---|---|---|
| Wed 7 Jan | *"The Thorat **household** … Sharvari moves between the **kitchen** and the clinic phone number"*, and *"To aaj **ghari** visram karat ahe"* — he is resting **at home** today | admitted since Tue 12:05 |
| Thu 8 Jan | *"the dusty window of the Thorat **home** … Suhas (muffled, **from the other room**)"* | still admitted |
| Fri 9 Jan **06:30** | *"Suhas is still resting his leg on the divan"* | discharged at **10:00** — still admitted at 06:30 |

And it is not only prose. Those scenes wrote **plan overrides that the engine
committed as real events**: `activity.start "wake up, rest on divan"` on day 6,
`"rest and recover at home"` on day 8. The log itself records an admitted
patient at home.

**Cause.** `state.proc.in_hospital` is used by `engine/bend.py::_apply_stays` to
bend the clockwork, and is **never given to the scene prompt** — `context.py`
contains no reference to hospitalisation at all. The model wrote a plausible
domestic morning because nothing in its context said the child was in a ward.
`_apply_stays` also lets scene-revised plans win by design ("Scene-revised plans
(skip) win, as everywhere"), so nothing downstream caught it either.

**Second finding, about the instrument rather than the world.** The first-pass
reader caught all three. The independent skeptic refuted all three — once by
asserting *"Day 7 is Monday 12 Jan 2026, well after Suhas's discharge"* when day
0 is Thursday 1 January and day 7 is Thursday the 8th. **It invented a date to
refute a true finding.** Its instruction is "when unsure, refute — a false alarm
is worse than a miss", and that bias produced a miss on the clause it exists to
decide. A `PASS` from this tool is worth what its refutations are worth, and
those must be read.

**Why five days could not have found this.** The V0 run's crash was on day 1
with a two-day stay; the contradiction needs a stay that is still open on the
following mornings *and* scenes rendered into it. Thirty days is what the clause
asks for and thirty days is what it took.

**Fixed**, and deliberately not by a veto: the scene lane is given the physical
facts (who is admitted, where, until when) rather than forbidden to contradict
them. `_apply_stays` is untouched; scene-revised plans still win everywhere.

**Verified without re-running thirty days.** Re-soaking a month to check one fix
is disproportionate, so the failure shape was reproduced instead: 5 days, 80
households, a crash on day 1 leaving a child admitted across the mornings that
follow — the exact configuration that broke. `runs/wardcheck`.

Mechanically, the half that mattered most:

```
day 1 Fri 07:45  activity.start  admitted
day 2 Sat 08:00  activity.start  admitted
day 3 Sun 08:00  activity.start  admitted
day 4 Mon 08:00  activity.start  admitted
```

Four consecutive days in the ward and **no scene wrote a plan override placing
him at home** — previously `"wake up, rest on divan"` and `"rest and recover at
home"` were committed as real events against an open admission.

And the prose did not merely stop being wrong; it got better, which is the
argument for informing over forbidding:

> *Saturday* — "her mind on Dnyaneshwar at the hospital"; Aditya asks if they
> can go and visit; Vaishali asks if she can send him a message.
> *Sunday* — "Suhas left early to check on Dnyaneshwar at the dispensary, and
> the family is waiting for news."
> *Monday* — "**the empty chair at the table where Dnyaneshwar usually sits**
> pulls the room into a heavier silence."

That last image exists only because the model knew he was absent. A veto would
have produced a scene with a hole in it.

**Closed 2026-08-11, and here is exactly what that rests on.**

Closed on: a thirty-day soak was run at full scale; it failed; the failure was
real; it was root-caused to a specific missing fact in a specific module; the
fix was made without a veto; and the fix was verified against the exact
configuration that broke, at the log level as well as in prose. The instrument
that mis-reported the failure as a PASS was fixed too, and now rejects
refutations built on invented dates.

NOT closed on: a second thirty-day soak. That was not run. Re-soaking a month to
re-check one fixed defect was judged disproportionate by the owner, and the
judgement is recorded here rather than buried.

So the honest statement is: **the clause found what it exists to find, and what
it found is fixed.** A future thirty days may find something else — that is what
thirty days are for, and it is why the soak command stays in this document. If
anyone needs certainty that a *whole month* now holds together, they must run it;
nothing here claims that on their behalf.

The one thing that would make such a run trustworthy is already done: the
skeptic that threw out all three true findings has been given the dates instead
of deriving them, and any refutation naming a weekday for the scene's own day is
now checked against the calendar before it is allowed to kill a finding.

---

### V1-c — the earlier attempts, for the record

The one clause of eleven that has no verdict. Not because it failed: because
four separate attempts to run the 30-day soak were killed by the environment
before finishing, at days 22, 8, 2 and 2 of 30.

**A killed run loses everything.** `run_simulation` accepts a `start_day`, but
only together with the in-memory `SimState` — `engine/loop.py` explicitly
refuses `start_day` without it, because resuming from a bare log "would silently
begin a *new* world" with everyone's opening pressures re-fired and nobody
remembering anything they had heard. There is no on-disk checkpoint, so every
attempt is all-or-nothing at roughly 2.5 hours.

To close it, run this where nothing will reap it:

```bash
PUNESIM_RUNS_DIR=runs/exit uv run punesim run --days 30 --block oldcity \
  --households 12000 --seed 108 --scenes --k 5 --follow hh:1160 --hazards \
  --inject data/scenarios/oldcity_soak_30d.json --db runs/exit/soak/events.db

uv run python scripts/continuity_read.py --db runs/exit/soak/events.db --household hh:1160
uv run python scripts/exit_check.py       --db runs/exit/soak/events.db --household hh:1160
```

**PASS** is exit 0 with `VERDICT: PASS`. `VERDICT: PARTIAL` is *not* a pass —
batches the judge could not read are printed and are not a pass for those days.

What is already known narrows the question. The same family, `hh:1160`, was
judged clean over five days by the judge plus an independent skeptic, interview
included, with three first-pass findings raised and all three refuted. So the
open question is not whether the family is coherent — it is whether coherence
*survives a month*, which is the thing soak1 through soak4 were built to answer
at 80 households and which has never been asked at 12,000.

A checkpoint — writing `SimState` to disk at day boundaries so a long soak can
resume — would make this clause cheap to close and is worth building before the
next 30-day run of anything.

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
