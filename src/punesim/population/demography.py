"""Per-block demography: the numbers `synthesize` draws households from.

V0-V2 used one hand-written table, tuned by eye on an 80-household block where
nobody could tell 3.9 people per household from 4.1. At V3 scale the block
claims to *be* a place with a published census, so the table is fitted to that
place's marginals instead — see `scripts/fit_synthesis.py`, which does the
fitting offline and prints the constants below. Nothing fits at runtime:
synthesis stays a pure function of (seed, block), which is the D0 invariant.

Fitted to *ratios*, not counts. The PMC census's old-city unit is the
Kasbavishrambaug ward office — 13 wards, 43,138 households, 178,484 people —
which is larger than the four-peth extract, so absolute totals do not tile onto
the block. Household size, sex share and the 0-6 share are stable across those
wards (per-ward size runs 3.66-4.92 around a mean of 4.14) and do transfer.
"""

from dataclasses import dataclass

# Source: data/anchors/pune_ward_census_2011.csv, the 13 wards whose ward
# office is "Kasbavishrambaug" (Kasba Ganpati, Shanivarwada, Vishrambaugwada,
# Mahatama Phule Mandi, Ganeshpeth Gurudwara, Panch Houd Mission, S.P. Collage,
# City Post, Dr. Kotnis Davakhana, Renuka Swarup Prashala, New English School,
# RajendraNagar, Subhashnagar) — the administrative old city, and the same
# landmarks the oldcity OSM extract is built around.
OLD_CITY_TARGETS = {
    "household_size": 4.138,
    "male_share": 0.4954,
    "under_7_share": 0.0726,
}


@dataclass(frozen=True)
class Demography:
    """Everything about a block's households that a census can argue with.

    Field order is draw order — `synthesize` consumes these in exactly the
    sequence they are declared per template, so swapping tables never reorders
    a keyed draw.
    """

    templates: tuple[tuple[str, float], ...]
    # nuclear_kids
    nuclear_parent_age: tuple[int, int]
    nuclear_spouse_gap: tuple[int, int]
    nuclear_kids: tuple[int, int]
    nuclear_kid_age: tuple[int, int]
    # joint
    joint_grandparent_age: tuple[int, int]
    joint_grandparent_gap: tuple[int, int]
    joint_generation_gap: tuple[int, int]
    joint_spouse_gap: tuple[int, int]
    joint_kids: tuple[int, int]
    joint_kid_age: tuple[int, int]
    # couple with no children at home
    couple_age: tuple[int, int]
    couple_gap: tuple[int, int]
    # elder living alone
    elder_age: tuple[int, int]
    p_male_elder: float
    # paying guests / students sharing a room
    pg_size: tuple[int, int]
    pg_age: tuple[int, int]
    p_male_pg: float
    # a child's sex; India's sex ratio at birth, not a free parameter
    p_male_child: float
    # How often a joint household's senior generation is one surviving widow
    # rather than a couple. Men in this cohort die earlier, and this — not the
    # composition of student hostels — is why an old-city ward reports more
    # women than men. 0.0 means the draw never happens at all, which is how the
    # frozen kasba table keeps its exact sequence.
    p_grandparent_widowed: float = 0.0


# The V0-V2 table, unchanged to the digit. Every determinism hash and every
# soak in docs/soaks/ is a function of these numbers, so they are frozen: the
# `kasba` block must keep drawing exactly what it drew.
KASBA = Demography(
    templates=(
        ("nuclear_kids", 0.40),
        ("joint", 0.25),
        ("pg_students", 0.15),
        ("nuclear_nokids", 0.12),
        ("elder_single", 0.08),
    ),
    nuclear_parent_age=(30, 46),
    nuclear_spouse_gap=(2, 7),
    nuclear_kids=(1, 4),
    nuclear_kid_age=(3, 17),
    joint_grandparent_age=(62, 78),
    joint_grandparent_gap=(2, 6),
    joint_generation_gap=(26, 34),
    joint_spouse_gap=(2, 7),
    joint_kids=(1, 3),
    joint_kid_age=(4, 16),
    couple_age=(25, 39),
    couple_gap=(1, 6),
    elder_age=(65, 86),
    p_male_elder=0.4,
    pg_size=(3, 6),
    pg_age=(18, 25),
    p_male_pg=0.6,
    p_male_child=0.52,
)

# Fitted 2026-08-07 by `scripts/fit_synthesis.py` against OLD_CITY_TARGETS, by
# coordinate descent on a 3,000-household sample and validated at 12,000:
#
#     household_size  4.1315 vs 4.1375   male_share 0.4940 vs 0.4954
#     under_7_share   0.0717 vs 0.0726
#
# Two knobs are deliberately constrained in the fitter, because left free it
# used them as error sinks rather than as mechanisms. It drove PG rooms to 42%
# male — in a student city that is not a finding — and single-elder households
# down to 2% purely to lift mean household size. Both were absorbing an error
# that is really about widowhood: a population of couples plus children lands
# near 51% male, and the only honest route below 50% is that men in the senior
# cohort die first. Given `p_grandparent_widowed` as a lever the search found
# 0.2 on its own, and the sex ratio fell out of it.
#
# What moved off the V0 table, and why:
#   - more joint households, fewer childless couples: the V0 mix gave 3.89
#     people per household against a census 4.14
#   - 1 in 5 joint households is a surviving grandmother rather than a couple
#   - 3 in 4 people living alone in old age are women (was 3 in 5)
#   - PG rooms are mixed rather than 60% male, which was set on no evidence
#   - children start at 4 rather than 3, which was putting 8.3% of the
#     population under 7 against a census 7.3%
#
# Known weak spot: nuclear_nokids at 0.04 is lower than it should be. It is the
# template the fitter has least reason to keep, since nothing in these three
# marginals distinguishes a childless couple from a couple whose children have
# left. A fourth marginal — the census's literacy or age-band columns — would
# pin it; that is the next calibration, not this one.
OLDCITY = Demography(
    templates=(
        ("nuclear_kids", 0.42),
        ("joint", 0.31),
        ("pg_students", 0.17),
        ("nuclear_nokids", 0.04),
        ("elder_single", 0.06),
    ),
    nuclear_parent_age=(30, 46),
    nuclear_spouse_gap=(2, 7),
    nuclear_kids=(1, 4),
    nuclear_kid_age=(4, 18),
    joint_grandparent_age=(62, 78),
    joint_grandparent_gap=(2, 6),
    joint_generation_gap=(26, 34),
    joint_spouse_gap=(2, 7),
    joint_kids=(1, 3),
    joint_kid_age=(4, 16),
    couple_age=(25, 39),
    couple_gap=(1, 6),
    elder_age=(65, 86),
    p_male_elder=0.25,
    pg_size=(3, 6),
    pg_age=(18, 25),
    p_male_pg=0.5,
    p_male_child=0.52,
    p_grandparent_widowed=0.2,
)

BY_BLOCK = {"kasba": KASBA, "oldcity": OLDCITY}


def for_block(name: str) -> Demography:
    """The demography a named block draws from; kasba's table for anything else."""
    return BY_BLOCK.get(name, KASBA)
