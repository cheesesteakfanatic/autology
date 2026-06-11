"""IND discovery & join-candidate scoring tests (§3.1): declared FK recovery,
coverage gating, type compatibility, direction scoring, determinism."""

from __future__ import annotations

from m3_helpers import ACTIVE_CUST, N_CUST, make_tpch
from ontoforge.profiling import discover_inds, name_token_jaccard


def _find(inds, lt, lc, rt, rc):
    hits = [i for i in inds
            if (i.lhs_table, i.lhs_column, i.rhs_table, i.rhs_column) == (lt, lc, rt, rc)]
    return hits[0] if hits else None


def test_declared_fks_recovered_with_full_coverage():
    inds = discover_inds(make_tpch())
    fk1 = _find(inds, "orders", "o_custkey", "customers", "c_custkey")
    fk2 = _find(inds, "lineitems", "l_orderkey", "orders", "o_orderkey")
    assert fk1 is not None and fk1.coverage == 1.0
    assert fk2 is not None and fk2.coverage == 1.0


def test_reverse_inclusion_below_coverage_floor_absent():
    # only ACTIVE_CUST of N_CUST customers place orders -> reverse coverage ~0.83 < 0.95
    assert ACTIVE_CUST / N_CUST < 0.95  # fixture sanity
    inds = discover_inds(make_tpch())
    assert _find(inds, "customers", "c_custkey", "orders", "o_custkey") is None


def test_fk_outscores_reverse_direction():
    # every order has lines, so o_orderkey ⊆ l_orderkey also holds at coverage 1.0 —
    # but the FK direction must score higher (rhs of a join should be key-like)
    inds = discover_inds(make_tpch())
    fwd = _find(inds, "lineitems", "l_orderkey", "orders", "o_orderkey")
    rev = _find(inds, "orders", "o_orderkey", "lineitems", "l_orderkey")
    assert fwd is not None and rev is not None
    assert fwd.score > rev.score


def test_name_evidence_lifts_true_fk_over_accidental_ind():
    # l_linenumber values (1..4) ⊆ c_custkey (1..120): a true-but-accidental inclusion;
    # the shared `custkey`/`orderkey` token must rank declared FKs above it
    inds = discover_inds(make_tpch())
    fk = _find(inds, "orders", "o_custkey", "customers", "c_custkey")
    acc = _find(inds, "lineitems", "l_linenumber", "customers", "c_custkey")
    assert fk is not None and acc is not None
    assert acc.coverage == 1.0
    assert fk.score > acc.score


def test_coverage_threshold_boundary():
    corpus = {
        "a": {"fk": list(range(20))},                   # 0..19
        "b": {"pk": list(range(19))},                   # 0..18 -> coverage 19/20 = 0.95
        "c": {"pk": list(range(18))},                   # coverage 18/20 = 0.90 < floor
    }
    inds = discover_inds(corpus)
    assert _find(inds, "a", "fk", "b", "pk") is not None
    assert _find(inds, "a", "fk", "c", "pk") is None


def test_integer_fk_found_inside_float_pk():
    # the BIGINT-FK vs DOUBLE-PK cross-engine wart: integral floats collapse to int keys
    corpus = {
        "dim": {"id": [1.0, 2.0, 3.0, 4.0]},
        "fact": {"dim_id": [1, 2, 3, 1, 2]},
    }
    ind = _find(discover_inds(corpus), "fact", "dim_id", "dim", "id")
    assert ind is not None and ind.coverage == 1.0


def test_incompatible_types_never_pair():
    corpus = {
        "t1": {"code": ["A1", "B2", "C3"]},
        "t2": {"num": [1, 2, 3], "when": ["2024-01-01", "2024-01-02", "2024-01-03"]},
    }
    inds = discover_inds(corpus)
    pairs = {(i.lhs_table, i.lhs_column, i.rhs_table, i.rhs_column) for i in inds}
    assert ("t2", "num", "t2", "when") not in pairs
    assert ("t1", "code", "t2", "num") not in pairs


def test_boolean_and_constant_columns_excluded():
    corpus = {
        "t1": {"flag": [True, False, True, False], "konst": ["x", "x", "x", "x"]},
        "t2": {"flag2": [True, False, True, True], "vals": ["x", "y", "x", "z"]},
    }
    inds = discover_inds(corpus)
    cols = {(i.lhs_table, i.lhs_column) for i in inds} | {(i.rhs_table, i.rhs_column) for i in inds}
    assert ("t1", "flag") not in cols and ("t2", "flag2") not in cols
    assert ("t1", "konst") not in cols


def test_name_token_jaccard():
    assert name_token_jaccard("o_custkey", "c_custkey") == 1 / 3
    assert name_token_jaccard("customer_id", "customers_id") == 1.0   # plural stemming
    assert name_token_jaccard("altitudeFt", "altitude_ft") == 1.0     # camel == snake
    assert name_token_jaccard("l_linenumber", "c_custkey") == 0.0


def test_discover_inds_deterministic_and_sorted():
    a = discover_inds(make_tpch())
    b = discover_inds(make_tpch())
    assert a == b
    assert all(x.score >= y.score for x, y in zip(a, a[1:]))
