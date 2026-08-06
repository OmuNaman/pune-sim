"""INFO v1 — information as a first-class simulated object (03-cognition §6).

A rumor is data, not code: a structured Claim that spreads person-to-person
over co-presence windows, mutates through mechanical distortion ops, and moves
belief through a closed-form logit update. The LLM never propagates anything —
it only *renders* conversations for spotlighted households, whose context
already contains what each member has heard (the `info.heard` events in the
log). Every hop carries `caused_by`, so the consequence cone shows a rumor's
exact path and drift, hop by hop.

Determinism: every draw is keyed_rng(run_seed, "info", ...) — injecting a
rumor never perturbs unrelated draws, and replay is hash-identical.
"""

import math
from dataclasses import dataclass, field, replace

from ..kernel.rng import keyed_rng
from ..kernel.timebase import SECONDS_PER_DAY
from ..population.synth import Person
from ..world.block import Block

# --- tuning (V1; calibrated on the exam block, revisit at 30-day soak) ------
P_SHARE_BASE = 0.7  # scaled by charge, sociability, freshness
FRESHNESS_TAU_DAYS = 4.0  # rumors die: e^(-age/tau) damps sharing
SATURATION_EXPOSURES = 3  # stop re-telling someone who has heard it this often
STIFLE_P = 0.3  # Maki-Thompson: telling a knower converts the teller to stifler
MAX_SHARES_PER_DAY = 6  # one mouth, finite chai breaks
MIN_OVERLAP_S = 300  # you don't gossip in a 4-minute overlap
MIN_CREDENCE_TO_SHARE = 0.3
LAMBDA = 2.9  # logit update step size — one vivid telling should genuinely move you
PRIOR_CREDENCE = 0.15  # first hearing of an unheard claim starts here
WITNESS_CREDENCE = 0.95

TRUST_W = {"witness": 1.0, "household": 0.9, "f2f": 0.6, "phone": 0.7, "official": 0.9}

# claim topic -> what a believer does about it (mechanical action lane; the
# scene lane renders the same decision in prose for spotlit households)
ACTION_THRESHOLDS: dict[str, tuple[str, float]] = {
    "water": ("store_water", 0.6),
    "safety": ("avoid_place", 0.65),
    "health": ("avoid_place", 0.65),
    "crime": ("avoid_place", 0.7),
    "fraud": ("stop_patronage", 0.7),
}
DEFAULT_ACTION = ("avoid_place", 0.7)


@dataclass(frozen=True)
class Claim:
    """One variant of one claim family. `key` names the family; the rest is
    the variant's current content after zero or more distortion ops."""

    key: str  # e.g. 'cl:water_kasba'
    subject: str  # entity/place ref it is about
    predicate: str  # 'contaminated' | 'misappropriated' | 'dangerous' | ...
    text: str  # rendered narrator text of THIS variant
    quantity: float | None = None
    unit: str | None = None  # 'rupees' | 'people' | None
    valence: float = -0.5  # -1 alarming .. +1 good news
    charge: float = 0.6  # emotional arousal 0..1 (drives spread)
    specificity: float = 0.6  # 1 = precise account; low = vague (mutates more)
    veracity: str = "unknown"  # 'true' | 'distorted' | 'false' | 'unknown'
    topics: tuple[str, ...] = ()
    ops: tuple[str, ...] = ()  # distortion ops applied so far (drift audit)
    blame: str | None = None  # REATTRIBUTE / MORALIZE target
    hop: int = 0

    def to_payload(self) -> dict:
        return {
            "key": self.key, "subject": self.subject, "predicate": self.predicate,
            "text": self.text, "quantity": self.quantity, "unit": self.unit,
            "valence": self.valence, "charge": self.charge,
            "specificity": self.specificity, "veracity": self.veracity,
            "topics": list(self.topics), "ops": list(self.ops),
            "blame": self.blame, "hop": self.hop,
        }

    @classmethod
    def from_payload(cls, p: dict) -> "Claim":
        return cls(
            key=p["key"], subject=p["subject"], predicate=p["predicate"],
            text=p["text"], quantity=p.get("quantity"), unit=p.get("unit"),
            valence=p.get("valence", -0.5), charge=p.get("charge", 0.6),
            specificity=p.get("specificity", 0.6), veracity=p.get("veracity", "unknown"),
            topics=tuple(p.get("topics", [])), ops=tuple(p.get("ops", [])),
            blame=p.get("blame"), hop=p.get("hop", 0),
        )


# --- thin deterministic traits (03-cognition §1.2, V1 subset) ---------------


@dataclass(frozen=True)
class Traits:
    sociability: float
    credulity: float
    conscientiousness: float


def traits(run_seed: int, person_id: str) -> Traits:
    """Timeless per-person scalars; derived, never stored (D0 is a function)."""
    rng = keyed_rng(run_seed, "traits", person_id, 0, "base")
    v = rng.random(3)
    return Traits(sociability=float(v[0]), credulity=float(v[1]), conscientiousness=float(v[2]))


# --- rendering (deterministic templates; LLM prose only inside scenes) ------

_PREDICATE_PHRASE = {
    "contaminated": "the water at {subject} is contaminated — people are falling sick",
    "misappropriated": "money was misappropriated at {subject}",
    "dangerous": "{subject} is not safe right now",
    "collision": "there was a road accident near {subject}",
    "supply_cut": "the water supply around {subject} has been cut",
    "outage": "power has gone out around {subject}",
    "fire": "a fire broke out at {subject}",
    "adulterated": "the food at {subject} is adulterated",
    "communal_tension": "there was trouble near {subject} — the air has turned tense, people say stay away",
    "clash": "there was a clash near {subject}",
}


def render_text(claim: Claim, block: Block) -> str:
    place = block.get(claim.subject)
    subject = place.name if place and place.name else claim.subject
    fallback = f"there was {'an' if claim.predicate[:1] in 'aeiou' else 'a'} {claim.predicate.replace('_', ' ')} at {{subject}}"
    body = _PREDICATE_PHRASE.get(claim.predicate, fallback).format(subject=subject)
    if claim.quantity is not None:
        unit = claim.unit or ""
        qty = int(claim.quantity) if float(claim.quantity).is_integer() else round(claim.quantity, 1)
        body += f" — {'₹' if unit == 'rupees' else ''}{qty}{' ' + unit if unit == 'people' else ''}"
        if unit == "people":
            body += " affected"
    if claim.specificity < 0.35:
        body = "they say " + body
    if claim.blame:
        bp = block.get(claim.blame)
        who = bp.name if bp and bp.name else claim.blame
        body += f"; people are blaming {who}"
    return body[0].upper() + body[1:]


# --- mutation: mechanical distortion ops (03-cognition §6.3) ----------------

OPS = ("EXAGGERATE", "GENERALIZE", "SPECIFY", "REATTRIBUTE", "MORALIZE")


def _op_exaggerate(claim: Claim, rng) -> Claim:
    q = claim.quantity if claim.quantity is not None else 2.0
    factor = 1.5 + rng.random() * 1.5
    q = float(max(round(q * factor), int(q) + 1))
    return replace(claim, quantity=q, unit=claim.unit or "people",
                   charge=min(1.0, claim.charge + 0.1),
                   specificity=max(0.0, claim.specificity - 0.05))


def _op_generalize(claim: Claim, rng) -> Claim:
    return replace(claim, specificity=max(0.0, claim.specificity - 0.15),
                   veracity="distorted" if claim.veracity == "true" else claim.veracity)


def _op_specify(claim: Claim, rng, block: Block) -> Claim:
    """Inject a plausible nearby detail from canon — false precision."""
    near = [p for p in block.places if p.name and p.id != claim.subject]
    if not near:
        return claim
    pick = near[int(rng.integers(0, len(near)))]
    return replace(claim, specificity=min(1.0, claim.specificity + 0.2),
                   veracity="distorted" if claim.veracity == "true" else claim.veracity,
                   blame=claim.blame or pick.id)


def _op_reattribute(claim: Claim, rng, block: Block) -> Claim:
    prominent = [p for p in block.places if p.name and p.kind in ("temple", "school", "hospital", "police", "bank", "market")]
    if not prominent:
        return claim
    pick = prominent[int(rng.integers(0, len(prominent)))]
    return replace(claim, blame=pick.id, veracity="distorted" if claim.veracity == "true" else claim.veracity)


def _op_moralize(claim: Claim, rng) -> Claim:
    return replace(claim, charge=min(1.0, claim.charge + 0.15),
                   valence=max(-1.0, claim.valence - 0.1))


def maybe_mutate(claim: Claim, run_seed: int, sharer_id: str, day: int, block: Block) -> Claim:
    """Per hop: p_mutate ∝ (1-specificity)·(1-conscientiousness). One op max."""
    tr = traits(run_seed, sharer_id)
    rng = keyed_rng(run_seed, "info", f"{sharer_id}|{claim.key}", day, "mutate")
    p = 0.45 * (1.0 - claim.specificity * 0.7) * (0.4 + 0.6 * (1.0 - tr.conscientiousness))
    if rng.random() >= p:
        return replace(claim, hop=claim.hop + 1)
    op = OPS[int(rng.integers(0, len(OPS)))]
    if op == "EXAGGERATE":
        out = _op_exaggerate(claim, rng)
    elif op == "GENERALIZE":
        out = _op_generalize(claim, rng)
    elif op == "SPECIFY":
        out = _op_specify(claim, rng, block)
    elif op == "REATTRIBUTE":
        out = _op_reattribute(claim, rng, block)
    else:
        out = _op_moralize(claim, rng)
    out = replace(out, ops=(*claim.ops, op), hop=claim.hop + 1)
    return replace(out, text=render_text(out, block))


# --- belief update (03-cognition §6.4, closed form) -------------------------


def _logit(p: float) -> float:
    p = min(max(p, 1e-4), 1 - 1e-4)
    return math.log(p / (1 - p))


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def update_credence(
    prior: float, channel: str, exposures: int, credulity: float, charge: float,
    same_source: bool = False,
) -> float:
    """Trust-weighted, lineage-discounted, credulity-aligned logit step.
    Repetition saturates (novelty discounts every re-hearing), and the same
    mouth repeating itself counts half an independent account."""
    trust_w = TRUST_W.get(channel, 0.5)
    novelty = 1.0 / (1.0 + exposures)
    if same_source:
        novelty *= 0.5
    align = 0.7 + 0.6 * credulity
    return _sigmoid(_logit(prior) + LAMBDA * trust_w * novelty * align * (0.5 + 0.5 * charge))


# --- state (projection of info.heard events; rebuildable from the log) ------


@dataclass
class Holding:
    claim: Claim  # the variant THIS person holds (last heard)
    credence: float
    exposures: int
    first_day: int
    last_seq: int  # seq of their latest info.heard event (lineage anchor)
    heard_abs: int = 0  # sim-time they first heard — no sharing before this
    last_source: str = ""
    stifled: bool = False  # Maki-Thompson: no longer retells this claim
    shares_today: int = 0


@dataclass
class InfoState:
    holdings: dict[str, dict[str, Holding]] = field(default_factory=dict)  # person -> claim_key -> Holding

    def hear(
        self, person_id: str, claim: Claim, credence: float, day: int, seq: int,
        source: str = "", t_abs: int = 0,
    ) -> None:
        by_key = self.holdings.setdefault(person_id, {})
        h = by_key.get(claim.key)
        if h is None:
            by_key[claim.key] = Holding(
                claim=claim, credence=credence, exposures=1, first_day=day,
                last_seq=seq, heard_abs=t_abs, last_source=source,
            )
        else:
            h.claim, h.credence, h.last_seq = claim, credence, seq
            h.exposures, h.last_source = h.exposures + 1, source

    def reset_day(self) -> None:
        for by_key in self.holdings.values():
            for h in by_key.values():
                h.shares_today = 0


@dataclass(frozen=True)
class Heard:
    """One computed transmission, ready to commit as an info.heard event."""

    sim_time: int
    person: str
    claim: Claim
    source: str
    channel: str
    credence: float
    caused_by: int | None


# --- presence + propagation -------------------------------------------------


def presence_intervals(
    routine: list[tuple[int, str, str, dict]], people: dict[str, Person], day: int
) -> dict[str, list[tuple[str, int, int]]]:
    """(sim_time, person_id, type, payload) -> per-person [(place, t0, t1)].
    Everyone starts and ends the day at home; trips move them between places."""
    t0, t1 = day * SECONDS_PER_DAY, (day + 1) * SECONDS_PER_DAY
    out: dict[str, list[tuple[str, int, int]]] = {}
    by_person: dict[str, list[tuple[int, str, dict]]] = {pid: [] for pid in people}
    for t, pid, ty, payload in routine:
        if pid in by_person and ty in ("trip.start", "trip.end", "activity.start"):
            by_person[pid].append((t, ty, payload))
    for pid in sorted(people):
        here, since = people[pid].home_id, t0
        spans: list[tuple[str, int, int]] = []
        for t, ty, payload in sorted(by_person[pid], key=lambda x: x[0]):
            if ty == "trip.start":
                if t > since:
                    spans.append((here, since, t))
                here, since = None, t  # in transit
            elif ty == "trip.end":
                here, since = payload.get("at", here), t
            elif ty == "activity.start" and payload.get("at"):
                if here is not None and payload["at"] != here and t > since:
                    spans.append((here, since, t))
                    here, since = payload["at"], t
                elif here is None:
                    here, since = payload["at"], t
        if here is not None and t1 > since:
            spans.append((here, since, t1))
        out[pid] = spans
    return out


def _copresence_windows(
    intervals: dict[str, list[tuple[str, int, int]]],
) -> list[tuple[int, int, str, str, str]]:
    """Sorted (start, end, place, a, b) windows with overlap >= MIN_OVERLAP_S."""
    at_place: dict[str, list[tuple[int, int, str]]] = {}
    for pid in sorted(intervals):
        for place, a0, a1 in intervals[pid]:
            at_place.setdefault(place, []).append((a0, a1, pid))
    windows: list[tuple[int, int, str, str, str]] = []
    for place in sorted(at_place):
        spans = sorted(at_place[place])
        for i, (a0, a1, pa) in enumerate(spans):
            for b0, b1, pb in spans[i + 1:]:
                if b0 >= a1:
                    break
                lo, hi = max(a0, b0), min(a1, b1)
                if hi - lo >= MIN_OVERLAP_S and pa != pb:
                    windows.append((lo, hi, place, *sorted((pa, pb))))
    return sorted(windows)


def _try_share(
    state: InfoState, run_seed: int, day: int, block: Block,
    sharer: str, receiver: str, channel: str, t: int, commit_heard,
) -> list[Heard]:
    """All claims `sharer` passes to `receiver` at time t (keyed draws)."""
    out: list[Heard] = []
    holdings = state.holdings.get(sharer, {})
    for key in sorted(holdings):
        h = holdings[key]
        if t < h.heard_abs:  # can't retell what you haven't heard yet
            continue
        if h.stifled or h.credence < MIN_CREDENCE_TO_SHARE or h.shares_today >= MAX_SHARES_PER_DAY:
            continue
        rh = state.holdings.get(receiver, {}).get(key)
        if rh is not None:
            # Maki-Thompson: meeting someone who already knows may convert the
            # teller to a stifler — rumors die from saturation, not exhaustion
            srng = keyed_rng(run_seed, "info", f"{sharer}|{receiver}|{key}", day, "stifle")
            if srng.random() < STIFLE_P:
                h.stifled = True
                continue
            if rh.exposures >= SATURATION_EXPOSURES:
                continue
        fresh = math.exp(-max(0, day - h.first_day) / FRESHNESS_TAU_DAYS)
        tr = traits(run_seed, sharer)
        base = 0.9 if channel == "household" else P_SHARE_BASE * (0.4 + 0.6 * tr.sociability)
        p = base * (0.3 + 0.7 * h.claim.charge) * fresh * h.credence
        rng = keyed_rng(run_seed, "info", f"{sharer}|{receiver}|{key}", day, "share")
        if rng.random() >= p:
            continue
        h.shares_today += 1
        variant = maybe_mutate(h.claim, run_seed, sharer, day, block)
        prior = rh.credence if rh is not None else PRIOR_CREDENCE
        exposures = rh.exposures if rh is not None else 0
        credence = update_credence(
            prior, channel, exposures, traits(run_seed, receiver).credulity, variant.charge,
            same_source=rh is not None and rh.last_source == sharer,
        )
        heard = Heard(t, receiver, variant, sharer, channel, round(credence, 3), h.last_seq or None)
        seq = commit_heard(heard)
        state.hear(receiver, variant, credence, day, seq, source=sharer, t_abs=t)
        out.append(heard)
    return out


def propagate_day(
    state: InfoState,
    run_seed: int,
    day: int,
    block: Block,
    people: dict[str, Person],
    intervals: dict[str, list[tuple[str, int, int]]],
    households: dict[str, tuple[str, ...]],
    commit_heard,
) -> list[Heard]:
    """One day of mechanical spread: co-presence windows in time order, then
    the evening household exchange. Multi-hop within a day works because
    windows are processed chronologically. `commit_heard(Heard) -> seq`
    commits each hop so its seq can anchor the next hop's caused_by."""
    state.reset_day()
    heard: list[Heard] = []
    for lo, hi, _place, pa, pb in _copresence_windows(intervals):
        t = (lo + hi) // 2
        for sharer, receiver in ((pa, pb), (pb, pa)):
            heard.extend(_try_share(state, run_seed, day, block, sharer, receiver, "f2f", t, commit_heard))
    evening = day * SECONDS_PER_DAY + 20 * 3600
    for hid in sorted(households):
        members = [m for m in households[hid] if m in people and people[m].age >= 6]
        for sharer in members:
            for receiver in members:
                if sharer == receiver:
                    continue
                heard.extend(_try_share(state, run_seed, day, block, sharer, receiver, "household", evening, commit_heard))
    return heard


# --- action thresholds: belief -> behavior (E5 lane) ------------------------

# only claims asserting a STANDING state drive mechanical avoidance — a past
# one-off (collision, fire) prompts talk and scenes, not tomorrow's rerouting
ONGOING_PREDICATES = {
    "contaminated", "supply_cut", "outage", "dangerous", "adulterated", "misappropriated",
}


@dataclass(frozen=True)
class BeliefAction:
    person: str
    claim_key: str
    action: str  # 'avoid_place' | 'store_water' | 'stop_patronage'
    place: str  # subject place to avoid / act about
    caused_by: int | None


def crossed_actions(state: InfoState, prior_acted: set[tuple[str, str]]) -> list[BeliefAction]:
    """People whose credence crossed a claim family's action threshold.
    Fires once per (person, claim_key) — hysteresis V1-thin."""
    out: list[BeliefAction] = []
    for pid in sorted(state.holdings):
        for key in sorted(state.holdings[pid]):
            if (pid, key) in prior_acted:
                continue
            h = state.holdings[pid][key]
            if h.claim.valence > -0.1 or h.claim.predicate not in ONGOING_PREDICATES:
                continue
            action, threshold = DEFAULT_ACTION
            for topic in h.claim.topics:
                if topic in ACTION_THRESHOLDS:
                    action, threshold = ACTION_THRESHOLDS[topic]
                    break
            if h.credence >= threshold:
                out.append(BeliefAction(pid, key, action, h.claim.subject, h.last_seq or None))
    return out
