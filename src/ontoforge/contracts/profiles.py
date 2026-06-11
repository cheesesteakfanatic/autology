"""Profile sketches φ(p) — the universal feature object (whitepaper §3.1).

One profiling pass produces these; THREE consumers read them: STRATA's formal
context (M4), ANVIL's fix detectors and search pruning (M8), WARDEN's drift
sentinels (M9). The sketch is also the memo key for schema-level decisions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import xxhash

from .ontology import Datatype
from .units import Dimension


@dataclass(frozen=True, slots=True)
class ColumnProfile:
    source_id: str
    table: str
    column: str
    inferred_type: Datatype
    row_count: int
    null_count: int
    distinct_estimate: int                  # HLL-class estimate
    quantiles: tuple[float, ...] = ()       # numeric cols: deciles (KLL-class)
    minhash: tuple[int, ...] = ()           # k-MinHash signature of the value set
    format_signature: str = ""              # generalized regex over samples
    sample_values: tuple[str, ...] = ()     # small stratified sample (for T2/T3 prompts)
    token_stats: tuple[tuple[str, float], ...] = ()   # top tokens w/ freq (doc-ish cols)
    unit: Optional[str] = None              # detected unit symbol
    dimension: Optional[Dimension] = None
    semantic_type: str = ""                 # T1 semantic class, e.g. "tail_number"
    semantic_confidence: float = 0.0

    @property
    def null_rate(self) -> float:
        return self.null_count / self.row_count if self.row_count else 0.0

    @property
    def uniqueness(self) -> float:
        nn = self.row_count - self.null_count
        return self.distinct_estimate / nn if nn > 0 else 0.0

    def sketch_key(self) -> str:
        """Memo key: stable under re-profiling of identical data."""
        h = xxhash.xxh3_64()
        for part in (
            self.inferred_type.value,
            self.format_signature,
            str(self.distinct_estimate),
            str(round(self.null_rate, 3)),
            self.semantic_type,
            str(self.unit),
        ):
            h.update(part.encode())
            h.update(b"\x1f")
        return f"{h.intdigest():016x}"


def minhash_jaccard(a: tuple[int, ...], b: tuple[int, ...]) -> float:
    """Estimate Jaccard similarity of two value sets from equal-length signatures."""
    if not a or not b or len(a) != len(b):
        return 0.0
    return sum(1 for x, y in zip(a, b) if x == y) / len(a)


@dataclass(frozen=True, slots=True)
class FD:
    """Functional dependency lhs -> rhs within `table` (confidence < 1.0 ⇒ approximate)."""

    table: str
    lhs: tuple[str, ...]
    rhs: str
    confidence: float = 1.0


@dataclass(frozen=True, slots=True)
class IND:
    """Inclusion dependency: values(lhs) ⊆ values(rhs), across tables. Join evidence."""

    lhs_table: str
    lhs_column: str
    rhs_table: str
    rhs_column: str
    coverage: float = 1.0        # fraction of lhs values found in rhs
    score: float = 0.0           # composite join-candidate score (name/type/cardinality)


@dataclass(slots=True)
class TableProfile:
    source_id: str
    table: str
    row_count: int
    columns: dict[str, ColumnProfile] = field(default_factory=dict)
    candidate_keys: tuple[tuple[str, ...], ...] = ()
    fds: tuple[FD, ...] = ()
    append_mostly: bool = False    # §3.5 event signal: inserts >> updates
