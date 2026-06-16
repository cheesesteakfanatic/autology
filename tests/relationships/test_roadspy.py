"""RoadSpy scout payload — evidence, NEVER bulk data (v2.1 §1.2)."""

from __future__ import annotations

from ontoforge.contracts import RelationshipType, ScoutPayload
from ontoforge.relationships import (
    build_scout,
    classify_relationship,
    compute_signals,
    score_pair,
)
from ontoforge.relationships.roadspy import SCOUT_SAMPLE_CAP

from .rel_helpers import make_col


def _scout(left, right, candidate_types=()):
    s = compute_signals(left, right)
    res = classify_relationship(left, right, s)
    cand = score_pair(left, right, rel_type=res.rel_type, rationale=res.rationale, signals=s)
    payload = build_scout(cand, left, right, s, candidate_types=candidate_types)
    return cand, s, payload


def test_scout_carries_fired_and_conflicted_signals() -> None:
    """The payload separates the signals that FIRED from those that CONFLICTED."""
    # an FK pair: positives fire, no conflicts
    parent = make_col("id", list(range(1, 201)), table="customers")
    child = make_col("customer_id", [(i % 200) + 1 for i in range(2000)], table="orders")
    cand, s, payload = _scout(child, parent)
    assert isinstance(payload, ScoutPayload)
    assert payload.signals_fired  # at least the containment/key signals fired
    assert all(a.fired for a in payload.signals_fired)
    assert all(a.conflicts for a in payload.signals_conflicted)


def test_scout_records_divergence_conflict_for_false_positive() -> None:
    """For a look-alike-but-unrelated pair the conflicting signals must be present."""
    a = make_col("status", ["active"] * 900 + ["closed"] * 100, table="ta", with_counts=True)
    b = make_col("status", ["closed"] * 900 + ["active"] * 100, table="tb", with_counts=True)
    cand, s, payload = _scout(a, b)
    conflicted_kinds = {a.kind.value for a in payload.signals_conflicted}
    assert "distribution_divergence" in conflicted_kinds


def test_scout_samples_are_capped_and_no_bulk_data() -> None:
    """Even with large sampled columns the payload stays bounded (no bulk leak)."""
    big_left = make_col("k", [f"L{i:05d}" for i in range(400)], table="t")
    big_right = make_col("k", [f"R{i:05d}" for i in range(400)], table="u")
    cand, s, payload = _scout(big_left, big_right)
    assert len(payload.left_samples) <= SCOUT_SAMPLE_CAP
    assert len(payload.right_samples) <= SCOUT_SAMPLE_CAP
    assert len(payload.shared_samples) <= SCOUT_SAMPLE_CAP
    # no individual sample is an oversized blob
    assert all(len(v) <= 64 for v in payload.left_samples + payload.right_samples)


def test_scout_shared_samples_reflect_overlap() -> None:
    """Shared samples are the actual intersected values — the adjudicator's evidence."""
    parent = make_col("id", list(range(1, 51)), table="customers")
    child = make_col("customer_id", [(i % 50) + 1 for i in range(200)], table="orders")
    cand, s, payload = _scout(child, parent)
    assert payload.shared_samples  # FK pair shares values
    # shared values are present in both sides' sample universes
    for v in payload.shared_samples:
        assert v in set(payload.left_samples) | {str(x) for x in range(1, 51)}


def test_scout_hypothesis_and_candidate_types() -> None:
    parent = make_col("id", list(range(1, 201)), table="customers")
    child = make_col("customer_id", [(i % 200) + 1 for i in range(2000)], table="orders")
    cand, s, payload = _scout(
        child, parent, candidate_types=(RelationshipType.FK_JOIN, RelationshipType.LOOKUP_DIMENSION)
    )
    assert "orders.customer_id" in payload.hypothesis
    assert "customers.id" in payload.hypothesis
    assert RelationshipType.FK_JOIN in payload.candidate_types
    assert RelationshipType.LOOKUP_DIMENSION in payload.candidate_types


def test_scout_default_candidate_type_is_candidate_own_type() -> None:
    a = make_col("order_id", list(range(1, 501)), table="orders")
    b = make_col("product_id", list(range(5000, 5500)), table="products")
    cand, s, payload = _scout(a, b)
    assert payload.candidate_types == (cand.rel_type,)
