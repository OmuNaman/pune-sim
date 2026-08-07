"""Fit a block's household demography to its ward census, offline.

V0's household table was tuned by eye on 80 households, where nobody could tell
3.9 people per household from 4.1. V3's block claims to be a place with a
published census, so the table should answer to it.

This does the fitting *here*, once, and prints constants to paste into
`population/demography.py`. Synthesis itself never fits at runtime — it stays a
pure function of (seed, block), which is the D0 invariant the whole event log
rests on.

Fits ratios, not counts. The census's old-city unit is the Kasbavishrambaug
ward office: 13 wards, 43,138 households, larger than the four-peth extract, so
totals do not tile onto the block. Household size, sex share and the 0-6 share
are stable across those wards and do transfer.

    uv run python scripts/fit_synthesis.py
    uv run python scripts/fit_synthesis.py --office Bhavanipeth --households 6000
"""

import argparse
import csv
import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from punesim.population.demography import KASBA, Demography
from punesim.population.synth import synthesize
from punesim.world.block import load_for

CENSUS = "data/anchors/pune_ward_census_2011.csv"


def targets(office: str) -> dict:
    """Ratio marginals for one PMC ward office, from the pinned 2011 census."""
    with open(CENSUS, encoding="utf-8-sig") as fh:
        rows = list(csv.reader(fh))
    wards = [r for r in rows[4:] if r and r[0].strip().lower().startswith("ward no")]
    sel = [r for r in wards if len(r) > 20 and r[20].strip() == office]
    if not sel:
        raise SystemExit(f"no wards under ward office {office!r}")

    def total(col: int) -> int:
        out = 0
        for r in sel:
            s = (r[col] or "").replace(",", "").strip()
            try:
                out += int(float(s))
            except ValueError:
                pass
        return out

    hh, pop, male, kids = total(1), total(2), total(3), total(17)
    return {
        "office": office, "wards": len(sel), "households": hh, "people": pop,
        "household_size": pop / hh, "male_share": male / pop, "under_7_share": kids / pop,
    }


def measure(demo: Demography, block, n: int, seed: int) -> dict:
    hh, people = synthesize(seed, block, n_households=n, demo=demo)
    total = len(people)
    return {
        "household_size": total / len(hh),
        "male_share": sum(1 for p in people.values() if p.sex == "m") / total,
        "under_7_share": sum(1 for p in people.values() if p.age < 7) / total,
    }


KEYS = ("household_size", "male_share", "under_7_share")
# Scales that make one unit of error comparable across marginals: a tenth of a
# person per household matters about as much as a percentage point of sex share.
SCALE = {"household_size": 0.10, "male_share": 0.010, "under_7_share": 0.010}


def loss(got: dict, want: dict) -> float:
    return sum(((got[k] - want[k]) / SCALE[k]) ** 2 for k in KEYS)


def _weights(joint: float, pg: float, nokids: float, elder: float) -> tuple:
    """Template weights with nuclear_kids taking the remainder."""
    return (
        ("nuclear_kids", round(1.0 - joint - pg - nokids - elder, 4)),
        ("joint", joint), ("pg_students", pg),
        ("nuclear_nokids", nokids), ("elder_single", elder),
    )


def candidates(d: Demography):
    """One coordinate's worth of neighbours, as (label, Demography) pairs."""
    w = dict(d.templates)
    j, pg, nk, el = (w["joint"], w["pg_students"], w["nuclear_nokids"], w["elder_single"])
    for delta in (-0.02, 0.02):
        for label, vals in (
            ("joint", (j + delta, pg, nk, el)),
            ("pg_students", (j, pg + delta, nk, el)),
            ("nuclear_nokids", (j, pg, nk + delta, el)),
            ("elder_single", (j, pg, nk, el + delta)),
        ):
            # elder_single has a floor for the same reason: people do live alone.
            if min(vals) < 0.01 or sum(vals) > 0.85 or vals[3] < 0.06:
                continue
            yield f"{label}{delta:+.2f}", replace(d, templates=_weights(*vals))
    for delta in (-0.02, 0.02):
        # Constrained on purpose. Left free, the fitter drives PG rooms to 42%
        # male because that is the cheapest way to absorb the sex-ratio error —
        # in a student city that is not a finding, it is a knob being abused.
        p = round(d.p_male_pg + delta, 3)
        if 0.50 <= p <= 0.62:
            yield f"p_male_pg{delta:+.2f}", replace(d, p_male_pg=p)
    # Bigger steps for the two levers that actually move the sex ratio. At 0.02
    # a widow step trades a whole person out of a joint household for a sliver
    # of sex-ratio correction and never pays for itself, so the search left the
    # mechanism at zero and pushed the error onto the template mix instead.
    for delta in (-0.05, 0.05):
        w = round(d.p_grandparent_widowed + delta, 3)
        if 0.0 <= w <= 0.60:
            yield f"widowed{delta:+.2f}", replace(d, p_grandparent_widowed=w)
        pe = round(d.p_male_elder + delta, 3)
        if 0.20 <= pe <= 0.45:
            yield f"p_male_elder{delta:+.2f}", replace(d, p_male_elder=pe)
    for delta in (-1, 1):
        lo, hi = d.nuclear_kid_age
        if 2 <= lo + delta <= 8:
            yield f"nuclear_kid_age{delta:+d}", replace(d, nuclear_kid_age=(lo + delta, hi + delta))
        lo, hi = d.joint_kid_age
        if 3 <= lo + delta <= 9:
            yield f"joint_kid_age{delta:+d}", replace(d, joint_kid_age=(lo + delta, hi + delta))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--office", default="Kasbavishrambaug")
    ap.add_argument("--block", default="oldcity")
    ap.add_argument("--households", type=int, default=4000, help="sample size while fitting")
    ap.add_argument("--validate", type=int, default=12000, help="sample size for the final check")
    ap.add_argument("--seed", type=int, default=108)
    ap.add_argument("--rounds", type=int, default=40)
    args = ap.parse_args()

    want = targets(args.office)
    print(f"target: {want['office']} — {want['wards']} wards, {want['households']:,} households, "
          f"{want['people']:,} people")
    for k in KEYS:
        print(f"   {k:<16}{want[k]:.4f}")

    block = load_for(max(args.households, args.validate), args.block)
    best = KASBA
    best_loss = loss(measure(best, block, args.households, args.seed), want)
    print(f"\nstart (the V0 table): loss {best_loss:.2f}")

    for step in range(args.rounds):
        moved = None
        for label, cand in candidates(best):
            score = loss(measure(cand, block, args.households, args.seed), want)
            if score < best_loss - 1e-9:
                best, best_loss, moved = cand, score, label
        if moved is None:
            print(f"converged after {step} moves")
            break
        print(f"  {step:>2}. {moved:<22} loss {best_loss:.3f}")

    got = measure(best, block, args.validate, args.seed)
    print(f"\nvalidation at {args.validate:,} households on `{args.block}`:")
    print(f"{'':<18}{'fitted':>9}{'census':>9}{'gap':>9}")
    for k in KEYS:
        print(f"{k:<18}{got[k]:>9.4f}{want[k]:>9.4f}{got[k] - want[k]:>+9.4f}")

    print("\n--- paste into population/demography.py ---")
    for field, value in vars(best).items():
        print(f"    {field}={value!r},")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
