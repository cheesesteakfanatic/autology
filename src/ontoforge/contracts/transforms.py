"""Transform artifacts (whitepaper §5.1): declarative, versioned, human-readable.

The DSL body is a restricted SQL dialect (DuckDB-executable, sqlglot-parseable;
M7 enforces the operator allowlist). Versions are content-fingerprinted; the
fingerprint is the virtual-environment memo key (changed transform ⇒ new
fingerprint ⇒ shadow output; unchanged upstream reused by fingerprint).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

import xxhash

from .ontology import ShapeConstraint


class Layer(str, Enum):
    RAW = "raw"
    CONFORMED = "conformed"
    ENTITY = "entity"


@dataclass(frozen=True, slots=True)
class TransformDef:
    name: str
    inputs: tuple[str, ...]               # input table names (layer-qualified, e.g. "raw.faa_master")
    output: str                           # output table name
    sql: str                              # DSL body (restricted SQL)
    output_layer: Layer = Layer.CONFORMED
    expectations: tuple[ShapeConstraint, ...] = ()
    description: str = ""
    synthesized_by: str = ""              # "" = human; else "anvil:T0", "anvil:T1", ...
    version: int = 1

    @property
    def fingerprint(self) -> str:
        h = xxhash.xxh3_64()
        for part in (self.name, *sorted(self.inputs), self.output, " ".join(self.sql.split())):
            h.update(part.encode())
            h.update(b"\x1f")
        return f"{h.intdigest():016x}"


@dataclass(frozen=True, slots=True)
class ColumnLineage:
    """Output column -> the input (table, column) set it derives from + the op chain."""

    output_column: str
    inputs: tuple[tuple[str, str], ...]   # (table, column) pairs
    operations: tuple[str, ...] = ()      # e.g. ("CAST", "UPPER")


@dataclass(frozen=True, slots=True)
class RunRecord:
    transform_fingerprint: str
    started_at: int                       # Instant
    finished_at: int
    rows_in: int
    rows_out: int
    status: str = "success"               # success | failed | skipped(memo)
    error: str = ""
    delta_run: bool = False


@dataclass(slots=True)
class VerificationReport:
    """ANVIL's per-candidate evidence (§5.2 step 2): holdout + provenance equivalence."""

    holdout_rows: int = 0
    holdout_pass_rate: float = 0.0
    shapes_satisfied: bool = False
    provenance_equivalent: Optional[bool] = None   # None = check not applicable
    program_complexity: int = 0                    # MDL-style prior input
    notes: list[str] = field(default_factory=list)
