"""Does watching a household change what happens to it?

The architecture asks for this and nothing checked it: "monitor incident-rate
divergence between watched and unwatched populations; prompt-level bias
correction so watching a family doesn't turn their life into a soap opera."
Following someone IS the product, so an instrument that changes what it
measures is worse than no instrument at all.

The obvious way to check — split one run into the households that got scenes
and the ones that did not — cannot work, and `audit_run.probe_observer_effect`
now says so in its own docstring. Being on camera is not assigned at random:
`scene.reaction` fires *on* the notable events you would be counting, and the
morning gate scores households by how much is happening to them. The camera
chases the action, and no amount of care with the arithmetic separates that
from the camera causing it.

So: two runs, same seed, scenes on and scenes off. Assignment is by run rather
than by attention, so selection bias is gone by construction. The keyed
six-tuple RNG is what makes the pair legitimate — every draw is keyed by
(run_seed, domain, entity_id, day, purpose, index) rather than pulled from a
sequential stream, so the clockwork lane is draw-for-draw identical across the
pair and nothing shifts merely because a scene consumed a random number.
Whatever differs between the two logs was caused by the scene lane.

    uv run punesim run --days 30 --households 80 --seed 108 --db runs/obs-off/events.db
    uv run punesim run --days 30 --households 80 --seed 108 --scenes --k 5 \
        --db runs/obs-on/events.db
    uv run python scripts/observer_effect.py --on runs/obs-on/events.db \
        --off runs/obs-off/events.db

Two numbers matter and both are printed. The **divergence** says how much of
the world the scene lane moved at all — without it, "no effect on outcomes" may
only mean the scene lane did nothing, which is the same mistake as a probe
passing because there was nothing to look at. The **outcome deltas** say
whether what it moved was systematically misfortune.
"""

import argparse
import json
import math
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

# The downstream events a scene could plausibly push a household toward: the
# ones with a mechanical cost attached. `kernel/diff.py` calls these notable
# and it is the same list, deliberately — if a type is worth surfacing to a
# reader it is worth counting here.
OUTCOMES = ("pressure.crossed", "belief.action", "plan.avoided",
            "hospital.admitted", "hospital.discharged", "loan.taken",
            "money.paid", "police.fir.registered")

# Events the scene lane writes directly. Counted separately: they are the dose,
# not the response, and folding them into divergence would just measure how
# many scenes ran.
SCENE_WRITES = ("scene.morning", "scene.reaction", "scene.skipped",
                "scene.invalid_ref", "llm.response", "plan.revised",
                "plan.step_dropped", "fact.established", "conversation.held",
                "message.sent", "memory.formed", "mood.delta")


def binom_two_sided(k: int, n: int, p: float = 0.5) -> float:
    """Exact two-sided binomial p-value, by summing every outcome at most as
    likely as the observed one. No scipy in this repo and no reason to add it
    for one distribution.

    Conditioning on the total is the standard trick for comparing two counts:
    if the scene lane adds nothing, each of the n events is equally likely to
    have landed in either arm, so `k` is Binomial(n, 1/2)."""
    if n == 0:
        return 1.0
    obs = math.comb(n, k) * p ** k * (1 - p) ** (n - k)
    tol = obs * (1 + 1e-9)
    return min(1.0, sum(
        pk for i in range(n + 1)
        if (pk := math.comb(n, i) * p ** i * (1 - p) ** (n - i)) <= tol
    ))


def pair_problem(on: dict, off: dict) -> str | None:
    """Why these two logs are not a pair, or None if they are.

    A pair that does not share a seed, a block and a population is not a pair,
    and diffing it produces a confident number about nothing."""
    for key in ("seed", "households", "block", "days"):
        a, b = on["meta"].get(key), off["meta"].get(key)
        if a != b:
            return f"{key} is {a!r} with scenes and {b!r} without"
    if on["types"].get("scene.morning", 0) == 0:
        return "the --on log has no scenes in it"
    if any(off["types"].get(t) for t in ("scene.morning", "scene.reaction")):
        return "the --off log has scenes in it"
    return None


def read(db: Path) -> dict:
    """Everything this script needs from one log, in one pass over sqlite."""
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    meta_row = con.execute("select payload from event where type='run.meta'").fetchone()
    meta = json.loads(meta_row[0]) if meta_row else {}
    types: Counter = Counter()
    by_hh: dict[str, Counter] = defaultdict(Counter)
    person_hh_needed: set[str] = set()
    rows = []
    for type_, payload in con.execute("select type, payload from event"):
        types[type_] += 1
        if type_ in OUTCOMES:
            p = json.loads(payload)
            hid, pid = p.get("household"), p.get("person")
            if hid:
                by_hh[hid][type_] += 1
            elif pid:
                person_hh_needed.add(pid)
                rows.append((type_, pid))
    con.close()
    return {"meta": meta, "types": types, "by_hh": by_hh,
            "unresolved": rows, "total": sum(types.values())}


def resolve(arm: dict, hh_of: dict[str, str]) -> None:
    """Attach the person-keyed outcomes to their households.

    Done after loading rather than during, because the household of a person is
    a fact about the synthesized population, not about the log — and the
    population is a pure function of (seed, block), so it is the same object
    for both arms and only has to be built once."""
    for type_, pid in arm["unresolved"]:
        norm = pid if ":" in pid else f"person:{pid}"
        hid = hh_of.get(norm)
        if hid:
            arm["by_hh"][hid][type_] += 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--on", required=True, help="log of the run WITH scenes")
    ap.add_argument("--off", required=True, help="log of the run WITHOUT scenes")
    ap.add_argument("--alpha", type=float, default=0.01,
                    help="p below which a per-kind gap is called out")
    args = ap.parse_args()

    on, off = read(Path(args.on)), read(Path(args.off))

    problem = pair_problem(on, off)
    if problem:
        print(f"NOT A PAIR: {problem}.")
        return 2
    seed = on["meta"].get("seed")
    households = on["meta"].get("households")
    block_name = on["meta"].get("block", "kasba")
    days = on["meta"].get("days")

    from punesim.population import synthesize
    from punesim.world.block import load_for

    block = load_for(households, block_name)
    _hhs, people = synthesize(seed, block, n_households=households)
    hh_of = {pid: p.household_id for pid, p in people.items()}
    resolve(on, hh_of)
    resolve(off, hh_of)

    print(f"\n=== observer effect: {args.on} vs {args.off} ===")
    print(f"seed {seed} | block {block_name} | {households} households | "
          f"{len(people)} people | {days} days")
    print(f"{on['total']:,} events with scenes, {off['total']:,} without\n")

    # --- dose: how much did the scene lane move at all? ---------------------
    scene_ev = sum(on["types"].get(t, 0) for t in SCENE_WRITES)
    clock_on = {t: n for t, n in on["types"].items() if t not in SCENE_WRITES}
    clock_off = {t: n for t, n in off["types"].items() if t not in SCENE_WRITES}
    moved = sorted(
        ((t, clock_on.get(t, 0), clock_off.get(t, 0))
         for t in set(clock_on) | set(clock_off)
         if clock_on.get(t, 0) != clock_off.get(t, 0)),
        key=lambda r: -abs(r[1] - r[2]),
    )
    clock_total_on, clock_total_off = sum(clock_on.values()), sum(clock_off.values())
    drift = abs(clock_total_on - clock_total_off) / max(1, clock_total_off)
    print(f"-- what the scene lane touched --")
    print(f"{scene_ev:,} events written by the scene lane itself "
          f"({on['types'].get('scene.morning', 0)} morning scenes, "
          f"{on['types'].get('scene.reaction', 0)} reactions)")
    print(f"clockwork events: {clock_total_on:,} with scenes vs {clock_total_off:,} "
          f"without — {drift:+.2%}")
    if not moved:
        print("NOTHING downstream moved. Any 'no observer effect' below is vacuous:\n"
              "     the scene lane wrote prose and changed no part of the world.")
    for t, a, b in moved[:12]:
        print(f"    {t:<26} {a:>8,} with   {b:>8,} without   {a - b:+,}")

    # --- response: was what moved systematically misfortune? ----------------
    print(f"\n-- outcomes, same households across the pair --")
    print(f"{'kind':<24}{'with':>8}{'without':>9}{'delta':>8}{'p':>10}")
    called_out, any_outcomes = [], False
    for kind in OUTCOMES:
        a = sum(c.get(kind, 0) for c in on["by_hh"].values())
        b = sum(c.get(kind, 0) for c in off["by_hh"].values())
        if a == b == 0:
            continue
        any_outcomes = True
        p = binom_two_sided(a, a + b)
        print(f"{kind:<24}{a:>8,}{b:>9,}{a - b:>+8,}{p:>10.3f}")
        if p < args.alpha:
            called_out.append(f"{kind}: {a} with scenes vs {b} without (p={p:.4f})")

    total_on = sum(sum(c.get(k, 0) for k in OUTCOMES) for c in on["by_hh"].values())
    total_off = sum(sum(c.get(k, 0) for k in OUTCOMES) for c in off["by_hh"].values())
    p_all = binom_two_sided(total_on, total_on + total_off)
    print(f"{'ALL':<24}{total_on:>8,}{total_off:>9,}{total_on - total_off:>+8,}{p_all:>10.3f}")

    # Per household, so a single unlucky family cannot carry the aggregate.
    worse = better = same = 0
    for hid in {*on["by_hh"], *off["by_hh"]}:
        a = sum(on["by_hh"][hid].get(k, 0) for k in OUTCOMES)
        b = sum(off["by_hh"][hid].get(k, 0) for k in OUTCOMES)
        worse += a > b
        better += a < b
        same += a == b
    p_sign = binom_two_sided(worse, worse + better)
    print(f"\nper household: {worse} worse under the camera, {better} better, "
          f"{same} unchanged (sign test p={p_sign:.3f})")

    if not any_outcomes:
        print("\nNo outcome events in either arm — this pair proves nothing about\n"
              "the observer effect. Run it longer, or on a block where something happens.")
        return 0
    print()
    if called_out or p_all < args.alpha or p_sign < args.alpha:
        print("DIVERGENCE — the scene lane moves outcomes, not just prose:")
        for line in called_out:
            print(f"    {line}")
        if p_all < args.alpha:
            print(f"    all outcomes together: p={p_all:.4f}")
        if p_sign < args.alpha:
            print(f"    direction across households: p={p_sign:.4f}")
        return 1
    print(f"No detectable observer effect at alpha={args.alpha}.")
    if not moved:
        print("But see above: nothing downstream moved, so this is a null result\n"
              "about an instrument that was not doing anything.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
