# The first soak of the V3 world

*2026-08-07. 30 sim days, 12,000 households, 49,578 people, the four-peth
`oldcity` block, zero LLM calls.*

```
uv run punesim run --days 30 --households 12000 --block oldcity --db runs/v3soak/events.db
uv run python scripts/audit_run.py --db runs/v3soak/events.db --seed 108 \
    --households 12000 --since-day 22 --until-day 29
```

Every slice in this project ends the same way: soak, audit, root-cause, fix.
V3's steps 0–2 had been verified only by 4-day probes and a test suite, so this
is the first time the new world had to hold together for a month. It is also a
named V3 exit in its own right — *"Old City breathing with zero LLM calls"*.

It breathes. It also had three defects that nothing smaller could have found,
and the reason is the same in all three cases: **they are invisible until the
population is large enough or the run is long enough**, and V0–V2 were neither.

## What only a long run could show

**A rumour that never died.** The audit found one claim — `cl:restored`, *"the
power is back"* — born on day 14 and still spreading on day 29, to 19,999
people, 40% of the city, a fortnight after the lights came on.

Freshness decayed as `exp(-(day - first_day)/tau)`, and `first_day` was the day
the *current teller* first heard it — not the day the claim was born. Every new
hearer restarted the clock. A claim stayed permanently fresh as long as it kept
finding people who hadn't heard it, and death came only from Maki-Thompson
saturation, which a large population outruns. At 306 people saturation lands in
about eight days and every V1 soak looked healthy. At 49,578 it does not.

Freshness now decays on the claim's own age. A fortnight-old restoration is
stale news to a man who heard it an hour ago.

**A day that got slower the longer the run went.** Days 0–8 ran at ~67 s/day and
days 8–13 at ~264. `scripts/day_cost.py` times each day separately and
attributes it to phases — the 4-day probe averages, which hides exactly this —
and it named `_apply_beliefs`, at 0.00s on early days and 9.14s of a 20.31s late
day. It re-read the whole day's event list once per believer, and `state.avoid`
only grows, because nobody ever stops avoiding a place. Indexing the day by
person once fixed it.

**An avoidance nobody had.** Chasing that, `state.avoid` turned out to hold 1,138
people shunning a pumping station. None of them were: their action was
`store_water`, and the engine recorded an avoidance for *every* belief action
regardless of which one it was. It changed no behaviour in this run only by
luck — none of the 1,138 ever had that place in their routine. This is the same
shape as the `outage` bug from V1.1, so the action vocabulary now has to say
which of its members mean "and so I stop going there", and a test fails if a new
one doesn't.

## What the audit could not do yet

The audit held the whole log in memory: ~1.1 kB per event with its payload
parsed, which is fine at 80 households and 7.6 GB for this run. It would have
found that out *after* the 39-minute soak. `load()` now bounds the window in SQL
and refuses an unwindowed load over 1.5M events with the window it suggests.

Windowing then produced its own lesson, and a sharp one. `run.meta` sits at
sim_time 0, so `--since-day 25` dropped it — and with it the roster check, and
with that the block. The audit silently fell back to `kasba`, regenerated 46,671
people for a log of 49,578, and **passed 19 probes against the wrong world**. A
log's description of itself is now never outside the window, and the header
prints the block it regenerated.

Two probes also had to learn about windows: `RUMOR-IMMORTAL` skips when the
window ends before the run does (a claim alive at the edge of a slice is
mid-life, not immortal), and `run.meta` no longer counts as a prompt-coverage
gap.

## The probe that had to be rewritten to say what it meant

`RUMOR-IMMORTAL` asked "did anyone hear this claim in the last three days of the
run". At 306 people that is a fair question. At 49,578 a healthy dying claim's
tail is four hearings a day against a peak of thousands, and the probe failed a
decay curve that is textbook: 64, 44, 22, 14, 10, 4, 7, 4.

The first instinct — loosen the threshold — was wrong, and provably: a
tail-as-share-of-peak test tuned to pass the fixed run also passed the *broken*
one, because a windowed audit cannot see a claim's peak and because the metric
never matched the mechanism anyway.

The mechanism is recruitment. A rumour dies when it stops finding people who
have not heard it, and that is exactly what the freshness bug broke. Measured
directly:

| | reach | first heard it 12+ days after it happened |
|---|---:|---:|
| "the power is back", **before** | 19,999 | **254** |
| "the power is back", after | 5,776 | 17 |
| "a tanker came", **before** | 20,502 | **257** |
| "a tanker came", after | 2,770 | 7 |

So the probe now counts late first-time hearers, and it separates the two runs
by more than an order of magnitude rather than by a tuned constant.

## Verdict

Audited in four windows across the 30 days: **0 FAIL**. Run the same script
against the pre-fix log and days 22–29 still fail, which is the only evidence
that the gate cuts at all.

That control log lived in `runs/`, which is gitignored, so it is gone. The
numbers it produced are the table above; to rebuild it, check out `e81af21` —
the commit before the freshness fix — and re-run the same 30 days.

The one standing WARN is `INFO-WITNESS-HEARSAY` — hearsay reaching someone who
saw the thing themselves. Its own comment says it counts opportunities rather
than corruption, because stickiness lives in `InfoState` and not in the log, so
that was checked directly rather than assumed: of 427 witnesses who later met a
differing hearsay version, those who went on to tell someone **retold their own
account 120 times and the hearsay version zero times**. The V1.1 rule holds at
49,578 people.

## Standing observations, not defects

- **Four hazards in 30 days** across 49,578 people. `hazards.CLASSES` gives each
  class an absolute daily probability — 0.10 for a road collision, 0.02 for a
  fire — so the whole four-peth city draws ~0.25 hazards a day, exactly what an
  80-household block drew. They are not per-capita and were tuned when the world
  held 306 people; at 49,578 they are ~160× too low, and the old city plainly
  sees more than one road accident a fortnight. Nothing is *broken* — the lane
  works, the percepts land, the rumours spread — but the rate currently says
  nothing about Pune. Per-capita NCRB calibration is its own step, and it wants
  doing before anyone reads a casualty count as a finding.
- ~~The viewer cannot open this run at all.~~ Fixed the same day: it read the
  whole log and precomputed every person's movement for the whole run, which is
  ~7.6 GB here. Movement is now built one day at a time and everything else is
  read on demand; `punesim serve --db runs/v3soak/events.db --households 12000
  --block oldcity` opens in 7.6s at 90 MB, and scrubbing within a cached day
  costs 0.09s.
