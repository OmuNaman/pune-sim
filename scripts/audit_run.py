"""Mechanical, LLM-free audit of a run's event log.

The 30-day soak was read by hand. That found real defects and missed others —
the same sweep run mechanically turned up 11 verbatim memory duplications where
the hand-read found 1, and an entire defect class nobody had looked for (five
messages addressed to people who do not exist). Hand-reading also cannot be
repeated cheaply, which means every soak would start its audit from scratch.

So every finding in runs/soak/REPORT.md is a probe here, and each probe is a
pass/fail assertion against the log plus the regenerated roster. The log is the
only truth (law 1) and the population is a pure function of the seed (D0), so
the audit needs nothing but a db path and a seed.

    uv run python scripts/audit_run.py --db runs/soak2/events.db --seed 108

Exit 0 = no FAIL, 1 = at least one FAIL, 2 = the audit could not run (bad db,
seed disagreement, roster failure). WARN never fails the build unless --strict.
"""

from __future__ import annotations

import argparse
import difflib
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import orjson

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from punesim.engine import CASUALTY_PREFIXES, NO_WORK
from punesim.institutions.procedures import ABSENT_ACTIVITIES
from punesim.kernel.timebase import SECONDS_PER_DAY
from punesim.minds.scene import _SELF_OUTPUT_TYPES
from punesim.population import synthesize
from punesim.world.block import Block, load_for

# Rate card, $/M tokens (openrouter, pinned 2026-08). Used only when the
# provider did not report a cost, and reported cost always wins.
RATES = {
    "deepseek/deepseek-v4-flash": (0.084, 0.168),
    "deepseek/deepseek-v4-pro": (0.4225, 0.845),
}
DEFAULT_RATE = (0.5, 1.0)

CLOCKWORK_ACTIVITIES = {"work", "driving_rounds", "school", "errand", "admitted"}
ABSENCE_WORDS = re.compile(
    r"\b(home|rest|sick|stay|stays|shelter|hospital|admitted|bed|ill|unwell|indoors|ghar)\b", re.IGNORECASE
)
# Words that are true for one day and wrong forever after. A memory is read
# for weeks, so any of these surviving into one is a dated falsehood waiting
# to be copied - 147 of the 520 memories in the second soak carried one.
RELATIVE_TIME_WORDS = re.compile(
    r"\b(yesterday|last night|last week|tonight|this morning|tomorrow|"
    r"kal|kalchi|kalcha|kalche|parva|aaj|raatri|sakali|"
    r"day before|next day|earlier today)\b",
    re.IGNORECASE,
)

SPEAKER_RE = re.compile(r"^\s*([^:\n]{1,40}?)\s*:", re.MULTILINE)
HONORIFIC_RE = re.compile(
    r"\b([A-Z][a-z]{2,15})\s+(tai|dada|kaka|kaki|mavshi|aji|ajoba|mama|mami|didi|bhau)\b"
)
# NOT bare "kal": in Marathi and Hindi it means yesterday AND tomorrow, and it
# appears in a third of all scenes, which destroys the signal.
YESTERDAY_RE = re.compile(r"\b(yesterday|last night|raatri|kal ratri|parva)\b", re.IGNORECASE)
TOPIC_RE = {
    "fire": re.compile(r"\b(fire|agni|aag)\b", re.IGNORECASE),
    "collision": re.compile(r"\b(accident|collision|apghat|apghaat)\b", re.IGNORECASE),
    "supply_cut": re.compile(r"(water cut|supply cut|pani\s+\w*\s*nahi)", re.IGNORECASE),
    "outage": re.compile(r"(outage|power cut|light gela|vij)", re.IGNORECASE),
}


# --------------------------------------------------------------------------- #


@dataclass
class Event:
    seq: int
    sim_time: int
    type: str
    payload: dict
    caused_by: int | None
    provenance: str

    @property
    def day(self) -> int:
        return self.sim_time // SECONDS_PER_DAY


@dataclass
class Result:
    probe: str
    status: str  # PASS | WARN | FAIL | SKIP
    headline: str
    hits: list[str] = field(default_factory=list)


class Audit:
    def __init__(
        self, events: list[Event], people: dict, households: list, n_days: int,
        *, partial_last_day: bool = False, followed: set[str] | None = None,
    ):
        self.events = events
        self.people = people
        self.households = households
        self.n_days = n_days
        self.by_seq = {e.seq: e for e in events}
        self.by_type: dict[str, list[Event]] = defaultdict(list)
        for e in events:
            self.by_type[e.type].append(e)
        # Snapshot before any probe touches the defaultdict and conjures keys.
        self.types = frozenset(self.by_type)
        self.hh_of = {p.id: p.household_id for p in people.values()}
        self.results: list[Result] = []
        # Auditing a db while the run is still writing it is legitimate — you
        # want early signal — but the last day is half-built: its INFO pass and
        # nightly tick have not run, so probes that ask "did this get a
        # consequence?" would fail on a consequence that is merely not yet due.
        self.last_day = max((e.day for e in events), default=0)
        self.partial_last_day = partial_last_day
        self.followed = set(followed or ())

    # -- helpers ---------------------------------------------------------- #

    def add(self, probe: str, status: str, headline: str, hits: list[str] | None = None) -> None:
        self.results.append(Result(probe, status, headline, hits or []))

    def norm_id(self, pid: str | None) -> str:
        """The model emits both `person:002.1` and a bare `002.1`."""
        if not pid:
            return ""
        return pid if ":" in pid else f"person:{pid}"

    @staticmethod
    def _flat(s: str) -> str:
        return " ".join((s or "").lower().split())

    def scene_of(self, e: Event) -> Event | None:
        cause = self.by_seq.get(e.caused_by) if e.caused_by else None
        return cause if cause is not None and cause.type.startswith("scene.") else None

    # -- probes ----------------------------------------------------------- #

    def dup(self, kind: str, evs: list[Event], key_of, text_of) -> None:
        """Consecutive-day verbatim repetition — the model completing a pattern
        it was shown rather than writing something new."""
        seen: dict[tuple, dict[int, list[tuple[str, int]]]] = defaultdict(lambda: defaultdict(list))
        for e in evs:
            seen[key_of(e)][e.day].append((self._flat(text_of(e)), e.seq))
        exact: list[str] = []
        near: list[str] = []
        for who, by_day in sorted(seen.items(), key=lambda kv: str(kv[0])):
            for d in sorted(by_day):
                if d - 1 not in by_day:
                    continue
                prev = by_day[d - 1]
                for text, seq in by_day[d]:
                    for ptext, pseq in prev:
                        if not text or not ptext:
                            continue
                        if text == ptext:
                            exact.append(f"{who} d{d - 1}->d{d} seq{pseq}=={seq}: {text[:70]}")
                            break
                        if difflib.SequenceMatcher(None, ptext, text).ratio() > 0.9:
                            near.append(f"{who} d{d - 1}->d{d} seq{pseq}~{seq}: {text[:70]}")
                            break
        self.add(
            f"DUP-{kind}",
            "FAIL" if exact else "PASS",
            f"{len(exact)} exact consecutive-day duplicates (limit 0)",
            exact,
        )
        if near:
            self.add(f"DUP-{kind}-NEAR", "WARN", f"{len(near)} near-duplicates (>0.9 similar)", near)

    def probe_duplication(self) -> None:
        self.dup(
            "MEM",
            self.by_type["memory.formed"],
            lambda e: e.payload.get("person"),
            lambda e: e.payload.get("summary", ""),
        )
        self.dup(
            "MSG",
            self.by_type["message.sent"],
            lambda e: (
                e.payload.get("sender"),
                tuple(sorted(self.norm_id(r) for r in e.payload.get("recipients") or [])),
            ),
            lambda e: e.payload.get("text", ""),
        )

    def probe_self_echo(self) -> None:
        """The soak's central defect: a scene shown its own previous output.
        Detectable from the log alone — if a PERSON's memory or message text is
        reproduced by a LATER scene, the loop is back.

        Keyed per person and across scenes on purpose. Grouping by household
        instead reported one scene handing five witnesses of the same power cut
        the same sentence — lazy, but not the echo this probe exists to catch.
        """
        per: dict[str, list[tuple[int, int, str]]] = defaultdict(list)  # person -> (scene, seq, text)
        same_scene: list[str] = []
        for e in self.by_type["memory.formed"] + self.by_type["message.sent"]:
            pid = self.norm_id(e.payload.get("person") or e.payload.get("sender"))
            text = self._flat(e.payload.get("summary") or e.payload.get("text") or "")
            if pid in self.people and len(text) > 25:
                per[pid].append((e.caused_by or -e.seq, e.seq, text))
        repeats: list[str] = []
        for pid, items in sorted(per.items()):
            first: dict[str, tuple[int, int]] = {}
            for scene, seq, text in items:
                prior = first.get(text)
                if prior is None:
                    first[text] = (scene, seq)
                elif prior[0] != scene:
                    repeats.append(f"{pid} seq{prior[1]}=={seq}: {text[:70]}")
        # One scene, one sentence, several members — a different smell.
        by_scene: dict[int, Counter] = defaultdict(Counter)
        for pid, items in per.items():
            for scene, _seq, text in items:
                by_scene[scene][text] += 1
        for scene, texts in sorted(by_scene.items()):
            for text, n in texts.items():
                if n >= 3:
                    same_scene.append(f"scene {scene}: {n} people given \"{text[:60]}\"")
        self.add(
            "SELF-ECHO",
            "FAIL" if repeats else "PASS",
            f"{len(repeats)} texts a person's later scene reproduced verbatim (limit 0)",
            repeats,
        )
        if same_scene:
            self.add(
                "SCENE-COPY-PASTE", "WARN",
                f"{len(same_scene)} scenes gave 3+ members an identical memory",
                same_scene,
            )

    def probe_identity(self) -> None:
        dangling: list[str] = []
        for e in self.by_type["message.sent"]:
            refs = [e.payload.get("sender"), *(e.payload.get("recipients") or [])]
            for r in refs:
                n = self.norm_id(r)
                if n.startswith("person:") and n not in self.people:
                    dangling.append(f"seq{e.seq} d{e.day}: {r}")
        self.add(
            "ID-INVENTED-REF",
            "FAIL" if dangling else "PASS",
            f"{len(dangling)} references to people who do not exist (limit 0)",
            dangling,
        )

        rejected = self.by_type["scene.invalid_ref"]
        self.add(
            "ID-REJECTED",
            "WARN" if rejected else "PASS",
            f"{len(rejected)} scenes had an invented id dropped at the gate",
            [f"seq{e.seq} d{e.day} {e.payload.get('household')}: {e.payload.get('ids')}" for e in rejected],
        )

        child: list[str] = []
        for e in self.by_type["message.sent"]:
            scene = self.scene_of(e)
            if scene is None:
                continue  # institutional call, not an authored one
            text = f"{scene.payload.get('narration', '')}\n{scene.payload.get('transcript', '')}"
            hid = scene.payload.get("household")
            for r in e.payload.get("recipients") or []:
                p = self.people.get(self.norm_id(r))
                if p is None or p.household_id == hid or p.age >= 12:
                    continue
                if not re.search(rf"\b{re.escape(p.given)}\b", text, re.IGNORECASE):
                    child.append(f"seq{e.seq} d{e.day}: {p.name} ({p.age}) never named in the scene")
        self.add(
            "ID-CHILD-CORRESPONDENT",
            "FAIL" if child else "PASS",
            f"{len(child)} messages to an unnamed child outside the household (limit 0)",
            child,
        )

        bad_speakers: list[str] = []
        for e in self.by_type["scene.morning"] + self.by_type["scene.reaction"]:
            hid = e.payload.get("household")
            names = {
                self.people[m].given.lower()
                for h in self.households if h.id == hid for m in h.member_ids
            }
            for label in SPEAKER_RE.findall(e.payload.get("transcript", "") or ""):
                if len(label.split()) > 3:
                    continue
                first = label.split()[0].lower() if label.split() else ""
                if first and first not in names and ":" not in label and "person" in label.lower():
                    bad_speakers.append(f"seq{e.seq} d{e.day} {hid}: speaker label {label!r}")
        self.add(
            "ID-SPEAKER-ROSTER",
            "FAIL" if bad_speakers else "PASS",
            f"{len(bad_speakers)} transcript speakers labelled with an id (limit 0)",
            bad_speakers,
        )

        given = {p.given.lower() for p in self.people.values()}
        minors = {p.given.lower() for p in self.people.values() if p.age < 18}
        honorifics: list[str] = []
        for e in self.by_type["scene.morning"] + self.by_type["scene.reaction"]:
            text = f"{e.payload.get('narration', '')}\n{e.payload.get('transcript', '')}"
            for name, hon in HONORIFIC_RE.findall(text):
                low = name.lower()
                if low not in given:
                    honorifics.append(f"seq{e.seq} d{e.day}: '{name} {hon}' matches nobody")
                elif low in minors:
                    honorifics.append(f"seq{e.seq} d{e.day}: '{name} {hon}' is a minor's name")
        self.add(
            "ID-HONORIFIC",
            "WARN" if honorifics else "PASS",
            f"{len(honorifics)} honorific-addressed names outside the roster (heuristic)",
            honorifics[:20],
        )

    def probe_spotlight(self) -> None:
        by_day: dict[int, set[str]] = defaultdict(set)
        for e in self.by_type["scene.morning"]:
            by_day[e.day].add(e.payload.get("household", "?"))
        if not by_day:
            self.add("SPOTLIGHT-STREAK", "SKIP", "no morning scenes in this run")
            return
        if self.n_days < 12:
            self.add("SPOTLIGHT-STREAK", "SKIP", f"run is {self.n_days} days; streaks need >= 12")
        else:
            streak: dict[str, int] = defaultdict(int)
            best: dict[str, int] = defaultdict(int)
            for d in range(min(by_day), max(by_day) + 1):
                today = by_day.get(d, set())
                for hid in set(streak) | today:
                    streak[hid] = streak[hid] + 1 if hid in today else 0
                    best[hid] = max(best[hid], streak[hid])
            free = {h: n for h, n in best.items() if h not in self.followed}
            worst = sorted(free.items(), key=lambda kv: (-kv[1], kv[0]))[:5]
            top = worst[0][1] if worst else 0
            pinned = sorted(h for h in best if h in self.followed)
            self.add(
                "SPOTLIGHT-STREAK",
                "FAIL" if top > 10 else "PASS",
                f"longest streak among UNFOLLOWED households {top} (limit 10)"
                + (f"; {len(pinned)} pinned by --follow and exempt" if pinned else ""),
                [f"{h}: {n} days running" for h, n in worst if n > 5]
                + [f"(followed by design: {', '.join(pinned)})" if pinned else ""],
            )
        last10 = sorted(by_day)[-10:]
        distinct = len({h for d in last10 for h in by_day[d]})
        self.add(
            "SPOTLIGHT-VARIETY",
            "WARN" if distinct < 8 else "PASS",
            f"{distinct} distinct households in the last {len(last10)} days (want >= 8)",
        )
        ever = {h for s in by_day.values() for h in s}
        never = sorted({h.id for h in self.households} - ever)
        # A run can only reach as many households as it rendered scenes; judge
        # coverage against that ceiling, not against the whole block.
        slots = sum(len(s) for s in by_day.values())
        reachable = min(len(self.households), slots)
        self.add(
            "SPOTLIGHT-COVERAGE",
            "WARN" if len(ever) < 0.75 * reachable else "PASS",
            f"{len(ever)}/{len(self.households)} households on camera at least once "
            f"({slots} slots spent; ceiling {reachable})",
            [f"never rendered: {', '.join(never[:25])}"] if never and len(ever) < reachable else [],
        )

    def probe_pressure(self) -> None:
        counts = Counter(
            (e.payload["person"], e.payload["pressure"]) for e in self.by_type["pressure.crossed"]
        )
        refires = [f"{p} {d}: {n} crossings" for (p, d), n in sorted(counts.items()) if n >= 3]
        twice = [f"{p} {d}" for (p, d), n in sorted(counts.items()) if n == 2]
        self.add(
            "PRESSURE-REFIRE",
            "FAIL" if refires else "PASS",
            f"{len(refires)} (person, dimension) pairs crossed 3+ times (limit 0)",
            refires,
        )
        if twice:
            self.add("PRESSURE-REFIRE-TWICE", "WARN", f"{len(twice)} pairs crossed exactly twice", twice[:20])

        crossed = {
            e.payload["person"] for e in self.by_type["pressure.crossed"]
            if e.payload.get("pressure") == "p_financial"
        }
        by_occ: dict[str, list[str]] = defaultdict(list)
        for p in self.people.values():
            if p.occupation in NO_WORK or p.occupation == "student" or p.age < 18:
                continue
            by_occ[p.occupation].append(p.id)
        bad = []
        for occ, ids in sorted(by_occ.items()):
            if len(ids) < 5:
                continue
            share = sum(1 for i in ids if i in crossed) / len(ids)
            if share > 0.5:
                bad.append(f"{occ}: {share:.0%} of {len(ids)} crossed p_financial")
        self.add(
            "PRESSURE-CLASS",
            "FAIL" if bad else "PASS",
            f"{len(bad)} occupation classes with >50% financially crossed (limit 0)",
            bad,
        )

        loans = self.by_type["loan.taken"]
        interest = self.by_type["loan.interest"]
        spiral = [
            f"{hid}: {n} interest accruals and no repayment path"
            for hid, n in Counter(e.payload.get("household") for e in interest).items()
            if n >= 3
        ]
        self.add(
            "DEBT-SPIRAL",
            "WARN" if spiral else "PASS",
            f"{len(loans)} loans taken, {len(interest)} interest accruals; {len(spiral)} spiralling",
            spiral,
        )

    def probe_activity_vocab(self) -> None:
        free = Counter(
            e.payload.get("activity", "")
            for e in self.by_type["activity.start"]
            if e.payload.get("activity") not in CLOCKWORK_ACTIVITIES
            and e.payload.get("activity") not in ABSENT_ACTIVITIES
        )
        absence_shaped = sorted(s for s in free if s and ABSENCE_WORDS.search(s))
        self.add(
            "ACTIVITY-VOCAB",
            "WARN" if absence_shaped else "PASS",
            f"{sum(free.values())} free-text activities ({len(free)} distinct); "
            f"{len(absence_shaped)} look like an absence but are counted as worked",
            absence_shaped[:20],
        )

    def probe_hazards(self) -> None:
        hazards = [
            e for e in self.events
            if e.type.startswith("hazard.")
            and not (self.partial_last_day and e.day == self.last_day)
        ]
        if not hazards:
            self.add("HAZARD-PERCEPT", "SKIP", "no hazards in this run")
            return
        no_percept, wrong_amb, no_cond, rows = [], [], [], []
        for h in hazards:
            percepts = sum(1 for e in self.by_type["info.heard"] if e.caused_by == h.seq)
            ambs = sum(1 for e in self.by_type["ambulance.dispatched"] if e.caused_by == h.seq)
            conds = sum(1 for e in self.by_type["condition.set"] if e.caused_by == h.seq)
            parts = h.payload.get("participants") or []
            rows.append(
                f"d{h.day:02d} {h.type} sev={h.payload.get('severity')} "
                f"percepts={percepts} amb={ambs} cond={conds} hurt={len(parts)}"
            )
            if percepts == 0:
                no_percept.append(f"seq{h.seq} d{h.day} {h.type}: nobody perceived it")
            if ambs and not h.type.startswith(CASUALTY_PREFIXES):
                wrong_amb.append(f"seq{h.seq} d{h.day} {h.type}: {ambs} ambulance(s)")
            if parts and conds == 0:
                no_cond.append(f"seq{h.seq} d{h.day} {h.type}: {len(parts)} hurt, no condition set")
        self.add("HAZARD-PERCEPT", "FAIL" if no_percept else "PASS",
                 f"{len(no_percept)} of {len(hazards)} hazards perceived by nobody (limit 0)",
                 no_percept + rows)
        self.add("HAZARD-AMBULANCE", "FAIL" if wrong_amb else "PASS",
                 f"{len(wrong_amb)} ambulances for non-casualty hazards (limit 0)", wrong_amb)
        self.add("HAZARD-CONDITION", "FAIL" if no_cond else "PASS",
                 f"{len(no_cond)} hazards with victims but no condition (limit 0)", no_cond)

    def probe_info(self) -> None:
        heard = self.by_type["info.heard"]
        if not heard:
            self.add("INFO-ECHO", "SKIP", "no rumours in this run")
            return
        if not any("lineage" in e.payload for e in heard):
            # Pre-V1.1 logs carry no transmission chain, so "no echoes found"
            # would mean "cannot look" — which must never read as a pass.
            self.add("INFO-ECHO", "SKIP", "this log predates lineage on info.heard — cannot check")
        else:
            echoes = [
                f"seq{e.seq} d{e.day}: {e.payload['person']} is in its own chain"
                for e in heard
                if e.payload.get("person") in (e.payload.get("lineage") or [])
            ]
            self.add("INFO-ECHO", "FAIL" if echoes else "PASS",
                     f"{len(echoes)} of {len(heard)} hearings came back to their own teller (limit 0)",
                     echoes[:20])

        fields = ("text", "quantity", "specificity", "veracity", "blame", "ops")
        witness: dict[tuple[str, str], dict] = {}
        corrupted: list[str] = []
        for e in sorted(heard, key=lambda x: x.seq):
            key = (e.payload.get("person"), e.payload.get("claim_key"))
            claim = e.payload.get("claim", {})
            if e.payload.get("channel") == "witness":
                witness.setdefault(key, claim)
            elif key in witness:
                seen = witness[key]
                changed = any(seen.get(f) != claim.get(f) for f in fields)
                enriches = (
                    claim.get("veracity") == "true"
                    and claim.get("specificity", 0) > seen.get("specificity", 0)
                )
                if changed and not enriches:
                    corrupted.append(f"seq{e.seq} d{e.day}: {key[0]} told a different story than they saw")
        # NB: the log records what was SAID; stickiness lives in InfoState, so
        # this counts opportunities, not confirmed corruption. WARN, not FAIL.
        self.add("INFO-WITNESS-HEARSAY", "WARN" if corrupted else "PASS",
                 f"{len(corrupted)} hearsay retellings reached a witness of the same event",
                 corrupted[:15])

        by_claim: dict[str, list[Event]] = defaultdict(list)
        for e in heard:
            by_claim[e.payload.get("claim_key", "?")].append(e)
        last_day = max(e.day for e in self.events)
        immortal, saturated, rows = [], [], []
        for key, evs in sorted(by_claim.items()):
            people_n = len({e.payload["person"] for e in evs})
            days = sorted({e.day for e in evs})
            hops = max(e.payload.get("claim", {}).get("hop", 0) for e in evs)
            variants = len({e.payload.get("claim", {}).get("text") for e in evs})
            rows.append(
                f"{key[:56]:56s} hearings={len(evs):4d} people={people_n:3d} "
                f"({people_n / max(1, len(self.people)):.0%}) d{days[0]}-d{days[-1]} hop={hops} variants={variants}"
            )
            # A claim born in the last few days has not had time to die; only
            # an OLD claim still spreading is evidence of a broken lifecycle.
            if days[-1] > last_day - 3 and days[0] <= last_day - 6:
                immortal.append(f"{key}: born d{days[0]}, still spreading on d{days[-1]} of {last_day}")
            if people_n > 0.9 * len(self.people):
                saturated.append(f"{key}: reached {people_n}/{len(self.people)}")
        self.add("RUMOR-IMMORTAL", "FAIL" if immortal else "PASS",
                 f"{len(immortal)} claims still alive in the last 3 days (limit 0)", immortal + rows)
        self.add("RUMOR-SATURATION", "FAIL" if saturated else "PASS",
                 f"{len(saturated)} claims past 90% of the block (limit 0)", saturated)

    def probe_scenes(self) -> None:
        rendered = len(self.by_type["scene.morning"]) + len(self.by_type["scene.reaction"])
        skipped = self.by_type["scene.skipped"]
        attempted = rendered + len(skipped)
        if not attempted:
            self.add("SCENE-SKIP-RATE", "SKIP", "no scenes in this run (clockwork only)")
            return
        rate = len(skipped) / attempted
        status = "FAIL" if rate > 0.10 else "WARN" if rate > 0.01 else "PASS"
        hazard_days = {e.day for e in self.events if e.type.startswith("hazard.")}
        hits = [
            f"seq{e.seq} d{e.day} {e.payload.get('household')}: {e.payload.get('reason', '')[:90]}"
            + ("  <- ON A HAZARD DAY" if e.day in hazard_days else "")
            for e in skipped
        ]
        self.add("SCENE-SKIP-RATE", status,
                 f"{len(skipped)}/{attempted} scenes skipped ({rate:.1%}; warn >1%, fail >10%)", hits)
        days_with = {e.day for e in self.by_type["scene.morning"]}
        quiet = [d for d in range(self.n_days) if d not in days_with]
        self.add("SCENE-EVERY-DAY", "WARN" if quiet else "PASS",
                 f"{len(quiet)} days rendered no morning scene at all",
                 [f"days: {quiet[:20]}"] if quiet else [])

    def probe_belief_actions(self) -> None:
        """Who changed their behaviour, and over what.

        Two 30-day soaks each had 255 of 306 people avoiding a place because
        the power had been off there — the largest behavioural event in either
        run, and nobody was watching. A belief that moves most of a block is
        either a genuine emergency or a modelling error, and it should have to
        say which."""
        actions = self.by_type["belief.action"]
        if not actions:
            self.add("BELIEF-ACTION-SCALE", "SKIP", "nobody acted on a rumour in this run")
            return
        by_claim: dict[str, Counter] = defaultdict(Counter)
        for e in actions:
            by_claim[e.payload.get("claim_key", "?")][e.payload.get("action", "?")] += 1
        rows, loud = [], []
        for key, acts in sorted(by_claim.items(), key=lambda kv: -sum(kv[1].values())):
            n = sum(acts.values())
            share = n / max(1, len(self.people))
            rows.append(f"{key[:52]:52s} {n:4d} people ({share:.0%}) {dict(acts)}")
            if share > 0.25:
                loud.append(f"{key}: {n} of {len(self.people)} people ({share:.0%}) {dict(acts)}")
        self.add(
            "BELIEF-ACTION-SCALE",
            "WARN" if loud else "PASS",
            f"{len(actions)} behaviour changes over {len(by_claim)} claims; "
            f"{len(loud)} claim(s) moved more than a quarter of the block",
            loud + rows,
        )

    def probe_memory_time(self) -> None:
        """A memory is read for weeks; a relative time word in one is true for a
        day and wrong forever after. absolutize() rewrites the ones it knows —
        this probe reports the ones it does not, because the vocabulary is open
        and a silent gap is exactly how the last drift survived a whole soak."""
        leftovers: list[str] = []
        for e in self.by_type["memory.formed"]:
            hits = RELATIVE_TIME_WORDS.findall(e.payload.get("summary", "") or "")
            if hits:
                leftovers.append(
                    f"seq{e.seq} d{e.day} {e.payload.get('person')}: "
                    f"{sorted({h.lower() for h in hits})} in \"{e.payload.get('summary', '')[:70]}\""
                )
        self.add(
            "MEMORY-RELATIVE-TIME",
            "WARN" if leftovers else "PASS",
            f"{len(leftovers)} memories still carry a relative time word",
            leftovers[:20],
        )

    def probe_talk(self) -> None:
        """Does anyone in this block ever talk to someone from another house?
        The first soak's answer was no, across 30 days and 306 people."""
        talks = [e for e in self.by_type["conversation.held"] if e.payload.get("participants")]
        if not self.by_type["scene.morning"]:
            self.add("TALK-COVERAGE", "SKIP", "clockwork run — no camera to render talk")
            return
        pairs = {tuple(sorted(e.payload["participants"])) for e in talks}
        days = {e.day for e in talks}
        cross = [
            e for e in talks
            if len({self.hh_of.get(self.norm_id(p)) for p in e.payload["participants"]}) > 1
        ]
        self.add(
            "TALK-COVERAGE",
            "WARN" if not cross else "PASS",
            f"{len(cross)} cross-household exchanges on {len(days)} of {self.n_days} days, "
            f"{len(pairs)} distinct pairs",
            [] if cross else ["nobody spoke to anyone outside their own household all run"],
        )

    def probe_cost(self) -> None:
        calls = self.by_type["llm.response"]
        if not calls:
            self.add("COST", "SKIP", "no LLM calls in this run")
            return
        reported = 0.0
        per_model: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])
        missing = 0
        for e in calls:
            usage = e.payload.get("usage") or {}
            if not usage:
                missing += 1
            model = e.payload.get("model", "?")
            row = per_model[model]
            row[0] += int(usage.get("prompt_tokens") or 0)
            row[1] += int(usage.get("completion_tokens") or 0)
            row[2] += 1
            reported += float(usage.get("cost") or 0.0)
        card = sum(
            (t[0] * RATES.get(m, DEFAULT_RATE)[0] + t[1] * RATES.get(m, DEFAULT_RATE)[1]) / 1e6
            for m, t in per_model.items()
        )
        total = reported or card
        per_day = total / max(1, self.n_days)
        rows = [
            f"{m}: {t[2]} calls, {t[0]:,} in / {t[1]:,} out" for m, t in sorted(per_model.items())
        ]
        rows.append(f"provider-reported ${reported:.4f} vs rate-card ${card:.4f}")
        if missing:
            rows.append(f"WARNING: {missing} calls carried no usage block — total is biased low")
        self.add("COST", "FAIL" if per_day > 1.0 else "PASS",
                 f"${per_day:.4f}/sim-day over {self.n_days} days (limit $1.00)", rows)

    def probe_temporal_drift(self) -> None:
        """Heuristic, WARN only. Detects day-offset drift ("yesterday's fire"
        for a fire five days ago). It cannot see time-of-day drift — an
        afternoon fire retold as a night fire reads identically here."""
        heard_by_person: dict[tuple[str, str], list[int]] = defaultdict(list)
        for e in self.by_type["info.heard"]:
            pred = e.payload.get("claim", {}).get("predicate", "")
            heard_by_person[(e.payload.get("person", ""), pred)].append(e.day)
        members: dict[str, tuple[str, ...]] = {h.id: h.member_ids for h in self.households}
        flags: list[str] = []
        for e in self.by_type["scene.morning"] + self.by_type["scene.reaction"]:
            text = f"{e.payload.get('narration', '')}\n{e.payload.get('transcript', '')}"
            if not YESTERDAY_RE.search(text):
                continue
            for topic, rx in TOPIC_RE.items():
                if not rx.search(text):
                    continue
                days = [
                    d
                    for m in members.get(e.payload.get("household", ""), ())
                    for d in heard_by_person.get((m, topic), [])
                    if d <= e.day
                ]
                if not days:
                    flags.append(f"seq{e.seq} d{e.day}: says 'yesterday' about a {topic} nobody heard of")
                elif e.day - max(days) > 1:
                    flags.append(
                        f"seq{e.seq} d{e.day}: 'yesterday' for a {topic} last heard d{max(days)}"
                        f" ({e.day - max(days)} days stale)"
                    )
        self.add("TEMPORAL-DRIFT", "WARN" if flags else "PASS",
                 f"{len(flags)} scenes said 'yesterday' about something older (heuristic)", flags[:20])

    def probe_prompt_hygiene(self) -> None:
        """Types the scene humanizer would render as a raw dict, and scene
        bookkeeping that must never reach a prompt."""
        from punesim.minds.scene import _humanize

        block_stub = Block([], [])
        unrendered = sorted(
            t
            for t in self.types
            if t not in _SELF_OUTPUT_TYPES
            and t not in ("trip.start", "trip.end", "activity.start", "llm.response")
            and not _humanize(t, self.by_type[t][0].payload, block_stub, self.people)
        )
        self.add(
            "PROMPT-COVERAGE",
            "WARN" if unrendered else "PASS",
            f"{len(unrendered)} event types render as nothing in a scene prompt",
            unrendered,
        )

    def run(self) -> None:
        self.probe_duplication()
        self.probe_self_echo()
        self.probe_identity()
        self.probe_spotlight()
        self.probe_pressure()
        self.probe_activity_vocab()
        self.probe_hazards()
        self.probe_info()
        self.probe_scenes()
        self.probe_belief_actions()
        self.probe_memory_time()
        self.probe_talk()
        self.probe_cost()
        self.probe_temporal_drift()
        self.probe_prompt_hygiene()


# --------------------------------------------------------------------------- #


class TooBig(Exception):
    """The requested window will not fit in memory; the message says what to do."""


# Every probe holds the whole window in memory at once, at roughly 1.1 kB per
# event with its payload parsed. That was free at 80 households; a 30-day run at
# 12k commits 232k events a day, so the same call would try to build a 7.6 GB
# list and die *after* the soak, which is the worst possible time to find out.
# Above this, refuse and say how to window it rather than OOM.
MAX_EVENTS_UNBOUNDED = 1_500_000


def load(db: Path, branch: int, since_day: int | None = None,
         until_day: int | None = None) -> list[Event]:
    q = ("SELECT seq, sim_time, type, payload, caused_by, provenance "
         "FROM event WHERE branch_id = ?")
    args: list = [branch]
    if since_day is not None:
        q += " AND sim_time >= ?"
        args.append(since_day * SECONDS_PER_DAY)
    if until_day is not None:
        q += " AND sim_time < ?"
        args.append((until_day + 1) * SECONDS_PER_DAY)
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        if since_day is None and until_day is None:
            n = con.execute(
                "SELECT COUNT(*) FROM event WHERE branch_id = ?", (branch,)
            ).fetchone()[0]
            if n > MAX_EVENTS_UNBOUNDED:
                last = con.execute(
                    "SELECT MAX(sim_time) FROM event WHERE branch_id = ?", (branch,)
                ).fetchone()[0] or 0
                days = last // SECONDS_PER_DAY
                raise TooBig(
                    f"{n:,} events across {days + 1} days would need about "
                    f"{n * 1.1 / 1e6:.1f} GB to audit in one pass. Audit a window "
                    f"instead, e.g. --since-day {max(0, days - 9)} --until-day {days}, "
                    "and run more than one window if you need the whole run."
                )
        rows = con.execute(q + " ORDER BY seq", args).fetchall()
    finally:
        con.close()
    return [Event(r[0], r[1], r[2], orjson.loads(r[3]), r[4], r[5]) for r in rows]


def main() -> int:
    # Detail lines quote Marathi and Hindi scene text. On a Windows console
    # redirected to a file that is cp1252, which raises mid-print and turns a
    # clean audit into exit 1 — the one thing the exit code must never lie about.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", required=True, type=Path)
    ap.add_argument("--seed", type=int, default=108)
    ap.add_argument("--households", type=int, default=80)
    ap.add_argument("--branch", type=int, default=0)
    ap.add_argument("--strict", action="store_true", help="promote WARN to FAIL")
    ap.add_argument("--follow", action="append",
                    help="household pinned on camera by the run (read from run.meta when present)")
    ap.add_argument("--live", action="store_true",
                    help="the run is still writing: skip the last, half-built day")
    ap.add_argument("--since-day", type=int, default=None,
                    help="audit only from this sim day (large runs cannot be held whole)")
    ap.add_argument("--until-day", type=int, default=None,
                    help="audit only up to and including this sim day")
    ap.add_argument("--details", type=int, default=12, help="hits printed per failing probe")
    args = ap.parse_args()

    if not args.db.exists():
        print(f"audit: no such db: {args.db}", file=sys.stderr)
        return 2
    try:
        events = load(args.db, args.branch, args.since_day, args.until_day)
    except TooBig as err:
        print(f"audit: {err}", file=sys.stderr)
        return 2
    except sqlite3.Error as err:
        print(f"audit: cannot read {args.db}: {err}", file=sys.stderr)
        return 2
    if args.since_day is not None or args.until_day is not None:
        lo = args.since_day if args.since_day is not None else 0
        hi = args.until_day if args.until_day is not None else "end"
        print(f"audit: window is days {lo}..{hi} — probes that reason about a whole "
              f"run (rumour lifetime, spotlight coverage) see only this slice")
    if not events:
        print(f"audit: {args.db} branch {args.branch} is empty", file=sys.stderr)
        return 2

    seed, households = args.seed, args.households
    block_name = "kasba"  # overridden by run.meta below; see engine/loop.py
    followed: set[str] = set(args.follow or [])
    meta = [e for e in events if e.type == "run.meta"]
    corroborated = False
    if meta:
        m = meta[0].payload
        block_name = m.get("block", "kasba")
        if m.get("seed") != seed or m.get("households") != households:
            print(
                f"audit: run.meta says seed={m.get('seed')} households={m.get('households')}, "
                f"you passed seed={seed} households={households}. Refusing to audit against the "
                "wrong roster — every identity probe would emit confident nonsense.",
                file=sys.stderr,
            )
            return 2
        corroborated = True
        for ref in m.get("follow") or []:
            followed.add(ref if ref.startswith("hh:") else "")
        followed.discard("")

    try:
        block = load_for(households, block_name)
        hhs, people = synthesize(seed, block, n_households=households)
    except Exception as err:  # noqa: BLE001 — a bad roster invalidates everything
        print(f"audit: cannot regenerate the population: {err}", file=sys.stderr)
        return 2

    n_days = max(e.day for e in events) - min(e.day for e in events) + 1
    audit = Audit(events, people, hhs, n_days, partial_last_day=args.live,
                  followed=followed)
    audit.run()

    order = {"FAIL": 0, "WARN": 1, "SKIP": 2, "PASS": 3}
    results = sorted(audit.results, key=lambda r: (order[r.status], r.probe))
    print(f"\n=== audit {args.db} ===")
    print(
        f"branch {args.branch} | seed {seed}{' (corroborated by run.meta)' if corroborated else ' (unverified)'}"
        f" | {households} households | {len(people)} people | {n_days} days | {len(events):,} events\n"
    )
    if audit.partial_last_day:
        print(
            f"NOTE: --live, so day {audit.last_day} is treated as half-built (its evening\n"
            "      passes have not run) and probes that need a whole day skip it.\n"
        )
    for r in results:
        print(f"{r.status:5s} {r.probe:24s} {r.headline}")
    fails = [r for r in results if r.status == "FAIL"]
    warns = [r for r in results if r.status == "WARN"]
    detail = [r for r in results if r.status in ("FAIL", "WARN") and r.hits]
    if detail:
        print("\n--- detail ---")
        for r in detail:
            print(f"\n[{r.status}] {r.probe}")
            for h in r.hits[: args.details]:
                print(f"    {h}")
            if len(r.hits) > args.details:
                print(f"    ... and {len(r.hits) - args.details} more")
    print(
        f"\n{len(results)} probes: {sum(1 for r in results if r.status == 'PASS')} pass, "
        f"{len(warns)} warn, {len(fails)} fail, "
        f"{sum(1 for r in results if r.status == 'SKIP')} skip"
    )
    if fails:
        return 1
    return 1 if (args.strict and warns) else 0


if __name__ == "__main__":
    raise SystemExit(main())
