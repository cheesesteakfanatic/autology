"""DSL allowlist enforcement (§5.1): everything outside the v0 grammar is
rejected with a named, stable error code."""

from __future__ import annotations

import pytest
from sqlglot import exp

from ontoforge.transforms import DslError, validate_sql

REJECTIONS = [
    # (sql, expected error code)
    ("CREATE TABLE x (a INT)", "ddl"),
    ("DROP TABLE raw.t", "ddl"),
    ("ALTER TABLE raw.t ADD COLUMN z INT", "ddl"),
    ("INSERT INTO raw.t VALUES (1)", "dml"),
    ("UPDATE raw.t SET a = 1", "dml"),
    ("DELETE FROM raw.t", "dml"),
    ("SELECT a, row_number() OVER (ORDER BY a) AS r FROM raw.t", "window_function"),
    ("SELECT a, sum(b) OVER (PARTITION BY a) AS s FROM raw.t", "window_function"),
    # depth 3 (outer SELECT is depth 1)
    ("SELECT a FROM (SELECT a FROM (SELECT a FROM raw.t) AS x) AS y", "subquery_depth"),
    ("SELECT a FROM raw.t RIGHT JOIN raw.u ON raw.t.a = raw.u.a", "bad_join"),
    ("SELECT a FROM raw.t FULL JOIN raw.u ON raw.t.a = raw.u.a", "bad_join"),
    ("SELECT a FROM raw.t CROSS JOIN raw.u", "bad_join"),
    ("SELECT now() AS n FROM raw.t", "disallowed_function"),
    ("SELECT random() AS r FROM raw.t", "disallowed_function"),
    ("SELECT len(a) AS l FROM raw.t", "disallowed_function"),
    ("SELECT a FROM raw.t UNION SELECT a FROM raw.u", "set_operation"),
    ("SELECT upper(a) FROM raw.t", "unaliased_projection"),
    ("SELECT a FROM raw.t LIMIT 5", "disallowed_construct"),
    ("SELECT a, sum(b) AS s FROM raw.t GROUP BY a HAVING sum(b) > 1", "disallowed_construct"),
    ("SELECT a FROM raw.t; SELECT b FROM raw.u", "multiple_statements"),
]


@pytest.mark.parametrize("sql,code", REJECTIONS, ids=[c + ":" + s[:40] for s, c in REJECTIONS])
def test_rejected_with_named_error(sql: str, code: str) -> None:
    with pytest.raises(DslError) as ei:
        validate_sql(sql)
    assert ei.value.code == code
    assert str(ei.value).startswith(f"[{code}]")


ACCEPTED = [
    "SELECT a, b FROM raw.t",
    "SELECT upper(trim(a)) AS x, CAST(b AS INT) AS n FROM raw.t WHERE c = 'ok'",
    "SELECT t.a AS x, u.d AS y FROM raw.t AS t JOIN raw.u AS u ON t.a = u.a",
    "SELECT t.a AS x FROM raw.t AS t LEFT JOIN raw.u AS u ON t.a = u.a",
    "SELECT a, count(*) AS n, sum(b) AS s, min(b) AS lo, max(b) AS hi, avg(b) AS m "
    "FROM raw.t GROUP BY a ORDER BY a",
    "SELECT CASE WHEN a = 'x' THEN b ELSE c END AS v FROM raw.t",
    "SELECT * FROM raw.t",
    "SELECT t.* FROM raw.t AS t",
    "SELECT x AS y FROM (SELECT upper(a) AS x FROM raw.t) AS s",
    "SELECT coalesce(nullif(trim(a), ''), b) AS v, split_part(a, '-', 1) AS p, "
    "substr(a, 1, 3) AS s3, regexp_extract(a, '(N[0-9]+)', 1) AS tail, "
    "regexp_replace(a, 'x', 'y') AS rr, replace(a, 'u', 'U') AS rp, "
    "round(abs(b) / 2, 1) AS h, concat(a, b) AS ab, lower(a) AS lo FROM raw.t",
    "SELECT strptime(d, '%Y-%m-%d') AS ts, strftime(ts2, '%Y') AS y, "
    "date_part('year', ts3) AS yr FROM raw.t",
    "SELECT a + b AS s, a - b AS d, a * b AS p, a / b AS q, -a AS n FROM raw.t",
    "SELECT a FROM raw.t WHERE a IN ('x', 'y') AND b IS NULL OR NOT (c > 1)",
]


@pytest.mark.parametrize("sql", ACCEPTED, ids=[s[:50] for s in ACCEPTED])
def test_accepted(sql: str) -> None:
    tree = validate_sql(sql)
    assert isinstance(tree, exp.Select)


def test_returns_parsed_tree_for_reuse() -> None:
    tree = validate_sql("SELECT a AS x FROM raw.t")
    assert tree.expressions[0].alias_or_name == "x"
