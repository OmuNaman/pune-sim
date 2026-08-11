"""V3's exit test, as predicates over a log rather than a reading of the prose.

V3's exit is "V0's scenario re-runs unchanged on 4 real peths / 12k households".
V0's, V1's and V2's exits are sentences in docs/architecture.md — "consequences
fire on schedule", "a gossip hop reaches neighbors", "the crash yields an FIR +
hospital bill that raises p_financial and triggers a money scene weeks later" —
and a sentence is not a check. This turns each into a query with a verdict, so
the exit can be re-run by anyone and disagreed with by inspection.

Two clauses are deliberately NOT here, because inventing a mechanical proxy for
them would be worse than admitting they are judgements: "the day-3 interview
matches canon" and "a believable un-injected ripple". Those go to
scripts/continuity_read.py's judge-plus-skeptic, and this script reports them
UNJUDGED so that a green summary never implies they passed.

    uv run python scripts/exit_check.py --db runs/exit/v0/events.db --household hh:1160
    uv run python scripts/exit_check.py --db runs/exit/v0/events.db --replay-db runs/exit/v0-replay/events.db
    uv run python scripts/exit_check.py --db runs/exit/v1/events.db --clause V1-a

The plan these predicates come from, with the file:line for every constant, is
docs/exits/v3-exit-plan.md.
"""

import argparse
import importlib.util
import pathlib
import sqlite3
import sys
from dataclasses import dataclass, field

import orjson

_HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parents[0] / "src"))

# The collision/fire/water regexes are already tuned for Marathi and Hindi
# scene text; re-typing them here would be a second copy to drift.
_SPEC = importlib.util.spec_from_file_location("audit_run", _HERE / "audit_run.py")
audit_run = importlib.util.module_from_spec(_SPEC)
sys.modules["audit_run"] = audit_run
_SPEC.loader.exec_module(audit_run)
TOPIC_RE = audit_run.TOPIC_RE

DAY = 86400

# Deltas in seconds from the injected hazard to each consequence. These are
# arithmetic in engine/reactions.py, not draws, so an inexact match is a
# regression rather than noise.
CONSEQUENCE_DELTAS = {
    "condition.set": 300,
    "ambulance.dispatched": 480,
    "message.sent": 1200,
    "hospital.admitted": 1500,
}


@dataclass
class Clause:
    id: str
    title: str
    verdict: str = "UNJUDGED"  # PASS | FAIL | UNJUDGED | SKIP
    detail: list[str] = field(default_factory=list)

    def say(self, verdict: str, *lines: str) -> "Clause":
        self.verdict, self.detail = verdict, [str(x) for x in lines]
        return self


class Log:
    """Read-only access to one run, with the payload already decoded."""

    def __init__(self, db: pathlib.Path):
        self.con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        self.db = db

    def rows(self, where: str = "", args: tuple = ()) -> list[tuple]:
        q = ("SELECT seq, sim_time, type, payload, caused_by, provenance FROM event "
             "WHERE branch_id=0 " + where + " ORDER BY seq")
        return [(s, t, ty, orjson.loads(p), c, pr)
                for s, t, ty, p, c, pr in self.con.execute(q, args)]

    def meta(self) -> dict:
        r = self.con.execute("select payload from event where type='run.meta'").fetchone()
        return orjson.loads(r[0]) if r else {}

    def hash(self) -> str:
        from punesim.kernel.log import EventLog

        log = EventLog(self.db)
        try:
            return log.determinism_hash()
        finally:
            log.close()


class _MetaOnly:
    """Just enough of an EventLog for `world_for_log` to read run.meta — this
    script talks to sqlite directly and never wants a writable handle."""

    class _Ev:
        def __init__(self, payload):
            self.payload, self.type = payload, "run.meta"

    def __init__(self, meta: dict):
        self._meta = meta

    def events(self, *, type=None, **_kw):
        if type == "run.meta" and self._meta:
            yield self._Ev(dict(self._meta))


def _injection(log: Log) -> tuple | None:
    """The injected hazard: user provenance, no parent, and a hazard."""
    for r in log.rows("AND provenance='user' AND caused_by IS NULL AND type LIKE 'hazard.%'"):
        return r
    return None


def v0a_consequences_on_schedule(log: Log) -> Clause:
    c = Clause("V0-a", "consequences fire on schedule")
    inj = _injection(log)
    if inj is None:
        return c.say("FAIL", "no injected hazard in this log")
    seq, t0 = inj[0], inj[1]
    # A hazard's children are mostly percepts — one info.heard per witness, all
    # at the same instant — so they are counted rather than listed. The named
    # consequences are what the clause is about.
    seen: dict[str, set[int]] = {}
    for _s, t, ty, _p, _cause, _pr in log.rows("AND caused_by=?", (seq,)):
        seen.setdefault(ty, set()).add(t - t0)
    wrong = [
        f"{ty}: expected +{want}s, got "
        + ("absent" if ty not in seen else "+" + "s, +".join(str(d) for d in sorted(seen[ty])) + "s")
        for ty, want in CONSEQUENCE_DELTAS.items()
        if seen.get(ty) != {want}
    ]
    lines = [f"injected {inj[2]} at seq {seq}"] + [
        f"  {'+' + ', +'.join(f'{d}s' for d in sorted(dts)):<22} {ty}"
        + (f"  (x{len(log.rows('AND caused_by=? AND type=?', (seq, ty)))})" if ty == "info.heard" else "")
        for ty, dts in sorted(seen.items(), key=lambda kv: min(kv[1]))
    ]
    return c.say("FAIL" if wrong else "PASS", *(wrong + lines))


def v0b_scenes_reference_it(log: Log, household: str) -> Clause:
    c = Clause("V0-b", "the family's scenes reference it for days")
    rx = TOPIC_RE["collision"]
    days: dict[int, int] = {}
    for _s, t, ty, p, _c, _pr in log.rows("AND type IN ('scene.morning','scene.reaction')"):
        if p.get("household") != household:
            continue
        text = f"{p.get('narration', '')}\n{p.get('transcript', '')}"
        if rx.search(text):
            days[t // DAY] = days.get(t // DAY, 0) + 1
    hits = sorted(days)
    lines = [f"{household} scenes mentioning the collision, by day: "
             f"{', '.join(f'day {d} ({days[d]})' for d in hits) or 'none'}"]
    if len(hits) >= 2:
        return c.say("PASS", *lines)
    return c.say("FAIL", f"referenced on {len(hits)} day(s); the clause says 'for days'", *lines)


def v0c_gossip_reaches_neighbours(log: Log, household: str, hh_of: dict) -> Clause:
    c = Clause("V0-c", "a gossip hop reaches neighbours")
    inj = _injection(log)
    if inj is None:
        return c.say("FAIL", "no injected hazard in this log")
    seeded = {p["claim_key"] for _s, _t, _ty, p, cause, _pr
              in log.rows("AND type='info.heard' AND caused_by=?", (inj[0],))}
    if not seeded:
        return c.say("FAIL", "the injected hazard seeded no claim at all")
    by_channel: dict[str, dict] = {}
    outside = 0
    for _s, _t, _ty, p, _c, _pr in log.rows("AND type='info.heard'"):
        if p.get("claim_key") not in seeded:
            continue
        ch = p.get("channel", "?")
        row = by_channel.setdefault(ch, {"n": 0, "people": set(), "hop": 0})
        row["n"] += 1
        row["people"].add(p.get("person"))
        row["hop"] = max(row["hop"], int(p.get("claim", {}).get("hop") or 0))
        if ch != "witness" and hh_of.get(p.get("person")) != household:
            outside += 1
    lines = [f"claim(s) {', '.join(sorted(seeded))}"] + [
        f"  {ch:<10} {r['n']:>6} hearings  {len(r['people']):>6} people  max hop {r['hop']}"
        for ch, r in sorted(by_channel.items())
    ] + [f"  {outside} hearings by someone outside {household} on a non-witness channel"]
    told = [r for ch, r in by_channel.items() if ch != "witness" and r["hop"] >= 1]
    return c.say("PASS" if told and outside else "FAIL", *lines)


def v0e_replay_is_identical(log: Log, replay: Log) -> Clause:
    c = Clause("V0-e", "replay is hash-identical with zero API calls")
    a, b = log.hash(), replay.hash()
    lines = [f"record {a}", f"replay {b}"]
    if a != b:
        return c.say("FAIL", "the two runs do not agree", *lines)
    return c.say("PASS", *lines,
                 "(zero API calls is enforced by PUNESIM_LLM=replay: a miss raises CassetteMiss)")


def v1a_rumour_lives(log: Log, claim_key: str, seeds: set[str]) -> Clause:
    c = Clause("V1-a", "a rumour propagates, mutates and changes behaviour in 3 days")
    people, variants, ops, hop, hearings = set(), set(), 0, 0, 0
    for _s, t, _ty, p, _c, _pr in log.rows("AND type='info.heard' AND sim_time < ?", (4 * DAY,)):
        if p.get("claim_key") != claim_key:
            continue
        hearings += 1
        people.add(p.get("person"))
        cl = p.get("claim", {})
        variants.add(cl.get("text", ""))
        ops += len(cl.get("ops") or ())
        hop = max(hop, int(cl.get("hop") or 0))
    actors: dict[str, int] = {}
    for _s, t, _ty, p, _c, _pr in log.rows("AND type='belief.action' AND sim_time < ?", (4 * DAY,)):
        if p.get("claim_key") == claim_key and p.get("person") not in seeds:
            actors.setdefault(p["person"], t // DAY)
    lines = [
        f"{hearings} hearings, {len(people)} people, {len(variants)} distinct texts, "
        f"{ops} mutation ops, max hop {hop}",
        f"{len(actors)} NON-SEED people acted"
        + (f", first on day {min(actors.values())}" if actors else ""),
        f"(the {len(seeds)} seeds are excluded: injected credence clears the action "
        f"threshold by construction, so counting them makes the clause vacuous)",
    ]
    if not hearings:
        # Three different things look identical in the counts, and only one of
        # them is a failure of the clause.
        keys = sorted({p.get("claim_key") for _s, _t, _ty, p, _c, _pr
                       in log.rows("AND type='info.heard'") if p.get("claim_key")})
        seeded_here = log.rows("AND provenance='user' AND type LIKE 'info.%'")
        if not seeded_here:
            return c.say("SKIP", "no rumour was injected into this run at all — this clause "
                                 "is decided on the v1_exam run, not here")
        return c.say("FAIL", f"a rumour WAS injected but there are no hearings of {claim_key} "
                             f"in days 0-3.",
                     f"claims this log does carry: {', '.join(keys) or 'none at all'}")
    ok = (len(people) >= 5 and (len(variants) > 1 or ops > 0) and hop >= 1
          and actors and min(actors.values()) <= 3)
    return c.say("PASS" if ok else "FAIL", *lines)


def v1b_random_hazard_ripples(log: Log, population: int = 0) -> Clause:
    c = Clause("V1-b", "a random hazard produces an un-injected ripple")
    lines, any_ok = [], False
    for seq, t, ty, _p, _cause, prov in log.rows("AND type LIKE 'hazard.%'"):
        n = len(log.rows("AND type='info.heard' AND caused_by=?", (seq,)))
        lines.append(f"  day {t // DAY:>2}  {prov:<10} {ty:<26} {n} percepts")
        if prov == "clockwork" and n > 0:
            any_ok = True
    if not lines:
        # Rates are per-capita, so an empty log can mean the machinery is broken
        # OR that this world was too small and too short for anything to be
        # likely — and those are different answers. Silence in a world that
        # expected 0.1 hazards is not evidence of anything.
        from punesim.world import classdefs

        defs = classdefs.load()
        days = (max((t for _s, t, *_ in log.rows()), default=0) // DAY) + 1
        expected = sum(cd.expected_per_day(population) for cd in defs) * days
        if expected < 3.0:
            per_1k = sum(cd.rate_per_1k_per_year for cd in defs)
            need = 3.0 * 365.0 * 1000.0 / (per_1k * days)
            return c.say(
                "SKIP",
                f"no hazards, and none were likely: {population:,} people over "
                f"{days} day(s) expects {expected:.2f}. Judging this needs a world "
                f"where a few are — about {need:,.0f} people for {days} day(s), or "
                f"the same people for {need / max(1, population) * days:,.0f} days",
            )
        return c.say("FAIL", f"no hazards in a run that expected {expected:.1f}")
    return c.say(
        "PASS" if any_ok else "FAIL", *lines,
        "'believable' is a judgement, not a predicate — this only shows the ripple exists",
    )


def v1d_cost(log: Log) -> Clause:
    c = Clause("V1-d", "cost per sim-day (<$1 for V1, <$2 for V3)")
    total, calls, missing = 0.0, 0, 0
    for _s, _t, _ty, p, _c, _pr in log.rows("AND type='llm.response'"):
        calls += 1
        usage = p.get("usage")
        if not usage or usage.get("cost") is None:
            missing += 1
            continue
        total += float(usage["cost"])
    days = (max((t for _s, t, *_ in log.rows()), default=0) // DAY) + 1
    per = total / max(1, days)
    if not calls:
        # $0.00 from zero calls is not a cost result, it is a clockwork run.
        # Passing on it is the same mistake as a probe passing because there
        # was nothing to look at.
        return c.say("SKIP", f"no llm.response events — this run made no model calls at all, "
                             f"so it says nothing about cost")
    lines = [f"${total:.4f} over {days} sim-days = ${per:.4f}/sim-day, {calls} calls",
             f"{missing} call(s) reported no usage — the total is biased LOW by that much"]
    return c.say("PASS" if per < 1.0 else "FAIL", *lines)


def v2a_the_chain(log: Log, household: str, hh_of: dict) -> Clause:
    c = Clause("V2-a", "crash -> FIR + bill -> p_financial -> a scene after")
    def pick(kind: str, pred=lambda p: True):
        return [(t // DAY, p) for _s, t, _ty, p, _c, _pr in log.rows("AND type=?", (kind,))
                if pred(p)]
    fir = pick("police.fir.registered")
    disc = pick("hospital.discharged", lambda p: p.get("household") == household)
    paid = pick("money.paid", lambda p: p.get("household") == household)
    # Resolved through the roster, never by string prefix: at 12,000 households
    # "person:1160" is a prefix of "person:11600", so a startswith test on hh:1160
    # silently counts hh:11600's crossings as this family's. The bug only exists
    # above 1,000 households, which is to say only where this test runs.
    crossed = pick("pressure.crossed", lambda p: p.get("pressure") == "p_financial"
                   and hh_of.get(p.get("person")) == household)
    scenes = {d for d, _p in pick("scene.morning", lambda p: p.get("household") == household)}
    lines = [
        f"FIR         : {[(d, p.get('victim'), p.get('complainant')) for d, p in fir] or 'none'}",
        f"discharge   : {[(d, p.get('bill')) for d, p in disc] or 'none'}",
        f"money.paid  : {[(d, p.get('amount')) for d, p in paid] or 'none'}",
        f"p_financial : {[(d, p.get('person'), p.get('value')) for d, p in crossed] or 'none'}",
        f"scenes      : days {sorted(scenes) or 'none'}",
    ]
    after = [d for d, _p in crossed if any(s > d for s in scenes)]
    ok = bool(fir) and any(p.get("bill") for _d, p in disc) and bool(paid) and bool(crossed) and bool(after)
    lines.append("the gate's REASON is never logged, so 'money scene' is provable only as "
                 "'a scene after the crossing', never as 'a scene because of money'")
    return c.say("PASS" if ok else "FAIL", *lines)


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", required=True, type=pathlib.Path)
    ap.add_argument("--replay-db", type=pathlib.Path, help="the PUNESIM_LLM=replay twin, for V0-e")
    ap.add_argument("--household", default="hh:1160", help="the followed family")
    ap.add_argument("--claim", default="cl:tulshibaug_water", help="V1-a's claim key")
    ap.add_argument("--seeds", default="person:001.1,person:002.3", help="V1-a's injected first-hearers")
    ap.add_argument("--clause", action="append", help="run only these (repeatable)")
    args = ap.parse_args()

    log = Log(args.db)
    meta = log.meta()
    from punesim.world.roster import world_for_log

    block, _hhs, people, _m = world_for_log(_MetaOnly(meta))
    hh_of = {pid: p.household_id for pid, p in people.items()}

    print(f"\n=== V3 exit check: {args.db} ===")
    print(f"block {block.name} | {meta.get('households')} households | {len(people):,} people "
          f"| seed {meta.get('seed')} | following {args.household}\n")

    clauses = [
        v0a_consequences_on_schedule(log),
        v0b_scenes_reference_it(log, args.household),
        v0c_gossip_reaches_neighbours(log, args.household, hh_of),
        v1b_random_hazard_ripples(log, len(people)),
        v1d_cost(log),
        v2a_the_chain(log, args.household, hh_of),
        v1a_rumour_lives(log, args.claim, set(args.seeds.split(","))),
        Clause("V0-d", "the day-3 interview matches canon").say(
            "UNJUDGED", "decided by scripts/continuity_read.py's judge + skeptic, not here"),
        Clause("V0-f", "refusal behaviour on identity-salient content").say(
            "UNJUDGED", "decided by scripts/refusal_probe.py, which is model-facing"),
        Clause("V1-c", "30 days, zero canon contradictions").say(
            "UNJUDGED", "decided by scripts/continuity_read.py on the 30-day soak"),
    ]
    if args.replay_db:
        clauses.append(v0e_replay_is_identical(log, Log(args.replay_db)))
    if args.clause:
        clauses = [c for c in clauses if c.id in set(args.clause)]

    for c in sorted(clauses, key=lambda c: c.id):
        print(f"{c.verdict:<9} {c.id:<6} {c.title}")
        for line in c.detail:
            print(f"          {line}")
        print()
    fails = [c for c in clauses if c.verdict == "FAIL"]
    unjudged = [c for c in clauses if c.verdict == "UNJUDGED"]
    print(f"{len(clauses)} clauses: {sum(c.verdict == 'PASS' for c in clauses)} pass, "
          f"{len(fails)} fail, {len(unjudged)} not decidable here")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
