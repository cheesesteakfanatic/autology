"""Signal-level math + EvidenceArtifact behavior (v2.1 §1.1)."""

from __future__ import annotations

import math

from ontoforge.contracts import SignalKind
from ontoforge.relationships import (
    cardinality_ratio_signal,
    containment_signals,
    distribution_divergence_signal,
    entropy_signal,
    jaccard_signal,
    jensen_shannon,
    key_uniqueness_signal,
    name_similarity_signal,
    quantile_divergence,
    sampled_row_signal,
    shannon_entropy,
    type_compat_signal,
)
from ontoforge.relationships.signals import trigram_similarity

from .rel_helpers import make_col


# --------------------------------------------------------------- pure math


def test_shannon_entropy_uniform_is_log_n() -> None:
    assert math.isclose(shannon_entropy([0.25, 0.25, 0.25, 0.25]), math.log(4), rel_tol=1e-9)


def test_shannon_entropy_point_mass_is_zero() -> None:
    assert shannon_entropy([1.0, 0.0, 0.0]) == 0.0


def test_jensen_shannon_identical_is_zero() -> None:
    p = {"a": 0.5, "b": 0.5}
    assert jensen_shannon(p, dict(p)) == 0.0


def test_jensen_shannon_disjoint_is_one() -> None:
    assert jensen_shannon({"a": 1.0}, {"b": 1.0}) == 1.0


def test_jensen_shannon_same_support_different_freq_is_high() -> None:
    # SAME vocabulary, very different frequencies — the look-alike-but-unrelated core
    p = {"active": 0.9, "closed": 0.1}
    q = {"active": 0.1, "closed": 0.9}
    assert jensen_shannon(p, q) >= 0.5


def test_jensen_shannon_bounded_unit_interval() -> None:
    p = {"x": 0.7, "y": 0.2, "z": 0.1}
    q = {"y": 0.4, "z": 0.4, "w": 0.2}
    v = jensen_shannon(p, q)
    assert 0.0 <= v <= 1.0


def test_quantile_divergence_identical_zero() -> None:
    q = (0.0, 1.0, 2.0, 3.0, 4.0)
    assert quantile_divergence(q, q) == 0.0


def test_quantile_divergence_shifted_ranges_high() -> None:
    a = (0.0, 1.0, 2.0, 3.0, 4.0)
    b = (100.0, 101.0, 102.0, 103.0, 104.0)
    assert quantile_divergence(a, b) >= 0.5


def test_trigram_similarity_exact_is_one() -> None:
    assert trigram_similarity("customer_id", "customer_id") == 1.0
    assert 0.0 <= trigram_similarity("custkey", "custid") <= 1.0


# --------------------------------------------------- signal artifacts / flags


def test_containment_fk_direction_fires() -> None:
    parent = make_col("id", list(range(1, 201)), table="customers")
    child = make_col("customer_id", [(i % 200) + 1 for i in range(2000)], table="orders")
    lr, rl = containment_signals(child, parent)
    assert lr.kind is SignalKind.VALUE_CONTAINMENT
    assert lr.value >= 0.99  # child fully contained in parent key
    assert lr.fired


def test_containment_disjoint_does_not_fire() -> None:
    a = make_col("order_id", list(range(1, 501)), table="orders")
    b = make_col("product_id", list(range(5000, 5500)), table="products")
    lr, rl = containment_signals(a, b)
    assert lr.value == 0.0
    assert not lr.fired


def test_divergence_categorical_conflicts_on_frequency_disagreement() -> None:
    a = make_col("status", ["active"] * 90 + ["closed"] * 10, table="ta", with_counts=True)
    b = make_col("status", ["closed"] * 90 + ["active"] * 10, table="tb", with_counts=True)
    d = distribution_divergence_signal(a, b)
    assert d.kind is SignalKind.DISTRIBUTION_DIVERGENCE
    assert d.fired and d.conflicts  # high divergence FIRES as conflict


def test_divergence_aligned_distribution_does_not_conflict() -> None:
    vals = ["a"] * 50 + ["b"] * 30 + ["c"] * 20
    a = make_col("k", vals, table="ta", with_counts=True)
    b = make_col("k", list(vals), table="tb", with_counts=True)
    d = distribution_divergence_signal(a, b)
    assert not d.conflicts


def test_key_uniqueness_unique_parent_fires() -> None:
    parent = make_col("id", list(range(1, 201)), table="customers")
    child = make_col("customer_id", [(i % 200) + 1 for i in range(2000)], table="orders")
    k = key_uniqueness_signal(child, parent)
    assert k.value >= 0.99 and k.fired


def test_key_uniqueness_non_unique_parent_conflicts() -> None:
    child = make_col("customer_id", [(i % 200) + 1 for i in range(2000)], table="orders")
    not_key = make_col("status", [(i % 3) for i in range(2000)], table="orders2")
    k = key_uniqueness_signal(child, not_key)
    assert not k.fired and k.conflicts


def test_entropy_low_entropy_flag_conflicts() -> None:
    flag = make_col("status_id", [1] * 985 + [2] * 15, table="t", with_counts=True)
    other = make_col("status_id", [1] * 985 + [2] * 15, table="u", with_counts=True)
    e = entropy_signal(flag, other)
    assert e.fired and e.conflicts  # low-entropy column is a poor key


def test_name_similarity_is_weak_weight() -> None:
    a = make_col("customer_id", [1, 2, 3], table="orders")
    b = make_col("customer_id", [1, 2, 3], table="customers")
    n = name_similarity_signal(a, b)
    assert n.kind is SignalKind.NAME_SIMILARITY
    assert n.value == 1.0
    assert n.weight <= 0.05  # WEAK by design — never carries a verdict


def test_type_compat_incompatible_conflicts() -> None:
    s = make_col("name", ["alice", "bob"], table="t")
    d = make_col("created", ["2020-01-01", "2020-02-01"], table="u")
    art = type_compat_signal(s, d)
    # string vs date are incompatible groups
    assert art.value == 0.0 and art.conflicts


def test_cardinality_and_jaccard_and_sampled_row_kinds() -> None:
    a = make_col("k", list(range(50)), table="t")
    b = make_col("k", list(range(50)), table="u")
    assert cardinality_ratio_signal(a, b).kind is SignalKind.CARDINALITY_RATIO
    assert jaccard_signal(a, b).kind is SignalKind.VALUE_JACCARD
    sr = sampled_row_signal(a, b)
    assert sr.kind is SignalKind.SAMPLED_ROW
    assert sr.value >= 0.9  # identical sets


def test_artifact_values_are_rounded_and_bounded() -> None:
    a = make_col("k", list(range(100)), table="t")
    b = make_col("k", list(range(50, 150)), table="u")
    for art in (*containment_signals(a, b), jaccard_signal(a, b), sampled_row_signal(a, b)):
        assert 0.0 <= art.value <= 1.0
        # rounded to <= 6 dp (no long float tails)
        assert abs(art.value - round(art.value, 6)) < 1e-12
