"""V2 injection compiler: free text -> grounded, validated Injection.

"The city DM was killed in broad daylight near Shaniwar Wada" must land
without new engine code (architecture §9.4, novelty ladder). The LLM's only
job is TRANSLATION — English to a structured InjectionSpec whose every
reference is checked against canon (real place ids, real person ids). The
validator is the firewall: the model may hallucinate, the world may not.
Ground/validate/preview, one repair round with concrete suggestions, then
CompileError — never a silent guess.
"""

import difflib
from dataclasses import dataclass

from pydantic import BaseModel, Field

from ..engine import Injection
from ..llm.gateway import Gateway
from ..population.synth import Person
from ..world.block import Block


class ClaimSpec(BaseModel):
    model_config = {"extra": "forbid"}

    key: str = ""
    subject: str = ""
    predicate: str = "dangerous"
    topics: list[str] = Field(default_factory=list)
    quantity: float | None = None
    unit: str | None = None
    charge: float = 0.7
    specificity: float = 0.5
    veracity: str = "unknown"
    valence: float = -0.6


class InjectionSpec(BaseModel):
    """What the model returns; every ref is validated before it becomes real."""

    model_config = {"extra": "forbid"}

    day: int = 0
    time: str = "09:00"  # HH:MM
    type: str = "event.incident"
    place_ref: str | None = None
    participants: list[str] = Field(default_factory=list)
    severity: float | None = None
    claim: ClaimSpec | None = None
    narrative: str = ""  # one-sentence preview for the operator
    notes: str = ""  # what the model had to guess or drop


@dataclass(frozen=True)
class Compiled:
    injection: Injection
    spec: InjectionSpec
    preview: str


class CompileError(Exception):
    def __init__(self, errors: list[str], spec: InjectionSpec | None = None):
        super().__init__("; ".join(errors))
        self.errors = errors
        self.spec = spec


SYSTEM = """You compile a free-text scenario written by the operator of a life simulation of Pune's
old city into ONE structured injection. You translate; you do not invent world state.

Rules:
- place_ref MUST be one of the place ids in the WORLD CARD (pick the closest named real place;
  the card is the entire world — anything else does not exist).
- participants MUST be person ids from the PEOPLE directory. Only include residents who are
  DIRECTLY struck/involved. If the text names a figure who is not a resident (a District
  Magistrate, a celebrity), leave them out of participants and explain in notes — the event
  still happens at the place and residents will witness and spread it.
- type is a lowercase dotted class. Use "hazard.<family>.<kind>" for physical harm/danger
  (e.g. hazard.road.collision, hazard.violence.attack, hazard.fire.small) — hazard.* triggers
  ambulance/hospital/school reactions for participants. Use "info.rumor" when the text describes
  a RUMOR or claim spreading (fill in claim). Otherwise "event.<kind>".
- info.* claims: predicate is one lowercase word (contaminated, misappropriated, dangerous,
  adulterated...); topics from: water, health, safety, crime, fraud, power.
- severity 0..1 (0.2 minor, 0.5 serious, 0.8 grave). day/time: when it happens (sim day number,
  24h HH:MM). If the text gives no day, use the provided default day.
- narrative: ONE neutral sentence describing exactly what will be committed.
- notes: every liberty you took.
Output ONLY one JSON object with fields:
{"day": int, "time": "HH:MM", "type": "...", "place_ref": "..." or null,
 "participants": ["person:..."], "severity": 0.0-1.0 or null,
 "claim": {"key": "cl:...", "subject": "place id", "predicate": "...", "topics": [...],
           "quantity": number or null, "unit": "rupees"/"people"/null, "charge": 0..1,
           "specificity": 0..1, "veracity": "true"/"false"/"unknown", "valence": -1..1} or null,
 "narrative": "...", "notes": "..."}"""


def world_card(block: Block, people: dict[str, Person]) -> str:
    lines = ["WORLD CARD — places (id | name | kind):"]
    for p in block.places:
        if p.name:
            lines.append(f"{p.id} | {p.name} | {p.kind}")
    lines.append("")
    lines.append("PEOPLE directory (id | name | age | occupation):")
    for pid in sorted(people):
        q = people[pid]
        lines.append(f"{q.id} | {q.name} | {q.age} | {q.occupation}")
    return "\n".join(lines)


def _validate(spec: InjectionSpec, block: Block, people: dict[str, Person], max_day: int) -> list[str]:
    errors: list[str] = []
    if not 0 <= spec.day <= max_day:
        errors.append(f"day {spec.day} outside run range 0..{max_day}")
    try:
        hh, mm = spec.time.split(":")
        if not (0 <= int(hh) < 24 and 0 <= int(mm) < 60):
            raise ValueError
    except ValueError:
        errors.append(f"time '{spec.time}' is not HH:MM")
    if not spec.type or not spec.type.islower() or "." not in spec.type:
        errors.append(f"type '{spec.type}' must be a lowercase dotted class like hazard.road.collision")
    if spec.place_ref is not None and block.get(spec.place_ref) is None:
        sugg = _closest_places(spec.place_ref, block)
        errors.append(f"place_ref '{spec.place_ref}' does not exist; closest real places: {sugg}")
    for pid in spec.participants:
        if pid not in people:
            sugg = _closest_people(pid, people)
            errors.append(f"participant '{pid}' does not exist; closest real people: {sugg}")
    if spec.severity is not None and not 0.0 <= spec.severity <= 1.0:
        errors.append(f"severity {spec.severity} outside 0..1")
    if spec.type.startswith("info."):
        if spec.claim is None:
            errors.append("info.* injections need a claim")
        else:
            subj = spec.claim.subject or spec.place_ref
            if subj is None or block.get(subj) is None:
                errors.append(f"claim.subject '{spec.claim.subject}' is not a real place")
    return errors


def _closest_places(ref: str, block: Block, n: int = 3) -> str:
    names = {f"{p.name}": p.id for p in block.places if p.name}
    hits = difflib.get_close_matches(ref.split("/")[-1].replace("place:", ""), list(names), n=n, cutoff=0.0)
    return ", ".join(f"{names[h]} ({h})" for h in hits) or "none"


def _closest_people(ref: str, people: dict[str, Person], n: int = 3) -> str:
    names = {p.name: p.id for p in people.values()}
    hits = difflib.get_close_matches(ref, list(names), n=n, cutoff=0.0)
    return ", ".join(f"{names[h]} ({h})" for h in hits) or "none"


def _to_injection(spec: InjectionSpec, block: Block) -> Injection:
    hh, mm = spec.time.split(":")
    payload: dict = {}
    if spec.claim is not None and spec.type.startswith("info."):
        from .info import Claim, render_text

        c = spec.claim
        claim = Claim(
            key=c.key or f"cl:injected:d{spec.day}",
            subject=c.subject or spec.place_ref or "",
            predicate=c.predicate, text="",
            quantity=c.quantity, unit=c.unit, valence=c.valence, charge=c.charge,
            specificity=c.specificity, veracity=c.veracity, topics=tuple(c.topics),
        )
        payload["claim"] = {**claim.to_payload(), "text": render_text(claim, block)}
    return Injection(
        day=spec.day,
        time_s=int(hh) * 3600 + int(mm) * 60,
        type=spec.type,
        place=spec.place_ref,
        participants=tuple(spec.participants),
        severity=spec.severity,
        payload=payload,
    )


def _preview(spec: InjectionSpec, inj: Injection, block: Block, people: dict[str, Person]) -> str:
    place = block.get(inj.place) if inj.place else None
    who = ", ".join(people[p].name for p in inj.participants if p in people) or "no direct participants"
    lines = [
        f"day {inj.day}, {spec.time} — {inj.type}",
        f"where : {place.name if place and place.name else inj.place or '(no place)'}",
        f"who   : {who}",
    ]
    if inj.severity is not None:
        lines.append(f"severity: {inj.severity}")
    if "claim" in inj.payload:
        lines.append(f"claim : “{inj.payload['claim']['text']}”")
    lines.append(f"story : {spec.narrative}")
    if spec.notes:
        lines.append(f"notes : {spec.notes}")
    return "\n".join(lines)


def compile_injection(
    gateway: Gateway,
    block: Block,
    people: dict[str, Person],
    text: str,
    *,
    default_day: int = 0,
    max_day: int = 365,
) -> Compiled:
    """Free text -> validated Injection. One repair round, then CompileError."""
    card = world_card(block, people)
    user = f"{card}\n\nDefault day if unspecified: {default_day}\n\nOPERATOR TEXT:\n{text}\n\nCompile it."
    msgs = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]
    res = gateway.call("compile", msgs, InjectionSpec, temperature=0.1, max_tokens=1200)
    spec: InjectionSpec = res.parsed
    errors = _validate(spec, block, people, max_day)
    if errors:
        repair = (
            f"{card}\n\nYour previous compilation:\n{spec.model_dump_json()}\n\n"
            f"It failed validation:\n- " + "\n- ".join(errors)
            + f"\n\nOPERATOR TEXT:\n{text}\n\nFix every error and output the corrected JSON object only."
        )
        res = gateway.call(
            "compile",
            [{"role": "system", "content": SYSTEM}, {"role": "user", "content": repair}],
            InjectionSpec, temperature=0.1, max_tokens=1200,
        )
        spec = res.parsed
        errors = _validate(spec, block, people, max_day)
        if errors:
            raise CompileError(errors, spec)
    inj = _to_injection(spec, block)
    return Compiled(injection=inj, spec=spec, preview=_preview(spec, inj, block, people))
