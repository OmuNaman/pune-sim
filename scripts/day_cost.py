"""Where does a sim day's time go, and does it grow as the run gets longer?

The scale probe measures 4 days and reports an average, which hides anything
that grows with run *length* rather than population. The 30-day V3 soak found
exactly that: days 0-8 ran at ~67 s/day and days 8-13 at ~264. An average over
four days would never have shown it.

This times every day separately and attributes it to phases, so the shape of the
growth is visible rather than inferred.

    uv run python scripts/day_cost.py --days 20 --households 2000 --block oldcity
"""

import argparse
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

# (module path, attribute) pairs timed as phases. Named rather than discovered,
# so the report stays readable and stable across refactors.
#
# The module listed is where the name is *looked up at call time*, which is not
# always where it is defined: loop.py does `from .info_pass import _info_pass`,
# so it holds its own reference and patching the defining module silently
# measures nothing. The first version of this script did exactly that and
# reported that 14 of a 19-second day happened nowhere.
PHASES = [
    ("punesim.engine.loop", "_compile_day"),
    ("punesim.engine.loop", "_apply_beliefs"),
    ("punesim.engine.loop", "_apply_stays"),
    ("punesim.engine.loop", "_apply_zones"),
    ("punesim.engine.loop", "_commit"),
    ("punesim.engine.loop", "_sorted"),
    ("punesim.engine.loop", "_apply_admissions"),
    ("punesim.engine.loop", "_info_pass"),
    ("punesim.engine.loop", "_pressure_tick"),
    ("punesim.engine.loop", "_seed_rumor"),
    ("punesim.engine.loop", "stub_institution_reactions"),
    ("punesim.engine.loop", "_unrest_response"),
    ("punesim.minds.info", "propagate_day"),
    ("punesim.minds.info", "_copresence_windows"),
    ("punesim.minds.info", "_try_share"),
    ("punesim.minds.info", "presence_intervals"),
    ("punesim.minds.info", "crossed_actions"),
    ("punesim.institutions.procedures", "step"),
    ("punesim.institutions.procedures", "daily_finance_tick"),
    ("punesim.world.hazards", "sample_day"),
    ("punesim.world.hazards", "witness_tiers"),
    ("punesim.kernel.log", "EventLog.commit"),
]


class Timers:
    """Wall clock and call count per phase, resettable per day."""

    def __init__(self):
        self.t: dict[str, float] = defaultdict(float)
        self.n: dict[str, int] = defaultdict(int)
        self._undo: list = []

    def install(self) -> None:
        import importlib

        for mod_path, attr in PHASES:
            mod = importlib.import_module(mod_path)
            if "." in attr:  # a method on a class in that module
                cls_name, meth = attr.split(".", 1)
                mod = getattr(mod, cls_name)
                attr = meth
            original = getattr(mod, attr, None)
            if original is None:
                continue
            label = f"{mod_path.rsplit('.', 1)[-1]}.{attr}"

            def make(fn, label):
                def timed(*a, **kw):
                    t0 = time.perf_counter()
                    try:
                        return fn(*a, **kw)
                    finally:
                        self.t[label] += time.perf_counter() - t0
                        self.n[label] += 1
                return timed

            setattr(mod, attr, make(original, label))
            self._undo.append((mod, attr, original))

    def uninstall(self) -> None:
        for mod, attr, original in self._undo:
            setattr(mod, attr, original)

    def take(self) -> tuple[dict, dict]:
        t, n = dict(self.t), dict(self.n)
        self.t.clear()
        self.n.clear()
        return t, n


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--days", type=int, default=20)
    ap.add_argument("--households", type=int, default=2000)
    ap.add_argument("--block", default="oldcity")
    ap.add_argument("--seed", type=int, default=108)
    ap.add_argument("--db", default=None)
    ap.add_argument("--top", type=int, default=10, help="phases shown in the growth table")
    args = ap.parse_args()

    from punesim import engine
    from punesim.kernel.log import EventLog
    from punesim.population import synthesize
    from punesim.world.block import load_for

    db = Path(args.db) if args.db else Path("runs/daycost/events.db")
    db.parent.mkdir(parents=True, exist_ok=True)
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(db) + suffix)
        if p.exists():
            p.unlink()

    block = load_for(args.households, args.block)
    hhs, people = synthesize(args.seed, block, n_households=args.households)
    log = EventLog(db)
    print(f"{len(hhs):,} households / {len(people):,} people on `{args.block}`\n")

    timers = Timers()
    timers.install()
    per_day: list[tuple[float, dict, dict]] = []
    state = None
    try:
        for day in range(args.days):
            t0 = time.perf_counter()
            # start_day advances the same state object, which is what makes the
            # later days comparable to the earlier ones — a fresh SimState each
            # day would hide exactly the accumulation being looked for.
            _n, state = engine.run_simulation(
                log, args.seed, block, hhs, people, days=1, start_day=day,
                gateway=None, scenes_k=0, hazards=True, state=state,
            )
            elapsed = time.perf_counter() - t0
            t, n = timers.take()
            per_day.append((elapsed, t, n))
            top = sorted(t.items(), key=lambda kv: -kv[1])[:3]
            print(f"day {day:>3}  {elapsed:7.2f}s   " +
                  "  ".join(f"{k.split('.')[-1]} {v:.2f}s" for k, v in top), flush=True)
    finally:
        timers.uninstall()
        log.close()

    print("\n--- growth: first third vs last third ---")
    third = max(1, len(per_day) // 3)
    early, late = per_day[:third], per_day[-third:]

    def mean(rows, key=None):
        if key is None:
            return sum(r[0] for r in rows) / len(rows)
        return sum(r[1].get(key, 0.0) for r in rows) / len(rows)

    keys = sorted({k for _e, t, _n in per_day for k in t},
                  key=lambda k: -mean(late, k))[:args.top]
    print(f"{'phase':<34}{'early s':>9}{'late s':>9}{'growth':>9}")
    print(f"{'WHOLE DAY':<34}{mean(early):>9.2f}{mean(late):>9.2f}"
          f"{mean(late) / max(mean(early), 1e-9):>8.1f}x")
    for k in keys:
        e, latest = mean(early, k), mean(late, k)
        print(f"{k:<34}{e:>9.2f}{latest:>9.2f}{latest / max(e, 1e-9):>8.1f}x")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
