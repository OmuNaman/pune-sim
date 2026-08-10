# Does watching a household change what happens to it?

*2026-08-10.*

The architecture asks for this and nothing checked it:

> monitor incident-rate divergence between watched and unwatched populations;
> prompt-level bias correction so watching a family doesn't turn their life into
> a soap opera.

It matters here more than in most simulations, because following someone **is**
the product. An instrument that changes what it measures is worse than no
instrument.

## The obvious check does not work

Split one run into the households that got scenes and the ones that did not,
compare their rates. It reads like the right experiment and it cannot be one.

Being on camera is not assigned at random. `scene.reaction` fires **on** the
notable events you would be counting — `minds/scene/run.py` builds a reaction
from `recent_notable_events`, and `kernel/diff.py` lists `hospital.admitted`,
`pressure.crossed`, `loan.taken` among them. So a household earns its way into
the watched group by having exactly the misfortune the watched column then
attributes to being watched. The morning gate compounds it: scoring households
by how much is happening to them is its whole job.

Watched and unwatched rates diverge here with the observer effect at **zero**.

Left as a threshold it would have been worse than useless. At V3 scale 14
households of 12,000 are on camera, so the unwatched rate predicts under one
event per kind in the watched group; two events against an expectation of 0.9 is
a "2× divergence", and six kinds means a WARN nearly every run — a false-alarm
generator on the one probe whose subject is trust.

So `audit_run.probe_observer_effect` is **INFO**: descriptive, never a verdict,
with the confound in its own docstring and the expected count printed beside
every ratio.

There is a second reason nobody caught this earlier. **Below V3 scale there is
no control group at all.** The scene budget reaches every household inside a
month: soak2 and soak3 are both *80 on camera, 0 off*. The probe can only ever
SKIP at the sizes this repo spent its first four soaks on.

## The instrument that does work

Two runs, same seed, scenes on and scenes off. `scripts/observer_effect.py`.

Assignment is by **run** rather than by attention, so selection bias is gone by
construction. The keyed six-tuple RNG is what makes the pair legitimate — every
draw is keyed by `(run_seed, domain, entity_id, day, purpose, index)` rather than
pulled from a sequential stream, so the clockwork lane is draw-for-draw
identical across the pair and nothing shifts merely because a scene consumed a
random number. Whatever differs between the two logs was caused by the scene
lane.

It reports the **dose** as well as the response. Without that, "no effect on
outcomes" may only mean the scene lane did nothing — the same mistake as a probe
passing because there was nothing to look at.

It also reports **power** before any verdict, because a null is worth only as
much as the split it could have caught.

## Run 1 — hazards only, no injection

30 sim-days, 80 households, 306 people, seed 108, kasba.

| | with scenes | without |
|---|---|---|
| events | 43,029 | 42,256 |
| scene-lane events | 909 (150 morning scenes) | 0 |
| clockwork events | 42,120 | 42,256 (**−0.32%**) |
| trips | 16,326 | 16,454 |
| `activity.start` | 8,539 | 8,423 |
| `info.heard` | 917 | 913 |

The dose is real: the camera moved 128 trips and swapped them for 116
activities-in-place, which is a scene revising a household's plans and keeping
somebody in.

The response could not be measured. **Two outcome events across both arms.**
No split of two, not even 2–0, clears α=0.01, so this pair could not have
detected an observer effect of any size.

That is not a defect in the method — it is what the model now is. Removing
`DEFAULT_ACTION` and dropping `outage` from `ONGOING_PREDICATES` (both
deliberate, both documented) cut belief-driven action to near zero on a run
where nothing is injected. A hazards-only month at 80 households simply does not
produce outcomes to count.

The differ says so rather than reporting the null, which is the whole point of
computing power first.

## Run 2 — with the 30-day soak scenario injected

*Running: `data/scenarios/soak_30d.json`, which lands a collision on day 5 and a
fraud rumour on day 12 — the scenario that produced 510 outcome events in
soak2. Results here when it lands.*

## What this cannot show

- **One block, one seed.** A pair is one sample of the scene lane's behaviour,
  not a distribution over them.
- **Power stays modest at 80 households** even with an injection: ~18 outcomes
  per arm resolves roughly a 2.5× shift and nothing subtler.
- **Diluted, not absent, at V3 scale.** With 14 of 12,000 households on camera,
  a real per-watched-household effect would be invisible in the aggregate. The
  honest V3-scale version compares watched households against *themselves* in
  the other arm, which the differ's per-household table already supports and
  which no run has yet been made to exercise.
- **The mechanism is not identified.** A confirmed divergence would say the
  scene lane moves outcomes; it would not say whether that is prose bias, plan
  revision, or the wage arithmetic downstream of both.
