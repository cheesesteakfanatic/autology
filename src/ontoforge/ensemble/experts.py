"""Deterministic experts for the keyless DE decision ensemble.

Each expert votes on a data-engineering action (join / merge / retype) given an
:class:`ActionContext` — a typed, model-free description of the action and the
real evidence the engineer layer already measured (coverage, value overlap, parse
rate, type/unit compatibility). An expert returns a :class:`Vote`
(``decision`` 'fire'|'hold', a ``confidence`` in [0,1], and a ``rationale``).

The point (docs/AI_NATIVE_AND_UI_PLAN.md §D): the ensemble is genuinely diverse
and useful NOW with zero model calls — each expert weighs a *different* facet of
the evidence, so weighted-majority aggregation is meaningful today. Swapping in a
live model is registering a router ``ModelSpec`` as an additional expert; the gate
math is unchanged because every expert — heuristic or model — speaks this same
:class:`Vote` protocol.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional, Protocol, runtime_checkable

__all__ = [
    "ActionContext",
    "CoverageExpert",
    "Expert",
    "NameSimilarityExpert",
    "TypeCompatExpert",
    "ValueOverlapExpert",
    "Vote",
    "default_experts",
]

_TOK = re.compile(r"[a-z0-9]+")


def _tokens(s: str) -> set[str]:
    return set(_TOK.findall(str(s or "").lower()))


@dataclass(frozen=True, slots=True)
class ActionContext:
    """Everything an expert needs to vote, model-free. Populated by the engineer
    layer from real measurements over the live HEARTH.

    * ``action`` — 'join' | 'merge' | 'retype'.
    * ``coverage`` — the match-coverage / parse-rate the verifier measured (the
      same number the engineer's confidently-wrong floor uses).
    * ``value_overlap`` — fraction of distinct left values present on the right.
    * ``left_name`` / ``right_name`` — the column/class display names being related.
    * ``left_type`` / ``right_type`` — datatypes (for type/unit compatibility).
    * ``left_unit`` / ``right_unit`` — units, when known.
    """

    action: str
    coverage: Optional[float] = None
    value_overlap: Optional[float] = None
    left_name: str = ""
    right_name: str = ""
    left_type: str = ""
    right_type: str = ""
    left_unit: Optional[str] = None
    right_unit: Optional[str] = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Vote:
    """An expert's verdict. ``confidence`` is the expert's own certainty in its
    decision (NOT a probability the action is correct); the gate combines these."""

    decision: str  # 'fire' | 'hold'
    confidence: float
    rationale: str = ""
    name: str = ""

    def __post_init__(self) -> None:
        if self.decision not in ("fire", "hold"):
            raise ValueError(f"decision must be 'fire'|'hold', got {self.decision!r}")


@runtime_checkable
class Expert(Protocol):
    name: str

    def vote(self, ctx: ActionContext) -> Vote: ...


# --------------------------------------------------------------------------
# Concrete deterministic experts
# --------------------------------------------------------------------------


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, x))


@dataclass(frozen=True, slots=True)
class CoverageExpert:
    """Votes on the execution-grounded coverage the verifier measured — the
    strongest evidence for a join/retype. High coverage => confident fire."""

    name: str = "coverage"
    fire_at: float = 0.7

    def vote(self, ctx: ActionContext) -> Vote:
        cov = ctx.coverage
        if cov is None:
            return Vote("hold", 0.2, "no coverage measured", self.name)
        if cov >= self.fire_at:
            return Vote("fire", _clamp(cov), f"coverage {cov:.2f} >= {self.fire_at}", self.name)
        # the further below the floor, the more confident the HOLD
        return Vote("hold", _clamp(1.0 - cov), f"coverage {cov:.2f} < {self.fire_at}", self.name)


@dataclass(frozen=True, slots=True)
class ValueOverlapExpert:
    """Votes on raw distinct-value overlap (a join key signal independent of the
    verifier's coverage framing)."""

    name: str = "value_overlap"
    fire_at: float = 0.6

    def vote(self, ctx: ActionContext) -> Vote:
        ov = ctx.value_overlap
        if ov is None:
            ov = ctx.coverage  # fall back to coverage when overlap not separately given
        if ov is None:
            return Vote("hold", 0.2, "no overlap measured", self.name)
        if ov >= self.fire_at:
            return Vote("fire", _clamp(ov), f"value overlap {ov:.2f}", self.name)
        return Vote("hold", _clamp(1.0 - ov), f"low value overlap {ov:.2f}", self.name)


@dataclass(frozen=True, slots=True)
class NameSimilarityExpert:
    """Votes on lexical similarity of the two names (a 'cust_id' ~ 'customer_id'
    soft signal). Weak on its own — which is exactly why per-expert weighting
    matters: it should not outweigh coverage."""

    name: str = "name_similarity"
    fire_at: float = 0.34

    def vote(self, ctx: ActionContext) -> Vote:
        a, b = _tokens(ctx.left_name), _tokens(ctx.right_name)
        if not a or not b:
            return Vote("hold", 0.2, "names unavailable", self.name)
        inter = len(a & b)
        jac = inter / len(a | b)
        # substring affinity (id ~ customer_id)
        sub = 0.0
        for x in a:
            for y in b:
                if x != y and (x in y or y in x) and min(len(x), len(y)) >= 2:
                    sub = max(sub, 0.4)
        sim = min(1.0, jac + sub)
        if sim >= self.fire_at:
            return Vote("fire", _clamp(0.4 + 0.6 * sim), f"name similarity {sim:.2f}", self.name)
        return Vote("hold", _clamp(0.5 + 0.5 * (1 - sim)), f"names dissimilar {sim:.2f}", self.name)


@dataclass(frozen=True, slots=True)
class TypeCompatExpert:
    """Votes on type/unit compatibility. Incompatible datatypes or mismatched
    units are a strong HOLD (you cannot join a date to a number, or rescale
    across incompatible units silently)."""

    name: str = "type_compat"

    def vote(self, ctx: ActionContext) -> Vote:
        lt, rt = (ctx.left_type or "").lower(), (ctx.right_type or "").lower()
        if not lt or not rt:
            # no type info => abstain-ish low-confidence fire so it doesn't veto by weight
            return Vote("fire", 0.25, "no type info; deferring to other experts", self.name)
        numeric = {"integer", "float", "number", "decimal"}
        textual = {"string", "text"}
        compatible = (
            lt == rt
            or (lt in numeric and rt in numeric)
            or (lt in textual and rt in textual)
        )
        if not compatible:
            return Vote("hold", 0.85, f"incompatible types {lt} vs {rt}", self.name)
        # units: if both present and differ, mild hold
        if ctx.left_unit and ctx.right_unit and ctx.left_unit != ctx.right_unit:
            return Vote("hold", 0.6, f"unit mismatch {ctx.left_unit} vs {ctx.right_unit}", self.name)
        return Vote("fire", 0.7, f"types compatible ({lt}~{rt})", self.name)


def default_experts() -> list[Expert]:
    """The default keyless, diverse ensemble for DE-action gating."""
    return [CoverageExpert(), ValueOverlapExpert(), NameSimilarityExpert(), TypeCompatExpert()]
