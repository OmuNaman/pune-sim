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
from punesim.world.roster import RosterMismatch, world_for_log

SYSTEM = """You are auditing a life simulation for CONTINUITY, the way a script supervisor audits
a film. You are given (1) the CANON — a family's roster and every fact the simulation's event log
records about them, which is ground truth and cannot be wrong — and (2) every SCENE written about
them, in order.

Two things can be wrong, and they are NOT the same:

  scope="canon"   — the prose contradicts the EVENT LOG. A witnessed fire moved to nighttime, a
                    person who does not exist, someone at work while canon has them in hospital.
                    These are the ones that matter: the log is the world, and prose may not
                    overrule it.
  scope="texture" — the prose contradicts ANOTHER SCENE about something the log never recorded.
                    A lost notebook, what was cooked, whose turn it was. Worth knowing — a
                    household should not contradict itself — but the log is silent, so nothing
                    has been overruled. A character misremembering a small domestic detail by a
                    day is what people do.

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

CANON BEING SILENT IS NOT A CONTRADICTION. Only a canon line that says OTHERWISE is one. If two
siblings are both ten, "the twins" is a reasonable reading, not an error; if canon does not say
what someone ate, anything they ate is fine. Report what canon RULES OUT, never what it merely
fails to mention. Times in canon are exact to the minute — round them as a person would ("9:30",
"around midnight") without calling that a contradiction.

Every finding MUST cite the scene day and the canon line it contradicts. If you cannot cite,
do not report it. Being thorough matters, but a false positive is worse than a miss here —
this is a pass/fail gate on the simulation's core promise."""

SCHEMA_HINT = """Reply with ONE JSON object:
{"findings": [{"kind": "EVENT-TIME|IDENTITY|REPEAT|STATE",
               "severity": "major|minor",
               "scope": "canon" or "texture",
               "day": <sim day of the offending scene>,
               "quote": "the exact phrase from the scene",
               "canon": "the canon line it contradicts",
               "why": "one sentence"}],
 "verdict": "PASS" or "FAIL",
 "note": "one or two sentences on the family's overall coherence"}
Return "verdict": "PASS" with an empty findings list if the month holds together."""


VERIFY_SYSTEM = """You are checking ONE claimed continuity error in a life simulation, and your
job is to REFUTE it. A first-pass reader flags too much; you decide what survives.

Refute it — answer refuted=true — if ANY of these hold:
- the times agree once read as a person reads them (21:29 IS "9:30 at night"; 23:56 IS "around
  midnight" and IS "night"; a scene may say "about nine" for 21:29);
- the scene and canon are compatible even if differently worded, or the scene simply says less;
- canon is SILENT on the point and the scene is merely adding texture;
- the "contradiction" is only that a scene mentions something canon never recorded;
- the reasoning given for it actually shows the scene is fine.

Confirm it — refuted=false — only when canon RULES OUT what the scene says: a different day, a
different time of day, a person canon says does not exist or is elsewhere, a state canon
contradicts. When you are unsure, refute. A false alarm is worse than a miss here.

NEVER CALCULATE A DATE. The scene's date and weekday are given to you above the claim, and every
canon line carries its own. If your refutation depends on which day of the week something fell
on, or on how many days apart two things were, use the dates as printed — do not derive them.
A refutation built on a date you worked out yourself is not a refutation, it is a guess, and
this check exists precisely because one of those let a real contradiction through: it asserted
"day 7 is Monday 12 Jan, well after the discharge" when day 7 was Thursday the 8th and the
patient was still in a hospital bed.

"When unsure, refute" applies to JUDGEMENT — whether a limp counts as a contradiction of a
healed injury. It does not licence inventing a fact. If you cannot refute the claim from what
is printed in front of you, answer refuted=false.

Reply with ONE JSON object: {"refuted": true|false, "why": "one sentence"}"""


_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


def _bad_weekday(why: str, day: int) -> str | None:
    """Does this refutation name a weekday for the scene's day, and get it wrong?

    Narrow on purpose. It only fires when the sentence ties a weekday to *this
    day number* — "day 7 is Monday" — because a refutation may legitimately
    mention other days ("canon shows the discharge on Friday") and flagging
    those would make the guard useless. Returns the complaint, or None."""
    real = _WEEKDAYS[to_datetime(day * SECONDS_PER_DAY).weekday()]
    low = " ".join((why or "").lower().split())
    for pattern in (f"day {day} is ", f"day {day} was ", f"day {day} falls on ",
                    f"day {day}, ", f"day {day} ("):
        i = low.find(pattern)
        if i < 0:
            continue
        tail = low[i + len(pattern): i + len(pattern) + 60]
        for wd in _WEEKDAYS:
            if wd in tail and wd != real:
                return (f"it says day {day} is {wd.capitalize()}; day {day} is "
                        f"{real.capitalize()}")
    return None


class Verdict(BaseModel):
    model_config = {"extra": "ignore"}
    refuted: bool = True
    why: str = ""


class Finding(BaseModel):
    kind: str
    scope: str = "canon"  # canon = contradicts the log; texture = contradicts another scene
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


def build_canon(log: EventLog, hh, people, block, until_day: int | None = None) -> list[str]:
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
        if until_day is not None and e.sim_time // SECONDS_PER_DAY > until_day:
            continue
        who = people[p["person"]].name
        when = to_datetime(e.sim_time)
        lines.append(
            f"  {when:%a %d %b %Y, %H:%M} — {who} was present:"
            f" \"{p.get('claim', {}).get('text', '')}\""
        )
        seen = True
    if not seen:
        lines.append("  (nobody in this family witnessed anything first-hand)")
    lines.append("")
    lines.append("EVERYTHING ELSE THAT HAPPENED TO THEM (institutional and mechanical facts):")
    skip = {"scene.morning", "scene.reaction", "scene.skipped", "scene.invalid_ref",
            "memory.formed", "mood.delta", "plan.revised", "llm.response", "info.heard",
            "trip.start", "trip.end", "activity.start", "run.meta",
            # An interview answer is a thing a person SAID, exactly like scene
            # prose, and it was landing in canon — so the check that is supposed
            # to decide "the day-3 interview matches canon" was quietly treating
            # the interview as the truth it should have been measured against.
            # It is judged instead, in build_scenes below.
            "conversation.held"}
    facts = 0
    for e in log.events():
        if e.type in skip:
            continue
        # A scene's own message is not ground truth. It reaches the judge
        # attached to the scene that wrote it, where it belongs.
        if e.provenance == "llm_scene":
            continue
        if until_day is not None and e.sim_time // SECONDS_PER_DAY > until_day:
            continue
        p = e.payload
        touched = {p.get(k) for k in ("person", "sender", "complainant", "victim", "entity_id")}
        touched |= set(p.get("recipients") or []) | set(p.get("participants") or [])
        if not (touched & members) and p.get("household") != hh.id:
            continue
        line = _humanize(e.type, p, block, people)
        if line:
            lines.append(f"  {to_datetime(e.sim_time):%a %d %b %Y, %H:%M} — {line}")
            facts += 1
    if not facts:
        lines.append("  (nothing institutional touched them)")
    return lines


def build_scenes(log: EventLog, hh, people) -> list[tuple[int, str]]:
    """Each scene as the model actually asserted it: prose plus the memories it
    wrote and the messages it sent. Those payloads are where the first soak's
    invented colleague lived — she never appeared in a transcript, only in a
    memory summary, so a reader given prose alone cannot find her."""
    members = set(hh.member_ids)
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
        elif (e.type == "conversation.held" and e.payload.get("with") == "journalist"
              and e.payload.get("person") in members):
            # V0's exit says "the day-3 interview matches canon", and nothing
            # checked it: build_scenes collected only scene.morning and
            # scene.reaction, so the one answer the clause is about was never
            # put in front of the judge. It is prose a person asserted, judged
            # on the same terms as any other.
            who = people.get(e.payload.get("person"))
            scenes[e.seq] = {
                "t": e.sim_time,
                "kind": "INTERVIEW",
                "narration": f"A journalist asked: {e.payload.get('question', '')}",
                "transcript": f"{who.name if who else e.payload.get('person')}: "
                              f"{e.payload.get('answer', '')}",
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
    for stream in (sys.stdout, sys.stderr):  # findings quote Marathi scene text
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", required=True, type=Path)
    ap.add_argument("--household", default="hh:000")
    # All three default to None so that "the caller said so" and "nobody said
    # anything" are distinguishable: only an explicit value is worth refusing
    # over when it disagrees with the log.
    ap.add_argument("--seed", type=int, default=None, help="normally taken from run.meta")
    ap.add_argument("--households", type=int, default=None, help="normally taken from run.meta")
    ap.add_argument("--block", default=None, help="kasba | oldcity (normally taken from run.meta)")
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

    # The roster comes from the log. This used to be load_for(args.households,
    # args.block) against defaults of 80/kasba with no run.meta read at all,
    # which is the exact failure that cost a soak in audit_run.py and was never
    # ported here: `hh:000` exists in every world this repo can synthesize, so
    # pointing it at a 12,000-household oldcity run does not error. It assembles
    # a kasba family as CANON, pulls oldcity scenes for the same id, and asks a
    # premium judge to find contradictions between two different worlds. It
    # finds plenty. All artifacts.
    log = EventLog(args.db)
    try:
        block, hhs, people, meta = world_for_log(
            log, args.seed, args.households, args.block,
            fallback_seed=108, fallback_households=80,
        )
    except RosterMismatch as exc:
        print(f"continuity: {exc}", file=sys.stderr)
        return 2
    if not meta:
        print("continuity: this log has no run.meta, so the roster is uncorroborated — "
              "every name below is a guess from --seed/--households.", file=sys.stderr)
    hh = next((h for h in hhs if h.id == args.household), None)
    if hh is None:
        print(f"continuity: no such household: {args.household}", file=sys.stderr)
        return 2
    print(f"continuity: {block.name}, {len(people):,} people, household {args.household}",
          file=sys.stderr)
    try:
        scenes = build_scenes(log, hh, people)
        # Canon is rebuilt per batch and bounded by that batch's last day: a
        # judge shown the whole month faults a day-19 scene against a day-26
        # event, which is not a contradiction, it is the future.
        canon_by_day = {
            chunk_end: build_canon(log, hh, people, block, until_day=chunk_end)
            for chunk_end in {
                scenes[min(i + args.batch, len(scenes)) - 1][0]
                for i in range(0, len(scenes), args.batch)
            }
        } if scenes else {}
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
            "CANON (ground truth, up to this batch's last day):\n"
            + "\n".join(canon_by_day[chunk[-1][0]])
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

    # Every canon-scoped finding must survive a skeptic. The first-pass reader
    # flags rounding as drift and silence as contradiction; by the fourth soak
    # the simulation's error rate was below the reader's false-positive rate,
    # which makes an unverified count meaningless in both directions.
    survived: list[Finding] = []
    refuted: list[tuple[Finding, str]] = []
    for f in [x for x in findings if x.scope != "texture"]:
        # The claim used to be labelled "day 7" and nothing else, while every
        # canon line carries a real date — so the skeptic had to do the
        # arithmetic itself, and once it got it wrong ("day 7 is Monday 12 Jan")
        # it refuted a true finding with an invented fact. Print the date.
        scene_dt = to_datetime(f.day * SECONDS_PER_DAY)
        body = "\n".join([
            "CANON:",
            *canon_by_day[max(canon_by_day)],
            "",
            f"CLAIMED ERROR — the scene is on day {f.day}, which is "
            f"{scene_dt:%A %d %B %Y}. ({f.kind})",
            f"  scene says : {f.quote}",
            f"  canon line : {f.canon}",
            f"  reasoning  : {f.why}",
            "",
            "Try to refute it. Use the dates as printed; do not calculate any.",
        ])
        try:
            v = gw.call(
                "qc_judge",
                [{"role": "system", "content": VERIFY_SYSTEM}, {"role": "user", "content": body}],
                Verdict, temperature=0.0, max_tokens=1200,
                model_override=cfg.model_premium,
            ).parsed
        except Exception as err:  # noqa: BLE001 — an unverifiable finding stands
            survived.append(f)
            refuted.append((f, f"verifier failed ({type(err).__name__}) — kept"))
            continue
        bad_date = _bad_weekday(v.why, f.day)
        if v.refuted and bad_date:
            # A refutation is only worth what its facts are worth. If it names a
            # weekday for this scene and names the wrong one, its reasoning is
            # built on something untrue, so the finding stands and the reason is
            # recorded. This is the exact failure it exists to catch.
            survived.append(f)
            refuted.append((f, f"REFUTATION REJECTED — {bad_date}. Original: {v.why}"))
            continue
        (refuted.append((f, v.why)) if v.refuted else survived.append(f))
    canon_hits = survived
    texture = [f for f in findings if f.scope == "texture"]
    major = [f for f in canon_hits if f.severity == "major"]
    print(f"\n=== continuity read: {args.household} ({hh.surname} family), {args.db} ===")
    print(f"{len(scenes)} scenes over days {scenes[0][0]}-{scenes[-1][0]}, "
          f"judged by {cfg.model_premium} in {len(verdicts)} batches\n")
    if unread:
        print("  !! batches the judge could not read (NOT a pass for those days):")
        for u in unread:
            print(f"     {u}")
        print()
    if not canon_hits:
        verdict = "PASS" if not unread else "PARTIAL"
        head = ("VERDICT: PASS — no canon contradictions." if not unread
                else "VERDICT: PARTIAL — no canon contradictions in the batches read.")
        if refuted:
            head += (f"  ({len(refuted)} first-pass finding(s) refuted by the skeptic; "
                     f"see the JSON for why.)")
        if texture:
            head += f"  ({len(texture)} texture nit(s) below; the log is silent on those.)"
        print(head + "\n")
    else:
        verdict = "FAIL"
        print(
            f"VERDICT: FAIL — {len(canon_hits)} canon contradictions ({len(major)} major)"
            + (f", plus {len(texture)} texture nit(s)" if texture else "") + "\n"
        )
    shown = canon_hits + texture
    for f in sorted(shown, key=lambda x: (x.scope == "texture", x.severity != "major", x.day)):
        print(f"  [{f.scope}/{f.severity}] {f.kind} day {f.day}")
        print(f"      scene : {f.quote[:150]}")
        print(f"      canon : {f.canon[:150]}")
        print(f"      why   : {f.why[:150]}")
    for n in notes:
        print(f"\n  note: {n}")
    if args.out:
        args.out.write_text(
            orjson.dumps(
                # The verdict and the findings must agree in the file, not only
                # on the console. This used to dump the RAW first-pass findings
                # under one key called "findings", so a run whose skeptic
                # refuted everything wrote PASS to stdout and three "major
                # canon" contradictions to the artifact that docs/soaks/README
                # says exists "so the numbers in the prose can be checked rather
                # than trusted". Whoever read the JSON later would have read the
                # opposite of the result.
                {"household": args.household, "db": str(args.db),
                 "scenes": len(scenes), "verdict": verdict,
                 "canon_contradictions": [f.model_dump() for f in canon_hits],
                 "refuted_by_skeptic": [{**f.model_dump(), "refuted_because": why}
                                        for f, why in refuted],
                 "texture": [f.model_dump() for f in texture],
                 "batches_unread": unread,
                 "notes": notes},
                option=orjson.OPT_INDENT_2,
            ).decode(),
            encoding="utf-8",
        )
        print(f"\nwritten: {args.out}")
    return 1 if canon_hits else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except sqlite3.Error as err:
        print(f"continuity: {err}", file=sys.stderr)
        raise SystemExit(2) from err
