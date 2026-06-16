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

Typed-relationship voting (v2.1 §1.3 — CLOSED-CORE IP, OntoForge_Build_Instructions
.md §18) layers a SECOND gate on top, without touching the fire/hold one:

* :mod:`paths`   — three DISTINCT reasoning-path experts (schema-centric /
  value-centric / business-logic-centric) that each cast a typed
  :class:`~ontoforge.contracts.PathVote` from a
  :class:`~ontoforge.contracts.RelationshipCandidate` + its evidence + an optional
  :class:`~ontoforge.contracts.JoinValidation`. Distinct reasoning, not temperature
  noise; each path is a seam for a per-path LLM call later.
* :mod:`relgate` — :class:`~relgate.RelationshipGate`: PLURALITY vote on the
  relationship TYPE, MEDIAN-of-path confidence, SQL backward-validation as a strong
  booster/veto, commit only on consensus else route to a human. :func:`~relgate.
  should_vote` is the scalpel — vote only on ambiguous/borderline candidates.
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
from .paths import (
    BusinessLogicPath,
    PathExpert,
    ReasoningPathExpert,
    SchemaPath,
    ValuePath,
    default_paths,
)
from .relgate import (
    AMBIGUOUS_BAND,
    CONSENSUS_THRESHOLD,
    RelationshipGate,
    should_vote,
)

__all__ = [
    "ActionContext",
    "AMBIGUOUS_BAND",
    "BusinessLogicPath",
    "CONSENSUS_THRESHOLD",
    "CoverageExpert",
    "DEFAULT_THRESHOLD",
    "Expert",
    "Gate",
    "GateDecision",
    "NameSimilarityExpert",
    "PathExpert",
    "ReasoningPathExpert",
    "RelationshipGate",
    "SchemaPath",
    "TypeCompatExpert",
    "ValueOverlapExpert",
    "ValuePath",
    "Vote",
    "default_experts",
    "default_paths",
    "should_vote",
    "soft_self_consistency",
    "turn_temperature",
]
