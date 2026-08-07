"""V0 population: tiny D0 — a deterministic, regenerable pure function of
(run_seed, block). No storage: the population "exists" as a function of the
seed (the D0/canon invariant); canon holds only facts about touched people.

Religion shares are town-level Census C-1 adjusted by peth-level editorial
estimates (provenance=estimate; sources in data/anchors/MANIFEST.md and
docs/subsystems/08-identity.md §1). Identity conditions structure — names,
home placement — and never enters prompts below disclosure tier 1.
"""

from dataclasses import dataclass

from ..kernel.rng import keyed_rng
from ..world.block import Block
from . import names
from .demography import Demography, for_block

# Old-city core estimate (C-1 town-level x peth priors; provenance=estimate).
RELIGION_SHARES = [
    ("hindu", 0.78),
    ("muslim", 0.13),
    ("buddhist_navayana", 0.05),
    ("jain", 0.03),
    ("christian", 0.01),
]

# occupation -> place kinds searched for a workplace (None = works from home / roams)
WORKPLACE_KINDS: dict[str, tuple[str, ...] | None] = {
    "shopkeeper": ("shop", "market"),
    "shop_assistant": ("shop", "market"),
    "teacher": ("school",),
    "nurse": ("hospital", "clinic"),
    "doctor": ("hospital", "clinic"),
    "bank_clerk": ("bank",),
    "office_clerk": ("office", "bank", "venue"),
    "cook": ("restaurant",),
    "waiter": ("restaurant",),
    "priest": ("temple", "mosque", "church", "jain_temple", "vihara"),
    "police_constable": ("police",),
    "tailor": ("shop",),
    "rickshaw_driver": None,
    "domestic_worker": None,
    "homemaker": None,
    "retired": None,
    "student": ("school",),
}

_ADULT_OCCUPATIONS = [
    ("shopkeeper", 0.17),
    ("office_clerk", 0.16),
    ("shop_assistant", 0.12),
    ("tailor", 0.10),
    ("rickshaw_driver", 0.09),
    ("teacher", 0.08),
    ("domestic_worker", 0.08),
    ("cook", 0.06),
    ("nurse", 0.05),
    ("bank_clerk", 0.04),
    ("police_constable", 0.03),
    ("doctor", 0.01),
    ("priest", 0.01),
]


@dataclass(frozen=True)
class Person:
    id: str
    household_id: str
    given: str
    surname: str
    sex: str  # 'm' | 'f'
    age: int
    religion: str
    occupation: str
    home_id: str
    work_id: str | None  # school for students

    @property
    def name(self) -> str:
        return f"{self.given} {self.surname}"


@dataclass(frozen=True)
class Household:
    id: str
    template: str
    home_id: str
    religion: str
    surname: str
    member_ids: tuple[str, ...]


def _weighted(rng, pairs: list[tuple[str, float]]) -> str:
    x = rng.random()
    acc = 0.0
    for value, w in pairs:
        acc += w
        if x < acc:
            return value
    return pairs[-1][0]


def _quota(shares: list[tuple[str, float]], n: int) -> list[str]:
    """Largest-remainder allocation: a small block still matches the peth
    shares exactly ("statistics are honest" even at n=80)."""
    floors = [(name, int(share * n), share * n - int(share * n)) for name, share in shares]
    out: list[str] = []
    for name, f, _ in floors:
        out.extend([name] * f)
    for name, _, _ in sorted(floors, key=lambda t: -t[2])[: n - len(out)]:
        out.append(name)
    return out


def _pick(rng, pool: list[str]) -> str:
    return pool[int(rng.integers(0, len(pool)))]


def _given(rng, religion: str, sex: str) -> str:
    return _pick(rng, names.GIVEN[(religion, sex)])


def synthesize(
    run_seed: int, block: Block, n_households: int = 80,
    demo: Demography | None = None,
) -> tuple[list[Household], dict[str, Person]]:
    # A block that claims to be a real place draws from that place's census.
    # Passed explicitly only by the offline fitter; everything else takes the
    # table its block is named after.
    d = demo if demo is not None else for_block(getattr(block, "name", "kasba"))
    households: list[Household] = []
    people: dict[str, Person] = {}
    if not block.homes:
        raise ValueError("block has no home candidates")

    # Deterministic home assignment: shuffle candidates once with a keyed draw.
    order = keyed_rng(run_seed, "synth", "block", 0, "home_order").permutation(len(block.homes))
    # Religion by exact quota, deterministically shuffled across households.
    quota = _quota(RELIGION_SHARES, n_households)
    rperm = keyed_rng(run_seed, "synth", "block", 0, "religion_quota").permutation(n_households)
    shop_rotation = 0
    shops = block.of_kind("shop", "market")

    for i in range(n_households):
        hid = f"hh:{i:03d}"
        rng = keyed_rng(run_seed, "synth", hid, 0, "household")
        template = _weighted(rng, list(d.templates))
        religion = quota[rperm[i]]
        surname = _pick(rng, names.SURNAME[religion])
        # Pune's old city is wada housing: one mapped OSM building is a compound
        # holding several households, not one family. The 2011 ward census puts
        # Kasba Ganpati at 3,848 households against far fewer buildings, so
        # stacking is the ground truth rather than a workaround for a thin
        # extract. Below the pool size nothing stacks and every existing run
        # keeps the home assignment it had.
        home = block.homes[order[i % len(order)]]

        members: list[tuple[str, int, str | None]] = []  # (sex, age, surname_override)
        if template == "nuclear_kids":
            dad_age = int(rng.integers(*d.nuclear_parent_age))
            members = [("m", dad_age, None),
                       ("f", dad_age - int(rng.integers(*d.nuclear_spouse_gap)), None)]
            for _ in range(int(rng.integers(*d.nuclear_kids))):
                members.append(("m" if rng.random() < d.p_male_child else "f",
                                int(rng.integers(*d.nuclear_kid_age)), None))
        elif template == "joint":
            gdad = int(rng.integers(*d.joint_grandparent_age))
            dad = gdad - int(rng.integers(*d.joint_generation_gap))
            gran = ("f", gdad - int(rng.integers(*d.joint_grandparent_gap)), None)
            elders = [("m", gdad, None), gran]
            # Often the grandfather is simply not there any more. Guarded so a
            # table with p=0 draws nothing and keeps its sequence untouched.
            if d.p_grandparent_widowed and rng.random() < d.p_grandparent_widowed:
                elders = [gran]
            members = [
                *elders,
                ("m", dad, None),
                ("f", dad - int(rng.integers(*d.joint_spouse_gap)), None),
            ]
            for _ in range(int(rng.integers(*d.joint_kids))):
                members.append(("m" if rng.random() < d.p_male_child else "f",
                                int(rng.integers(*d.joint_kid_age)), None))
        elif template == "nuclear_nokids":
            a = int(rng.integers(*d.couple_age))
            members = [("m", a, None), ("f", a - int(rng.integers(*d.couple_gap)), None)]
        elif template == "elder_single":
            members = [("m" if rng.random() < d.p_male_elder else "f",
                        int(rng.integers(*d.elder_age)), None)]
        else:  # pg_students — separate families sharing a room
            for _ in range(int(rng.integers(*d.pg_size))):
                r2 = _weighted(rng, RELIGION_SHARES)
                members.append(
                    ("m" if rng.random() < d.p_male_pg else "f",
                     int(rng.integers(*d.pg_age)), r2)
                )

        member_ids: list[str] = []
        for m, (sex, age, rel_override) in enumerate(members):
            pid = f"person:{i:03d}.{m}"
            prng = keyed_rng(run_seed, "synth", pid, 0, "person")
            rel = rel_override or religion
            sur = surname if rel_override is None else _pick(prng, names.SURNAME[rel])
            if age < 5:
                occ = "infant"
            elif age < 18 or template == "pg_students":
                occ = "student"
            elif age >= 62:
                occ = "retired"
            elif sex == "f" and rng.random() < 0.25:
                occ = "homemaker"
            else:
                occ = _weighted(prng, _ADULT_OCCUPATIONS)

            work_id: str | None = None
            kinds = WORKPLACE_KINDS.get(occ)
            if occ == "shopkeeper" and shops:
                work_id = shops[shop_rotation % len(shops)].id
                shop_rotation += 1
            elif kinds:
                near = block.nearest(home.id, *kinds)
                work_id = near.id if near else None

            people[pid] = Person(
                id=pid,
                household_id=hid,
                given=_given(prng, rel, sex),
                surname=sur,
                sex=sex,
                age=age,
                religion=rel,
                occupation=occ,
                home_id=home.id,
                work_id=work_id,
            )
            member_ids.append(pid)

        households.append(
            Household(
                id=hid,
                template=template,
                home_id=home.id,
                religion=religion,
                surname=surname,
                member_ids=tuple(member_ids),
            )
        )
    return households, people
