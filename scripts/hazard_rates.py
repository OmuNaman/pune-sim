"""Derive hazard rates from the vendored anchors, and check the shipped file.

    uv run python scripts/hazard_rates.py            # show the derivation
    uv run python scripts/hazard_rates.py --city Delhi

`data/classdefs/hazards.json` carries numbers. This is where they come from, so
that a rate can be re-derived rather than believed — the same relationship
`scripts/fetch_osm_block.py` has with the geojson it builds.

Only ONE of the four classes has a source. That is not a gap this script papers
over: it prints what each class is anchored to, and `provenance` in the shipped
file says the same thing in the world's own vocabulary.
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

from punesim.world import classdefs

MORTH_CSV = Path("data/anchors/morth_road_accidents_large_cities_2023.csv")
CENSUS_CSV = Path("data/anchors/pune_ward_census_2011.csv")

# Which class each source calibrates. A source that calibrates nothing does not
# belong here; a class that has no source keeps `estimate@` and is listed as
# such, never quietly given a neighbour's number.
CALIBRATES = {"hazard.road.collision": "morth-2023:pune"}


def morth_accidents(city: str = "Pune", year: str = "2023") -> int:
    """Reported road accidents for one of the 50 million-plus cities."""
    with MORTH_CSV.open(encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            if row["City"].strip().lower() == city.lower():
                return int(row[f"{year} Accidents"].replace(",", ""))
    raise SystemExit(f"{city!r} is not one of the cities in {MORTH_CSV}")


def pmc_population() -> int:
    """PMC's Census-2011 population, from the row the census file labels `Pmc`.

    Read rather than typed because the denominator is half the rate: get it
    wrong by taking the urban agglomeration (5,049,968) instead of the municipal
    corporation and every rate below is 38% too low, with nothing to notice it.
    """
    with CENSUS_CSV.open(encoding="utf-8-sig", newline="") as fh:
        for row in csv.reader(fh):
            if row and row[0].strip().lower() == "pmc":
                return int(row[2])
    raise SystemExit(f"no PMC total row in {CENSUS_CSV}")


def road_rate_per_1k(city: str = "Pune", year: str = "2023") -> float:
    """Reported accidents per 1,000 people per year."""
    return morth_accidents(city, year) / pmc_population() * 1000.0


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--city", default="Pune")
    ap.add_argument("--year", default="2023", choices=("2022", "2023"))
    args = ap.parse_args()

    accidents, pop = morth_accidents(args.city, args.year), pmc_population()
    rate = accidents / pop * 1000.0
    print(f"MoRTH {args.year}: {accidents:,} road accidents in {args.city}")
    print(f"Census 2011: {pop:,} people in PMC")
    print(f"  -> {rate:.6f} per 1,000 per year\n")

    print(f"{'class':<28} {'per 1k/yr':>10} {'per day':>10}  provenance")
    for cd in classdefs.load():
        want = CALIBRATES.get(cd.type)
        flag = ""
        if want and abs(cd.rate_per_1k_per_year - rate) > 5e-6 and args.city == "Pune":
            flag = f"  <-- SHIPPED VALUE DRIFTED from {rate:.6f}"
        print(f"{cd.type:<28} {cd.rate_per_1k_per_year:>10.6f} "
              f"{cd.expected_per_day(classdefs.REFERENCE_POPULATION):>10.4f}  "
              f"{cd.provenance}{flag}")
    print(f"\nper-day column is at the reference population "
          f"({classdefs.REFERENCE_POPULATION:,}); measured rates: "
          f"{sum(c.measured for c in classdefs.load())} of {len(classdefs.load())}")


if __name__ == "__main__":
    main()
