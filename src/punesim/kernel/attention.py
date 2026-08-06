"""Law 5: one attention/budget authority.

score(entity) = user_focus + recent_perturbation (exponentially decayed).
Tier assignment anywhere in the sim is a pure function of this score — no other
gate exists. V0 uses top-k selection for morning scenes; arc_activity and
event_proximity terms join at V1+ per the architecture.

The scene gate mode lives beside it: "spotlight" renders top-k households,
"all" renders every household (the owner's LLM-for-everyone dial — same
machinery, bigger k, linear cost).
"""

import math

# Staleness: how much a household's claim on the camera grows for each day it
# has not been seen. Two inequalities make this rotation rather than chaos:
#   STALE_PER_DAY * STALE_CAP_DAYS  <  1.5 * 2**(-1/48)   = 1.4785
#       — maximum boredom never outranks a household something happened to;
#   STALE_PER_DAY                   >  1.5 * 2**(-289/48) = 0.0231
#       — but it does outrank the decayed residue of a bump from days ago,
#         which is what kept the same five households on screen for eleven
#         days straight while a family nothing had happened to was frozen out
#         from day 6 to the end of the month.
STALE_PER_DAY = 0.05
STALE_CAP_DAYS = 20


class AttentionField:
    def __init__(self, *, half_life_ticks: float = 48.0):
        self._decay = math.log(2.0) / half_life_ticks
        self._focus: dict[str, float] = {}
        self._perturb: dict[str, tuple[float, int]] = {}  # entity -> (amount, tick)
        self._rendered: dict[str, int] = {}  # entity -> last sim-day on camera

    def set_focus(self, entity: str, level: float) -> None:
        if level <= 0:
            self._focus.pop(entity, None)
        else:
            self._focus[entity] = level

    def focused(self) -> list[str]:
        """Explicitly followed entities — the owner's own choice, which no
        amount of drama elsewhere may outvote."""
        return sorted(self._focus)

    def bump(self, entity: str, amount: float, tick: int) -> None:
        """Perturbation from events touching this entity; accumulates decayed."""
        prior = self._decayed_perturb(entity, tick)
        self._perturb[entity] = (prior + amount, tick)

    def mark_rendered(self, entity: str, day: int) -> None:
        """The feedback edge the field was missing: being on camera is itself
        an event, and it lowers your claim on tomorrow's camera."""
        self._rendered[entity] = day

    def _decayed_perturb(self, entity: str, tick: int) -> float:
        if entity not in self._perturb:
            return 0.0
        amount, at = self._perturb[entity]
        return amount * math.exp(-self._decay * max(0, tick - at))

    def _staleness(self, entity: str, day: int | None) -> float:
        if day is None:
            return 0.0
        last = self._rendered.get(entity)
        gap = STALE_CAP_DAYS if last is None else max(0, min(day - last, STALE_CAP_DAYS))
        return STALE_PER_DAY * gap

    def score(self, entity: str, tick: int, day: int | None = None) -> float:
        return (
            self._focus.get(entity, 0.0)
            + self._decayed_perturb(entity, tick)
            + self._staleness(entity, day)
        )

    def top_k(self, entities: list[str], k: int, tick: int, day: int | None = None) -> list[str]:
        """Deterministic: explicit focus first, then score desc, then id asc.

        Focus is lexicographically dominant rather than merely additive — a
        followed family must not be pushed off the screen by a big enough
        accident somewhere else."""
        return sorted(
            entities,
            key=lambda e: (-self._focus.get(e, 0.0), -self.score(e, tick, day), e),
        )[:k]
