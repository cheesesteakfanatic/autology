"""Typed classifier + the FALSE-POSITIVE KILLER (v2.1 §1.2).

These are the load-bearing correctness tests: a real FK is typed FK_JOIN with high
proxy confidence; a bridge table is M2M_BRIDGE; and — the headline — two columns
that LOOK similar (name + cardinality) but whose value distributions DISAGREE are
typed UNRELATED, not a join.
"""

from __future__ import annotations

from ontoforge.contracts import RelationshipType
from ontoforge.relationships import classify_relationship, compute_signals, score_pair
from ontoforge.relationships.classify import TableShape

from .rel_helpers import make_col


def _classify(left, right, **kw):
    sigs = compute_signals(left, right)
    res = classify_relationship(left, right, sigs, **kw)
    cand = score_pair(left, right, rel_type=res.rel_type, rationale=res.rationale, signals=sigs)
    return res, cand, sigs


# =====================================================================
# THE FALSE-POSITIVE KILLER
# =====================================================================


def test_false_positive_disjoint_values_is_unrelated() -> None:
    """Similar names + similar cardinality, DISJOINT value ranges → UNRELATED.

    order_id (1..500) and product_id (5000..5499): both integer ids, both ~500
    distinct, names share the 'id' token — the exact shape that fools a
    name/cardinality heuristic. The distributions are disjoint, so the engine must
    REFUSE the join.
    """
    order_id = make_col("order_id", [(i % 500) + 1 for i in range(2000)], table="orders")
    product_id = make_col("product_id", [5000 + (i % 500) for i in range(2000)], table="products")
    res, cand, sigs = _classify(order_id, product_id)
    assert res.rel_type is RelationshipType.UNRELATED
    assert cand.confidence < 0.3
    # the divergence signal must have fired/conflicted — that is WHY it's unrelated
    assert any(a.conflicts for a in sigs.conflicted)
    assert "diverge" in res.rationale or "overlap" in res.rationale


def test_false_positive_same_vocab_divergent_frequency_is_unrelated() -> None:
    """SAME value vocabulary but DIVERGENT frequency distribution → UNRELATED.

    Two 'status' columns drawing from {active, closed, pending} but with opposite
    dominant values. Containment is high (same vocabulary!) yet they are unrelated
    because the DISTRIBUTIONS disagree and neither side is a key. This is the case
    a pure value-overlap heuristic gets wrong.
    """
    status_a = ["active"] * 900 + ["closed"] * 50 + ["pending"] * 50
    status_b = ["closed"] * 900 + ["active"] * 50 + ["pending"] * 50
    a = make_col("status", status_a, table="ta", with_counts=True)
    b = make_col("status", status_b, table="tb", with_counts=True)
    res, cand, sigs = _classify(a, b)
    assert res.rel_type is RelationshipType.UNRELATED
    # distribution-divergence signal is the discriminator and must conflict
    assert sigs.divergence.conflicts
    assert sigs.divergence.value >= 0.5


def test_false_positive_name_match_only_is_not_a_join() -> None:
    """Identical column NAME but no value overlap → never a join (name is WEAK)."""
    a = make_col("code", [f"AAA{i:04d}" for i in range(300)], table="ta")
    b = make_col("code", [f"BBB{i:04d}" for i in range(300)], table="tb")
    res, cand, sigs = _classify(a, b)
    assert res.rel_type is RelationshipType.UNRELATED
    assert sigs.name_similarity.value == 1.0  # names identical...
    assert cand.confidence < 0.3              # ...but proxy stays low


# =====================================================================
# GENUINE TYPED RELATIONSHIPS
# =====================================================================


def test_genuine_fk_is_fk_join_high_confidence() -> None:
    """Child values ⊆ unique parent key, many:1, aligned distributions → FK_JOIN."""
    parent = make_col("id", list(range(1, 201)), table="customers")
    child = make_col("customer_id", [(i % 200) + 1 for i in range(2000)], table="orders")
    res, cand, sigs = _classify(child, parent)
    assert res.rel_type is RelationshipType.FK_JOIN
    assert cand.confidence >= 0.7  # high proxy confidence
    assert sigs.containment_lr.fired and sigs.key_uniqueness.fired
    assert not cand.needs_adjudication


def test_lookup_dimension_small_unique_reference() -> None:
    """Child refs a SMALL unique reference table with descriptive attrs → LOOKUP_DIMENSION."""
    # 8 country codes, child fact references them many:1
    country = make_col("code", ["US", "GB", "FR", "DE", "JP", "CN", "BR", "IN"], table="country")
    fact = make_col(
        "country_code",
        [["US", "GB", "FR", "DE", "JP", "CN", "BR", "IN"][i % 8] for i in range(800)],
        table="sales",
    )
    right_shape = TableShape(total_columns=3, fk_like_columns=0, descriptive_columns=2, is_small=True)
    res, cand, sigs = _classify(fact, country, right_table=right_shape)
    assert res.rel_type is RelationshipType.LOOKUP_DIMENSION
    assert cand.confidence >= 0.6


def test_m2m_bridge_table() -> None:
    """A column in a two-FK junction table referencing a unique key → M2M_BRIDGE."""
    students = make_col("id", list(range(1, 101)), table="students")
    enroll_student = make_col(
        "student_id", [(i % 100) + 1 for i in range(500)], table="enrollment"
    )
    bridge = TableShape(total_columns=2, fk_like_columns=2, descriptive_columns=0, is_small=False)
    res, cand, sigs = _classify(enroll_student, students, left_table=bridge)
    assert res.rel_type is RelationshipType.M2M_BRIDGE
    assert cand.confidence >= 0.7


def test_denormalization_repeated_nonkey_attribute() -> None:
    """Non-key attr copied across tables with MATCHING distribution → DENORMALIZATION."""
    city = ["NYC"] * 500 + ["LA"] * 300 + ["SF"] * 200
    a = make_col("city", city, table="orders", with_counts=True)
    b = make_col("city", list(city), table="shipments", with_counts=True)
    res, cand, sigs = _classify(a, b)
    assert res.rel_type is RelationshipType.DENORMALIZATION
    assert sigs.divergence.value <= 0.2  # distributions match → it's a copy


def test_derived_field_computed_transform() -> None:
    """Left is a simple COMPUTED transform of right (uppercasing) → DERIVED_FIELD.

    A derived field is a deterministic function of another column. Here
    `code_upper` is `code` uppercased — the engine detects the element-wise
    transform and types it DERIVED_FIELD (not FK, not denormalization), since
    neither side is a key.
    """
    base = [f"sku-{i:03d}" for i in range(120)]
    right = make_col("code", [base[i % 120] for i in range(600)], table="catalog")
    left = make_col(
        "code_upper", [base[i % 120].upper() for i in range(600)], table="report"
    )
    res, cand, sigs = _classify(left, right)
    assert res.rel_type is RelationshipType.DERIVED_FIELD
    assert "transform" in res.rationale


# =====================================================================
# ADJUDICATION ROUTING
# =====================================================================


def test_ambiguous_band_sets_needs_adjudication() -> None:
    """Partial containment into a key (proxy lands in the ambiguous band) → escalate."""
    parent = make_col("code", [f"C{i:04d}" for i in range(300)], table="ref")
    child = make_col(
        "code",
        [f"C{(i % 180):04d}" for i in range(400)] + [f"N{i:04d}" for i in range(220)],
        table="fact",
    )
    res, cand, sigs = _classify(child, parent)
    assert cand.needs_adjudication  # mixed/partial → routed for adjudication


def test_conflicting_signals_set_needs_adjudication() -> None:
    """Real overlap + a fired CONFLICT (low-entropy key) → needs_adjudication."""
    flag = make_col("status_id", [1] * 985 + [2] * 15, table="fact", with_counts=True)
    status = make_col("id", [1, 2, 3, 4], table="status")
    res, cand, sigs = _classify(flag, status)
    assert len(sigs.conflicted) >= 1
    assert cand.needs_adjudication


def test_incompatible_types_is_unknown_not_typed() -> None:
    """Cross-type pair cannot be typed as a relationship → UNKNOWN (not UNRELATED join)."""
    s = make_col("label", ["alpha", "beta", "gamma"], table="t")
    d = make_col("created", ["2021-01-01", "2021-06-01", "2021-12-01"], table="u")
    res, cand, sigs = _classify(s, d)
    assert res.rel_type in (RelationshipType.UNKNOWN, RelationshipType.UNRELATED)
    assert sigs.type_compat.conflicts
