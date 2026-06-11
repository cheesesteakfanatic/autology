"""Drift routing (whitepaper §5.3): change-points become typed work items.

- schema drift  -> TemperProposal (suspected TEMPER operator + evidence)
- distribution  -> AnvilReverification (transform fingerprints whose inputs drifted)
- quality drift -> Quarantine record + Alert

Every alarm is a SPINE DECISION: a binary 'warden.alarm' judgement under
DecisionKind.SM, scored by a deterministic T0 rule from the signal's severity.
Alert precision is therefore tunable via the spine profile's tau_high — raising
it suppresses marginal alarms (the §5.3 alert-fatigue calibration knob). The
records are plain typed dataclasses: TEMPER (M10) and ANVIL (M8) consume them
later; no cross-module imports here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Optional, Sequence

from ontoforge.contracts import (
    DecisionKind,
    DecisionRequest,
    DecisionResult,
    SpineProfile,
    TierScore,
)
from ontoforge.spine import DecisionSpine

from .drift import DriftSignal

__all__ = [
    "TemperProposal",
    "AnvilReverification",
    "Quarantine",
    "Alert",
    "RoutingResult",
    "WardenRouter",
    "warden_spine",
    "WARDEN_PROFILE",
    "SEVERITY_FEATURE",
]

#: feature name carrying the sentinel severity into the spine's T0 rule.
SEVERITY_FEATURE = "warden.severity"

#: default warden spine profile: tau_high is the calibrated alert threshold.
#: severity_of() puts a just-over-threshold excursion at 0.5 and a >=2x
#: excursion at 0.99, so tau_high=0.6 alarms on clear excursions and routes the
#: marginal band to deferral. Raise tau_high for higher alert precision.
WARDEN_PROFILE = SpineProfile(name="economy", tau_high=0.6, tau_low=0.4)

#: detector -> suspected TEMPER operator (§3.6 operator vocabulary).
SCHEMA_OPERATORS: dict[str, str] = {
    "column_added": "AddProperty",
    "column_removed": "RemoveProperty",
    "column_renamed": "RenameProperty",
    "column_retyped": "RetypeProperty",
    "format_signature_changed": "ReformatProperty",
}


@dataclass(frozen=True, slots=True)
class TemperProposal:
    """Schema drift -> proposed ontology-evolution operator for TEMPER (M10)."""

    table: str
    column: str
    suspected_operator: str          # AddProperty | RemoveProperty | RenameProperty | ...
    evidence: tuple[str, ...]
    signal: DriftSignal
    decision_id: str
    confidence: float


@dataclass(frozen=True, slots=True)
class AnvilReverification:
    """Distribution drift -> re-verify the transforms reading the drifted input (M8)."""

    table: str
    column: str
    transform_ids: tuple[str, ...]   # fingerprints of dependent transforms
    signal: DriftSignal
    decision_id: str
    confidence: float


@dataclass(frozen=True, slots=True)
class Quarantine:
    """Quality drift -> hold the batch out of the entity layer pending review."""

    table: str
    column: str
    reason: str
    signal: DriftSignal
    decision_id: str
    confidence: float


@dataclass(frozen=True, slots=True)
class Alert:
    """Human-facing alert accompanying a quarantine (or any routed alarm)."""

    table: str
    column: str
    kind: str
    message: str
    severity: float
    decision_id: str


@dataclass(slots=True)
class RoutingResult:
    proposals: list[TemperProposal] = field(default_factory=list)
    reverifications: list[AnvilReverification] = field(default_factory=list)
    quarantines: list[Quarantine] = field(default_factory=list)
    alerts: list[Alert] = field(default_factory=list)
    decisions: list[DecisionResult] = field(default_factory=list)
    suppressed: list[DriftSignal] = field(default_factory=list)   # spine said no / deferred

    @property
    def alarm_count(self) -> int:
        return len(self.proposals) + len(self.reverifications) + len(self.quarantines)


def _alarm_rule(req: DecisionRequest) -> Optional[TierScore]:
    """T0 rule: the sentinel severity is the (deterministic) alarm score."""
    fmap = dict(req.features)
    sev = fmap.get(SEVERITY_FEATURE)
    if sev is None:
        return None
    sev = max(0.0, min(1.0, float(sev)))
    return TierScore(scores={"no-alarm": 1.0 - sev, "alarm": sev})


def warden_spine(profile: SpineProfile = WARDEN_PROFILE, ledger=None) -> DecisionSpine:
    """A spine wired with the warden.alarm T0 rule. Drift alarms are decisions:
    they land in the decision ledger and their threshold is spine-calibrated."""
    spine = DecisionSpine(profile, ledger=ledger)
    spine.register_rule(DecisionKind.SM, _alarm_rule)
    return spine


class WardenRouter:
    """Routes DriftSignals to TEMPER / ANVIL / quarantine work items via the spine.

    `transform_index` maps a drifted input to dependent transform fingerprints:
    keys are (table, column) and/or bare table names — accepted as a parameter
    because M7/M8 are built in parallel (plain data in, plain records out).
    """

    def __init__(
        self,
        spine: Optional[DecisionSpine] = None,
        *,
        transform_index: Optional[Mapping] = None,
        ledger=None,
    ) -> None:
        self.spine = spine if spine is not None else warden_spine(ledger=ledger)
        self.transform_index = dict(transform_index or {})

    # -- spine adjudication

    def _decide(self, sig: DriftSignal) -> DecisionResult:
        decision_id = (
            f"warden.alarm/{sig.table}/{sig.column}/{sig.detector}/c{sig.cycle}"
        )
        req = DecisionRequest(
            kind=DecisionKind.SM,
            decision_id=decision_id,
            candidates=("no-alarm", "alarm"),
            features=((SEVERITY_FEATURE, sig.severity),),
            context=(
                ("warden.kind", sig.kind),
                ("warden.detector", sig.detector),
                ("warden.statistic", sig.statistic),
                ("warden.threshold", sig.threshold),
                ("warden.detail", sig.detail),
            ),
        )
        return self.spine.decide(req)

    # -- routing

    def route(self, signals: Sequence[DriftSignal]) -> RoutingResult:
        out = RoutingResult()
        for sig in signals:
            result = self._decide(sig)
            out.decisions.append(result)
            if result.outcome != "alarm" or not result.auto_decided:
                out.suppressed.append(sig)
                continue
            if sig.kind == "schema":
                op = SCHEMA_OPERATORS.get(sig.detector, "ReviewProperty")
                out.proposals.append(TemperProposal(
                    table=sig.table,
                    column=sig.column,
                    suspected_operator=op,
                    evidence=(sig.detail, f"{sig.detector}: stat={sig.statistic}"),
                    signal=sig,
                    decision_id=result.decision_id,
                    confidence=result.confidence,
                ))
            elif sig.kind == "distribution":
                out.reverifications.append(AnvilReverification(
                    table=sig.table,
                    column=sig.column,
                    transform_ids=self._transforms_for(sig.table, sig.column),
                    signal=sig,
                    decision_id=result.decision_id,
                    confidence=result.confidence,
                ))
            elif sig.kind == "quality":
                out.quarantines.append(Quarantine(
                    table=sig.table,
                    column=sig.column,
                    reason=sig.detail,
                    signal=sig,
                    decision_id=result.decision_id,
                    confidence=result.confidence,
                ))
                out.alerts.append(Alert(
                    table=sig.table,
                    column=sig.column,
                    kind=sig.detector,
                    message=f"quality drift on {sig.table}.{sig.column}: {sig.detail}",
                    severity=sig.severity,
                    decision_id=result.decision_id,
                ))
        return out

    def _transforms_for(self, table: str, column: str) -> tuple[str, ...]:
        ids: list[str] = []
        ids.extend(self.transform_index.get((table, column), ()))
        ids.extend(self.transform_index.get(table, ()))
        seen: set[str] = set()
        uniq = [t for t in ids if not (t in seen or seen.add(t))]
        return tuple(uniq)
