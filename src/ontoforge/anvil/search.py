"""T1 — constrained program search (§5.2 step 2).

For residual gaps between the (T0-fixed) source program and the target class's
properties, run a beam search (beam <= 8, depth <= 4) over a small op grammar:

  project/rename, cast, split_part, regexp_extract (PROSE-lite: the extraction
  pattern/delimiter is induced from <= 5 input->output examples derived from the
  target shape pattern), join along a discovered IND, group-by along a
  discovered FD, dedupe.

PRUNING (the generalized Auto-Pipeline rule): a candidate is discarded the
moment its intermediate output — executed on a sample — violates a discovered
FD, the key-uniqueness implied by a discovered IND join, or a target
ShapeConstraint. Pruned counts are reported in SearchStats.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional, Sequence

import pandas as pd

from ontoforge.contracts import (
    IND,
    ClassDef,
    Datatype,
    PropertyDef,
    ShapeConstraint,
    TableProfile,
)

from .mapping import match_columns, normalize_name
from .program import CandidateProgram, ColumnExpr, JoinSpec, qident, qstr
from .verify import run_program, _value_passes

__all__ = ["SearchStats", "t1_search", "induce_extraction"]

BEAM = 8
DEPTH = 4
SAMPLE_ROWS = 200
SHAPE_TOLERANCE = 0.05
_DELIMS = (",", ";", "|", "/", ":", " ", "-")


@dataclass(slots=True)
class SearchStats:
    expanded: int = 0
    pruned_shape: int = 0
    pruned_fd: int = 0
    kept: int = 0
    notes: list[str] = field(default_factory=list)


# --------------------------------------------------- PROSE-lite induction


def _pattern_core(pattern: str) -> str:
    return pattern.lstrip("^").rstrip("$")


def induce_extraction(
    examples: Sequence[tuple[str, str]], shape_pattern: Optional[str]
) -> list[tuple[str, tuple[str, ...]]]:
    """Induce (sql_template, ops) extraction candidates from <=5 (input, output)
    examples. `{e}` in the template is the input expression placeholder.

    Candidates, by MDL preference: identity, TRIM, split_part(delim, idx),
    regexp_extract(target shape pattern).
    """
    if not examples:
        return []
    out: list[tuple[str, tuple[str, ...]]] = []
    if all(i == o for i, o in examples):
        out.append(("{e}", ("project",)))
    if all(i.strip() == o for i, o in examples) and any(i != o for i, o in examples):
        out.append(("TRIM({e})", ("trim",)))
    for d in _DELIMS:
        for idx in range(1, 6):
            ok = True
            for i, o in examples:
                parts = i.split(d)
                if len(parts) < idx or parts[idx - 1].strip() != o:
                    ok = False
                    break
            if ok:
                out.append(
                    (f"TRIM(SPLIT_PART({{e}}, {qstr(d)}, {idx}))", ("split_part", "trim"))
                )
                break  # one index per delimiter is enough
    if shape_pattern:
        core = _pattern_core(shape_pattern)
        try:
            rx = re.compile(core)
        except re.error:
            rx = None
        if rx is not None and all(
            (m := rx.search(i)) is not None and m.group(0) == o for i, o in examples
        ):
            out.append(
                (f"REGEXP_EXTRACT({{e}}, {qstr(core)}, 0)", ("regexp_extract",))
            )
    return out


def _examples_for(
    values: Sequence[str], shape_pattern: Optional[str], k: int = 5
) -> list[tuple[str, str]]:
    """Derive input->output examples from the target shape pattern: the expected
    output of a messy value is its unique substring matching the pattern."""
    if not shape_pattern:
        return []
    try:
        rx = re.compile(_pattern_core(shape_pattern))
    except re.error:
        return []
    ex: list[tuple[str, str]] = []
    for v in values:
        if len(ex) >= k:
            break
        m = rx.search(v)
        if m and m.group(0):
            ex.append((v, m.group(0)))
    return ex


# ------------------------------------------------------------ gap analysis


def _shapes_for(target_class: ClassDef, prop: str) -> list[ShapeConstraint]:
    return [sc for sc in target_class.shapes if sc.prop == prop]


def _required(target_class: ClassDef, p: PropertyDef) -> bool:
    return any(sc.min_count >= 1 for sc in _shapes_for(target_class, p.name))


def residual_gaps(
    program: CandidateProgram, target_class: ClassDef
) -> list[PropertyDef]:
    """Datatype (non-link) target properties not yet produced by the program."""
    have = set(program.output_columns())
    gaps = [
        p
        for p in target_class.properties
        if not p.is_link and p.name not in have
    ]
    gaps.sort(key=lambda p: (not _required(target_class, p), p.name))
    return gaps


# ----------------------------------------------------------------- pruning


def _shape_violation_rate(
    out: pd.DataFrame, prop: str, shapes: Sequence[ShapeConstraint]
) -> float:
    if prop not in out.columns or len(out) == 0:
        return 0.0
    bad = 0
    for v in out[prop]:
        if not all(_value_passes(v, sc) for sc in shapes):
            bad += 1
    return bad / len(out)


def _violates_fds(
    out: pd.DataFrame, program: CandidateProgram, table_profile: TableProfile
) -> bool:
    """Discovered-FD pruning: when the program projects both sides of a
    discovered exact FD straight through (project/trim-class ops only), the FD
    must still hold on the intermediate output."""
    col_by_src: dict[str, str] = {}
    passthrough_ops = {"project", "trim", "case", "null_tokens"}
    for c in program.columns:
        if len(c.inputs) == 1 and set(c.ops) <= passthrough_ops:
            col_by_src[c.inputs[0]] = c.target
    for fd in table_profile.fds:
        if fd.confidence < 1.0 or len(fd.lhs) != 1:
            continue
        lhs, rhs = fd.lhs[0], fd.rhs
        if lhs in col_by_src and rhs in col_by_src and len(out) > 1:
            grouped = out.groupby(col_by_src[lhs], dropna=True)[col_by_src[rhs]].nunique(dropna=True)
            if (grouped > 1).any():
                return True
    return False


def _violates_join_key(out: pd.DataFrame, program: CandidateProgram, key_col: Optional[str]) -> bool:
    """IND-join pruning: a join along an IND must not fan out the source key."""
    if program.join is None or key_col is None or key_col not in out.columns:
        return False
    vals = out[key_col].dropna()
    return len(vals) != vals.nunique()


# -------------------------------------------------------------------- search


def _score(
    out: pd.DataFrame, program: CandidateProgram, target_class: ClassDef
) -> float:
    score = 0.0
    for p in target_class.properties:
        if p.is_link or p.name not in out.columns:
            continue
        shapes = _shapes_for(target_class, p.name)
        score += 1.0 - _shape_violation_rate(out, p.name, shapes)
    return score - 0.01 * program.complexity


def t1_search(
    base: CandidateProgram,
    df_synth: pd.DataFrame,
    table_profile: TableProfile,
    target_class: ClassDef,
    *,
    extra_tables: Optional[dict[str, pd.DataFrame]] = None,
    inds: Sequence[IND] = (),
    beam: int = BEAM,
    depth: int = DEPTH,
) -> tuple[list[CandidateProgram], SearchStats]:
    stats = SearchStats()
    extra_tables = extra_tables or {}
    sample = df_synth.head(SAMPLE_ROWS).reset_index(drop=True)
    gaps = residual_gaps(base, target_class)[:depth]
    key_col = _program_key(base, target_class)

    frontier: list[tuple[float, CandidateProgram]] = [(0.0, base)]
    seen: set[str] = {base.signature()}

    for gap in gaps:
        expansions: list[tuple[float, CandidateProgram]] = list(frontier)
        for _, prog in frontier:
            for cand in _expand_gap(prog, gap, sample, target_class, table_profile, inds, extra_tables):
                sig = cand.signature()
                if sig in seen:
                    continue
                seen.add(sig)
                stats.expanded += 1
                kept = _admit(cand, sample, target_class, table_profile, key_col, extra_tables, stats)
                if kept is not None:
                    expansions.append(kept)
        expansions.sort(key=lambda t: (-t[0], t[1].signature()))
        frontier = expansions[:beam]

    # structural level: dedupe / group-by along discovered FDs when keys repeat
    expansions = list(frontier)
    for _, prog in frontier:
        for cand in _expand_structural(prog, sample, target_class, table_profile, extra_tables):
            sig = cand.signature()
            if sig in seen:
                continue
            seen.add(sig)
            stats.expanded += 1
            kept = _admit(cand, sample, target_class, table_profile, key_col, extra_tables, stats)
            if kept is not None:
                expansions.append(kept)
    expansions.sort(key=lambda t: (-t[0], t[1].signature()))
    frontier = expansions[:beam]

    out = [p for _, p in frontier if p.signature() != base.signature()]
    stats.kept = len(out)
    return out, stats


def _admit(
    cand: CandidateProgram,
    sample: pd.DataFrame,
    target_class: ClassDef,
    table_profile: TableProfile,
    key_col: Optional[str],
    extra_tables: dict[str, pd.DataFrame],
    stats: SearchStats,
) -> Optional[tuple[float, CandidateProgram]]:
    """Auto-Pipeline pruning on the sampled intermediate output."""
    try:
        out = run_program(cand, sample, extra_tables=extra_tables)
    except Exception:
        stats.pruned_shape += 1
        return None
    for c in cand.columns:
        shapes = _shapes_for(target_class, c.target)
        hard = [sc for sc in shapes if sc.pattern or sc.in_values or sc.min_value is not None or sc.max_value is not None]
        if hard and _shape_violation_rate(out, c.target, hard) > SHAPE_TOLERANCE:
            stats.pruned_shape += 1
            return None
    if _violates_fds(out, cand, table_profile) or _violates_join_key(out, cand, key_col):
        stats.pruned_fd += 1
        return None
    return _score(out, cand, target_class), cand


def _program_key(program: CandidateProgram, target_class: ClassDef) -> Optional[str]:
    for p in target_class.properties:
        if p.functional and p.name in program.output_columns():
            return p.name
    for sc in target_class.shapes:
        if sc.min_count >= 1 and sc.max_count == 1 and sc.prop in program.output_columns():
            return sc.prop
    return None


# -------------------------------------------------------------- expansions


def _string_values(sample: pd.DataFrame, col: str) -> list[str]:
    return [str(v) for v in sample[col].dropna().tolist() if str(v).strip()][:50]


def _expand_gap(
    prog: CandidateProgram,
    gap: PropertyDef,
    sample: pd.DataFrame,
    target_class: ClassDef,
    table_profile: TableProfile,
    inds: Sequence[IND],
    extra_tables: dict[str, pd.DataFrame],
) -> list[CandidateProgram]:
    cands: list[CandidateProgram] = []
    shapes = _shapes_for(target_class, gap.name)
    pattern = next((sc.pattern for sc in shapes if sc.pattern), None)

    # 1. extraction / projection from each source column (PROSE-lite)
    for col in sorted(sample.columns):
        vals = _string_values(sample, col)
        if not vals:
            continue
        examples = _examples_for(vals, pattern)
        templates = induce_extraction(examples, pattern) if examples else []
        if not templates and pattern is None and gap.datatype in (Datatype.FLOAT, Datatype.INTEGER):
            castable = sum(1 for v in vals if _is_num(v))
            if castable >= 0.9 * len(vals) and normalize_name(col) and _name_overlap(col, gap):
                templates = [("{e}", ("project",))]
        for tmpl, ops in templates:
            expr = tmpl.replace("{e}", f"s.{qident(col)}")
            expr, ops = _cast_for(expr, ops, gap)
            cands.append(prog.with_column(ColumnExpr(gap.name, expr, (col,), ops)))

    # 2. join along a discovered IND, pulling a name-matched right column
    for ind in sorted(inds, key=lambda i: (-i.score, i.lhs_column, i.rhs_table, i.rhs_column)):
        if ind.lhs_table != prog.source_table or prog.join is not None:
            continue
        right = extra_tables.get(ind.rhs_table)
        if right is None:
            continue
        matches = match_columns([gap], list(right.columns))
        col = matches.get(gap.name)
        if col is None:
            continue
        expr, ops = _cast_for(f"r.{qident(col)}", ("join", "project"), gap)
        joined = prog.with_column(ColumnExpr(gap.name, expr, (ind.lhs_column, col), ops))
        joined.join = JoinSpec(table=ind.rhs_table, lhs_col=ind.lhs_column, rhs_col=ind.rhs_column)
        joined.tier = "anvil:T1"
        cands.append(joined)

    for c in cands:
        c.tier = "anvil:T1"
    return cands


def _expand_structural(
    prog: CandidateProgram,
    sample: pd.DataFrame,
    target_class: ClassDef,
    table_profile: TableProfile,
    extra_tables: dict[str, pd.DataFrame],
) -> list[CandidateProgram]:
    out: list[CandidateProgram] = []
    key_col = _program_key(prog, target_class)
    if key_col is None or prog.dedupe_keys or prog.group_keys:
        return out
    try:
        res = run_program(prog, sample, extra_tables=extra_tables)
    except Exception:
        return out
    if key_col not in res.columns:
        return out
    vals = res[key_col].dropna()
    if len(vals) == vals.nunique():
        return out
    # duplicates at the entity grain: dedupe, and group-by when an FD backs the key
    import copy

    dd = copy.deepcopy(prog)
    dd.dedupe_keys = (key_col,)
    dd.tier = "anvil:T1"
    dd.notes.append(f"dedupe on key {key_col!r} (duplicate key rows observed)")
    out.append(dd)
    src_key = next((c.inputs[0] for c in prog.columns if c.target == key_col and c.inputs), None)
    if src_key is not None and any(
        fd.confidence >= 1.0 and fd.lhs == (src_key,) for fd in table_profile.fds
    ):
        gb = copy.deepcopy(prog)
        gb.group_keys = (key_col,)
        gb.tier = "anvil:T1"
        gb.notes.append(f"group-by along discovered FD {src_key!r} -> *")
        out.append(gb)
    return out


# ------------------------------------------------------------------ helpers


def _is_num(v: str) -> bool:
    try:
        float(v)
        return True
    except ValueError:
        return False


def _name_overlap(col: str, prop: PropertyDef) -> bool:
    ct = set(normalize_name(col).split("_"))
    pt = set(normalize_name(prop.name).split("_"))
    return bool(ct & pt)


def _cast_for(expr: str, ops: tuple[str, ...], prop: PropertyDef) -> tuple[str, tuple[str, ...]]:
    if prop.datatype is Datatype.INTEGER:
        return f"TRY_CAST({expr} AS BIGINT)", ops + ("cast",)
    if prop.datatype is Datatype.FLOAT:
        return f"TRY_CAST({expr} AS DOUBLE)", ops + ("cast",)
    if prop.datatype is Datatype.DATE:
        return f"TRY_CAST({expr} AS DATE)", ops + ("cast",)
    return expr, ops
