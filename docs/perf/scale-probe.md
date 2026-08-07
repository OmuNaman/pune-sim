# V3 step 0 — does the engine survive a peth?

*2026-08-07. Raw rows in `scale-probe-before.json`, `scale-probe-after.json`,
`scale-probe-oldcity.json`. Reproduce with:*

```
uv run python scripts/scale_probe.py --sizes 80,320,1280,2880 --days 4
uv run python scripts/scale_probe.py --sizes 3000,6000,12000 --days 4 --block oldcity
```

V0–V2 were built, soaked and gated at **80 households / 306 people**. V3's exit
is **4 real peths / 12k households** — about 47k people, roughly 150×. The
architecture's build order puts OSM ingest and IPF synthesis first. This probe
asked whether that order was right, or whether the engine would fall over before
the data arrived.

It would have. Three separate causes, all fixed, none of them requiring the data
work to move.

## Method

A ladder of household counts, each rung in its own process — peak RSS is
process-wide and monotonic, so one process per rung or the numbers lie. 4 sim
days, **zero LLM calls**. Day 1 carries a fixed injected hazard: on a quiet run
every holding set is empty, `_try_share` returns immediately, and the probe
reports a false green.

Two blocks. `kasba` is the V0–V2 pin (124 named places, 2,880 buildings) and is
where the before/after comparison lives. It is also a deliberate worst case:
holding the place count fixed while population grows 36× crowds every place far
past anything real. `oldcity` is V3's four-peth block (438 places, 7,008
buildings) and is where the V3 target is measured.

## Before

| households | people | s / sim-day | peak RSS | events/day | co-presence windows/day |
|---:|---:|---:|---:|---:|---:|
| 80 | 306 | 0.061 | 56 MB | 1,385 | 6,248 |
| 320 | 1,266 | 0.468 | 75 MB | 5,894 | 85,038 |
| 1,280 | 5,000 | 10.84 | 184 MB | 23,122 | 709,555 |
| 2,880 | 11,240 | 49.69 | 441 MB | 52,241 | 2,527,426 |

Segment slopes in population: time n^1.43 → n^2.29 → n^1.88; co-presence n^1.84
→ n^1.54 → n^1.57. Extrapolated to 47k people that is roughly **twelve minutes
per sim-day**, so a 30-day soak costs six hours before a single LLM call.

## What was actually wrong

**1. Co-presence enumerated every overlapping pair at a place.** The dominant
term, and the only one that was a modelling error rather than an oversight. The
giveaway is not the runtime — it is this:

| households | people | contacts/person/day, all-pairs | with the cap |
|---:|---:|---:|---:|
| 80 | 306 | 20.4 | 20.4 |
| 320 | 1,266 | 67.2 | 15.5 |
| 1,280 | 5,000 | 141.9 | 17.3 |
| 2,880 | 11,240 | **224.9** | **17.4** |
| 12,000 *(oldcity)* | 46,671 | — | **20.2** |

How many people you exchange news with in a day was a function of how big the
city is. A person in an 11k-person world was holding 225 information exchanges a
day and the number was still climbing. With the cap it is flat at ~17–20 across
a 150× population range — the same order as the 20.4 the 80-household soaks were
validated against. Contact rate should be bounded by a day's attention, not by
the size of the city you live in. A market with three thousand people through it
is not a room.

So: places below `CROWD_EXACT_SPANS` (128) stay exact — an 80-household day's
busiest place holds 92, so every soaked behaviour and the determinism hash are
untouched. Above it, each span keyed-samples `CONTACTS_IN_A_CROWD` (12) partners
from the people it overlaps.

The knock-on was the surprise: **rumours spread *more* after the cap** (9,047 →
11,687 hearings at 2,880 households). All-pairs made every crowd a perfect
mixing chamber, so tellers kept meeting people who already knew and
Maki-Thompson stifling killed claims by saturation. The cap is faster *and*
truer.

**2. Two places filtered a full log replay in Python to find one day's events** —
`_info_pass` and `run_simulation`'s institutions step — so a run's cost grew with
the square of its length. The bounds moved into SQL, which the `EventLog.events`
API already supported. Side effect: the test suite went from 43s to 20s.

**3. `daily_finance_tick` scanned the whole day's log inside its per-household
loop.** 1,280 households × 23k events is 29M comparisons a day to find the two
hospital discharges that mattered. Indexed by household.

**4. `witness_tiers` measured hazard→place distance once per span** instead of
once per place: 870k haversines in a 3-day probe when there are only ever 124
answers.

Three more fell out of the profile: `traits()` is a pure function of its key and
was rebuilding a Philox generator 56k times a day; `Block.nearest` recomputed a
full distance sweep on every errand; and a domestic worker sorted all 7,000
candidate client homes, though everyone in the same wada sees the same buildings
in the same order. All memoised — the block is immutable after load and traits
are documented as timeless.

## After — same block, same rungs

| households | people | s / sim-day | peak RSS | co-presence windows/day | speedup |
|---:|---:|---:|---:|---:|---:|
| 80 | 306 | 0.042 | 56 MB | 6,248 | 1.5× |
| 320 | 1,266 | 0.314 | 72 MB | 19,560 | 1.5× |
| 1,280 | 5,000 | 3.23 | 124 MB | 86,624 | 3.4× |
| 2,880 | 11,240 | **14.58** | 223 MB | **195,793** | **3.4×** |

Co-presence went from n^1.54–1.84 to **n^0.80–1.13** — linear, which was the
point.

## The V3 target — four peths, 12k households

| households | people | s / sim-day | peak RSS | events/day | co-presence/day |
|---:|---:|---:|---:|---:|---:|
| 3,000 | 11,703 | 16.4 | 222 MB | 54,472 | 199,724 |
| 6,000 | 23,269 | 29.2 | 375 MB | 105,765 | 429,562 |
| 12,000 | **46,671** | **73.2** | 712 MB | 214,282 | 941,680 |

Segment slopes: time n^0.84 → n^1.32, co-presence n^1.11 → n^1.13.

**V3's exit population runs at 73 seconds per sim-day with zero LLM calls** — a
30-day clockwork soak in 37 minutes, against the twelve-minutes-per-sim-day the
pre-fix engine was heading for. Population synthesises to 46,671 people; the census's
old-city ward office holds 178,484 across 13 wards, of which this four-peth
block is a part, so the block is a subset rather than a match — see
`population/demography.py` for the ratio marginals it *is* held to.

Note the two ladders disagree about the exponent — kasba still reads n^1.86 at
its top rung while oldcity reads n^1.32. That gap *is* the worst-case caveat
doing its job: 11,240 people in 124 places is a crowding artifact, not a city.
On the realistic block the residual superlinearity is mild, and what remains is
a large linear constant — which is what cohorts, already in V3's plan, exist to
cut. Not every one of 47k people needs full-fidelity per-person simulation every
day.

## Verdict

**The ingest work was not premature — go.** The engine's superlinearity was
local defects, not an architectural limit, and fixing them cost a day rather
than a redesign.

## Guardrails

- The determinism hash at 80 households is `f4d83a2c…` and was unchanged through
  every fix in this document. The crowd cap is the one deliberate behaviour
  change, and it cannot engage below 128 place-spans.
- `tests/test_scale_guard.py` pins that hash, pins the co-presence window count
  at a crowded size, and — the one that matters — asserts the 80-household
  block's busiest place stays under `CROWD_EXACT_SPANS`. That is the single
  silent way the hash could change later: a schedule tweak pushes a place over
  128, the cap engages at the soaked size, and nothing names the cause.
- Known property of the cap, deliberate: inside a crowded place it weakens law
  4's branch cleanliness, because the sampled partners depend on how many people
  are in the overlapping run. Any degree cap must depend on crowd size. It does
  not touch the sizes where branching has been validated.
