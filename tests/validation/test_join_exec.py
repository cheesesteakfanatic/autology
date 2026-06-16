"""SQL-execute backward join validation (v2.1 §1.4, CLOSED CORE).

Executes joins in-process via DuckDB over small synthetic tables and asserts the
measured shape + derived verdict:

  * a real FK pair  → high match_rate, fan-out≈1, verdict FK_JOIN, ok=True
  * a disjoint pair → match_rate≈0, verdict UNRELATED
  * a m2m pair      → fan-out>1 both sides, verdict M2M_BRIDGE
  * a lookup/dimension pair → small right, fan-out≈1, verdict LOOKUP_DIMENSION
  * NULL keys are counted (null_key_rate) and excluded from match/fan-out
  * deterministic: same inputs → identical JoinValidation
  * batch validate_candidates resolves tables and stratified-samples big inputs

ZERO-NETWORK: everything runs on an in-memory DuckDB connection.
"""

from __future__ import annotations

import dataclasses

import pytest

from ontoforge.contracts.relationships import (
    ColumnRef,
    RelationshipCandidate,
    RelationshipType,
)
from ontoforge.validation import (
    BatchValidationConfig,
    validate_candidates,
    validate_join,
    validate_join_frames,
)


# --------------------------------------------------------------------- fixtures


def _customers(n: int = 50) -> list[dict]:
    return [{"id": i, "name": f"c{i}"} for i in range(n)]


def _orders_fk(n_customers: int = 50, n_orders: int = 200) -> list[dict]:
    # Every order points at a valid customer (clean FK, parent-side unique).
    return [
        {"order_id": k, "customer_id": k % n_customers, "amt": k * 1.5}
        for k in range(n_orders)
    ]


# --------------------------------------------------------------------- FK join


def test_fk_pair_high_match_fanout_one_verdict_fk_join():
    left = _orders_fk(n_customers=50, n_orders=200)
    right = _customers(50)

    v = validate_join(left, right, "customer_id", "id")

    assert v.match_rate == pytest.approx(1.0)
    assert v.orphan_rate == pytest.approx(0.0)
    assert v.fanout_avg == pytest.approx(1.0)
    assert v.fanout_max == pytest.approx(1.0)
    assert v.null_key_rate == pytest.approx(0.0)
    assert v.rows_left == 200
    assert v.rows_right == 50
    assert v.verdict == RelationshipType.FK_JOIN
    assert v.ok is True
    assert "FK join" in v.detail


def test_fk_with_some_orphans_drops_match_rate():
    # 10% of orders point at a customer id that does not exist (dangling FK).
    left = [
        {"order_id": k, "customer_id": (k % 50) if k % 10 else 9999}
        for k in range(200)
    ]
    right = _customers(50)

    v = validate_join(left, right, "customer_id", "id")

    assert v.match_rate == pytest.approx(0.9, abs=0.01)
    assert v.orphan_rate == pytest.approx(0.1, abs=0.01)
    assert v.fanout_avg == pytest.approx(1.0)


# --------------------------------------------------------------------- unrelated


def test_disjoint_pair_match_zero_verdict_unrelated():
    left = [{"k": f"L{i}"} for i in range(100)]
    right = [{"k": f"R{i}"} for i in range(100)]

    v = validate_join(left, right, "k", "k")

    assert v.match_rate == pytest.approx(0.0)
    assert v.orphan_rate == pytest.approx(1.0)
    assert v.verdict == RelationshipType.UNRELATED
    assert v.ok is True
    assert "unrelated" in v.detail.lower()


# --------------------------------------------------------------------- m2m bridge


def test_many_to_many_pair_fanout_both_sides_verdict_m2m_bridge():
    # students <-> courses via shared term codes: each side repeats the key.
    # left: 5 students per term, right: 4 courses per term, terms 0..9.
    left = [{"term": t} for t in range(10) for _ in range(5)]   # 50 rows, 5 per term
    right = [{"term": t} for t in range(10) for _ in range(4)]  # 40 rows, 4 per term

    v = validate_join(left, right, "term", "term")

    assert v.match_rate == pytest.approx(1.0)
    # each matched left row joins to 4 right rows.
    assert v.fanout_avg == pytest.approx(4.0)
    assert v.fanout_max == pytest.approx(4.0)
    assert v.verdict == RelationshipType.M2M_BRIDGE
    assert v.ok is True
    assert "many-to-many" in v.detail.lower()


# --------------------------------------------------------------------- lookup/dimension


def test_lookup_dimension_small_right_fanout_one():
    # large fact table, tiny status dimension (3 codes), parent-side unique.
    left = [{"order_id": k, "status_code": k % 3} for k in range(300)]
    right = [
        {"status_code": 0, "label": "open"},
        {"status_code": 1, "label": "shipped"},
        {"status_code": 2, "label": "closed"},
    ]

    v = validate_join(left, right, "status_code", "status_code")

    assert v.match_rate == pytest.approx(1.0)
    assert v.fanout_avg == pytest.approx(1.0)
    assert v.rows_right == 3
    assert v.verdict == RelationshipType.LOOKUP_DIMENSION
    assert v.ok is True
    assert "lookup" in v.detail.lower()


# --------------------------------------------------------------------- null keys


def test_null_keys_counted_and_excluded_from_match():
    # 4 of 8 left rows have NULL customer_id; the rest are valid FKs.
    left = [
        {"order_id": 0, "customer_id": 0},
        {"order_id": 1, "customer_id": None},
        {"order_id": 2, "customer_id": 1},
        {"order_id": 3, "customer_id": None},
        {"order_id": 4, "customer_id": 2},
        {"order_id": 5, "customer_id": None},
        {"order_id": 6, "customer_id": 3},
        {"order_id": 7, "customer_id": None},
    ]
    right = _customers(10)

    v = validate_join(left, right, "customer_id", "id")

    assert v.null_key_rate == pytest.approx(0.5)
    # all 4 non-null keys matched → match_rate over non-null is 1.0.
    assert v.match_rate == pytest.approx(1.0)
    assert v.rows_left == 8


def test_all_null_keys_is_unknown_nothing_to_validate():
    left = [{"customer_id": None} for _ in range(10)]
    right = _customers(5)

    v = validate_join(left, right, "customer_id", "id")

    assert v.null_key_rate == pytest.approx(1.0)
    assert v.verdict == RelationshipType.UNKNOWN
    assert v.ok is False


def test_empty_side_is_unknown():
    v = validate_join([], _customers(5), "customer_id", "id")
    assert v.verdict == RelationshipType.UNKNOWN
    assert v.ok is False


# --------------------------------------------------------------------- determinism


def test_deterministic_identical_inputs_identical_validation():
    left = _orders_fk()
    right = _customers(50)

    v1 = validate_join(left, right, "customer_id", "id")
    v2 = validate_join(left, right, "customer_id", "id")

    assert dataclasses.astuple(v1) == dataclasses.astuple(v2)


# --------------------------------------------------------------------- mixed types


def test_int_vs_string_keys_join_via_coercion():
    # left keys are ints, right keys are the same values as strings → still join.
    left = [{"k": i} for i in range(20)]
    right = [{"k": str(i)} for i in range(20)]

    v = validate_join(left, right, "k", "k")

    assert v.match_rate == pytest.approx(1.0)
    assert v.verdict in (RelationshipType.FK_JOIN, RelationshipType.LOOKUP_DIMENSION)


# --------------------------------------------------------------------- frame variant


def test_quoted_and_spaced_column_names_are_safe():
    # Column names with spaces / quotes must be escaped, not break the SQL.
    left = [{'cust "id"': i} for i in range(20)]
    right = [{'cust "id"': i} for i in range(20)]

    v = validate_join(left, right, 'cust "id"', 'cust "id"')

    assert v.match_rate == pytest.approx(1.0)


def test_frame_variant_accepts_arrow_table():
    import pyarrow as pa

    left = pa.table({"customer_id": list(range(50)) * 4})  # 200 rows
    right = pa.table({"id": list(range(50))})

    v = validate_join_frames(left, right, "customer_id", "id")

    assert v.match_rate == pytest.approx(1.0)
    assert v.verdict in (RelationshipType.FK_JOIN, RelationshipType.LOOKUP_DIMENSION)
    assert v.ok is True


# --------------------------------------------------------------------- batch


def _cand(lt: str, lc: str, rt: str, rc: str, src: str = "s") -> RelationshipCandidate:
    return RelationshipCandidate(
        left=ColumnRef(source_id=src, table=lt, column=lc),
        right=ColumnRef(source_id=src, table=rt, column=rc),
        rel_type=RelationshipType.UNKNOWN,
        confidence=0.5,
    )


def test_validate_candidates_batch_resolves_and_validates():
    table_data = {
        ("s", "orders"): _orders_fk(50, 200),
        ("s", "customers"): _customers(50),
        ("s", "other"): [{"k": f"X{i}"} for i in range(50)],
    }
    fk = _cand("orders", "customer_id", "customers", "id")
    disjoint = _cand("orders", "customer_id", "other", "k")

    results = validate_candidates([fk, disjoint], table_data)

    assert results[fk].verdict == RelationshipType.FK_JOIN
    assert results[fk].ok is True
    assert results[disjoint].verdict == RelationshipType.UNRELATED


def test_validate_candidates_missing_table_yields_unknown_not_raise():
    table_data = {("s", "orders"): _orders_fk(50, 200)}
    cand = _cand("orders", "customer_id", "customers", "id")

    results = validate_candidates([cand], table_data)

    assert results[cand].verdict == RelationshipType.UNKNOWN
    assert results[cand].ok is False
    assert "not found" in results[cand].detail


def test_validate_candidates_keying_by_table_name_and_dotted():
    by_name = {"orders": _orders_fk(50, 200), "customers": _customers(50)}
    by_dotted = {"s.orders": _orders_fk(50, 200), "s.customers": _customers(50)}
    cand = _cand("orders", "customer_id", "customers", "id")

    assert validate_candidates([cand], by_name)[cand].verdict == RelationshipType.FK_JOIN
    assert validate_candidates([cand], by_dotted)[cand].verdict == RelationshipType.FK_JOIN


# --------------------------------------------------------------------- sampling


def test_stratified_sampling_preserves_verdict_on_large_table():
    # 120k-row FK table forces sampling; the sample must still read as an FK.
    n_cust = 500
    left = [{"order_id": k, "customer_id": k % n_cust} for k in range(120_000)]
    right = _customers(n_cust)
    cand = _cand("big_orders", "customer_id", "cust", "id")
    table_data = {("s", "big_orders"): left, ("s", "cust"): right}

    cfg = BatchValidationConfig(sample_threshold=50_000, sample_size=10_000, strata=16)
    res = validate_candidates([cand], table_data, cfg)[cand]

    # Sample is bounded.
    assert res.rows_left <= 12_000
    # Shape survives the sampling: still a high-match, fan-out≈1 FK-ish join.
    assert res.match_rate == pytest.approx(1.0, abs=0.02)
    assert res.fanout_avg == pytest.approx(1.0, abs=0.05)
    assert res.verdict in (RelationshipType.FK_JOIN, RelationshipType.LOOKUP_DIMENSION)


def test_stratified_sampling_is_deterministic():
    n_cust = 500
    left = [{"order_id": k, "customer_id": k % n_cust} for k in range(120_000)]
    right = _customers(n_cust)
    cand = _cand("big_orders", "customer_id", "cust", "id")
    table_data = {("s", "big_orders"): left, ("s", "cust"): right}
    cfg = BatchValidationConfig(sample_threshold=50_000, sample_size=10_000, strata=16)

    r1 = validate_candidates([cand], table_data, cfg)[cand]
    r2 = validate_candidates([cand], table_data, cfg)[cand]

    assert dataclasses.astuple(r1) == dataclasses.astuple(r2)


def test_sampling_preserves_null_fraction():
    # half the keys NULL in a large table → sampled null_key_rate ≈ 0.5.
    left = [
        {"customer_id": (k % 100) if k % 2 == 0 else None}
        for k in range(120_000)
    ]
    right = _customers(100)
    cand = _cand("big", "customer_id", "cust", "id")
    table_data = {("s", "big"): left, ("s", "cust"): right}
    cfg = BatchValidationConfig(sample_threshold=50_000, sample_size=10_000, strata=16)

    res = validate_candidates([cand], table_data, cfg)[cand]
    assert res.null_key_rate == pytest.approx(0.5, abs=0.05)
