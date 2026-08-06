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


class AttentionField:
    def __init__(self, *, half_life_ticks: float = 48.0):
        self._decay = math.log(2.0) / half_life_ticks
        self._focus: dict[str, float] = {}
        self._perturb: dict[str, tuple[float, int]] = {}  # entity -> (amount, tick)

    def set_focus(self, entity: str, level: float) -> None:
        if level <= 0:
            self._focus.pop(entity, None)
        else:
            self._focus[entity] = level

    def bump(self, entity: str, amount: float, tick: int) -> None:
        """Perturbation from events touching this entity; accumulates decayed."""
        prior = self._decayed_perturb(entity, tick)
        self._perturb[entity] = (prior + amount, tick)

    def _decayed_perturb(self, entity: str, tick: int) -> float:
        if entity not in self._perturb:
            return 0.0
        amount, at = self._perturb[entity]
        return amount * math.exp(-self._decay * max(0, tick - at))

    def score(self, entity: str, tick: int) -> float:
        return self._focus.get(entity, 0.0) + self._decayed_perturb(entity, tick)

    def top_k(self, entities: list[str], k: int, tick: int) -> list[str]:
        """Deterministic: score desc, then entity id asc as tie-break."""
        return sorted(entities, key=lambda e: (-self.score(e, tick), e))[:k]
