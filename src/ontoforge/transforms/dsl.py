"""M7 DSL validation (whitepaper §5.1): a transform body is a *restricted*
SQL SELECT in the DuckDB dialect, parsed with sqlglot and checked against an
explicit operator allowlist. Everything not allowed is rejected with a clear,
named error — the DSL is a curated grammar, not "whatever DuckDB executes".

Allowed (v0):
  SELECT projection (computed columns MUST be aliased), DISTINCT-free,
  WHERE, JOIN (inner / left only), GROUP BY, ORDER BY, CASE, CAST,
  scalar functions from the vetted list below, the aggregates that make
  GROUP BY meaningful (count/sum/min/max/avg), arithmetic (+ - * /),
  comparisons / boolean logic / IN (literal lists or one subquery level),
  subqueries to depth 2 (the outer SELECT is depth 1).

Rejected (each with its own error message): DDL, DML, set operations,
window functions, RIGHT/FULL/CROSS joins, HAVING/LIMIT/DISTINCT (not in the
§5.1 v0 grammar), any function outside the vetted list, subquery depth > 2,
multiple statements, unaliased computed projections.
"""

from __future__ import annotations

import sqlglot
from sqlglot import exp

__all__ = [
    "DslError",
    "validate_sql",
    "SCALAR_FUNCTIONS",
    "AGGREGATE_FUNCTIONS",
    "ANONYMOUS_FUNCTIONS",
    "MAX_SUBQUERY_DEPTH",
]

MAX_SUBQUERY_DEPTH = 2

DIALECT = "duckdb"


class DslError(ValueError):
    """A transform body violates the restricted-DSL grammar."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"[{code}] {message}")
        self.code = code


# Vetted scalar functions (§5.1 / M7 build sheet), as sqlglot expression
# classes mapped to the canonical operation label used in lineage chains.
SCALAR_FUNCTIONS: dict[type[exp.Expression], str] = {
    exp.Upper: "UPPER",
    exp.Lower: "LOWER",
    exp.Trim: "TRIM",
    exp.Replace: "REPLACE",
    exp.Substring: "SUBSTR",
    exp.SplitPart: "SPLIT_PART",
    exp.Concat: "CONCAT",
    exp.Coalesce: "COALESCE",
    exp.Nullif: "NULLIF",
    exp.RegexpExtract: "REGEXP_EXTRACT",
    exp.RegexpReplace: "REGEXP_REPLACE",
    exp.Round: "ROUND",
    exp.Abs: "ABS",
    exp.StrToTime: "STRPTIME",   # duckdb strptime()
    exp.TimeToStr: "STRFTIME",   # duckdb strftime()
}

# Aggregates: GROUP BY is in the allowlist, so the standard aggregates that
# make it meaningful come with it (documented in the module README).
AGGREGATE_FUNCTIONS: dict[type[exp.Expression], str] = {
    exp.Count: "COUNT",
    exp.Sum: "SUM",
    exp.Min: "MIN",
    exp.Max: "MAX",
    exp.Avg: "AVG",
}

# Functions sqlglot keeps as exp.Anonymous in the duckdb dialect.
ANONYMOUS_FUNCTIONS: dict[str, str] = {
    "date_part": "DATE_PART",
}

_FUNCTION_LABELS: dict[type[exp.Expression], str] = {
    **SCALAR_FUNCTIONS,
    **AGGREGATE_FUNCTIONS,
}

# Structural / leaf nodes that are always fine inside an allowed query.
_ALLOWED_NODES: tuple[type[exp.Expression], ...] = (
    exp.Select,
    exp.From,
    exp.Table,
    exp.TableAlias,
    exp.Subquery,
    exp.Join,
    exp.Where,
    exp.Group,
    exp.Order,
    exp.Ordered,
    exp.Alias,
    exp.Column,
    exp.Identifier,
    exp.Star,
    exp.Literal,
    exp.Boolean,
    exp.Null,
    exp.Cast,
    exp.DataType,
    exp.DataTypeParam,
    exp.Case,
    exp.If,
    exp.Paren,
    exp.Tuple,
    # arithmetic
    exp.Add,
    exp.Sub,
    exp.Mul,
    exp.Div,
    exp.Neg,
    # predicates / boolean logic
    exp.EQ,
    exp.NEQ,
    exp.GT,
    exp.GTE,
    exp.LT,
    exp.LTE,
    exp.And,
    exp.Or,
    exp.Not,
    exp.Is,
    exp.In,
)

_ALLOWED_NODE_SET = set(_ALLOWED_NODES)

_DDL_NODES = (exp.Create, exp.Drop, exp.Alter)
_DML_NODES = (exp.Insert, exp.Update, exp.Delete, exp.Merge)


def _select_depth(node: exp.Expression) -> int:
    depth = 0
    cur = node.parent
    while cur is not None:
        if isinstance(cur, exp.Select):
            depth += 1
        cur = cur.parent
    return depth + 1  # the node's own SELECT level


def _check_statement_kind(tree: exp.Expression) -> None:
    if isinstance(tree, _DDL_NODES):
        raise DslError("ddl", f"DDL is not allowed in transform bodies: {tree.sql(DIALECT)!r}")
    if isinstance(tree, _DML_NODES):
        raise DslError("dml", f"DML is not allowed in transform bodies: {tree.sql(DIALECT)!r}")
    if isinstance(tree, (exp.Union, exp.Intersect, exp.Except)):
        raise DslError(
            "set_operation",
            f"set operations (UNION/INTERSECT/EXCEPT) are not in the v0 DSL: {tree.sql(DIALECT)!r}",
        )
    if not isinstance(tree, exp.Select):
        raise DslError(
            "not_select",
            f"a transform body must be a single SELECT, got {type(tree).__name__}",
        )


def _check_join(node: exp.Join) -> None:
    side = (node.side or "").upper()
    kind = (node.kind or "").upper()
    if kind in ("CROSS", "SEMI", "ANTI") or side in ("RIGHT", "FULL"):
        raise DslError(
            "bad_join",
            f"only INNER and LEFT joins are allowed, got {side or kind or '?'} JOIN: "
            f"{node.sql(DIALECT)!r}",
        )
    if side not in ("", "LEFT") or kind not in ("", "INNER"):
        raise DslError("bad_join", f"unsupported join flavor: {node.sql(DIALECT)!r}")


def _check_function(node: exp.Func) -> None:
    if isinstance(node, (exp.Cast, exp.Case, exp.If)):
        return  # CAST/CASE parse as Func subclasses; both explicitly allowed
    if isinstance(node, exp.Anonymous):
        name = str(node.this).lower()
        if name not in ANONYMOUS_FUNCTIONS:
            raise DslError(
                "disallowed_function",
                f"function {name!r} is not in the vetted scalar-function list",
            )
        return
    if type(node) in _FUNCTION_LABELS:
        return
    raise DslError(
        "disallowed_function",
        f"function {node.sql_name().lower()!r} is not in the vetted scalar-function list "
        f"({node.sql(DIALECT)!r})",
    )


def _check_projections(select: exp.Select) -> None:
    for proj in select.expressions:
        if isinstance(proj, (exp.Alias, exp.Star)):
            continue
        if isinstance(proj, exp.Column):
            continue  # bare column or t.* keeps its own name
        raise DslError(
            "unaliased_projection",
            f"every computed output column must be aliased: {proj.sql(DIALECT)!r}",
        )


def validate_sql(sql: str) -> exp.Select:
    """Parse and validate a transform body. Returns the sqlglot Select on
    success; raises DslError (with a stable .code) on any violation."""
    try:
        statements = sqlglot.parse(sql, read=DIALECT)
    except sqlglot.errors.ParseError as e:  # pragma: no cover - msg passthrough
        raise DslError("parse_error", f"unparseable SQL: {e}") from e
    statements = [s for s in statements if s is not None]
    if len(statements) != 1:
        raise DslError(
            "multiple_statements",
            f"a transform body must be exactly one statement, got {len(statements)}",
        )
    tree = statements[0]
    _check_statement_kind(tree)
    assert isinstance(tree, exp.Select)

    for node in tree.walk():
        if isinstance(node, _DDL_NODES):
            raise DslError("ddl", f"DDL is not allowed: {node.sql(DIALECT)!r}")
        if isinstance(node, _DML_NODES):
            raise DslError("dml", f"DML is not allowed: {node.sql(DIALECT)!r}")
        if isinstance(node, exp.Window):
            raise DslError(
                "window_function",
                f"window functions are not allowed in v0: {node.sql(DIALECT)!r}",
            )
        if isinstance(node, (exp.Union, exp.Intersect, exp.Except)):
            raise DslError(
                "set_operation",
                f"set operations are not in the v0 DSL: {node.sql(DIALECT)!r}",
            )
        if isinstance(node, exp.Select):
            depth = _select_depth(node)
            if depth > MAX_SUBQUERY_DEPTH:
                raise DslError(
                    "subquery_depth",
                    f"subquery depth {depth} exceeds the v0 limit of {MAX_SUBQUERY_DEPTH}",
                )
            _check_projections(node)
            continue
        if isinstance(node, exp.Join):
            _check_join(node)
            continue
        if type(node) in _ALLOWED_NODE_SET:
            continue  # exact-type match; subclasses still face the checks below
        if isinstance(node, exp.Func):
            _check_function(node)
            continue
        raise DslError(
            "disallowed_construct",
            f"{type(node).__name__} is not in the v0 DSL grammar: {node.sql(DIALECT)!r}",
        )
    return tree
