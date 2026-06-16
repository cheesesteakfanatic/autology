"""Confidence-PROXY fusion behavior (v2.1 §1.1)."""

from __future__ import annotations

from ontoforge.contracts import RelationshipCandidate, RelationshipType
from ontoforge.relationships import (
    AMBIGUOUS_BAND,
    FK_PROXY_FLOOR,
    compute_signals,
    score_pair,
)
from ontoforge.relationships.score import fuse_confidence

from .rel_helpers import make_col


def test_fk_proxy_clears_floor() -> None:
    parent = make_col("id", list(range(1, 201)), table="customers")
    child = make_col("customer_id", [(i % 200) + 1 for i in range(2000)], table="orders")
    s = compute_signals(child, parent)
    assert fuse_confidence(s) >= FK_PROXY_FLOOR


def test_distribution_disagreement_is_strongly_negative() -> None:
    """A name match + partial overlap is dominated by distribution divergence."""
    a = make_col("status", ["active"] * 90 + ["x"] * 10, table="ta", with_counts=True)
    b = make_col("status", ["closed"] * 90 + ["x"] * 10, table="tb", with_counts=True)
    s = compute_signals(a, b)
    assert fuse_confidence(s) < 0.45


def test_name_only_match_scores_near_zero() -> None:
    """Pure name agreement with no value overlap must not manufacture confidence."""
    a = make_col("ref", [f"AA{i:04d}" for i in range(300)], table="ta")
    b = make_col("ref", [f"ZZ{i:04d}" for i in range(300)], table="tb")
    s = compute_signals(a, b)
    assert fuse_confidence(s) < 0.2


def test_proxy_is_bounded_unit_interval() -> None:
    for left, right in [
        (make_col("a", list(range(100)), table="t"), make_col("b", list(range(50, 150)), table="u")),
        (make_col("k", ["x"] * 100, table="t"), make_col("k", ["y"] * 100, table="u")),
    ]:
        c = fuse_confidence(compute_signals(left, right))
        assert 0.0 <= c <= 1.0


def test_score_pair_emits_candidate_with_evidence_and_rationale() -> None:
    parent = make_col("id", list(range(1, 201)), table="customers")
    child = make_col("customer_id", [(i % 200) + 1 for i in range(2000)], table="orders")
    s = compute_signals(child, parent)
    cand = score_pair(child, parent, rel_type=RelationshipType.FK_JOIN, rationale="fk", signals=s)
    assert isinstance(cand, RelationshipCandidate)
    assert cand.rel_type is RelationshipType.FK_JOIN
    assert cand.rationale == "fk"
    assert len(cand.evidence) == 10        # full reasoning trail rides along
    assert cand.left.table == "orders" and cand.right.table == "customers"


def test_ambiguous_band_constant_is_sane() -> None:
    lo, hi = AMBIGUOUS_BAND
    assert 0.0 < lo < hi < 1.0


def test_high_divergence_low_containment_is_vetoed() -> None:
    """The explicit trip-wire: strong divergence + no real containment floors the proxy."""
    a = make_col("score", [float(i) for i in range(100)], table="ta")
    b = make_col("score", [float(i) + 10_000 for i in range(100)], table="tb")
    s = compute_signals(a, b)
    assert fuse_confidence(s) <= 0.2
