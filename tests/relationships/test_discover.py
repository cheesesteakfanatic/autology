"""Ranked relationship discovery over table profiles (v2.1 §1.1, §1.2)."""

from __future__ import annotations

from ontoforge.contracts import IND, RelationshipType
from ontoforge.relationships import discover_relationships

from .rel_helpers import make_profile


def _three_table_estate():
    """customers (PK id) ← orders (FK customer_id) ; products is a red-herring."""
    customers = make_profile("src", "customers", {
        "id": list(range(1, 201)),
        "name": [f"cust-{i}" for i in range(1, 201)],
    })
    orders = make_profile("src", "orders", {
        "order_id": list(range(1, 2001)),
        "customer_id": [(i % 200) + 1 for i in range(2000)],
        "amount": [round(10 + (i % 90) + 0.49, 2) for i in range(2000)],
    })
    products = make_profile("src", "products", {
        # product ids share the 'id'-ish space numerically but are a DIFFERENT range
        "product_id": [9000 + (i % 300) for i in range(1500)],
        "title": [f"prod-{i}" for i in range(1500)],
    })
    return [customers, orders, products]


def test_discover_finds_fk_ranked_first() -> None:
    profiles = _three_table_estate()
    cands = discover_relationships(profiles, min_confidence=0.5)
    assert cands, "expected at least the orders→customers FK"
    top = cands[0]
    assert top.rel_type is RelationshipType.FK_JOIN
    assert {top.left.column, top.right.column} == {"customer_id", "id"}
    assert top.confidence >= 0.7
    # ranked by descending confidence
    confs = [c.confidence for c in cands]
    assert confs == sorted(confs, reverse=True)


def test_discover_does_not_assert_false_positive_join() -> None:
    """orders.order_id and products.product_id are disjoint → never a typed join."""
    profiles = _three_table_estate()
    cands = discover_relationships(profiles, min_confidence=0.5)
    for c in cands:
        if {c.left.column, c.right.column} == {"order_id", "product_id"}:
            assert c.rel_type is RelationshipType.UNRELATED


def test_discover_ind_seeding_restricts_pairs() -> None:
    """When INDs are supplied, only those pairs are considered."""
    profiles = _three_table_estate()
    inds = [
        IND(lhs_table="orders", lhs_column="customer_id",
            rhs_table="customers", rhs_column="id", coverage=1.0, score=0.9),
    ]
    cands = discover_relationships(profiles, inds=inds, min_confidence=0.0, keep_unrelated=True)
    pairs = {(c.left.column, c.right.column) for c in cands}
    assert pairs == {("customer_id", "id")}
    assert cands[0].rel_type is RelationshipType.FK_JOIN


def test_discover_keeps_unrelated_when_requested() -> None:
    profiles = _three_table_estate()
    kept = discover_relationships(profiles, min_confidence=0.5, keep_unrelated=True)
    dropped = discover_relationships(profiles, min_confidence=0.5, keep_unrelated=False)
    assert any(c.rel_type is RelationshipType.UNRELATED for c in kept)
    assert all(c.rel_type is not RelationshipType.UNRELATED for c in dropped)


def test_discover_returns_one_entry_per_ordered_pair() -> None:
    profiles = _three_table_estate()
    cands = discover_relationships(profiles, min_confidence=0.0, keep_unrelated=True)
    keys = [(c.left.table, c.left.column, c.right.table, c.right.column) for c in cands]
    assert len(keys) == len(set(keys))  # no duplicate ordered pairs


def test_discover_empty_estate_is_empty() -> None:
    assert discover_relationships([]) == []
