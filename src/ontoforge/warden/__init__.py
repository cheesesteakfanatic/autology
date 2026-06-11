"""M9 — WARDEN: Expectation & Drift Sentinel Synthesis (whitepaper §5.3, §11.2 M9).

Three generators, zero human authoring:

- Σ-compilation (`expectations`): every contracts.ShapeConstraint lowers to
  executable streaming expectations over DataFrame batches (coverage metric
  gated >= 0.95 on the gold ontology).
- Sketch-drift sentinels (`drift`): PSI on quantile sketches, MinHash-Jaccard
  shift, EWMA 3-sigma control charts on null-rate/cardinality, schema diffing.
- Routing (`routing`): schema drift -> TemperProposal, distribution drift ->
  AnvilReverification, quality drift -> Quarantine + Alert. Alarms are spine
  decisions (alert precision tunable via the spine threshold).
- Contract emission (`contracts_emit`): the implied per-table data contract as
  a markdown ledger artifact (kind 'data-contract').
"""

from .contracts_emit import contract_artifact_id, emit_contract, parse_contract
from .drift import (
    DriftSentinel,
    DriftSignal,
    EwmaChart,
    population_stability_index,
    severity_of,
)
from .expectations import (
    CompilationReport,
    Expectation,
    ExpectationResult,
    compile_class,
    compile_constraint,
    compile_ontology,
    evaluate_class,
)
from .routing import (
    WARDEN_PROFILE,
    Alert,
    AnvilReverification,
    Quarantine,
    RoutingResult,
    TemperProposal,
    WardenRouter,
    warden_spine,
)

__all__ = [
    "WARDEN_PROFILE",
    "Alert",
    "AnvilReverification",
    "CompilationReport",
    "DriftSentinel",
    "DriftSignal",
    "EwmaChart",
    "Expectation",
    "ExpectationResult",
    "Quarantine",
    "RoutingResult",
    "TemperProposal",
    "WardenRouter",
    "compile_class",
    "compile_constraint",
    "compile_ontology",
    "contract_artifact_id",
    "emit_contract",
    "evaluate_class",
    "parse_contract",
    "population_stability_index",
    "severity_of",
    "warden_spine",
]
