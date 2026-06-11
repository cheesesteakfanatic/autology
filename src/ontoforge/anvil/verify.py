"""ANVIL verification (§5.2): seeded synth/holdout split, Σ shape checks on the
holdout, and PROVENANCE-EQUIVALENCE — execute with row tags and assert every
output row derives only from the intended input rows. Value-equality testing
cannot see cross-row leakage (a fanned-out join can reproduce correct values
from the WRONG rows); row-tag accounting can.

Provenance semantics per program kind (program.py):
  rowwise   surviving input rows <-> output rows must be a bijection;
  join      left tags a bijection with surviving left rows; each right tag, when
            present, must actually satisfy the join condition for its left row;
  dedupe    one distinct representative tag per partition; output count must
            equal the independently-computed distinct-key count;
  group_by  group tag-lists must partition the surviving input rows exactly.

A static guard additionally rejects SQL whose AST smuggles in cross-row
operators (window functions, joins, subqueries) the program did not declare.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional, Sequence

import duckdb
import numpy as np
import pandas as pd
import sqlglot
from sqlglot import expressions as sge

from ontoforge.contracts import ClassDef, Datatype, ShapeConstraint, VerificationReport

from .program import PROV, PROV_LIST, PROV_R, ROW_ID, CandidateProgram

__all__ = ["split_indices", "run_program", "check_shapes", "verify_candidate", "row_pass_mask"]

HOLDOUT_FRACTION = 0.3
SHAPE_SATISFIED_FLOOR = 0.999


def split_indices(n: int, seed: int = 0, holdout: float = HOLDOUT_FRACTION) -> tuple[list[int], list[int]]:
    """Seeded, deterministic 70/30 split of row positions."""
    rng = np.random.default_rng(seed)
    perm = rng.permutation(n)
    cut = max(1, int(round(n * (1.0 - holdout)))) if n > 1 else n
    return sorted(int(i) for i in perm[:cut]), sorted(int(i) for i in perm[cut:])


def _with_row_ids(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out[ROW_ID] = np.arange(len(df), dtype=np.int64)
    return out


def run_program(
    program: CandidateProgram,
    df: pd.DataFrame,
    *,
    tagged: bool = False,
    extra_tables: Optional[dict[str, pd.DataFrame]] = None,
) -> pd.DataFrame:
    """Execute a candidate program on `df` (and any joined tables) via DuckDB."""
    con = duckdb.connect(":memory:")
    try:
        src = _with_row_ids(df) if tagged else df
        con.register(program.source_table, src)
        for name, extra in sorted((extra_tables or {}).items()):
            con.register(name, _with_row_ids(extra) if tagged else extra)
        return con.execute(program.sql(tagged=tagged)).df()
    finally:
        con.close()


# ------------------------------------------------------------- shape checks


def _is_null(v) -> bool:
    if v is None:
        return True
    if isinstance(v, float) and math.isnan(v):
        return True
    return v is pd.NaT


def _as_float(v) -> Optional[float]:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float, np.integer, np.floating)):
        return float(v)
    try:
        return float(str(v).strip())
    except (ValueError, TypeError):
        return None


def _datatype_ok(v, dt: Datatype) -> bool:
    if dt in (Datatype.STRING, Datatype.TEXT):
        return True
    if dt is Datatype.INTEGER:
        f = _as_float(v)
        return f is not None and float(f).is_integer()
    if dt is Datatype.FLOAT:
        return _as_float(v) is not None
    if dt is Datatype.BOOLEAN:
        return isinstance(v, (bool, np.bool_)) or str(v).strip().lower() in ("true", "false", "0", "1")
    if dt in (Datatype.DATE, Datatype.DATETIME):
        if isinstance(v, (date, datetime, pd.Timestamp, np.datetime64)):
            return True
        try:
            datetime.strptime(str(v).strip()[:10], "%Y-%m-%d")
            return True
        except ValueError:
            return False
    return True


def _lexical(v) -> str:
    if isinstance(v, (pd.Timestamp, datetime)):
        return v.date().isoformat() if (v.hour, v.minute, v.second) == (0, 0, 0) else v.isoformat()
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, (float, np.floating)) and float(v).is_integer():
        return str(int(v))
    return str(v)


def _value_passes(v, sc: ShapeConstraint) -> bool:
    if _is_null(v):
        return sc.min_count <= 0
    if sc.datatype is not None and not _datatype_ok(v, sc.datatype):
        return False
    if sc.pattern is not None and re.fullmatch(sc.pattern, _lexical(v).strip()) is None:
        return False
    if sc.in_values is not None and _lexical(v).strip() not in sc.in_values:
        return False
    if sc.min_value is not None or sc.max_value is not None:
        f = _as_float(v)
        if f is None:
            return False
        if sc.min_value is not None and f < sc.min_value:
            return False
        if sc.max_value is not None and f > sc.max_value:
            return False
    return True


def row_pass_mask(out: pd.DataFrame, shapes: Sequence[ShapeConstraint]) -> list[bool]:
    """Per-row conjunction of every applicable shape constraint."""
    mask = [True] * len(out)
    for sc in shapes:
        if sc.prop not in out.columns:
            if sc.min_count >= 1:
                mask = [False] * len(out)
            continue
        col = out[sc.prop]
        for i, v in enumerate(col):
            if mask[i] and not _value_passes(v, sc):
                mask[i] = False
    return mask


def check_shapes(
    out: pd.DataFrame, target_class: ClassDef
) -> tuple[float, bool, list[str]]:
    shapes = [sc for sc in target_class.shapes if not _link_shape(target_class, sc)]
    notes: list[str] = []
    if len(out) == 0:
        required = any(sc.min_count >= 1 for sc in shapes)
        return (0.0 if required else 1.0), not required, ["holdout produced 0 rows"]
    mask = row_pass_mask(out, shapes)
    rate = sum(mask) / len(mask)
    for sc in shapes:
        if sc.prop in out.columns:
            bad = sum(1 for v in out[sc.prop] if not _value_passes(v, sc))
            if bad:
                notes.append(f"shape {sc.prop}: {bad}/{len(out)} holdout violations")
        elif sc.min_count >= 1:
            notes.append(f"shape {sc.prop}: required property not produced")
    return rate, rate >= SHAPE_SATISFIED_FLOOR, notes


def _link_shape(target_class: ClassDef, sc: ShapeConstraint) -> bool:
    p = target_class.prop(sc.prop)
    return p is not None and p.is_link


# ----------------------------------------------------- provenance equivalence


_CROSS_ROW_NODES = (sge.Window, sge.Join, sge.Subquery, sge.Select)


def _undeclared_cross_row(program: CandidateProgram) -> Optional[str]:
    """Static guard: column expressions must be row-local — no windows, joins,
    or subqueries hiding inside an expression."""
    for c in program.columns:
        try:
            tree = sqlglot.parse_one(f"SELECT {c.expr} AS x", read="duckdb")
        except sqlglot.errors.ParseError:
            return f"column {c.target!r}: unparseable expression"
        for node in tree.find_all(*_CROSS_ROW_NODES):
            if isinstance(node, sge.Select) and node is tree:
                continue
            return f"column {c.target!r}: undeclared cross-row operator {type(node).__name__}"
    return None


def _surviving_row_ids(program: CandidateProgram, df: pd.DataFrame) -> list[int]:
    con = duckdb.connect(":memory:")
    try:
        con.register(program.source_table, _with_row_ids(df))
        where = f" WHERE {program.row_filter}" if program.row_filter else ""
        rows = con.execute(
            f'SELECT "{ROW_ID}" FROM "{program.source_table}" AS s{where} ORDER BY 1'
        ).fetchall()
        return [int(r[0]) for r in rows]
    finally:
        con.close()


def check_provenance(
    program: CandidateProgram,
    df: pd.DataFrame,
    tagged_out: pd.DataFrame,
    *,
    extra_tables: Optional[dict[str, pd.DataFrame]] = None,
) -> tuple[bool, list[str]]:
    notes: list[str] = []
    static = _undeclared_cross_row(program)
    if static is not None:
        return False, [f"provenance: {static}"]

    surviving = _surviving_row_ids(program, df)
    kind = program.kind

    if kind in ("rowwise", "join"):
        tags = [int(t) for t in tagged_out[PROV]]
        if sorted(tags) != surviving:
            extra = sorted(set(tags) - set(surviving))
            dup = sorted({t for t in tags if tags.count(t) > 1}) if len(tags) < 5000 else []
            notes.append(
                "provenance: output rows are not a bijection with surviving input rows"
                + (f"; duplicated tags {dup[:5]}" if dup else "")
                + (f"; foreign tags {extra[:5]}" if extra else "")
                + f" (out={len(tags)}, in={len(surviving)})"
            )
            return False, notes
        if kind == "join" and program.join is not None:
            right = (extra_tables or {}).get(program.join.table)
            if right is None:
                return False, ["provenance: joined table not supplied to verifier"]
            lvals = df[program.join.lhs_col].reset_index(drop=True)
            rvals = right[program.join.rhs_col].reset_index(drop=True)
            for ltag, rtag in zip(tagged_out[PROV], tagged_out[PROV_R]):
                if _is_null(rtag):
                    continue
                if str(lvals.iloc[int(ltag)]) != str(rvals.iloc[int(rtag)]):
                    notes.append(
                        f"provenance: output row tagged (l={int(ltag)}, r={int(rtag)}) "
                        "derives from a right row that does not satisfy the join condition"
                    )
                    return False, notes
        return True, notes

    if kind == "dedupe":
        tags = [int(t) for t in tagged_out[PROV]]
        if len(set(tags)) != len(tags):
            return False, ["provenance: duplicate representative tags after dedupe"]
        if not set(tags) <= set(surviving):
            return False, ["provenance: dedupe representative outside surviving input rows"]
        # independent distinct-key count: one output row per partition, exactly
        keys = set(program.dedupe_keys or ())
        key_exprs = [c.expr for c in program.columns if c.target in keys]
        if key_exprs:
            con = duckdb.connect(":memory:")
            try:
                con.register(program.source_table, _with_row_ids(df))
                for name, extra in sorted((extra_tables or {}).items()):
                    con.register(name, _with_row_ids(extra))
                where = f" WHERE {program.row_filter}" if program.row_filter else ""
                n_keys = con.execute(
                    f"SELECT COUNT(*) FROM (SELECT DISTINCT {', '.join(key_exprs)} "
                    f"{program._from_clause()}{where})"
                ).fetchone()[0]
            finally:
                con.close()
            if len(tags) != int(n_keys):
                return False, [
                    f"provenance: dedupe emitted {len(tags)} rows for {int(n_keys)} distinct keys"
                ]
        return True, notes

    if kind == "group_by":
        seen: list[int] = []
        for lst in tagged_out[PROV_LIST]:
            seen.extend(int(t) for t in lst)
        if sorted(seen) != surviving:
            return False, [
                "provenance: group tag-lists do not partition the surviving input rows "
                f"(tagged={len(seen)}, in={len(surviving)})"
            ]
        return True, notes

    return False, [f"provenance: unknown program kind {kind!r}"]


# -------------------------------------------------------------------- driver


@dataclass(slots=True)
class VerifiedCandidate:
    program: CandidateProgram
    report: VerificationReport


def verify_candidate(
    program: CandidateProgram,
    df: pd.DataFrame,
    target_class: ClassDef,
    *,
    seed: int = 0,
    extra_tables: Optional[dict[str, pd.DataFrame]] = None,
) -> VerificationReport:
    """§5.2 admission evidence: holdout Σ satisfaction + provenance equivalence."""
    _synth_idx, holdout_idx = split_indices(len(df), seed=seed)
    holdout = df.iloc[holdout_idx].reset_index(drop=True)
    report = VerificationReport(
        holdout_rows=len(holdout),
        program_complexity=program.complexity,
    )
    try:
        tagged = run_program(program, holdout, tagged=True, extra_tables=extra_tables)
    except Exception as exc:  # execution failure = hard reject evidence
        report.notes.append(f"execution failed on holdout: {exc}")
        report.provenance_equivalent = False
        return report

    out = tagged[[c for c in tagged.columns if c not in (PROV, PROV_R, PROV_LIST)]]
    rate, satisfied, shape_notes = check_shapes(out, target_class)
    report.holdout_pass_rate = round(rate, 6)
    report.shapes_satisfied = satisfied
    report.notes.extend(shape_notes)

    ok, prov_notes = check_provenance(program, holdout, tagged, extra_tables=extra_tables)
    report.provenance_equivalent = ok
    report.notes.extend(prov_notes)
    return report
