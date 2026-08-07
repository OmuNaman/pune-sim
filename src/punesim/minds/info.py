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
from bisect import bisect_left
from dataclasses import dataclass, field, replace
from functools import lru_cache

from ..kernel.rng import keyed_rng, keyed_uniform
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
LINEAGE_MAX = 16  # how far back a variant remembers whose mouths it passed through

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

# Of the action vocabulary, which ones actually mean "and so I stop going
# there". Storing water at home does not: it is a thing you do *because* the
# supply is cut, not a reason to stay away from the place it was cut at. The
# engine used to record an avoidance for every belief action regardless, so
# 1,138 people who filled a drum were also marked as shunning the pumping
# station — invisible in that run only because none of them ever went there,
# and quietly the reason the belief lane cost 9 of every 20 seconds by day 15.
AVOIDING_ACTIONS = frozenset({"avoid_place", "stop_patronage"})


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


@lru_cache(maxsize=1 << 17)
def traits(run_seed: int, person_id: str) -> Traits:
    """Timeless per-person scalars; derived, never stored (D0 is a function).

    Memoised because it is a pure function of its key and the info lane asks for
    the same person's traits thousands of times a day — 56k calls in a 4-day
    probe, each one building a fresh Philox generator, which is the single most
    expensive thing this codebase does per call.
    """
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
    "restored": "the power is back around {subject}",
    "water_tanker": "a municipal water tanker has come to {subject}",
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
    witnessed: bool = False  # they saw it themselves; set once, never cleared
    lineage: tuple[str, ...] = ()  # mouths this variant already passed through


@dataclass
class InfoState:
    holdings: dict[str, dict[str, Holding]] = field(default_factory=dict)  # person -> claim_key -> Holding
    # The day each claim family was first held by anyone. Freshness decays on
    # THIS, not on when the current teller happened to hear it: a fortnight-old
    # restoration is stale news to a man who heard it an hour ago. Keying decay
    # to the teller instead let every new hearer restart the clock, so in a big
    # enough population a claim never aged out — it kept recruiting eager
    # relayers faster than saturation could stifle them. A 30-day soak at 49,578
    # people had "the power is back" still spreading 16 days later, to 40% of
    # the city. At 306 people it died in eight, which is why no earlier soak saw
    # it: the bug is invisible until the population outruns saturation.
    born: dict[str, int] = field(default_factory=dict)  # claim_key -> day

    def hear(
        self, person_id: str, claim: Claim, credence: float, day: int, seq: int,
        source: str = "", t_abs: int = 0, channel: str = "",
        lineage: tuple[str, ...] = (),
    ) -> None:
        by_key = self.holdings.setdefault(person_id, {})
        if claim.key not in self.born:
            self.born[claim.key] = day
        h = by_key.get(claim.key)
        if h is None:
            by_key[claim.key] = Holding(
                claim=claim, credence=credence, exposures=1, first_day=day,
                last_seq=seq, heard_abs=t_abs, last_source=source,
                witnessed=channel == "witness", lineage=lineage[-LINEAGE_MAX:],
            )
            return
        # What you saw with your own eyes is not overwritten by what you are
        # told about it (03-cognition §6.4: a percept outranks a report). The
        # one exception is genuine enrichment — an UNDISTORTED account that is
        # more precise than your own glimpse, which is how someone two lanes
        # away learns what actually happened. Your confidence still moves
        # either way: hearing it again from the family is corroboration.
        enriches = claim.veracity == "true" and claim.specificity > h.claim.specificity
        sticky = h.witnessed and channel != "witness" and not enriches
        if not sticky:
            # claim and last_seq move TOGETHER: last_seq anchors the caused_by
            # of the next hop, so a mismatched pair would make the drift audit
            # point at an event carrying a different variant.
            h.claim, h.last_seq, h.lineage = claim, seq, lineage[-LINEAGE_MAX:]
        h.credence = credence
        h.exposures, h.last_source = h.exposures + 1, source
        if channel == "witness":
            h.witnessed = True

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
    lineage: tuple[str, ...] = ()  # the chain of mouths, oldest first


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


# A crowd is not a room. Below this many place-spans in a day every overlapping
# pair is enumerated, exactly as V0-V2 did and as the 30-day soaks validated
# (the busiest place in an 80-household run holds 92). Above it, all-pairs is
# both unaffordable and untrue: a 2880-household probe already emitted 2.5M
# windows a day, a peth would emit tens of millions, and nobody exchanges news
# with three thousand strangers because they passed through the same market.
CROWD_EXACT_SPANS = 128
CONTACTS_IN_A_CROWD = 12  # how many of a crowd one person actually engages


def _copresence_windows(
    intervals: dict[str, list[tuple[str, int, int]]],
    run_seed: int = 0,
    day: int = 0,
) -> list[tuple[int, int, str, str, str]]:
    """Sorted (start, end, place, a, b) windows with overlap >= MIN_OVERLAP_S.

    Quiet places are exact. In a crowd each span takes at most
    CONTACTS_IN_A_CROWD partners by keyed draw from the people it overlaps, so
    contact count is bounded by attention rather than by footfall. The draw is
    forward-only (a span samples partners that start after it), so a person's
    expected degree is about twice the cap, not exactly it.

    KNOWN PROPERTY, deliberate: inside a crowded place this weakens law 4's
    branch cleanliness. The sampled indices depend on how many people are in the
    overlapping run, so a branch that moves one extra person into a crowded
    market changes who *others* there bump into — under all-pairs, adding a
    person only ever added pairs. Any degree cap must depend on crowd size, so
    some version of this is unavoidable; the honest fix is per-pair keyed draws,
    which cost a Philox construction per pair and are therefore exactly the
    thing being avoided. It does not touch the sizes where branching has been
    validated: below CROWD_EXACT_SPANS nothing is sampled at all.
    """
    at_place: dict[str, list[tuple[int, int, str]]] = {}
    for pid in sorted(intervals):
        for place, a0, a1 in intervals[pid]:
            at_place.setdefault(place, []).append((a0, a1, pid))
    windows: list[tuple[int, int, str, str, str]] = []
    for place in sorted(at_place):
        spans = sorted(at_place[place])
        starts = [s[0] for s in spans]
        crowded = len(spans) > CROWD_EXACT_SPANS
        for i, (a0, a1, pa) in enumerate(spans):
            # Spans are sorted by start, so everyone who can still overlap is
            # the contiguous run up to the first span starting at or after a1.
            # This used to be a slice-and-break, which copied the tail of the
            # list once per span — quadratic in memory traffic before it was
            # quadratic in work.
            end = bisect_left(starts, a1, i + 1)
            if crowded and end - (i + 1) > CONTACTS_IN_A_CROWD:
                rng = keyed_rng(run_seed, "copresence", f"{place}|{pa}|{a0}", day, "contacts")
                picks = sorted(set(rng.integers(i + 1, end, CONTACTS_IN_A_CROWD).tolist()))
            else:
                picks = range(i + 1, end)
            for j in picks:
                b0, b1, pb = spans[j]
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
    if not holdings:
        return out
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
            # One coin flip per contact per held claim — the hottest draw in the
            # simulation, and the reason keyed_uniform exists.
            if keyed_uniform(run_seed, "info", f"{sharer}|{receiver}|{key}", day, "stifle") < STIFLE_P:
                h.stifled = True
                continue
            if rh.exposures >= SATURATION_EXPOSURES:
                continue
        if receiver in h.lineage:
            # You are already in this story's chain — this is your own account
            # coming back to you. Hearing your own words repeated is not
            # evidence; before this guard, 12% of all hearings were echoes and
            # they were the fastest route to false certainty. Placed AFTER the
            # stifle draw so Maki-Thompson death dynamics are untouched.
            continue
        born = state.born.get(h.claim.key, h.first_day)
        fresh = math.exp(-max(0, day - born) / FRESHNESS_TAU_DAYS)
        tr = traits(run_seed, sharer)
        base = 0.9 if channel == "household" else P_SHARE_BASE * (0.4 + 0.6 * tr.sociability)
        p = base * (0.3 + 0.7 * h.claim.charge) * fresh * h.credence
        if keyed_uniform(run_seed, "info", f"{sharer}|{receiver}|{key}", day, "share") >= p:
            continue
        h.shares_today += 1
        variant = maybe_mutate(h.claim, run_seed, sharer, day, block)
        prior = rh.credence if rh is not None else PRIOR_CREDENCE
        exposures = rh.exposures if rh is not None else 0
        credence = update_credence(
            prior, channel, exposures, traits(run_seed, receiver).credulity, variant.charge,
            same_source=rh is not None and rh.last_source == sharer,
        )
        onward = (*h.lineage, sharer)[-LINEAGE_MAX:]
        heard = Heard(
            t, receiver, variant, sharer, channel, round(credence, 3), h.last_seq or None, onward
        )
        seq = commit_heard(heard)
        state.hear(
            receiver, variant, credence, day, seq, source=sharer, t_abs=t,
            channel=channel, lineage=onward,
        )
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
    holdings = state.holdings
    for lo, hi, _place, pa, pb in _copresence_windows(intervals, run_seed, day):
        # Two people who both know nothing cannot tell each other anything, and
        # in a crowded place they are the overwhelming majority of pairs: at
        # 5000 people this guard is the difference between 4.3M _try_share
        # calls and a few thousand. Equivalent, not approximate — _try_share
        # draws no keyed randomness before its first holding.
        if not holdings.get(pa) and not holdings.get(pb):
            continue
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

# Only claims asserting a STANDING state drive mechanical avoidance — a past
# one-off (collision, fire) prompts talk and scenes, not tomorrow's rerouting.
#
# "outage" is deliberately NOT here. A power cut is a nuisance, not a reason to
# change where you go, and leaving it in made 255 of 306 people avoid a place
# because the lights had been off there — the largest single behavioural event
# in two 30-day soaks, and nonsense. The block's honest response to a load-shed
# is the complaint the institution lane already files.
ONGOING_PREDICATES = {
    "contaminated", "supply_cut", "dangerous", "adulterated", "misappropriated",
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
