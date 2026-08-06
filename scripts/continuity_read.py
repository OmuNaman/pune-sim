"""The V1 exit criterion, as a command.

"Thirty sim-days with zero canon contradictions on a followed family" is the
one exit test no mechanical probe can decide: it needs a reader who can notice
that a fire someone watched at 14:05 has been retold as happening at night.
scripts/audit_run.py says so itself — its temporal-drift probe is WARN-only and
time-of-day drift is invisible to it.

So this is the other half: it assembles the family's canon (roster, everything
they personally witnessed with exact times, every institutional fact about
them) and every scene they appear in, and asks a judge model to find where the
prose contradicts the log. The judge is given the log as ground truth and told
to cite; it never gets to invent a contradiction without a seq to point at.

    uv run python scripts/continuity_read.py --db runs/soak2/events.db --household hh:000

Exit 0 = no contradictions, 1 = contradictions found, 2 = could not run.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

import orjson
from pydantic import BaseModel, Field

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from punesim import config
from punesim.kernel.log import EventLog
from punesim.kernel.timebase import SECONDS_PER_DAY, to_datetime
from punesim.llm import Cassette, Gateway
from punesim.minds.scene import _humanize
from punesim.population import synthesize
from punesim.world.block import Block

SYSTEM = """You are auditing a life simulation for CONTINUITY, the way a script supervisor audits
a film. You are given (1) the CANON — a family's roster and every fact the simulation's event log
records about them, which is ground truth and cannot be wrong — and (2) every SCENE written about
them, in order.

Find places where the prose contradicts the canon. The four kinds that matter:
  EVENT-TIME     — an event retold at a different time or day than canon records, including
                   time of day ("the fire broke out at night" when canon says 14:05).
  IDENTITY       — a person given a name, age, job or relationship that canon does not have;
                   someone addressed who is not in the roster; a child written as an adult.
  REPEAT         — a scene restating an earlier day's events as if they happened today, or
                   re-living the same incident on multiple days.
  STATE          — an injury, hospital stay, debt, or absence that contradicts canon (a person
                   walking to work while canon has them admitted; an injury that never healed
                   reappearing; a bill nobody owes).

Do NOT report: invented small texture (what was cooked, who spoke first, a neighbour mentioned in
passing without an id), ordinary routine repetition (chai every morning is not a contradiction),
or anything you cannot tie to a specific canon line. A family that argues about the same rumour
on two days is normal; a family that re-lives the same incident is not.

Every finding MUST cite the scene day and the canon line it contradicts. If you cannot cite,
do not report it. Being thorough matters, but a false positive is worse than a miss here —
this is a pass/fail gate on the simulation's core promise."""

SCHEMA_HINT = """Reply with ONE JSON object:
{"findings": [{"kind": "EVENT-TIME|IDENTITY|REPEAT|STATE",
               "severity": "major|minor",
               "day": <sim day of the offending scene>,
               "quote": "the exact phrase from the scene",
               "canon": "the canon line it contradicts",
               "why": "one sentence"}],
 "verdict": "PASS" or "FAIL",
 "note": "one or two sentences on the family's overall coherence"}
Return "verdict": "PASS" with an empty findings list if the month holds together."""


class Finding(BaseModel):
    kind: str
    severity: str = "minor"
    day: int = -1
    quote: str = ""
    canon: str = ""
    why: str = ""


class Report(BaseModel):
    model_config = {"extra": "ignore"}
    findings: list[Finding] = Field(default_factory=list)
    verdict: str = "PASS"
    note: str = ""


def build_canon(log: EventLog, hh, people, block) -> list[str]:
    """Everything the log knows about this family, as flat assertable lines."""
    members = set(hh.member_ids)
    lines = [f"ROSTER of household {hh.id} ({hh.surname} family, {hh.template}):"]
    for pid in hh.member_ids:
        p = people[pid]
        work = block.get(p.work_id) if p.work_id else None
        lines.append(
            f"  {p.id} = {p.name}, age {p.age}, {p.occupation}"
            + (f", goes to {work.name}" if work and work.name else ", no fixed workplace")
        )
    lines.append("")
    lines.append("NOBODY ELSE LIVES IN THIS HOUSEHOLD. Any other person named as a member,")
    lines.append("relative in the house, or resident is a contradiction.")
    lines.append("")
    lines.append("WHAT THEY PERSONALLY SAW (exact times — these cannot be retold differently):")
    seen = False
    for e in log.events(type="info.heard"):
        p = e.payload
        if p.get("channel") != "witness" or p.get("person") not in members:
            continue
        who = people[p["person"]].name
        when = to_datetime(e.sim_time)
        lines.append(
            f"  day {e.sim_time // SECONDS_PER_DAY} {when:%a %d %b %H:%M} — {who} was present:"
            f" \"{p.get('claim', {}).get('text', '')}\""
        )
        seen = True
    if not seen:
        lines.append("  (nobody in this family witnessed anything first-hand)")
    lines.append("")
    lines.append("EVERYTHING ELSE THAT HAPPENED TO THEM (institutional and mechanical facts):")
    skip = {"scene.morning", "scene.reaction", "scene.skipped", "scene.invalid_ref",
            "memory.formed", "mood.delta", "plan.revised", "llm.response", "info.heard",
            "trip.start", "trip.end", "activity.start", "run.meta"}
    facts = 0
    for e in log.events():
        if e.type in skip:
            continue
        p = e.payload
        touched = {p.get(k) for k in ("person", "sender", "complainant", "victim", "entity_id")}
        touched |= set(p.get("recipients") or []) | set(p.get("participants") or [])
        if not (touched & members) and p.get("household") != hh.id:
            continue
        line = _humanize(e.type, p, block, people)
        if line:
            lines.append(f"  day {e.sim_time // SECONDS_PER_DAY} {to_datetime(e.sim_time):%a %H:%M} — {line}")
            facts += 1
    if not facts:
        lines.append("  (nothing institutional touched them)")
    return lines


def build_scenes(log: EventLog, hh, people) -> list[tuple[int, str]]:
    """Each scene as the model actually asserted it: prose plus the memories it
    wrote and the messages it sent. Those payloads are where the first soak's
    invented colleague lived — she never appeared in a transcript, only in a
    memory summary, so a reader given prose alone cannot find her."""
    scenes: dict[int, dict] = {}
    for e in log.events():
        if e.type in ("scene.morning", "scene.reaction") and e.payload.get("household") == hh.id:
            scenes[e.seq] = {
                "t": e.sim_time,
                "kind": "MORNING" if e.type == "scene.morning" else "REACTION",
                "narration": e.payload.get("narration", ""),
                "transcript": e.payload.get("transcript", ""),
                "memories": [],
                "messages": [],
            }
        elif e.caused_by in scenes:
            s = scenes[e.caused_by]
            if e.type == "memory.formed":
                who = people.get(e.payload.get("person"))
                s["memories"].append(
                    f"    {who.given if who else e.payload.get('person')} will remember:"
                    f" {e.payload.get('summary', '')}"
                )
            elif e.type == "message.sent":
                to = ", ".join(
                    (people[r].name if r in people else r)
                    for r in e.payload.get("recipients") or []
                )
                sender = people.get(e.payload.get("sender"))
                s["messages"].append(
                    f"    {sender.given if sender else e.payload.get('sender')} -> {to}:"
                    f" {e.payload.get('text', '')}"
                )
    out = []
    for s in sorted(scenes.values(), key=lambda x: x["t"]):
        day = s["t"] // SECONDS_PER_DAY
        parts = [
            f"--- day {day}, {to_datetime(s['t']):%A %d %b %Y %H:%M} ({s['kind']}) ---",
            s["narration"], s["transcript"],
        ]
        if s["memories"]:
            parts.append("  memories this scene wrote:")
            parts.extend(s["memories"])
        if s["messages"]:
            parts.append("  messages this scene sent:")
            parts.extend(s["messages"])
        out.append((day, "\n".join(p for p in parts if p)))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", required=True, type=Path)
    ap.add_argument("--household", default="hh:000")
    ap.add_argument("--seed", type=int, default=108)
    ap.add_argument("--households", type=int, default=80)
    ap.add_argument("--batch", type=int, default=6, help="scenes per judge call")
    ap.add_argument("--out", type=Path, default=None, help="write findings as JSON")
    args = ap.parse_args()

    if not args.db.exists():
        print(f"continuity: no such db: {args.db}", file=sys.stderr)
        return 2
    cfg = config.from_env()
    if not cfg.openrouter_api_key:
        print("continuity: no OPENROUTER_API_KEY — this check needs a judge model", file=sys.stderr)
        return 2

    block = Block.load()
    hhs, people = synthesize(args.seed, block, n_households=args.households)
    hh = next((h for h in hhs if h.id == args.household), None)
    if hh is None:
        print(f"continuity: no such household: {args.household}", file=sys.stderr)
        return 2

    log = EventLog(args.db)
    try:
        canon = build_canon(log, hh, people, block)
        scenes = build_scenes(log, hh, people)
    finally:
        log.close()
    if not scenes:
        print(f"continuity: {args.household} was never rendered — nothing to read", file=sys.stderr)
        return 2

    gw = Gateway(cfg, Cassette(cfg.cassette_path))
    findings: list[Finding] = []
    notes: list[str] = []
    verdicts: list[str] = []
    unread: list[str] = []
    for i in range(0, len(scenes), args.batch):
        chunk = scenes[i : i + args.batch]
        body = (
            "CANON (ground truth):\n" + "\n".join(canon)
            + f"\n\nSCENES (days {chunk[0][0]}-{chunk[-1][0]} of {scenes[-1][0]}):\n"
            + "\n\n".join(b for _, b in chunk)
            + "\n\n" + SCHEMA_HINT
        )
        msgs = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": body}]
        # The premium reasoning model sometimes returns nothing at all on a long
        # prompt — every token went to reasoning. Fall back to the workhorse
        # rather than lose a batch: a read that silently skipped days would be
        # worse than one done by a smaller model, as long as it says so.
        res = None
        for model in (cfg.model_premium, cfg.model_workhorse):
            try:
                res = gw.call(
                    "qc_judge", msgs, Report, temperature=0.0,
                    max_tokens=6000, model_override=model,
                )
                break
            except Exception as err:  # noqa: BLE001 — refusal/schema/transport
                unread.append(f"days {chunk[0][0]}-{chunk[-1][0]} via {model}: {type(err).__name__}")
        if res is None:
            continue
        findings.extend(res.parsed.findings)
        verdicts.append(res.parsed.verdict)
        if res.parsed.note:
            notes.append(res.parsed.note)

    major = [f for f in findings if f.severity == "major"]
    print(f"\n=== continuity read: {args.household} ({hh.surname} family), {args.db} ===")
    print(f"{len(scenes)} scenes over days {scenes[0][0]}-{scenes[-1][0]}, "
          f"judged by {cfg.model_premium} in {len(verdicts)} batches\n")
    if unread:
        print("  !! batches the judge could not read (NOT a pass for those days):")
        for u in unread:
            print(f"     {u}")
        print()
    if not findings:
        print("VERDICT: PASS — no contradictions found." if not unread
              else "VERDICT: PARTIAL — no contradictions in the batches that were read.")
    else:
        print(f"VERDICT: FAIL — {len(findings)} contradictions ({len(major)} major)\n")
        for f in sorted(findings, key=lambda x: (x.severity != "major", x.day)):
            print(f"  [{f.severity}] {f.kind} day {f.day}")
            print(f"      scene : {f.quote[:150]}")
            print(f"      canon : {f.canon[:150]}")
            print(f"      why   : {f.why[:150]}")
    for n in notes:
        print(f"\n  note: {n}")
    if args.out:
        args.out.write_text(
            orjson.dumps(
                {"household": args.household, "db": str(args.db),
                 "scenes": len(scenes), "findings": [f.model_dump() for f in findings],
                 "notes": notes},
                option=orjson.OPT_INDENT_2,
            ).decode(),
            encoding="utf-8",
        )
        print(f"\nwritten: {args.out}")
    return 1 if findings else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except sqlite3.Error as err:
        print(f"continuity: {err}", file=sys.stderr)
        raise SystemExit(2) from err
