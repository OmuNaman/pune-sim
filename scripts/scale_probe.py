"""V3 step 0: does the engine survive a peth?

V0-V2 were built and soaked at 80 households / 306 people. V3's exit is 4 real
peths and 12k households — roughly 150x. Before spending weeks on OSM ingest and
IPF synthesis it is worth an hour finding out which parts of the day pipeline are
superlinear, because a fix is cheap now and expensive after the data lands.

Runs a ladder of household counts and reports, per rung: wall-clock per sim-day,
peak RSS, events committed, and the size of the all-pairs co-presence sweep.
Each rung runs in its own process (`--rung`), because peak RSS is process-wide
and the largest rung would otherwise poison every reading after it.

Two day shapes matter and both are run: a quiet day measures the clockwork
floor, and a day after an injection measures propagation — on a quiet run every
holding set is empty, `_try_share` returns immediately, and the probe reports a
false green.

    uv run python scripts/scale_probe.py --sizes 80,320,1280,2880 --days 4
    uv run python scripts/scale_probe.py --rung 2880 --days 4 --profile
"""

import argparse
import cProfile
import io
import json
import pstats
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from probe_meter import Copresence, peak_rss_mb

# Injected on day 1 at a fixed place so every rung gets the same shock, and so
# days 2+ measure a populated info graph rather than an empty one. No
# participants: person ids are stable across rungs but a *specific* person is
# not necessarily near this place at every size.
INJECTION = {
    "day": 1,
    "time": "11:40",
    "type": "hazard.fire.small",
    "place": "place:node/3337848241",
    "participants": [],
    "severity": 0.7,
    "payload": {"mechanism": "scale probe: fixed shock, identical at every rung"},
}


def run_rung(households: int, days: int, seed: int, db: Path, profile: bool,
             block_name: str = "kasba") -> dict:
    """One rung, in this process. Returns the measurement row."""
    from punesim import engine
    from punesim.kernel.log import EventLog
    from punesim.population import synthesize
    from punesim.world.block import load_for

    for suffix in ("", "-wal", "-shm"):
        p = Path(str(db) + suffix)
        if p.exists():
            p.unlink()
    db.parent.mkdir(parents=True, exist_ok=True)

    t_load = time.perf_counter()
    block = load_for(households, block_name)
    hhs, people = synthesize(seed, block, n_households=households)
    setup_s = time.perf_counter() - t_load

    log = EventLog(db)
    cop = Copresence()
    uninstall = cop.install()
    injections = [engine.Injection.parse(INJECTION)]

    prof = cProfile.Profile() if profile else None
    t0 = time.perf_counter()
    if prof:
        prof.enable()
    events, _state = engine.run_simulation(
        log, seed, block, hhs, people,
        days=days, gateway=None, scenes_k=0, injections=injections, hazards=True,
    )
    if prof:
        prof.disable()
    elapsed = time.perf_counter() - t0
    uninstall()
    hash_ = log.determinism_hash()
    log.close()

    row = {
        "households": households,
        "block": block_name,
        "people": len(people),
        "places": len(block.places),
        "days": days,
        "setup_s": round(setup_s, 3),
        "total_s": round(elapsed, 3),
        "s_per_day": round(elapsed / days, 3),
        "events": events,
        "events_per_day": round(events / days, 1),
        "peak_rss_mb": round(peak_rss_mb(), 1),
        "copresence_windows": cop.windows,
        "copresence_per_day": round(cop.windows / max(1, cop.calls), 1),
        "max_windows_in_a_day": cop.max_windows_in_a_day,
        "worst_place": cop.worst_place,
        "worst_place_windows": cop.worst_place_windows,
        "max_co_present_at_one_place": cop.max_at_one_place,
        "hash": hash_,
    }
    if prof:
        row["profile"] = _top_frames(prof)
    return row


def _top_frames(prof: cProfile.Profile, n: int = 18) -> list[str]:
    """Top cumulative-time frames inside punesim, as printable lines."""
    buf = io.StringIO()
    stats = pstats.Stats(prof, stream=buf)
    stats.sort_stats("cumulative")
    rows: list[str] = []
    for func, (_cc, nc, tt, ct, _callers) in stats.stats.items():  # type: ignore[attr-defined]
        filename, lineno, name = func
        if "punesim" not in filename.replace("\\", "/"):
            continue
        short = "/".join(Path(filename).parts[-2:])
        rows.append((ct, tt, nc, f"{ct:8.3f}s cum {tt:8.3f}s own {nc:>9} calls  {short}:{lineno} {name}"))
    rows.sort(reverse=True)
    return [r[3] for r in rows[:n]]


def _fmt_table(rows: list[dict]) -> str:
    cols = [
        ("households", "hh", 6), ("people", "people", 7), ("s_per_day", "s/day", 8),
        ("peak_rss_mb", "RSS MB", 8), ("events_per_day", "events/d", 9),
        ("copresence_per_day", "cowin/d", 10), ("max_co_present_at_one_place", "max@place", 10),
    ]
    head = "  ".join(f"{label:>{w}}" for _k, label, w in cols)
    out = [head, "  ".join("-" * w for _k, _l, w in cols)]
    for r in rows:
        out.append("  ".join(f"{r.get(k, ''):>{w}}" for k, _l, w in cols))
    return "\n".join(out)


def _exponent(rows: list[dict], key: str) -> str:
    """Fit y ~ n^a across the ladder's endpoints. The whole point of a ladder."""
    usable = [r for r in rows if r.get(key)]
    if len(usable) < 2:
        return "n/a"
    import math

    a, b = usable[0], usable[-1]
    if a["people"] <= 0 or a[key] <= 0:
        return "n/a"
    ratio_n = b["people"] / a["people"]
    ratio_y = b[key] / a[key]
    if ratio_n <= 1 or ratio_y <= 0:
        return "n/a"
    return f"{math.log(ratio_y) / math.log(ratio_n):.2f}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sizes", default="80,320,1280,2880", help="household ladder")
    ap.add_argument("--rung", type=int, help="run ONE rung in this process (internal)")
    ap.add_argument("--days", type=int, default=4)
    ap.add_argument("--seed", type=int, default=108)
    ap.add_argument("--profile", action="store_true", help="cProfile the largest rung")
    ap.add_argument("--out", default="docs/perf/scale-probe.json")
    ap.add_argument("--db-dir", default=None, help="where rung dbs go (default: a temp dir)")
    ap.add_argument("--block", default="kasba", help="named block: kasba (V0-V2 pin) | oldcity (V3)")
    args = ap.parse_args()

    db_dir = Path(args.db_dir) if args.db_dir else Path("runs/probe")

    if args.rung is not None:
        row = run_rung(args.rung, args.days, args.seed,
                       db_dir / f"probe-{args.block}-{args.rung}.db", args.profile, args.block)
        print("@@ROW@@" + json.dumps(row))
        return 0

    sizes = [int(s) for s in args.sizes.split(",") if s.strip()]
    rows: list[dict] = []
    for i, n in enumerate(sizes):
        last = i == len(sizes) - 1
        cmd = [sys.executable, __file__, "--rung", str(n), "--days", str(args.days),
               "--seed", str(args.seed), "--db-dir", str(db_dir), "--block", args.block]
        if args.profile and last:
            cmd.append("--profile")
        print(f"-- rung {n} households ...", flush=True)
        t0 = time.perf_counter()
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            print(proc.stdout[-3000:])
            print(proc.stderr[-3000:], file=sys.stderr)
            print(f"   rung {n} FAILED after {time.perf_counter() - t0:.1f}s")
            rows.append({"households": n, "error": proc.stderr.strip().splitlines()[-1:] or ["?"]})
            continue
        line = next((ln for ln in proc.stdout.splitlines() if ln.startswith("@@ROW@@")), None)
        if line is None:
            print("   no row emitted", proc.stdout[-500:])
            continue
        row = json.loads(line[len("@@ROW@@"):])
        rows.append(row)
        print(f"   {row['people']:>6} people  {row['s_per_day']:>7}s/day  "
              f"{row['peak_rss_mb']:>7}MB  {row['copresence_per_day']:>10} cowin/day", flush=True)

    ok = [r for r in rows if "error" not in r]
    print("\n" + _fmt_table(ok))
    print(f"\nscaling in people:  time ~ n^{_exponent(ok, 's_per_day')}   "
          f"rss ~ n^{_exponent(ok, 'peak_rss_mb')}   "
          f"copresence ~ n^{_exponent(ok, 'copresence_per_day')}")
    for r in ok:
        if r.get("profile"):
            print(f"\ncProfile, {r['households']} households, {r['days']} days:")
            for ln in r["profile"]:
                print("  " + ln)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"days": args.days, "seed": args.seed,
                               "block": args.block, "rows": rows},
                              indent=2), encoding="utf-8")
    print(f"\nwrote {out}")
    return 0 if len(ok) == len(rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
