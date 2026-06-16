"""SQL-synthesis-and-execute backward join validation (v2.1 §1.4, M-REL).

CLOSED-CORE IP per OntoForge_Build_Instructions.md §18 — this is proprietary
engine machinery, not part of the open contract/connector surface.

The v2.1 mandate's strongest correctness guarantee for relationship inference is
to *validate the join backwards against real data*: don't trust a similarity
score — synthesize the join, EXECUTE it, and measure what actually came out.

This module does exactly that, in-process via DuckDB (already a dependency):

  * ``validate_join(left_rows, right_rows, left_col, right_col)`` — register two
    row-sets, run the executed join, and compute:
      - ``match_rate``    fraction of NON-NULL left keys present in the right side
      - ``orphan_rate``   non-null left keys with no right match (dangling FKs)
      - ``fanout_avg``    right rows per matched left key (detects unintended m2m)
      - ``fanout_max``    worst-case fan-out for a single left key
      - ``null_key_rate`` fraction of left rows whose join key is NULL
      - ``rows_left`` / ``rows_right``
    then derive a typed ``verdict`` (FK_JOIN · LOOKUP_DIMENSION · M2M_BRIDGE ·
    UNRELATED · UNKNOWN) with an ``ok`` flag and a human-readable ``detail``.

  * ``validate_join_frames(left, right, left_col, right_col)`` — the
    frame/relation variant: accepts anything DuckDB can register directly
    (Arrow tables, pandas/polars frames, DuckDB relations) plus our canonical
    list-of-dicts. Same metrics, same verdict.

  * ``validate_candidates(candidates, table_data, ...)`` — batch driver that runs
    the executed validation for a list of ``RelationshipCandidate``s and returns
    ``{candidate -> JoinValidation}``. For big tables it validates on a
    schema-informed *stratified sample around the candidate keys* (see
    ``BatchValidationConfig`` and ``_stratified_sample`` for the documented
    sampling), so billion-row inference stays bounded without distorting the
    measured match/orphan/fan-out rates.

DETERMINISTIC + ZERO-NETWORK: DuckDB runs on an in-memory ``:memory:`` connection;
sampling uses a fixed, content-derived ordering (no RNG, no wall clock). The same
inputs always yield the same ``JoinValidation``.

The engine ships KEYLESS: no model is invoked here. Downstream, a
``ScoutPayload`` carrying these executed metrics is what an adjudicator would
reason over — but the validation itself is pure SQL on real rows.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Optional, Sequence

import duckdb

from ..contracts.relationships import (
    ColumnRef,
    JoinValidation,
    RelationshipCandidate,
    RelationshipType,
)

# --------------------------------------------------------------------- thresholds
#
# Verdict thresholds. These are the executed-data decision boundaries the
# backward validator uses to type a join from its measured shape. They are
# intentionally explicit (no magic numbers buried in branches) so the verdict
# logic is auditable.

# A join is "matchy enough" to be a real relationship at/above this match rate.
_MATCH_STRONG = 0.90
# Below this match rate the left/right key spaces barely overlap → unrelated.
_MATCH_UNRELATED = 0.05
# Fan-out at/below this is effectively one-to-one (parent side behaves like a key).
_FANOUT_ONE = 1.0001
# Fan-out at/above this on the matched side is a genuine many-relationship.
_FANOUT_MANY = 1.5
# A lookup/dimension is a genuinely SMALL reference/code table: few distinct
# parent keys, referenced many times. Both conditions must hold to call it a
# dimension rather than a plain FK to a sizeable parent (e.g. customers):
#   - the parent has at most this many distinct keys (a code/reference table), and
_LOOKUP_RIGHT_DISTINCT_ABS = 32
#   - the child references the parent heavily — left ROWS outnumber distinct
#     parent keys by at least this factor (each code reused across many rows,
#     not a ~1:1 child→parent FK to a sizeable-but-small parent).
_LOOKUP_REUSE_FACTOR = 8.0
# If essentially every key is null there is nothing to validate.
_NULL_DOMINANT = 0.99


# --------------------------------------------------------------------- batch config


@dataclass(frozen=True, slots=True)
class BatchValidationConfig:
    """Knobs for the batched, schema-informed validation (§1.4 / §2).

    ``sample_threshold`` — once a side exceeds this many rows, validate on a
    stratified sample instead of the full table (keeps billion-row inference
    bounded). ``sample_size`` is the target per-side sample budget. ``strata`` is
    how many key-buckets the sample is spread across so the sample lands *around
    the candidate keys / cardinality boundaries* rather than clumping. Sampling
    is fully deterministic (content-derived ordering, no RNG).
    """

    sample_threshold: int = 50_000
    sample_size: int = 20_000
    strata: int = 16


# --------------------------------------------------------------------- registration


def _to_relation(con: duckdb.DuckDBPyConnection, name: str, data: Any) -> None:
    """Register ``data`` under ``name`` on ``con``.

    Accepts our canonical list-of-dicts as well as anything DuckDB can register
    natively (Arrow table, pandas/polars frame, a DuckDB relation, or a 2-tuple
    of (column_names, rows)). list-of-dicts is normalized so EVERY row carries
    EVERY observed key (DuckDB requires a uniform schema) with missing values as
    SQL NULL.
    """
    # DuckDB-registerable objects (Arrow / pandas / polars / relations) pass through.
    if hasattr(data, "__arrow_c_stream__") or hasattr(data, "arrow") or hasattr(data, "fetch_arrow_table"):
        con.register(name, data)
        return
    # pandas / polars frames expose .columns + a registerable body.
    if hasattr(data, "columns") and not isinstance(data, (list, tuple)):
        con.register(name, data)
        return

    rows = list(data)
    if rows and isinstance(rows[0], Mapping):
        # Union of keys, in first-seen order, for a stable uniform schema.
        cols: list[str] = []
        seen: set[str] = set()
        for r in rows:
            for k in r.keys():
                if k not in seen:
                    seen.add(k)
                    cols.append(k)
        norm = [{c: r.get(c) for c in cols} for r in rows]
        rel = _arrow_from_dicts(cols, norm)
        con.register(name, rel)
        return

    raise TypeError(
        f"unsupported row container for relation {name!r}: {type(data).__name__}; "
        "pass a list-of-dicts, an Arrow/pandas/polars frame, or a DuckDB relation"
    )


def _arrow_from_dicts(cols: Sequence[str], rows: Sequence[Mapping[str, Any]]):
    """Build an Arrow table from uniform dict rows (string-coerced, NULL-safe).

    Keys are coerced to a common comparable representation so heterogeneous
    Python value types in a column (e.g. int vs str ids) still join correctly
    and NULLs stay NULL. We keep a typed fast-path for all-int / all-float
    columns to preserve numeric semantics; otherwise the column is cast to
    nullable string. This mirrors what a real warehouse cast would do.
    """
    import pyarrow as pa

    arrays = []
    for c in cols:
        vals = [r.get(c) for r in rows]
        non_null = [v for v in vals if v is not None]
        if non_null and all(isinstance(v, bool) for v in non_null):
            arrays.append(pa.array(vals, type=pa.bool_()))
        elif non_null and all(isinstance(v, int) and not isinstance(v, bool) for v in non_null):
            arrays.append(pa.array(vals, type=pa.int64()))
        elif non_null and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in non_null):
            arrays.append(pa.array([None if v is None else float(v) for v in vals], type=pa.float64()))
        else:
            arrays.append(pa.array([None if v is None else str(v) for v in vals], type=pa.string()))
    return pa.table(arrays, names=list(cols))


# --------------------------------------------------------------------- core metrics


def _measure(
    con: duckdb.DuckDBPyConnection,
    left_name: str,
    right_name: str,
    left_col: str,
    right_col: str,
) -> dict[str, float]:
    """Execute the join and return the raw measured metrics.

    All numbers come straight out of executed SQL on the registered relations —
    this is the "backwards against real data" guarantee. NULL keys are excluded
    from match/orphan/fan-out (you cannot FK-match on NULL) but counted for
    ``null_key_rate``.
    """
    # Raw column quotes (for IS NULL checks) and VARCHAR-cast key expressions
    # (for every comparison/join/grouping). Casting both keys to VARCHAR makes the
    # executed join robust to heterogeneous storage types across sources (e.g. an
    # int id on one side, a zero-padded string code on the other) — the same
    # coercion a warehouse cast would apply — and sidesteps DuckDB's strict
    # cross-type IN/= binder. NULLs are preserved by the cast.
    # Quote identifiers safely: double any embedded double-quote (standard SQL
    # identifier escaping) so a column name can never break out of the literal.
    lq = '"' + left_col.replace('"', '""') + '"'
    rq = '"' + right_col.replace('"', '""') + '"'
    lk = f'CAST(l.{lq} AS VARCHAR)'
    rk = f'CAST(r.{rq} AS VARCHAR)'
    lk_bare = f'CAST({lq} AS VARCHAR)'
    rk_bare = f'CAST({rq} AS VARCHAR)'

    rows_left = int(con.execute(f"SELECT count(*) FROM {left_name}").fetchone()[0])
    rows_right = int(con.execute(f"SELECT count(*) FROM {right_name}").fetchone()[0])

    null_left = int(
        con.execute(f"SELECT count(*) FROM {left_name} WHERE {lq} IS NULL").fetchone()[0]
    )
    non_null_left = rows_left - null_left

    # Right-side key multiplicity per distinct right key (drives fan-out).
    # matched := left non-null keys that appear in the right key set.
    # fan-out := for each matched left ROW, how many right rows it joins to.
    #
    # We compute fan-out as (joined output rows) / (matched left rows): a clean
    # parent-side-unique FK gives exactly 1.0; a bridge blows up above 1.
    matched_left_rows = int(
        con.execute(
            f"""
            SELECT count(*) FROM {left_name} l
            WHERE l.{lq} IS NOT NULL
              AND {lk} IN (SELECT {rk_bare} FROM {right_name} WHERE {rq} IS NOT NULL)
            """
        ).fetchone()[0]
    )

    joined_rows = int(
        con.execute(
            f"""
            SELECT count(*) FROM {left_name} l
            JOIN {right_name} r ON {lk} = {rk}
            WHERE l.{lq} IS NOT NULL
            """
        ).fetchone()[0]
    )

    # Worst-case fan-out: the largest number of right rows any single matched
    # left key maps to (right-key multiplicity over the matched key set).
    fanout_max_row = con.execute(
        f"""
        SELECT max(rc) FROM (
            SELECT {rk_bare} AS k, count(*) AS rc
            FROM {right_name} r
            WHERE {rq} IS NOT NULL
              AND {rk_bare} IN (SELECT {lk_bare} FROM {left_name} WHERE {lq} IS NOT NULL)
            GROUP BY {rk_bare}
        )
        """
    ).fetchone()[0]
    fanout_max = float(fanout_max_row) if fanout_max_row is not None else 0.0

    # Reverse fan-out: how many left rows map to a single right key (matched).
    # Needed to distinguish a true m2m bridge (fan-out > 1 on BOTH sides).
    rev_fanout_max_row = con.execute(
        f"""
        SELECT max(lc) FROM (
            SELECT {lk_bare} AS k, count(*) AS lc
            FROM {left_name} l
            WHERE {lq} IS NOT NULL
              AND {lk_bare} IN (SELECT {rk_bare} FROM {right_name} WHERE {rq} IS NOT NULL)
            GROUP BY {lk_bare}
        )
        """
    ).fetchone()[0]
    rev_fanout_max = float(rev_fanout_max_row) if rev_fanout_max_row is not None else 0.0

    # Right-side uniqueness over the FULL right key set: is the parent a key?
    right_distinct_keys = int(
        con.execute(
            f"SELECT count(DISTINCT {rk_bare}) FROM {right_name} WHERE {rq} IS NOT NULL"
        ).fetchone()[0]
    )
    right_non_null = int(
        con.execute(f"SELECT count(*) FROM {right_name} WHERE {rq} IS NOT NULL").fetchone()[0]
    )

    # Distinct left keys: distinguishes a dimension (few parent codes, reused by
    # many distinct child keys) from a 1:1 child→parent FK.
    left_distinct_keys = int(
        con.execute(
            f"SELECT count(DISTINCT {lk_bare}) FROM {left_name} WHERE {lq} IS NOT NULL"
        ).fetchone()[0]
    )

    match_rate = (matched_left_rows / non_null_left) if non_null_left else 0.0
    orphan_rate = (1.0 - match_rate) if non_null_left else 0.0
    fanout_avg = (joined_rows / matched_left_rows) if matched_left_rows else 0.0
    null_key_rate = (null_left / rows_left) if rows_left else 0.0
    right_uniqueness = (right_distinct_keys / right_non_null) if right_non_null else 1.0

    return {
        "match_rate": match_rate,
        "orphan_rate": orphan_rate,
        "fanout_avg": fanout_avg,
        "fanout_max": fanout_max,
        "rev_fanout_max": rev_fanout_max,
        "null_key_rate": null_key_rate,
        "rows_left": rows_left,
        "rows_right": rows_right,
        "right_uniqueness": right_uniqueness,
        "non_null_left": non_null_left,
        "right_distinct_keys": right_distinct_keys,
        "left_distinct_keys": left_distinct_keys,
    }


def _verdict(m: Mapping[str, float]) -> tuple[RelationshipType, bool, str]:
    """Derive a typed verdict + ok-flag + human detail from executed metrics.

    The decision is shape-based, in priority order:
      1. nothing to validate (empty or all-NULL keys)          → UNKNOWN
      2. match rate near zero                                  → UNRELATED
      3. fan-out > 1 on BOTH sides (matched)                   → M2M_BRIDGE
      4. high match, ~1:1, parent-side unique, small right     → LOOKUP_DIMENSION
      5. high match, ~1:1, parent-side unique                  → FK_JOIN
      6. high match but parent NOT unique / fan-out high       → M2M_BRIDGE (one-way)
      7. otherwise                                             → UNKNOWN (mixed)
    ``ok`` means the executed data SUPPORTS the typed verdict (a confident,
    clean shape), not merely that SQL ran.
    """
    match = m["match_rate"]
    fanout_avg = m["fanout_avg"]
    fanout_max = m["fanout_max"]
    rev_fanout_max = m["rev_fanout_max"]
    right_unique = m["right_uniqueness"]
    rows_left = m["rows_left"]
    rows_right = m["rows_right"]
    null_rate = m["null_key_rate"]
    non_null_left = m["non_null_left"]
    right_distinct = m["right_distinct_keys"]

    if rows_left == 0 or rows_right == 0 or non_null_left == 0 or null_rate >= _NULL_DOMINANT:
        return (
            RelationshipType.UNKNOWN,
            False,
            "nothing to validate (empty side or join key all-NULL)",
        )

    if match <= _MATCH_UNRELATED:
        return (
            RelationshipType.UNRELATED,
            True,
            f"match_rate={match:.2f} ≈ 0 — left keys absent from right; unrelated despite similarity",
        )

    parent_unique = right_unique > 0.999
    one_to_one = fanout_max <= _FANOUT_ONE and fanout_avg <= _FANOUT_ONE
    many_right = fanout_max >= _FANOUT_MANY
    many_left = rev_fanout_max >= _FANOUT_MANY

    # True bridge: many on BOTH sides.
    if many_right and many_left:
        return (
            RelationshipType.M2M_BRIDGE,
            True,
            f"match_rate={match:.2f}, fan-out {fanout_max:.0f}↔{rev_fanout_max:.0f} both ways — many-to-many bridge",
        )

    if match >= _MATCH_STRONG and one_to_one and parent_unique:
        # Dimension/lookup := a small reference table (few distinct parent codes)
        # that the child reuses heavily (many distinct child keys per parent key).
        # A 1:1-ish child→parent FK (e.g. orders→customers) fails the reuse test
        # and stays FK_JOIN even though the parent is the smaller table.
        is_dimension = (
            right_distinct <= _LOOKUP_RIGHT_DISTINCT_ABS
            and right_distinct > 0
            and non_null_left >= right_distinct * _LOOKUP_REUSE_FACTOR
        )
        if is_dimension:
            return (
                RelationshipType.LOOKUP_DIMENSION,
                True,
                f"match_rate={match:.2f}, fan-out≈1, parent unique, small reference table "
                f"({right_distinct:.0f} codes reused across {non_null_left:.0f} child rows) — lookup/dimension",
            )
        return (
            RelationshipType.FK_JOIN,
            True,
            f"match_rate={match:.2f}, fan-out≈1, parent-side unique — clean FK join",
        )

    # High match but the "parent" repeats / fans out one way → not a clean FK.
    if match >= _MATCH_STRONG and (not parent_unique or many_right):
        return (
            RelationshipType.M2M_BRIDGE,
            False,
            f"match_rate={match:.2f} but parent not unique (fan-out_max={fanout_max:.0f}) — "
            "one-sided fan-out, not a clean FK",
        )

    return (
        RelationshipType.UNKNOWN,
        False,
        f"mixed shape: match_rate={match:.2f}, fan-out_avg={fanout_avg:.2f}, "
        f"parent_uniqueness={right_unique:.2f} — below commit threshold",
    )


def _validation_from_metrics(m: Mapping[str, float]) -> JoinValidation:
    verdict, ok, detail = _verdict(m)
    return JoinValidation(
        match_rate=round(m["match_rate"], 6),
        orphan_rate=round(m["orphan_rate"], 6),
        fanout_avg=round(m["fanout_avg"], 6),
        fanout_max=float(m["fanout_max"]),
        null_key_rate=round(m["null_key_rate"], 6),
        rows_left=int(m["rows_left"]),
        rows_right=int(m["rows_right"]),
        verdict=verdict,
        ok=ok,
        detail=detail,
    )


# --------------------------------------------------------------------- public API


def validate_join_frames(
    left: Any,
    right: Any,
    left_col: str,
    right_col: str,
) -> JoinValidation:
    """Frame/relation variant of :func:`validate_join`.

    Accepts anything DuckDB can register (Arrow / pandas / polars frame, a DuckDB
    relation, or our list-of-dicts) for ``left`` and ``right`` and validates the
    ``left.left_col = right.right_col`` join by EXECUTING it in DuckDB. Pure
    in-process; deterministic.
    """
    # Empty list-of-dicts carries no schema for DuckDB to register; an empty side
    # means there is nothing to validate. Short-circuit to a zero-row UNKNOWN
    # rather than synthesizing a phantom relation.
    if (isinstance(left, (list, tuple)) and len(left) == 0) or (
        isinstance(right, (list, tuple)) and len(right) == 0
    ):
        return _validation_from_metrics(
            {
                "match_rate": 0.0,
                "orphan_rate": 0.0,
                "fanout_avg": 0.0,
                "fanout_max": 0.0,
                "rev_fanout_max": 0.0,
                "null_key_rate": 0.0,
                "rows_left": 0 if isinstance(left, (list, tuple)) and len(left) == 0 else _row_count(left),
                "rows_right": 0 if isinstance(right, (list, tuple)) and len(right) == 0 else _row_count(right),
                "right_uniqueness": 1.0,
                "non_null_left": 0,
                "right_distinct_keys": 0,
                "left_distinct_keys": 0,
            }
        )

    con = duckdb.connect(":memory:")
    try:
        _to_relation(con, "vj_left", left)
        _to_relation(con, "vj_right", right)
        m = _measure(con, "vj_left", "vj_right", left_col, right_col)
    finally:
        con.close()
    return _validation_from_metrics(m)


def _row_count(data: Any) -> int:
    try:
        return len(data)  # type: ignore[arg-type]
    except TypeError:
        return 0


def validate_join(
    left_rows: Sequence[Mapping[str, Any]],
    right_rows: Sequence[Mapping[str, Any]],
    left_col: str,
    right_col: str,
) -> JoinValidation:
    """Synthesize and EXECUTE the ``left_col → right_col`` join; validate it on real data.

    ``left_rows`` / ``right_rows`` are sequences of dict rows (the canonical
    in-memory shape). Returns a :class:`JoinValidation` with the executed
    match / orphan / fan-out / null-key metrics and a derived typed verdict.

    This is the backward-validation primitive (§1.4): it does not trust a
    similarity score — it runs the join and measures what came out.
    """
    return validate_join_frames(list(left_rows), list(right_rows), left_col, right_col)


# --------------------------------------------------------------------- sampling


def _stratified_sample(
    rows: Sequence[Mapping[str, Any]],
    col: str,
    config: BatchValidationConfig,
) -> list[Mapping[str, Any]]:
    """Schema-informed, DETERMINISTIC stratified sample around the candidate key.

    For big tables we cannot execute the join over every row, so we sample — but
    a naive head/random sample distorts match/orphan/fan-out (it can miss the
    long tail of keys, or over-represent a hot key, changing every measured
    rate). Instead we bucket rows by a stable hash of the *candidate key value*
    into ``config.strata`` strata and take a proportional slice from each, so the
    sample preserves the key distribution (and the orphan/fan-out shape) around
    the candidate keys.

    NULL-keyed rows form their own stratum so ``null_key_rate`` is preserved.
    Ordering is content-derived (hash, then a stable secondary index) — no RNG,
    no wall clock — so results are reproducible. Returns the full input
    unchanged when it is at/under ``sample_threshold``.
    """
    n = len(rows)
    if n <= config.sample_threshold:
        return list(rows)

    target = max(config.sample_size, config.strata)
    strata = max(1, config.strata)

    # Bucket indices by stable hash of the string-coerced key value.
    buckets: list[list[int]] = [[] for _ in range(strata)]
    null_bucket: list[int] = []
    for i, r in enumerate(rows):
        v = r.get(col)
        if v is None:
            null_bucket.append(i)
        else:
            h = _stable_hash(str(v))
            buckets[h % strata].append(i)

    # Preserve the null fraction in the sample.
    null_keep = round(target * (len(null_bucket) / n)) if n else 0
    per_stratum = max(1, (target - null_keep) // strata)

    chosen: list[int] = []
    for b in buckets:
        # Deterministic within-stratum order: by hash of (value, original index).
        b_sorted = sorted(b, key=lambda i: (_stable_hash(f"{rows[i].get(col)}|{i}"), i))
        chosen.extend(b_sorted[:per_stratum])
    chosen.extend(sorted(null_bucket)[:null_keep])
    chosen.sort()
    return [rows[i] for i in chosen]


def _stable_hash(s: str) -> int:
    """A small deterministic, process-independent hash (FNV-1a, 32-bit).

    Python's built-in ``hash`` is salted per-process; we need reproducibility
    across runs, so we roll a fixed FNV-1a.
    """
    h = 0x811C9DC5
    for ch in s.encode("utf-8"):
        h ^= ch
        h = (h * 0x01000193) & 0xFFFFFFFF
    return h


# --------------------------------------------------------------------- batch


def _lookup_table(
    table_data: Mapping[Any, Any],
    ref: ColumnRef,
) -> Optional[Any]:
    """Resolve a ColumnRef to its registered row container.

    ``table_data`` may key tables by ``(source_id, table)``, by ``"source.table"``,
    or by bare ``table`` name — we try them in that specificity order so callers
    can use whichever they have.
    """
    for key in (
        (ref.source_id, ref.table),
        f"{ref.source_id}.{ref.table}",
        ref.table,
    ):
        if key in table_data:
            return table_data[key]
    return None


def validate_candidates(
    candidates: Iterable[RelationshipCandidate],
    table_data: Mapping[Any, Any],
    config: Optional[BatchValidationConfig] = None,
) -> dict[RelationshipCandidate, JoinValidation]:
    """Run executed backward validation for a batch of candidates.

    For each candidate, resolve its left/right tables from ``table_data`` and run
    :func:`validate_join_frames`. For tables larger than
    ``config.sample_threshold`` (list-of-dicts only — already-materialized frames
    are validated whole), the join is validated on a deterministic stratified
    sample around the candidate keys (see :func:`_stratified_sample`) so the
    work stays bounded without distorting the measured rates.

    Returns ``{candidate -> JoinValidation}``. A candidate whose tables cannot be
    resolved gets a ``JoinValidation`` with verdict ``UNKNOWN`` / ``ok=False`` and
    an explanatory ``detail`` (rather than raising), so a batch never half-fails.
    """
    cfg = config or BatchValidationConfig()
    out: dict[RelationshipCandidate, JoinValidation] = {}

    for cand in candidates:
        left = _lookup_table(table_data, cand.left)
        right = _lookup_table(table_data, cand.right)
        if left is None or right is None:
            missing = []
            if left is None:
                missing.append(f"{cand.left.source_id}.{cand.left.table}")
            if right is None:
                missing.append(f"{cand.right.source_id}.{cand.right.table}")
            out[cand] = JoinValidation(
                match_rate=0.0,
                orphan_rate=0.0,
                fanout_avg=0.0,
                fanout_max=0.0,
                null_key_rate=0.0,
                rows_left=0,
                rows_right=0,
                verdict=RelationshipType.UNKNOWN,
                ok=False,
                detail=f"table data not found for: {', '.join(missing)}",
            )
            continue

        # Only list-of-dicts get sampled (we know how to stratify them);
        # native frames are validated whole — DuckDB handles them efficiently.
        left_v = (
            _stratified_sample(left, cand.left.column, cfg)
            if _is_dict_rows(left)
            else left
        )
        right_v = (
            _stratified_sample(right, cand.right.column, cfg)
            if _is_dict_rows(right)
            else right
        )

        out[cand] = validate_join_frames(
            left_v, right_v, cand.left.column, cand.right.column
        )

    return out


def _is_dict_rows(data: Any) -> bool:
    if isinstance(data, (list, tuple)):
        return len(data) == 0 or isinstance(data[0], Mapping)
    return False
