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
# There is deliberately no default. A claim whose topic nothing maps spreads,
# is talked about and is remembered — but it changes nobody's route, because
# the sim was never told what someone would *do* about it.
#
# The fallback used to be ("avoid_place", 0.7), and it produced nonsense twice.
# A power cut fell through it and 255 of 306 people stopped going to a market
# because the lights had been off — the largest behavioural event in two 30-day
# soaks. Guessing a specific mechanical behaviour for an arbitrary belief is
# worse than admitting there is none: the ripple is not lost, it just stays in
# the gossip and the scenes where it belongs until somebody maps the topic.

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


# When a body is up. The lower bound on waking is set by the block's own
# routines: the earliest anybody leaves the house is a schoolchild at 07:10
# (world.schedule), and a chronotype that woke someone after their own front
# door closed would quietly delete their morning at home.
WAKE_S = (int(5.5 * 3600), 7 * 3600)
BED_S = (22 * 3600, int(23.5 * 3600))


@lru_cache(maxsize=1 << 17)
def awake_window(run_seed: int, person_id: str) -> tuple[int, int]:
    """(wake, bed) in seconds after midnight — a person's chronotype.

    Derived, never stored, and memoised, for the same reasons as `traits`: it
    is a pure function of its key and the info lane asks for it once per person
    per day, which at V3 scale is a Philox construction the run cannot afford.
    Timeless rather than per-day on purpose — a chronotype is a habit, and
    re-keying it on the day would multiply that cost by the length of the run
    to buy a few minutes of jitter nothing measures.
    """
    rng = keyed_rng(run_seed, "sleep", person_id, 0, "chronotype")
    v = rng.random(2)
    return (
        WAKE_S[0] + int(v[0] * (WAKE_S[1] - WAKE_S[0])),
        BED_S[0] + int(v[1] * (BED_S[1] - BED_S[0])),
    )


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


# `block.places` are BUILDINGS, and for some troubles the culprit a
# neighbourhood names is an ORGANISATION — nobody blames a pumping station for
# a dry tap, they blame the municipality. The world already acts through these
# two ids (engine.reactions files the complaint about a supply cut to
# org:pmc_water and about a load-shed to org:mseb); this is only what a
# neighbour calls them out loud. Kept deliberately separate from the viewer's
# and the scene renderer's own phrasings of the same ids: those are UI copy,
# this is words in a rumour's mouth, and unifying them would change prose in
# two lanes that have nothing to do with this one.
ORG_NAMES = {
    "org:pmc_water": "the municipal water department",
    "org:mseb": "the electricity board",
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
        who = bp.name if bp and bp.name else ORG_NAMES.get(claim.blame, claim.blame)
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


# How far "nearby" reaches: five minutes on foot. Measured over both blocks,
# that is a median of 13 named places on kasba and 16 on oldcity — the lane you
# are standing in and the two around the corner, which is the scale at which a
# person inventing a corroborating detail actually reaches for one. It used to
# be the entire block: 437 places on oldcity, uniformly, which is how a rumour
# about the water at Tulshibaug Mandir acquired "people are blaming Blackberrys",
# a menswear shop half the old city away.
NEARBY_WALK_S = 300


def _op_specify(claim: Claim, rng, block: Block) -> Claim:
    """Inject a plausible nearby detail from canon — false precision.

    Nearby means nearby. Twelve of oldcity's 438 places have nothing within
    five minutes' walk; a claim about one of those gets no invented detail,
    because there is genuinely no landmark to hand for the teller to reach for.
    """
    near = block.nearby(claim.subject, NEARBY_WALK_S)
    if not near:
        return claim
    pick = near[int(rng.integers(0, len(near)))]
    return replace(claim, specificity=min(1.0, claim.specificity + 0.2),
                   veracity="distorted" if claim.veracity == "true" else claim.veracity,
                   blame=claim.blame or pick.id)


# Who a claim's topics could plausibly implicate.
#
# This is a table because the world holds no other representation of
# responsibility: places carry a `kind`, claims carry `topics`, and nothing
# anywhere connects the two vocabularies. Without the connection the op picked
# uniformly from 205 "prominent" places, so a water-contamination rumour blamed
# a bank. Nothing less arbitrary than saying it outright was available; what
# keeps it honest is that it is *small*, it lives against the op that reads it,
# and — like ACTION_THRESHOLDS above — it has no default. A topic nothing maps
# gets no reattribution at all: the op fires, the claim is still marked as
# drifting, and nobody is named, which is a truer account of a rumour with no
# obvious villain than picking one at random.
#
# Utilities are the case where the culprit is not a building at all — you blame
# the municipality for a dry tap, not the pumping station — and the world
# already names the two organisations that answer for them (see ORG_NAMES).
BLAMED_ORG = {"water": "org:pmc_water", "power": "org:mseb"}
# For the rest the culprit IS a building, and which one is not a matter of
# taste: it is whoever has the job. Candidates are drawn from the subject's own
# neighbourhood first, and only if the responsible kind is absent from it does
# blame reach across the block — your police station may well be in the next
# peth, but it is still yours.
BLAMED_KINDS = {
    "safety": ("police",),
    "crime": ("police",),
    "health": ("hospital", "clinic"),
    "fraud": ("bank",),
    "food": ("restaurant", "market"),
}


def _op_reattribute(claim: Claim, rng, block: Block) -> Claim:
    who = next((BLAMED_ORG[t] for t in claim.topics if t in BLAMED_ORG), None)
    if who is None:
        kinds = next((BLAMED_KINDS[t] for t in claim.topics if t in BLAMED_KINDS), None)
        if kinds is None:
            return claim
        pool = [p for p in block.nearby(claim.subject, NEARBY_WALK_S) if p.kind in kinds]
        if pool:
            pick = pool[int(rng.integers(0, len(pool)))]
        else:
            pick = block.nearest(claim.subject, *kinds) if block.get(claim.subject) else None
        if pick is None or pick.id == claim.subject:
            return claim  # the only candidate is the place it is already about
        who = pick.id
    return replace(claim, blame=who, veracity="distorted" if claim.veracity == "true" else claim.veracity)


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
    # Per person, the claims they could still pass on today. Measured on a
    # 14-day run at 1,500 households: of every holding _try_share stopped to
    # examine, 93.9% were already stifled, below the credence floor, or out of
    # the day's shares — re-checked on every one of a million contacts. The set
    # holds only the three state-based conditions; "have they heard it yet at
    # this hour" stays in the loop, because it depends on the time of the
    # contact rather than on the holding.
    shareable: dict[str, set[str]] = field(default_factory=dict)

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
            fresh_h = by_key[claim.key] = Holding(
                claim=claim, credence=credence, exposures=1, first_day=day,
                last_seq=seq, heard_abs=t_abs, last_source=source,
                witnessed=channel == "witness", lineage=lineage[-LINEAGE_MAX:],
            )
            self.mark_shareable(person_id, claim.key, fresh_h)
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
        self.mark_shareable(person_id, claim.key, h)

    def _can_share(self, h: "Holding") -> bool:
        return (not h.stifled and h.credence >= MIN_CREDENCE_TO_SHARE
                and h.shares_today < MAX_SHARES_PER_DAY)

    def mark_shareable(self, person_id: str, key: str, h: "Holding") -> None:
        """Add or drop one claim from a person's shareable set."""
        got = self.shareable.get(person_id)
        if self._can_share(h):
            if got is None:
                got = self.shareable[person_id] = set()
            got.add(key)
        elif got is not None:
            got.discard(key)

    def reset_day(self) -> None:
        """New day: everyone's share allowance refills, so the index is rebuilt
        rather than patched — credence may have moved overnight too."""
        self.shareable.clear()
        for pid, by_key in self.holdings.items():
            live = set()
            for k, h in by_key.items():
                h.shares_today = 0
                if self._can_share(h):
                    live.add(k)
            if live:
                self.shareable[pid] = live


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

    Spans are clipped to the person's waking hours first. `presence_intervals`
    is right that you are at home all night — a fire at 03:00 has to find you
    there, and witness_tiers reads the same intervals to do it — but being
    somewhere is not being available to talk. Unclipped, the whole night at
    home was one contact window per pair of housemates, and a 30-day soak has
    Mahavir Bafna telling two people something at 03:31.

    What that cost is worth stating precisely, because the obvious guess is
    wrong. Measured at 320 households over 3 days it barely changes the NUMBER
    of contacts — 58,509 windows to 58,464 — since a pair asleep in one house
    made exactly one window either way. What it changes is when a contact
    happens and how long it lasts: 342,054 contact-hours to 289,331 (-15%),
    and of the 50 hearings of 631 that landed between 03:00 and 05:00, 42
    reappear between 06:00 and 08:00 and 8 stop happening at all (631 -> 617
    hearings, from the pairs whose only overlap was while both were asleep).
    So the defect was not really inflating contact
    rate; it was putting a sixth of the block's talking time in the middle of
    the night, which is worse for anything that reads the diurnal profile and
    nearly invisible to anything that only counts.
    """
    at_place: dict[str, list[tuple[int, int, str]]] = {}
    day0 = day * SECONDS_PER_DAY
    for pid in sorted(intervals):
        wake, bed = awake_window(run_seed, pid)
        up, down = day0 + wake, day0 + bed
        for place, a0, a1 in intervals[pid]:
            a0, a1 = max(a0, up), min(a1, down)
            # a span shorter than the minimum overlap cannot make a window with
            # anything, so it never has to be sorted or scanned
            if a1 - a0 < MIN_OVERLAP_S:
                continue
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
    live = state.shareable.get(sharer)
    if not live:
        return out
    for key in sorted(live):
        h = holdings.get(key)
        if h is None:
            continue
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
                state.mark_shareable(sharer, key, h)
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
        state.mark_shareable(sharer, key, h)
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
            mapped = next(
                (ACTION_THRESHOLDS[t] for t in h.claim.topics if t in ACTION_THRESHOLDS),
                None,
            )
            if mapped is None:  # nothing designed; nothing mechanical happens
                continue
            action, threshold = mapped
            if h.credence >= threshold:
                out.append(BeliefAction(pid, key, action, h.claim.subject, h.last_seq or None))
    return out
