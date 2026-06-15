"""The DE decision gate — weighted-majority voting over deterministic experts.

docs/AI_NATIVE_AND_UI_PLAN.md §D, realized keyless. The gate decides whether a
data-engineering action (join / merge / retype) should FIRE:

* :mod:`experts` — the :class:`~experts.Expert` protocol (``vote(ctx) -> Vote``)
  and a diverse deterministic ensemble (coverage / value-overlap / name-similarity
  / type-compatibility). Live models register as additional experts speaking the
  same protocol — the gate math is unchanged.
* :mod:`gate`    — :class:`~gate.Gate`: per-expert Weighted-Majority Aggregation
  with a Littlestone–Warmuth multiplicative penalty (ε = √(ln N / T)) wired to the
  review queue's Confirm/Reject; a label-free TURN aggregation temperature;
  Soft-Self-Consistency continuous scoring gated on a calibrated threshold; and an
  execution-grounded verifier VETO that overrides any vote (the confidently-wrong
  guard for the gate).
"""

from .experts import (
    ActionContext,
    CoverageExpert,
    Expert,
    NameSimilarityExpert,
    TypeCompatExpert,
    ValueOverlapExpert,
    Vote,
    default_experts,
)
from .gate import (
    DEFAULT_THRESHOLD,
    Gate,
    GateDecision,
    soft_self_consistency,
    turn_temperature,
)

__all__ = [
    "ActionContext",
    "CoverageExpert",
    "DEFAULT_THRESHOLD",
    "Expert",
    "Gate",
    "GateDecision",
    "NameSimilarityExpert",
    "TypeCompatExpert",
    "ValueOverlapExpert",
    "Vote",
    "default_experts",
    "soft_self_consistency",
    "turn_temperature",
]
