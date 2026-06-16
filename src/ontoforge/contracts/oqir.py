"""OQIR — the Ontology-Grounded Query Intermediate Representation (whitepaper §6.2).

A LODESTONE plan is a term in this small typed algebra over the INDUCED ontology,
never over physical schemas. Static type-checking (M12) rejects the dominant
NL2SQL error classes before execution: phantom joins (traverse must follow a real
link type), wrong-grain aggregation, and unit mixing (Measure dimensions must agree).

Terms here are pure data; the checker, lowering rules, and execution live in M12.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Union

from .temporal import Stance
from .units import Dimension


# ------------------------------------------------------------------- types


@dataclass(frozen=True, slots=True)
class EntitySetT:
    class_uri: str


@dataclass(frozen=True, slots=True)
class TableT:
    columns: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MeasureT:
    dimension: Dimension
    unit: Optional[str] = None


@dataclass(frozen=True, slots=True)
class ScalarT:
    pass


OQIRType = Union[EntitySetT, TableT, MeasureT, ScalarT]


# ------------------------------------------------------------- predicates


class CmpOp(str, Enum):
    EQ = "="
    NE = "!="
    LT = "<"
    LE = "<="
    GT = ">"
    GE = ">="
    CONTAINS = "contains"      # substring / text match
    IN = "in"
    BETWEEN = "between"


@dataclass(frozen=True, slots=True)
class Condition:
    prop: str
    op: CmpOp
    value: object
    value2: object = None      # BETWEEN upper bound
    unit: Optional[str] = None  # unit the literal is expressed in (checker converts)


# ------------------------------------------------------------------ terms


class Agg(str, Enum):
    COUNT = "count"
    SUM = "sum"
    AVG = "avg"
    MIN = "min"
    MAX = "max"


@dataclass(frozen=True, slots=True)
class Select:
    """select(class, conditions) -> EntitySet<class>"""

    class_uri: str
    conditions: tuple[Condition, ...] = ()


@dataclass(frozen=True, slots=True)
class Traverse:
    """traverse(E, link) -> EntitySet<range(link)>; link must exist in O."""

    source: "OQIRTerm"
    link: str                   # property name on the source class
    reverse: bool = False       # follow incoming edges
    conditions: tuple[Condition, ...] = ()   # filter on the target class


@dataclass(frozen=True, slots=True)
class TextJoin:
    """textJoin(E, pattern) -> EntitySet: entities whose TEXT property matches (§6.2)."""

    source: "OQIRTerm"
    text_prop: str
    pattern: str


@dataclass(frozen=True, slots=True)
class Aggregate:
    """aggregate(E, measure, group_by) -> Table"""

    source: "OQIRTerm"
    agg: Agg
    measure_prop: Optional[str] = None       # None only for COUNT
    group_by: tuple[str, ...] = ()           # property names; may be "link.prop" paths
    having: tuple[Condition, ...] = ()


@dataclass(frozen=True, slots=True)
class TopK:
    source: "OQIRTerm"          # must type to Table
    by: str                     # column
    k: int = 10
    descending: bool = True


@dataclass(frozen=True, slots=True)
class AsOf:
    """asOf(stance, term): wraps any subterm in a temporal stance (§4.4)."""

    stance: Stance
    term: "OQIRTerm"


OQIRTerm = Union[Select, Traverse, TextJoin, Aggregate, TopK, AsOf]


# ------------------------------------------------------------ plan results


@dataclass(frozen=True, slots=True)
class CitedCell:
    """One answer cell with its provenance citations (atom_ids) — §6.2 per-cell citations."""

    row: int
    column: str
    value: object
    atom_ids: tuple[str, ...]


@dataclass(slots=True)
class Answer:
    columns: list[str] = field(default_factory=list)
    rows: list[list[object]] = field(default_factory=list)
    citations: list[CitedCell] = field(default_factory=list)
    confidence: float = 0.0
    oqir: Optional[OQIRTerm] = None
    abstained: bool = False
    abstain_reason: str = ""
    clarification: Optional[str] = None      # the ONE question, when conformal set > 1
    clarification_options: tuple[str, ...] = ()
    # --- the Ask flywheel (v2.1 §4): when this answer was SERVED from the
    # cached-work store instead of freshly composed, `cached` is True and
    # `cache_object_id` / `cache_description` reference the stored object so the
    # served answer is itself referenceable downstream. A live composition leaves
    # these at their defaults.
    cached: bool = False
    cache_object_id: Optional[str] = None
    cache_description: str = ""


@dataclass(frozen=True, slots=True)
class TypeError_:
    """A static OQIR type-check failure (named with underscore to avoid builtin clash)."""

    message: str
    term_repr: str = ""
