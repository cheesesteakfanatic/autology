"""Signal-level math + EvidenceArtifact behavior (v2.1 §1.1)."""

from __future__ import annotations

import math

from ontoforge.contracts import SignalKind
from ontoforge.relationships import (
    cardinality_ratio_signal,
    containment_signals,
    distribution_divergence_signal,
    entropy_signal,
    infrequent_token_signal,
    jaccard_signal,
    jensen_shannon,
    key_uniqueness_signal,
    name_similarity_signal,
    quantile_divergence,
    sampled_row_signal,
    shannon_entropy,
    type_compat_signal,
)
from ontoforge.relationships.signals import (
    infrequent_token_sets,
    trigram_similarity,
    value_tokens,
)

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


# ----------------------------------------------- infrequent-token (St ↔ Street)


def test_value_tokens_splits_and_drops_singletons() -> None:
    assert value_tokens("123 Main St.") == frozenset({"123", "main", "st"})
    # length-1 tokens carry no discriminating power
    assert value_tokens("a b cd") == frozenset({"cd"})


def test_infrequent_token_sets_drop_boilerplate_keep_rare() -> None:
    left = frozenset({"1 Main St", "2 Oak St", "3 Elm St"})
    right = frozenset({"1 Main Street", "2 Oak Street", "3 Elm Street"})
    la, ra = infrequent_token_sets(left, right)
    # the discriminating street names survive on both sides …
    assert {"main", "oak", "elm"} <= la
    assert {"main", "oak", "elm"} <= ra
    # … and the rare-token sets agree on them while differing only on st/street.
    assert "st" in la and "st" not in ra
    assert "street" in ra and "street" not in la


def test_infrequent_token_signal_catches_st_street_format_variant() -> None:
    """The headline case: "St" vs "Street" addresses share ZERO whole values, so
    Jaccard/containment collapse — but the rare-token signal recovers the join."""
    addr_short = make_col(
        "address",
        ["1 Main St", "2 Oak St", "3 Elm St", "4 Pine St", "5 Cedar St"],
        table="customers",
    )
    addr_long = make_col(
        "addr",
        ["1 Main Street", "2 Oak Street", "3 Elm Street", "4 Pine Street", "5 Cedar Street"],
        table="shipments",
    )
    # verbatim overlap is gone …
    jac = jaccard_signal(addr_short, addr_long)
    assert jac.value < 0.2
    # … but the infrequent-token signal fires strongly on the shared rare tokens.
    sig = infrequent_token_signal(addr_short, addr_long)
    assert sig.kind is SignalKind.INFREQUENT_TOKEN
    assert sig.value >= 0.5
    assert sig.fired
    assert not sig.conflicts  # positive corroborator only — never a veto


def test_infrequent_token_signal_silent_on_disjoint_numeric_ids() -> None:
    """Disjoint numeric-id ranges share no tokens ⇒ the signal stays silent (0.0,
    not fired) rather than manufacturing a relationship out of two id spaces."""
    a = make_col("id", list(range(1000, 1100)), table="t")
    b = make_col("id", list(range(9000, 9100)), table="u")
    sig = infrequent_token_signal(a, b)
    assert sig.value == 0.0
    assert not sig.fired


def test_infrequent_token_signal_low_on_unrelated_text() -> None:
    a = make_col("city", ["London Town", "Paris City", "Berlin Hub"], table="t")
    b = make_col("note", ["red apple", "green pear", "blue plum"], table="u")
    sig = infrequent_token_signal(a, b)
    assert sig.value == 0.0
    assert not sig.fired


def test_infrequent_token_signal_deterministic() -> None:
    a = make_col("address", ["1 Main St", "2 Oak St", "3 Elm St"], table="t")
    b = make_col("addr", ["1 Main Street", "2 Oak Street", "3 Elm Street"], table="u")
    assert infrequent_token_signal(a, b) == infrequent_token_signal(a, b)
