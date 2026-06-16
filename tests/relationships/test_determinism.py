"""Determinism: a fixed input yields byte-identical candidates and evidence (§18.4)."""

from __future__ import annotations

from dataclasses import astuple

from ontoforge.relationships import (
    build_scout,
    classify_relationship,
    compute_signals,
    discover_relationships,
    score_pair,
)

from .rel_helpers import make_col, make_profile


def _estate():
    customers = make_profile("src", "customers", {
        "id": list(range(1, 151)),
        "city": (["NYC"] * 60 + ["LA"] * 50 + ["SF"] * 40),
    })
    orders = make_profile("src", "orders", {
        "order_id": list(range(1, 1201)),
        "customer_id": [(i % 150) + 1 for i in range(1200)],
        "city": ([["NYC", "LA", "SF"][i % 3] for i in range(1200)]),
    })
    return [customers, orders]


def test_signals_are_deterministic() -> None:
    a = make_col("customer_id", [(i % 150) + 1 for i in range(1200)], table="orders")
    b = make_col("id", list(range(1, 151)), table="customers")
    s1 = compute_signals(a, b)
    s2 = compute_signals(a, b)
    assert s1.artifacts == s2.artifacts  # frozen dataclasses compare by value


def test_candidate_is_deterministic() -> None:
    a = make_col("customer_id", [(i % 150) + 1 for i in range(1200)], table="orders")
    b = make_col("id", list(range(1, 151)), table="customers")
    s = compute_signals(a, b)
    res = classify_relationship(a, b, s)
    c1 = score_pair(a, b, rel_type=res.rel_type, rationale=res.rationale, signals=s)
    c2 = score_pair(a, b, rel_type=res.rel_type, rationale=res.rationale, signals=s)
    assert c1 == c2


def test_scout_payload_is_deterministic() -> None:
    a = make_col("customer_id", [(i % 150) + 1 for i in range(1200)], table="orders")
    b = make_col("id", list(range(1, 151)), table="customers")
    s = compute_signals(a, b)
    res = classify_relationship(a, b, s)
    c = score_pair(a, b, rel_type=res.rel_type, rationale=res.rationale, signals=s)
    p1 = build_scout(c, a, b, s)
    p2 = build_scout(c, a, b, s)
    assert astuple(p1) == astuple(p2)


def test_discover_is_deterministic_across_runs() -> None:
    r1 = discover_relationships(_estate(), min_confidence=0.0, keep_unrelated=True)
    r2 = discover_relationships(_estate(), min_confidence=0.0, keep_unrelated=True)
    assert len(r1) == len(r2)
    for a, b in zip(r1, r2):
        assert a == b


def test_discover_is_order_independent_of_table_listing() -> None:
    """Profiling/discovery must not depend on the order tables are passed in."""
    estate = _estate()
    forward = discover_relationships(estate, min_confidence=0.0, keep_unrelated=True)
    reverse = discover_relationships(list(reversed(estate)), min_confidence=0.0, keep_unrelated=True)
    # same set of typed candidates (ranking key is the address, deterministic)
    def key(c):
        return (c.left.table, c.left.column, c.right.table, c.right.column, c.rel_type.value)

    assert sorted(map(key, forward)) == sorted(map(key, reverse))
