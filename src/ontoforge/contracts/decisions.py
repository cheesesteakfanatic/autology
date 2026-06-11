"""The Decision Spine contract (whitepaper §8, M2).

Every consequential judgment in the platform — entity match, schema correspondence,
relationship inference, extraction grounding, transform acceptance, query
interpretation, concept admission (AMD-0005) — is ONE abstract problem:
cost-sensitive selective classification with calibrated confidence and conformal
deferral. Modules construct a DecisionRequest; the spine answers with a
DecisionResult carrying confidence, conformal set, tier-of-record, and cost.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol, Sequence


class DecisionKind(str, Enum):
    ER = "er"          # entity match
    SM = "sm"          # schema correspondence
    REL = "rel"        # relationship inference
    EX = "ex"          # extraction grounding
    TX = "tx"          # transform-synthesis acceptance
    QI = "qi"          # query-interpretation selection
    ADMIT = "admit"    # STRATA concept admission (AMD-0005)


class Tier(int, Enum):
    T0 = 0   # deterministic rules
    T1 = 1   # classical ML / statistical
    T2 = 2   # distilled specialist
    T3 = 3   # frontier LLM
    HUMAN = 4


@dataclass(frozen=True, slots=True)
class DecisionRequest:
    kind: DecisionKind
    decision_id: str                      # caller-stable id (memo key participates)
    candidates: tuple[str, ...]           # candidate outcome labels (≥2; binary = ("no","yes"))
    features: tuple[tuple[str, float], ...] = ()   # numeric evidence for T1 scoring
    context: tuple[tuple[str, Any], ...] = ()      # opaque evidence for T2/T3 prompts
    impact: float = 1.0                   # high-impact decisions escalate more readily
    prov_atoms: tuple[str, ...] = ()      # atom_ids supporting this decision (ledger record)


@dataclass(frozen=True, slots=True)
class DecisionResult:
    decision_id: str
    outcome: str                          # chosen candidate label
    confidence: float                     # CALIBRATED probability of outcome
    conformal_set: tuple[str, ...]        # prediction set at level alpha
    tier: Tier
    cost_tokens: int = 0
    deferred_to_human: bool = False
    quarantined: bool = False             # budget-exhaustion fail-closed (§8 economy)
    rationale: str = ""

    @property
    def auto_decided(self) -> bool:
        return not self.deferred_to_human and not self.quarantined


@dataclass(frozen=True, slots=True)
class CalibrationSample:
    """Ground-truth feedback for recalibration (human review, gold labels, AL loop)."""

    kind: DecisionKind
    features: tuple[tuple[str, float], ...]
    candidates: tuple[str, ...]
    true_outcome: str
    predicted_confidence: float = 0.0


@dataclass(frozen=True, slots=True)
class SpineProfile:
    """Economy binds a token budget; CRUCIBLE sets the budget shadow price ~0 (§8)."""

    name: str = "economy"                 # "economy" | "crucible"
    budget_tokens: int = 1_000_000        # per-cycle budget (economy)
    alpha: float = 0.1                    # conformal miscoverage level
    tau_high: float = 0.92                # auto-accept threshold
    tau_low: float = 0.30                 # auto-reject threshold (binary kinds)


class Spine(Protocol):
    def decide(self, req: DecisionRequest) -> DecisionResult: ...
    def recalibrate(self, kind: DecisionKind, samples: Sequence[CalibrationSample]) -> None: ...
    def set_profile(self, profile: SpineProfile) -> None: ...
    def spent_tokens(self) -> int: ...


@dataclass(slots=True)
class TierScore:
    """What a tier hands back to the spine's router for one decision."""

    scores: dict[str, float] = field(default_factory=dict)  # candidate -> raw score
    cost_tokens: int = 0
    abstain: bool = False                 # tier cannot score this decision
