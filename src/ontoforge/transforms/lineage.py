"""M7 column-level lineage (whitepaper §5.1): derive, from the sqlglot AST,
a map from each output column to the input (table, column) set it depends on
plus the operation chain applied — the SQLMesh-proven approach of computing
lineage and change impact from the SQL itself.

Handles: aliases (column and table), qualified refs, CASE branches, functions
of multiple columns, JOIN provenance, SELECT * / t.* expansion, aggregates,
and FROM-subquery composition (outer ops are prepended to inner ops).

Operation chains are the pre-order (outer-to-inner, left-to-right) sequence of
operation labels in the defining expression: vetted function names (canonical
DSL spelling, e.g. SUBSTR, STRPTIME), "CAST", "CASE", and the arithmetic
symbols "+", "-", "*", "/". Bare column passthrough has an empty chain.
"""

from __future__ import annotations

from typing import Mapping, Sequence

from sqlglot import exp

from ontoforge.contracts.transforms import ColumnLineage, TransformDef

from .dsl import ANONYMOUS_FUNCTIONS, SCALAR_FUNCTIONS, AGGREGATE_FUNCTIONS, validate_sql

__all__ = ["LineageError", "lineage_for_sql", "lineage_for_transform"]

_OP_LABELS: dict[type[exp.Expression], str] = {
    **SCALAR_FUNCTIONS,
    **AGGREGATE_FUNCTIONS,
    exp.Cast: "CAST",
    exp.Case: "CASE",
    exp.Add: "+",
    exp.Sub: "-",
    exp.Mul: "*",
    exp.Div: "/",
    exp.Neg: "-",
}


class LineageError(ValueError):
    """The lineage of an output column cannot be resolved."""


# One resolved output column: ordered column list (for star expansion order),
# inputs as a set of (table, column), ops as an ordered list.
_Entry = tuple[frozenset[tuple[str, str]], tuple[str, ...]]


class _Source:
    """One FROM/JOIN source: a physical table or a subquery (virtual table)."""

    def __init__(
        self,
        alias: str,
        columns: Sequence[str],
        entries: Mapping[str, _Entry],
    ) -> None:
        self.alias = alias
        self.columns = list(columns)       # declaration order, for * expansion
        self.entries = dict(entries)       # column -> (inputs, ops)

    def has(self, column: str) -> bool:
        return column in self.entries


def _table_name(t: exp.Table) -> str:
    parts = [p.name for p in (t.args.get("catalog"), t.args.get("db"), t.args.get("this")) if p]
    return ".".join(parts)


def _physical_source(t: exp.Table, schemas: Mapping[str, Sequence[str]]) -> _Source:
    full = _table_name(t)
    if full not in schemas:
        raise LineageError(f"unknown input table {full!r}; provide its schema")
    cols = list(schemas[full])
    alias = t.alias or t.name
    return _Source(alias, cols, {c: (frozenset({(full, c)}), ()) for c in cols})


def _sources(select: exp.Select, schemas: Mapping[str, Sequence[str]]) -> list[_Source]:
    out: list[_Source] = []
    from_ = select.args.get("from_") or select.args.get("from")  # sqlglot ≥30 uses "from_"
    rel_nodes = ([from_.this] if from_ is not None else []) + [
        j.this for j in select.args.get("joins") or []
    ]
    for rel in rel_nodes:
        if isinstance(rel, exp.Table):
            out.append(_physical_source(rel, schemas))
        elif isinstance(rel, exp.Subquery):
            alias = rel.alias
            if not alias:
                raise LineageError(f"FROM-subquery must be aliased: {rel.sql('duckdb')!r}")
            inner = _select_lineage(rel.this, schemas)
            out.append(
                _Source(alias, [n for n, _, _ in inner], {n: (i, o) for n, i, o in inner})
            )
        else:  # pragma: no cover - dsl validation prevents this
            raise LineageError(f"unsupported FROM relation: {type(rel).__name__}")
    if not out:
        raise LineageError("transform body has no FROM clause")
    return out


def _resolve_column(col: exp.Column, sources: list[_Source]) -> _Entry:
    name = col.name
    qual = col.table
    if qual:
        for s in sources:
            if s.alias == qual:
                if not s.has(name):
                    raise LineageError(f"column {qual}.{name} not found in source {qual!r}")
                return s.entries[name]
        raise LineageError(f"unknown table qualifier {qual!r} for column {name!r}")
    owners = [s for s in sources if s.has(name)]
    if len(owners) == 1:
        return owners[0].entries[name]
    if not owners:
        raise LineageError(f"column {name!r} not found in any input")
    raise LineageError(
        f"column {name!r} is ambiguous across sources "
        f"{[s.alias for s in owners]}; qualify it"
    )


def _expr_lineage(node: exp.Expression, sources: list[_Source]) -> _Entry:
    """Pre-order walk: collect op labels outer-to-inner and union column inputs."""
    if isinstance(node, exp.Column):
        if isinstance(node.this, exp.Star):
            raise LineageError("t.* may only appear as a top-level projection")
        return _resolve_column(node, sources)
    inputs: set[tuple[str, str]] = set()
    ops: list[str] = []
    label = _OP_LABELS.get(type(node))
    if label is None and isinstance(node, exp.Anonymous):
        label = ANONYMOUS_FUNCTIONS.get(str(node.this).lower())
    if label is not None:
        ops.append(label)
    for child in node.args.values():
        children = child if isinstance(child, list) else [child]
        for c in children:
            if not isinstance(c, exp.Expression) or isinstance(c, (exp.Star, exp.DataType)):
                continue
            sub_inputs, sub_ops = _expr_lineage(c, sources)
            inputs |= sub_inputs
            ops.extend(sub_ops)
    return frozenset(inputs), tuple(ops)


def _star_entries(sources: list[_Source], only_alias: str | None = None) -> list[tuple[str, _Entry]]:
    out: list[tuple[str, _Entry]] = []
    for s in sources:
        if only_alias is not None and s.alias != only_alias:
            continue
        for c in s.columns:
            out.append((c, s.entries[c]))
    if only_alias is not None and not out:
        raise LineageError(f"unknown table qualifier {only_alias!r} in star expansion")
    return out


def _select_lineage(
    select: exp.Select, schemas: Mapping[str, Sequence[str]]
) -> list[tuple[str, frozenset[tuple[str, str]], tuple[str, ...]]]:
    sources = _sources(select, schemas)
    out: list[tuple[str, frozenset[tuple[str, str]], tuple[str, ...]]] = []
    for proj in select.expressions:
        if isinstance(proj, exp.Star):
            for name, (inputs, ops) in _star_entries(sources):
                out.append((name, inputs, ops))
            continue
        if isinstance(proj, exp.Column) and isinstance(proj.this, exp.Star):
            for name, (inputs, ops) in _star_entries(sources, only_alias=proj.table):
                out.append((name, inputs, ops))
            continue
        name = proj.alias_or_name
        if not name:  # pragma: no cover - dsl validation requires aliases
            raise LineageError(f"projection has no name: {proj.sql('duckdb')!r}")
        expr = proj.this if isinstance(proj, exp.Alias) else proj
        inputs, ops = _expr_lineage(expr, sources)
        out.append((name, inputs, ops))
    return out


def lineage_for_sql(
    sql: str, schemas: Mapping[str, Sequence[str]]
) -> list[ColumnLineage]:
    """Validate `sql` against the DSL and compute per-output-column lineage.

    `schemas` maps each input table's full (layer-qualified) name to its
    column list — required to expand `*` and resolve unqualified columns.
    Inputs in each ColumnLineage are sorted for determinism.
    """
    tree = validate_sql(sql)
    return [
        ColumnLineage(output_column=name, inputs=tuple(sorted(inputs)), operations=ops)
        for name, inputs, ops in _select_lineage(tree, schemas)
    ]


def lineage_for_transform(
    tdef: TransformDef, schemas: Mapping[str, Sequence[str]]
) -> list[ColumnLineage]:
    missing = [t for t in tdef.inputs if t not in schemas]
    if missing:
        raise LineageError(f"missing schemas for declared inputs: {missing}")
    return lineage_for_sql(tdef.sql, schemas)
