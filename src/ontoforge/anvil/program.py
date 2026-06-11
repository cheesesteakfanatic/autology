"""Candidate-program representation for ANVIL (§5.2).

A CandidateProgram is a small declarative pipeline over ONE source table
(optionally joined to one auxiliary table along a discovered IND), compiled to
restricted DuckDB SQL. The same program compiles in two modes:

* production SQL  — what ships inside a contracts.TransformDef;
* tagged SQL      — identical logic, but a ``__row_id`` column is threaded
  through so the verifier (verify.py) can check provenance equivalence:
  every output row must derive only from the intended input rows.

Program *kinds* fix the intended provenance semantics:

  rowwise   each surviving input row -> exactly one output row (a bijection);
  dedupe    each output row is one representative per partition key;
  group_by  each output row derives from exactly the rows of its group;
  join      rowwise on the left table, plus exactly one matched right row.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Optional

ROW_ID = "__row_id"
PROV = "__prov"
PROV_R = "__prov_r"
PROV_LIST = "__prov_list"


def qident(name: str) -> str:
    """Quote a SQL identifier."""
    return '"' + name.replace('"', '""') + '"'


def qstr(s: str) -> str:
    """Quote a SQL string literal."""
    return "'" + s.replace("'", "''") + "'"


@dataclass(frozen=True, slots=True)
class Fix:
    """One detected T0 fix, already lowered to a SQL fragment by detectors.py."""

    column: str
    kind: str            # null_tokens | trim | case | date_format | numeric_string |
                         # unit_convert | header_row | drop_constant | dedupe_rows
    note: str = ""
    params: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ColumnExpr:
    """One output column: a SQL expression over the aliased source(s)."""

    target: str                       # output column name (target property)
    expr: str                         # SQL expr; source alias `s`, join alias `r`
    inputs: tuple[str, ...] = ()      # source columns referenced
    ops: tuple[str, ...] = ()         # op-chain labels (complexity / lineage)


@dataclass(frozen=True, slots=True)
class JoinSpec:
    """LEFT JOIN along a discovered IND: s.lhs_col ⊆ r.rhs_col."""

    table: str
    lhs_col: str
    rhs_col: str


@dataclass(slots=True)
class CandidateProgram:
    source_table: str
    columns: list[ColumnExpr] = field(default_factory=list)
    row_filter: Optional[str] = None          # WHERE predicate over alias `s`
    dedupe_keys: Optional[tuple[str, ...]] = None   # TARGET col names
    group_keys: Optional[tuple[str, ...]] = None    # TARGET col names (FD lhs)
    join: Optional[JoinSpec] = None
    tier: str = "anvil:T0"
    fixes: tuple[Fix, ...] = ()
    notes: list[str] = field(default_factory=list)

    # ------------------------------------------------------------- identity

    @property
    def kind(self) -> str:
        if self.group_keys:
            return "group_by"
        if self.dedupe_keys:
            return "dedupe"
        if self.join is not None:
            return "join"
        return "rowwise"

    @property
    def complexity(self) -> int:
        """MDL-style node count: column ops + structural ops."""
        n = sum(max(1, len(c.ops)) for c in self.columns)
        if self.row_filter:
            n += 2
        if self.dedupe_keys:
            n += 2
        if self.group_keys:
            n += 2
        if self.join is not None:
            n += 3
        return n

    def output_columns(self) -> tuple[str, ...]:
        return tuple(c.target for c in self.columns)

    def with_column(self, col: ColumnExpr) -> "CandidateProgram":
        out = replace(
            self,
            columns=[c for c in self.columns if c.target != col.target] + [col],
            notes=list(self.notes),
        )
        return out

    def signature(self) -> str:
        """Deterministic identity for memoization / dedup of candidates."""
        parts = [self.source_table, self.kind, self.row_filter or ""]
        parts += [f"{c.target}={c.expr}" for c in sorted(self.columns, key=lambda c: c.target)]
        if self.join:
            parts.append(f"join:{self.join.table}:{self.join.lhs_col}:{self.join.rhs_col}")
        if self.dedupe_keys:
            parts.append("dedupe:" + ",".join(self.dedupe_keys))
        if self.group_keys:
            parts.append("group:" + ",".join(self.group_keys))
        return "\x1f".join(parts)

    # ------------------------------------------------------------------ SQL

    def _from_clause(self) -> str:
        f = f"FROM {qident(self.source_table)} AS s"
        if self.join is not None:
            f += (
                f" LEFT JOIN {qident(self.join.table)} AS r"
                f" ON s.{qident(self.join.lhs_col)} = r.{qident(self.join.rhs_col)}"
            )
        return f

    def sql(self, tagged: bool = False) -> str:
        """Compile to DuckDB SQL. ``tagged=True`` threads __row_id provenance."""
        cols = sorted(self.columns, key=lambda c: c.target)
        select = [f"{c.expr} AS {qident(c.target)}" for c in cols]
        where = f" WHERE {self.row_filter}" if self.row_filter else ""
        frm = self._from_clause()

        if self.group_keys:
            keys = set(self.group_keys)
            sel = []
            for c in cols:
                if c.target in keys:
                    sel.append(f"{c.expr} AS {qident(c.target)}")
                else:
                    sel.append(f"MIN({c.expr}) AS {qident(c.target)}")
            if tagged:
                sel.append(f"LIST(s.{qident(ROW_ID)}) AS {qident(PROV_LIST)}")
            group = ", ".join(c.expr for c in cols if c.target in keys)
            return f"SELECT {', '.join(sel)} {frm}{where} GROUP BY {group}"

        if self.dedupe_keys:
            keys = set(self.dedupe_keys)
            part = ", ".join(c.expr for c in cols if c.target in keys)
            order = ", ".join(c.expr for c in cols)
            inner = list(select)
            if tagged:
                inner.append(f"s.{qident(ROW_ID)} AS {qident(PROV)}")
                order = f"s.{qident(ROW_ID)}"
            inner.append(
                f"ROW_NUMBER() OVER (PARTITION BY {part} ORDER BY {order}) AS \"__rn\""
            )
            outer_cols = [qident(c.target) for c in cols]
            if tagged:
                outer_cols.append(qident(PROV))
            return (
                f"SELECT {', '.join(outer_cols)} FROM "
                f"(SELECT {', '.join(inner)} {frm}{where}) WHERE \"__rn\" = 1"
            )

        if tagged:
            select.append(f"s.{qident(ROW_ID)} AS {qident(PROV)}")
            if self.join is not None:
                select.append(f"r.{qident(ROW_ID)} AS {qident(PROV_R)}")
        return f"SELECT {', '.join(select)} {frm}{where}"
