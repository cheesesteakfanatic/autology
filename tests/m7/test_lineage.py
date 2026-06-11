"""Column-level lineage correctness (§5.1, SQLMesh-style AST derivation) on a
curated corpus of SQL bodies with hand-derived expected lineage."""

from __future__ import annotations

import pytest

from ontoforge.contracts.transforms import ColumnLineage, TransformDef
from ontoforge.transforms import LineageError, lineage_for_sql, lineage_for_transform

SCHEMAS = {
    "raw.t": ["a", "b", "c"],
    "raw.u": ["a", "d", "e"],
    "raw.v": ["k", "b"],
}


def cl(name: str, inputs: list[tuple[str, str]], ops: tuple[str, ...] = ()) -> ColumnLineage:
    return ColumnLineage(output_column=name, inputs=tuple(sorted(inputs)), operations=ops)


# 15 hand-derived cases.
CORPUS: list[tuple[str, str, list[ColumnLineage]]] = [
    (
        "rename+passthrough",
        "SELECT a AS x, b FROM raw.t",
        [cl("x", [("raw.t", "a")]), cl("b", [("raw.t", "b")])],
    ),
    (
        "nested scalar functions",
        "SELECT upper(trim(a)) AS x FROM raw.t",
        [cl("x", [("raw.t", "a")], ("UPPER", "TRIM"))],
    ),
    (
        "cast",
        "SELECT CAST(b AS INT) AS n FROM raw.t",
        [cl("n", [("raw.t", "b")], ("CAST",))],
    ),
    (
        "case over three columns",
        "SELECT CASE WHEN a = 'x' THEN b ELSE c END AS v FROM raw.t",
        [cl("v", [("raw.t", "a"), ("raw.t", "b"), ("raw.t", "c")], ("CASE",))],
    ),
    (
        "multi-column function",
        "SELECT concat(a, b) AS ab FROM raw.t",
        [cl("ab", [("raw.t", "a"), ("raw.t", "b")], ("CONCAT",))],
    ),
    (
        "arithmetic inside function",
        "SELECT round(b / c, 2) AS r FROM raw.t",
        [cl("r", [("raw.t", "b"), ("raw.t", "c")], ("ROUND", "/"))],
    ),
    (
        "join, qualified refs",
        "SELECT t.a AS x, u.d AS y FROM raw.t AS t JOIN raw.u AS u ON t.a = u.a",
        [cl("x", [("raw.t", "a")]), cl("y", [("raw.u", "d")])],
    ),
    (
        "left join, unqualified-but-unique refs",
        "SELECT b, e FROM raw.t AS t LEFT JOIN raw.u AS u ON t.a = u.a",
        [cl("b", [("raw.t", "b")]), cl("e", [("raw.u", "e")])],
    ),
    (
        "select star",
        "SELECT * FROM raw.t",
        [cl("a", [("raw.t", "a")]), cl("b", [("raw.t", "b")]), cl("c", [("raw.t", "c")])],
    ),
    (
        "qualified star + extra column in join",
        "SELECT t.*, u.d FROM raw.t AS t LEFT JOIN raw.u AS u ON t.a = u.a",
        [
            cl("a", [("raw.t", "a")]),
            cl("b", [("raw.t", "b")]),
            cl("c", [("raw.t", "c")]),
            cl("d", [("raw.u", "d")]),
        ],
    ),
    (
        "group by with aggregates; count(*) has no column inputs",
        "SELECT a, count(*) AS n, sum(b) AS total FROM raw.t GROUP BY a",
        [
            cl("a", [("raw.t", "a")]),
            cl("n", [], ("COUNT",)),
            cl("total", [("raw.t", "b")], ("SUM",)),
        ],
    ),
    (
        "FROM-subquery composition: outer ops prepend inner ops",
        "SELECT lower(x) AS z FROM (SELECT upper(a) AS x FROM raw.t) AS s",
        [cl("z", [("raw.t", "a")], ("LOWER", "UPPER"))],
    ),
    (
        "coalesce/nullif chain over two columns",
        "SELECT coalesce(nullif(trim(a), ''), b) AS v FROM raw.t",
        [cl("v", [("raw.t", "a"), ("raw.t", "b")], ("COALESCE", "NULLIF", "TRIM"))],
    ),
    (
        "string surgery + date functions",
        "SELECT split_part(a, '-', 1) AS p, regexp_extract(a, '(N[0-9]+)', 1) AS tail, "
        "date_part('year', strptime(b, '%Y-%m-%d')) AS yr FROM raw.t",
        [
            cl("p", [("raw.t", "a")], ("SPLIT_PART",)),
            cl("tail", [("raw.t", "a")], ("REGEXP_EXTRACT",)),
            cl("yr", [("raw.t", "b")], ("DATE_PART", "STRPTIME")),
        ],
    ),
    (
        "case inside cast, cross-table branches",
        "SELECT CAST(CASE WHEN t.b = 'm' THEN u.d ELSE t.c END AS DOUBLE) AS alt "
        "FROM raw.t AS t JOIN raw.u AS u ON t.a = u.a",
        [
            cl(
                "alt",
                [("raw.t", "b"), ("raw.t", "c"), ("raw.u", "d")],
                ("CAST", "CASE"),
            )
        ],
    ),
]


@pytest.mark.parametrize("name,sql,expected", CORPUS, ids=[c[0] for c in CORPUS])
def test_lineage_corpus(name: str, sql: str, expected: list[ColumnLineage]) -> None:
    assert lineage_for_sql(sql, SCHEMAS) == expected


def test_corpus_size_floor() -> None:
    assert len(CORPUS) >= 12


def test_ambiguous_unqualified_column_rejected() -> None:
    # `b` exists in raw.t and raw.v
    with pytest.raises(LineageError, match="ambiguous"):
        lineage_for_sql(
            "SELECT b FROM raw.t AS t JOIN raw.v AS v ON t.a = v.k", SCHEMAS
        )


def test_unknown_column_rejected() -> None:
    with pytest.raises(LineageError, match="not found"):
        lineage_for_sql("SELECT zz FROM raw.t", SCHEMAS)


def test_unknown_table_rejected() -> None:
    with pytest.raises(LineageError, match="unknown input table"):
        lineage_for_sql("SELECT a FROM raw.nope", SCHEMAS)


def test_lineage_for_transform_checks_declared_inputs() -> None:
    t = TransformDef("x", ("raw.missing",), "out.x", "SELECT a FROM raw.t")
    with pytest.raises(LineageError, match="missing schemas"):
        lineage_for_transform(t, SCHEMAS)
    t2 = TransformDef("x", ("raw.t",), "out.x", "SELECT a FROM raw.t")
    assert lineage_for_transform(t2, SCHEMAS)[0].output_column == "a"
