"""M2 — the Decision Spine (whitepaper §8, §11.2 M2; MVP plan §2, §5.3).

Every consequential judgment in the platform is one abstract problem:
cost-sensitive selective classification with calibrated confidence and conformal
deferral. This package implements the `ontoforge.contracts.decisions.Spine`
protocol: a four-tier escalation chain (T0 rules -> T1 calibrated logistic ->
T2 distilled specialist -> T3 frontier model -> human), per-kind Platt/isotonic
recalibration, split conformal prediction sets, the two-threshold selective
rule, and a fail-closed token-budget governor with economy/CRUCIBLE profiles.
"""

from .adjudicator import Adjudication, build_prompt, parse_adjudication
from .calibration import (
    CalibrationReport,
    KindCalibrator,
    expected_calibration_error,
    heuristic_probabilities,
)
from .spine import DecisionSpine

__all__ = [
    "Adjudication",
    "CalibrationReport",
    "DecisionSpine",
    "KindCalibrator",
    "build_prompt",
    "expected_calibration_error",
    "heuristic_probabilities",
    "parse_adjudication",
]
