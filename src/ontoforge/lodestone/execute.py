"""LODESTONE staged execution with execution-guided repair (§6.2; M12 step 5).

evaluate() walks the OQIR term bottom-up over the lowering primitives;
Aggregate/TopK run as DuckDB SQL over the materialized frame. An empty
intermediate result raises EmptyResult naming the failing leaf; the driver
(execute_candidate) re-runs once per relaxation level (re-grounding the
failing literal: case-insensitive, then normalized matching) and reports which
level repaired the plan. Repair exhausted -> the failure (with the leaf shown)
goes back to the caller, which abstains — the system never guesses.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Union

import duckdb
import pyarrow as pa

from ontoforge.contracts.ontology import Ontology
from ontoforge.contracts.oqir import (
    Agg,
    Aggregate,
    AsOf,
    CmpOp,
    OQIRTerm,
    Select,
    TextJoin,
    TopK,
    Traverse,
)
from ontoforge.profiling.units_table import UNITS

from .lower import ExecContext, convert_value, eval_condition
from .model import Candidate, resolve_prop
from .typecheck import infer

MAX_RELAX = 2


class EmptyResult(Exception):
    """An intermediate stage produced zero rows."""

    def __init__(self, leaf: str) -> None:
        super().__init__(leaf)
        self.leaf = leaf


@dataclass(slots=True)
class XRow:
    uri: str
    vals: dict[str, Any]
    provs: dict[str, tuple[str, ...]]
    refs: tuple[str, ...] = ()


@dataclass(slots=True)
class XTable:
    columns: list[str]
    rows: list[dict[str, Any]]
    provs: list[dict[str, tuple[str, ...]]]


@dataclass(slots=True)
class ExecOutcome:
    columns: list[str]
    rows: list[list[Any]]
    cell_provs: list[list[tuple[str, ...]]]   # parallel to rows
    repaired: int = 0                          # relaxation level that succeeded


# ------------------------------------------------------------- evaluation


def evaluate(term: OQIRTerm, ctx: ExecContext, targets: dict[int, tuple[str, ...]]
             ) -> Union[list[XRow], XTable]:
    if isinstance(term, Select):
        classes = targets.get(id(term), (term.class_uri,))
        rows: list[XRow] = []
        for class_uri in classes:
            for uri in sorted(ctx.scan_cells(class_uri)):
                cells = ctx.scan_cells(class_uri)[uri]
                refs: list[str] = []
                keep = True
                for cond in term.conditions:
                    ok, used = eval_condition(ctx, class_uri, uri, cells, cond)
                    if not ok:
                        keep = False
                        break
                    refs.extend(used)
                if keep:
                    rows.append(
                        XRow(
                            uri=uri,
                            vals={p: c.value for p, c in cells.items()},
                            provs={p: (c.prov_ref,) for p, c in cells.items()},
                            refs=tuple(sorted(set(refs))),
                        )
                    )
        if not rows:
            raise EmptyResult(f"select {classes} where {[c.prop for c in term.conditions]}")
        return rows

    if isinstance(term, Traverse):
        src = evaluate(term.source, ctx, targets)
        assert isinstance(src, list)
        adj = ctx.link_adj(term.link, term.reverse)
        t_classes = targets.get(id(term), ())
        member: dict[str, str] = {}
        for c in t_classes:
            for uri in ctx.scan_cells(c):
                member.setdefault(uri, c)
        out: dict[str, XRow] = {}
        for row in src:
            for nbr, prov in adj.get(row.uri, []):
                cls = member.get(nbr)
                if cls is None:
                    continue
                cells = ctx.scan_cells(cls).get(nbr, {})
                refs = list(row.refs) + ([prov] if prov else [])
                keep = True
                for cond in term.conditions:
                    ok, used = eval_condition(ctx, cls, nbr, cells, cond)
                    if not ok:
                        keep = False
                        break
                    refs.extend(used)
                if not keep:
                    continue
                vals = {p: c.value for p, c in cells.items()}
                provs = {p: (c.prov_ref,) for p, c in cells.items()}
                for k, v in row.vals.items():       # retain upstream columns
                    if k not in vals:
                        vals[k] = v
                        provs[k] = row.provs.get(k, ())
                if nbr in out:
                    out[nbr].refs = tuple(sorted(set(out[nbr].refs) | set(refs)))
                else:
                    out[nbr] = XRow(uri=nbr, vals=vals, provs=provs, refs=tuple(sorted(set(refs))))
        if not out:
            raise EmptyResult(f"traverse {'^' if term.reverse else ''}{term.link}")
        return [out[u] for u in sorted(out)]

    if isinstance(term, TextJoin):
        src = evaluate(term.source, ctx, targets)
        assert isinstance(src, list)
        rows = []
        needle = term.pattern.casefold()
        for row in src:
            v = row.vals.get(term.text_prop)
            if v is not None and needle in str(v).casefold():
                refs = set(row.refs) | set(row.provs.get(term.text_prop, ()))
                rows.append(XRow(row.uri, row.vals, row.provs, tuple(sorted(refs))))
        if not rows:
            raise EmptyResult(f"textJoin {term.text_prop} ~ {term.pattern!r}")
        return rows

    if isinstance(term, Aggregate):
        src = evaluate(term.source, ctx, targets)
        assert isinstance(src, list)
        return _aggregate(term, src, ctx)

    if isinstance(term, TopK):
        src = evaluate(term.source, ctx, targets)
        assert isinstance(src, XTable)
        order = sorted(
            range(len(src.rows)),
            key=lambda i: (-(src.rows[i][term.by] or 0) if term.descending else (src.rows[i][term.by] or 0),
                           str(src.rows[i])),
        )[: term.k]
        return XTable(src.columns, [src.rows[i] for i in order], [src.provs[i] for i in order])

    if isinstance(term, AsOf):
        return evaluate(term.term, ctx.with_stance(term.stance), targets)

    raise TypeError(f"unknown OQIR term {term!r}")


def _group_value(row: XRow, path: str, ctx: ExecContext) -> tuple[Any, tuple[str, ...]]:
    parts = path.split(".")
    if len(parts) == 1:
        return row.vals.get(path), row.provs.get(path, ())
    # single forward hop paths: link.prop
    adj = ctx.link_adj(parts[0], reverse=False)
    for obj, prov in adj.get(row.uri, []):
        cells = ctx.entity_cells(obj)
        cell = cells.get(parts[1])
        if cell is not None:
            return cell.value, ((prov,) if prov else ()) + (cell.prov_ref,)
    return None, ()


def _aggregate(term: Aggregate, src: list[XRow], ctx: ExecContext) -> XTable:
    agg_col = f"{term.agg.value}_{term.measure_prop or 'rows'}"
    group_cols = list(term.group_by)

    # materialize the frame
    keys: list[tuple[Any, ...]] = []
    measures: list[Any] = []          # float for numeric aggs, str for COUNT DISTINCT
    row_provs: list[tuple[str, ...]] = []
    key_provs: list[tuple[tuple[str, ...], ...]] = []
    count_distinct = term.agg is Agg.COUNT and term.measure_prop is not None
    for row in src:
        kvals, kprovs = [], []
        for g in group_cols:
            v, p = _group_value(row, g, ctx)
            kvals.append(v)
            kprovs.append(p)
        m = row.vals.get(term.measure_prop) if term.measure_prop else None
        prov_pool: set[str] = set(row.refs)
        if term.measure_prop:
            prov_pool |= set(row.provs.get(term.measure_prop, ()))
        if not prov_pool:
            # COUNT over an unfiltered scan: cite the row's identity cells
            for p in sorted(row.provs)[:1]:
                prov_pool |= set(row.provs[p])
        if term.agg is not Agg.COUNT and term.measure_prop:
            if m is None or not isinstance(m, (int, float)):
                continue  # missing measure = unknown, excluded (blank-vs-zero)
        keys.append(tuple(kvals))
        if count_distinct:
            measures.append(str(m) if m is not None else None)
        else:
            measures.append(float(m) if isinstance(m, (int, float)) else None)
        row_provs.append(tuple(sorted(prov_pool)))
        key_provs.append(tuple(kprovs))

    if not keys:
        raise EmptyResult(f"aggregate {term.agg.value} over empty/measureless frame")

    # DuckDB SQL over the materialized frame (§6.2 lowering)
    n = len(keys)
    data: dict[str, Any] = {"__row__": pa.array(range(n), type=pa.int64())}
    for gi, g in enumerate(group_cols):
        data[f"g{gi}"] = pa.array([str(k[gi]) if k[gi] is not None else None for k in keys])
    data["m"] = pa.array(measures, type=pa.string() if count_distinct else pa.float64())
    con = duckdb.connect()
    con.register("frame", pa.table(data))
    gsel = ", ".join(f"g{gi}" for gi in range(len(group_cols)))
    if term.agg is Agg.COUNT:
        expr = "COUNT(DISTINCT m)" if count_distinct else "COUNT(*)"
    else:
        expr = {Agg.SUM: "SUM(m)", Agg.AVG: "AVG(m)", Agg.MIN: "MIN(m)", Agg.MAX: "MAX(m)"}[term.agg]
    if group_cols:
        sql = (
            f"SELECT {gsel}, {expr} AS agg, LIST(__row__) AS rows_ FROM frame "
            f"GROUP BY {gsel} ORDER BY {gsel}"
        )
    else:
        sql = f"SELECT {expr} AS agg, LIST(__row__) AS rows_ FROM frame"
    res = con.execute(sql).fetchall()
    con.close()

    out_rows: list[dict[str, Any]] = []
    out_provs: list[dict[str, tuple[str, ...]]] = []
    for rec in res:
        if group_cols:
            gvals = rec[: len(group_cols)]
            aggval, members = rec[len(group_cols)], rec[len(group_cols) + 1]
        else:
            gvals, aggval, members = [], rec[0], rec[1]
        row: dict[str, Any] = {}
        provs: dict[str, tuple[str, ...]] = {}
        member_provs: set[str] = set()
        for mi in members:
            member_provs |= set(row_provs[mi])
        for gi, g in enumerate(group_cols):
            row[g] = gvals[gi]
            gp: set[str] = set()
            for mi in members:
                gp |= set(key_provs[mi][gi])
            provs[g] = tuple(sorted(gp or member_provs))
        row[agg_col] = aggval
        provs[agg_col] = tuple(sorted(member_provs))
        out_rows.append(row)
        out_provs.append(provs)

    # having filters on the aggregate column
    for h in term.having:
        kept = []
        for i, row in enumerate(out_rows):
            v = row[agg_col]
            x = float(v) if v is not None else 0.0
            y = float(h.value)  # checker proved numeric
            ok = {
                CmpOp.GT: x > y, CmpOp.GE: x >= y, CmpOp.LT: x < y,
                CmpOp.LE: x <= y, CmpOp.EQ: x == y, CmpOp.NE: x != y,
            }.get(h.op, False)
            if ok:
                kept.append(i)
        out_rows = [out_rows[i] for i in kept]
        out_provs = [out_provs[i] for i in kept]
    if not out_rows:
        raise EmptyResult(f"aggregate {agg_col} (having filtered all groups)")
    return XTable(group_cols + [agg_col], out_rows, out_provs)


# ----------------------------------------------------------------- driver


def _find_aggregate(term: OQIRTerm) -> Optional[Aggregate]:
    if isinstance(term, Aggregate):
        return term
    if isinstance(term, (TopK,)):
        return _find_aggregate(term.source)
    if isinstance(term, AsOf):
        return _find_aggregate(term.term)
    return None


def execute_candidate(
    cand: Candidate, onto: Ontology, hearth, *, base_stance=None
) -> Union[ExecOutcome, EmptyResult]:
    """Run one candidate with staged repair. Returns the outcome or the final
    EmptyResult (the failed leaf) when repair is exhausted."""
    inference = infer(cand.term, onto, expect_unit=cand.expect_unit)
    targets = inference.targets
    last: Optional[EmptyResult] = None
    for relax in range(MAX_RELAX + 1):
        ctx = ExecContext(hearth=hearth, onto=onto, stance=cand.stance or base_stance, relax=relax)
        try:
            result = evaluate(cand.term, ctx, targets)
            return _project(cand, result, onto, targets, repaired=relax)
        except EmptyResult as e:
            last = e
            continue
    assert last is not None
    return last


def _project(
    cand: Candidate,
    result: Union[list[XRow], XTable],
    onto: Ontology,
    targets: dict[int, tuple[str, ...]],
    repaired: int,
) -> ExecOutcome:
    # output unit conversion for aggregate measures
    unit_factor_apply = None
    agg_node = _find_aggregate(cand.term)
    if cand.expect_unit and agg_node is not None and agg_node.measure_prop:
        for c in targets.get(id(agg_node.source), ()):
            p = resolve_prop(onto, c, agg_node.measure_prop)
            if p is not None and p.unit and p.unit in UNITS and cand.expect_unit in UNITS:
                src_unit, dst_unit = p.unit, cand.expect_unit

                def unit_factor_apply(v: float, _s=src_unit, _d=dst_unit) -> float:
                    return convert_value(v, _s, _d)

                break

    def post(v: Any) -> Any:
        if isinstance(v, (int, float)):
            x = float(v)
            if unit_factor_apply is not None:
                x = unit_factor_apply(x)
            if cand.round_digits is not None:
                x = round(x, cand.round_digits)
                if cand.round_digits == 0:
                    return int(x)
            if x.is_integer():
                return int(x)
            return x
        return v

    if isinstance(result, XTable):
        cols = list(cand.project) if cand.project else list(result.columns)
        cols = [c for c in cols if c in result.columns] or list(result.columns)
        rows, provs = [], []
        for row, prow in zip(result.rows, result.provs):
            rows.append([post(row.get(c)) for c in cols])
            provs.append([tuple(prow.get(c, ())) for c in cols])
        order = sorted(range(len(rows)), key=lambda i: [str(x) for x in rows[i]])
        return ExecOutcome(cols, [rows[i] for i in order], [provs[i] for i in order], repaired)

    cols = list(cand.project)
    out: dict[tuple, tuple[list, list]] = {}
    for row in result:
        vals, provs = [], []
        missing = False
        for c in cols:
            v = row.vals.get(c)
            if v is None or (isinstance(v, str) and not v.strip()):
                missing = True
                break
            vals.append(post(v))
            provs.append(tuple(sorted(set(row.provs.get(c, ())) | set(row.refs))))
        if missing:
            continue
        key = tuple(str(v) for v in vals)
        if key in out:
            merged = [tuple(sorted(set(a) | set(b))) for a, b in zip(out[key][1], provs)]
            out[key] = (out[key][0], merged)
        else:
            out[key] = (vals, provs)
    rows = [out[k][0] for k in sorted(out)]
    provs = [out[k][1] for k in sorted(out)]
    if not rows:
        # every row lacked a projected value: that is an empty answer
        raise EmptyResult(f"projection {cols} produced no values")
    return ExecOutcome(cols, rows, provs, repaired)
