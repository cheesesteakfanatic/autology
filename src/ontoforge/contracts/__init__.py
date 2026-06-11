"""OntoForge contract package — the shared typed interfaces every module builds against.

Whitepaper §18.1: "shared interfaces live in dedicated contract crates/packages...
Implementer agents work in disjoint trees and integrate only through versioned contracts."

Module agents: import from here; NEVER edit this package. Interface changes are spec
amendments (docs/DEVIATIONS.md) made by the architect.
"""

from .atoms import Atom, cell_uri, make_cell_atom, make_span_atom, span_uri, value_repr
from .cells import LinkCell, ValueCell
from .decisions import (
    CalibrationSample,
    DecisionKind,
    DecisionRequest,
    DecisionResult,
    Spine,
    SpineProfile,
    Tier,
    TierScore,
)
from .models import CostMeter, ModelClient, ModelRequest, ModelResponse
from .ontology import (
    ClassDef,
    Datatype,
    Ontology,
    PropertyDef,
    ShapeConstraint,
    class_uri_from_intent,
    property_uri,
)
from .oqir import (
    Agg,
    Aggregate,
    Answer,
    AsOf,
    CitedCell,
    CmpOp,
    Condition,
    EntitySetT,
    MeasureT,
    OQIRTerm,
    OQIRType,
    ScalarT,
    Select,
    TableT,
    TextJoin,
    TopK,
    Traverse,
    TypeError_,
)
from .profiles import FD, IND, ColumnProfile, TableProfile, minhash_jaccard
from .provenance import (
    ONE,
    ZERO,
    Leaf,
    Prod,
    ProvTerm,
    Semiring,
    Sum,
    leaf,
    leaves,
    map_leaves,
    prov_prod,
    prov_sum,
    term_hash,
    valuate,
)
from .temporal import CURRENT, FOREVER, Instant, Interval, Stance, from_instant, now_instant, to_instant
from .transforms import ColumnLineage, Layer, RunRecord, TransformDef, VerificationReport
from .units import (
    BASIS,
    COUNT,
    CURRENCY,
    DIMENSIONLESS,
    LENGTH,
    MASS,
    SPEED,
    TEMPERATURE,
    TIME,
    Dimension,
    UnitDef,
    dim,
)

__all__ = [name for name in dir() if not name.startswith("_")]
