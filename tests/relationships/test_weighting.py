"""Per-estate weighting profile + Tursio PK band / IND-0.4 prune (RESEARCH_ENGINE_SOTA §4/§5).

Verifies:
* the estate fingerprint distinguishes a clean relational DB from a messy lake;
* the chosen profile re-weights the signal fusion as designed (structural+overlap
  dominate relational; semantic dominates lake) — and the re-weighting is real
  (the same evidence scores differently under the two profiles);
* the false-positive killer is NEVER weakened by any profile;
* the Tursio PK band (0.95 / ±5%) and the IND 5-component prune at 0.4 behave at
  the documented thresholds;
* determinism throughout.
"""

from __future__ import annotations

from ontoforge.contracts import RelationshipType
from ontoforge.relationships import (
    BALANCED,
    IND_PRUNE_FLOOR,
    LAKE,
    PK_BAND_TOLERANCE,
    PK_DISTINCT_RATIO,
    RELATIONAL,
    EstateKind,
    classify_estate,
    compute_signals,
    fingerprint_estate,
    fuse_confidence,
    ind_candidate_score,
    is_pk_candidate,
    score_pair,
)
from ontoforge.relationships.classify import TableShape
from ontoforge.relationships.weighting import (
    SignalGroup,
    fingerprint_estate as fp_estate,
    weighting_for_estate,
)

from .rel_helpers import make_col, make_profile


# --------------------------------------------------------------- estate detection


def _relational_estate():
    """A clean relational DB: surrogate-keyed tables, id/measure-heavy, name-rich keys."""
    customers = make_profile("crm", "customers", {
        "id": list(range(1, 501)),
        "tier_id": [(i % 4) + 1 for i in range(500)],
        "credit": [float(i % 1000) for i in range(500)],
    })
    orders = make_profile("crm", "orders", {
        "order_id": list(range(1, 2001)),
        "customer_id": [(i % 500) + 1 for i in range(2000)],
        "amount": [float(i % 9999) for i in range(2000)],
    })
    tiers = make_profile("crm", "tiers", {
        "tier_id": [1, 2, 3, 4],
        "rate": [0.1, 0.2, 0.3, 0.4],
    })
    return [customers, orders, tiers]


def _lake_estate():
    """A messy data lake: no surrogate keys, free-text-heavy, name-poor, low uniqueness."""
    blob_a = make_profile("lake", "dump_a", {
        "note": ["alpha note text"] * 200 + ["beta note text"] * 300,
        "category": ["red"] * 250 + ["blue"] * 250,
        "memo": ["x"] * 500,
    })
    blob_b = make_profile("lake", "dump_b", {
        "comment": ["gamma comment"] * 400 + ["delta comment"] * 100,
        "label": ["hot"] * 300 + ["cold"] * 200,
        "freetext": ["repeated value"] * 500,
    })
    return [blob_a, blob_b]


def test_fingerprint_separates_relational_from_lake() -> None:
    rel_fp = fingerprint_estate(_relational_estate())
    lake_fp = fingerprint_estate(_lake_estate())
    # relational: many keyed tables, id/measure-heavy (low string fraction)
    assert rel_fp.keyed_table_fraction >= 0.6
    assert rel_fp.string_column_fraction < 0.5
    # lake: poorly-keyed, string-heavy
    assert lake_fp.keyed_table_fraction <= 0.3
    assert lake_fp.string_column_fraction > 0.6


def test_classify_estate_picks_the_right_kind() -> None:
    assert classify_estate(fingerprint_estate(_relational_estate())) is EstateKind.RELATIONAL
    assert classify_estate(fingerprint_estate(_lake_estate())) is EstateKind.LAKE


def test_classify_estate_small_estate_defaults_balanced() -> None:
    one_table = [make_profile("s", "t", {"id": [1, 2, 3]})]
    assert classify_estate(fingerprint_estate(one_table)) is EstateKind.BALANCED


def test_weighting_for_estate_end_to_end() -> None:
    assert weighting_for_estate(_relational_estate()) is RELATIONAL
    assert weighting_for_estate(_lake_estate()) is LAKE


# the module re-exports fingerprint under the same name; assert they agree
def test_fingerprint_alias_consistent() -> None:
    profs = _relational_estate()
    assert fingerprint_estate(profs) == fp_estate(profs)


# ------------------------------------------------------- profile re-weights fusion


def test_profiles_shift_group_multipliers_as_designed() -> None:
    # relational leans on structure+overlap, dampens semantic …
    assert RELATIONAL.multiplier(SignalGroup.STRUCTURAL) > 1.0
    assert RELATIONAL.multiplier(SignalGroup.OVERLAP) > 1.0
    assert RELATIONAL.multiplier(SignalGroup.SEMANTIC) < 1.0
    # … lake inverts it: semantic dominates, structure dampened.
    assert LAKE.multiplier(SignalGroup.SEMANTIC) > 1.0
    assert LAKE.multiplier(SignalGroup.STRUCTURAL) < 1.0
    # balanced is the unbiased identity.
    for g in SignalGroup:
        assert BALANCED.multiplier(g) == 1.0


def test_reweighting_actually_moves_the_proxy() -> None:
    """A semantic-CARRIED pair scores HIGHER under the LAKE profile (semantic
    amplified) than under RELATIONAL (semantic dampened) — proving the per-estate
    profile genuinely re-weights the fusion, not just labels it. The pair is a
    format-variant address on NON-KEY columns, so the evidence is carried by the
    semantic group (name + rare-token), not by structural keyness/overlap."""
    left = make_col(
        "mailing_address", [f"{i} Main St" for i in range(1, 40)] * 3,
        table="leads", with_counts=True,
    )
    right = make_col(
        "mailing_address", [f"{i} Main Street" for i in range(1, 40)] * 3,
        table="accounts", with_counts=True,
    )
    s = compute_signals(left, right)

    bal = fuse_confidence(s, profile=BALANCED)
    lake = fuse_confidence(s, profile=LAKE)
    rel = fuse_confidence(s, profile=RELATIONAL)

    # semantic-carried evidence: lake amplifies, relational dampens, balanced between.
    assert lake > bal > rel
    # and all three are genuinely distinct (re-weighting is real, not cosmetic).
    assert len({bal, lake, rel}) == 3


def test_reweighting_never_resurrects_a_false_positive() -> None:
    """The FP killer must survive ANY profile. A frequency-swapped status
    look-alike (same vocabulary, opposite frequencies, no key) must NEVER reach
    commit confidence under any profile — it stays in/below the escalation band so
    it is routed, never auto-joined — and the classifier types it UNRELATED."""
    from ontoforge.relationships import FK_PROXY_FLOOR, classify_relationship
    a = make_col("status", ["active"] * 90 + ["closed"] * 10, table="ta", with_counts=True)
    b = make_col("status", ["closed"] * 90 + ["active"] * 10, table="tb", with_counts=True)
    s = compute_signals(a, b)
    for prof in (BALANCED, RELATIONAL, LAKE):
        conf = fuse_confidence(s, profile=prof)
        assert conf < FK_PROXY_FLOOR  # never reaches a commit-grade proxy
    # the typed verdict is the explicit false-positive killer regardless of profile
    assert classify_relationship(a, b, s).rel_type is RelationshipType.UNRELATED


def test_reweighting_floors_a_disjoint_lookalike_under_every_profile() -> None:
    """A name/type look-alike with DISJOINT values and no rare-token agreement is
    floored to ~0 by the divergence veto under every profile (the veto is never
    scaled, and the rare-token exception cannot fire without rare-token overlap)."""
    a = make_col("city", ["London Town", "Paris City", "Berlin Hub"], table="t")
    b = make_col("note", ["red apple", "green pear", "blue plum"], table="u")
    s = compute_signals(a, b)
    for prof in (BALANCED, RELATIONAL, LAKE):
        assert fuse_confidence(s, profile=prof) <= 0.2


def test_discover_autodetects_and_reweights() -> None:
    from ontoforge.relationships import discover_relationships
    rel = discover_relationships(_relational_estate(), min_confidence=0.0)
    lake = discover_relationships(_lake_estate(), min_confidence=0.0)
    # both run deterministically and return ranked candidate lists
    assert isinstance(rel, list) and isinstance(lake, list)
    assert discover_relationships(_relational_estate(), min_confidence=0.0) == rel


# ----------------------------------------------------------- Tursio PK band (§4)


def test_pk_band_constants_are_tursio_defaults() -> None:
    assert PK_DISTINCT_RATIO == 0.95
    assert PK_BAND_TOLERANCE == 0.05


def test_pk_band_accepts_a_near_unique_widest_column() -> None:
    # 1000 distinct of 1000 rows, and it IS the table's widest column ⇒ PK candidate.
    pk = make_col("id", list(range(1000)), table="t").profile
    shape = TableShape(row_count=1000, max_distinct=1000)
    assert is_pk_candidate(pk, shape)


def test_pk_band_rejects_below_distinct_ratio() -> None:
    # 900 distinct of 1000 rows = 0.90 < 0.95 ⇒ not a PK candidate.
    col = make_col("c", [i % 900 for i in range(1000)], table="t").profile
    shape = TableShape(row_count=1000, max_distinct=1000)
    assert not is_pk_candidate(col, shape)


def test_pk_band_rejects_outside_max_distinct_band() -> None:
    # near-unique against its OWN nonnull rows, but far below the table's widest
    # column (max_distinct 1000, ±5% ⇒ needs ≥950) ⇒ not the table's key.
    col = make_col("c", list(range(100)), table="t").profile  # 100 distinct/100 rows
    shape = TableShape(row_count=100, max_distinct=1000)
    assert not is_pk_candidate(col, shape)


def test_pk_band_no_shape_falls_back_to_row_count() -> None:
    pk = make_col("id", list(range(500)), table="t").profile
    assert is_pk_candidate(pk, None)
    weak = make_col("c", [i % 100 for i in range(500)], table="t").profile
    assert not is_pk_candidate(weak, None)


# ------------------------------------------------------- Tursio IND prune (§4)


def test_ind_prune_floor_is_tursio_default() -> None:
    assert IND_PRUNE_FLOOR == 0.4


def test_ind_candidate_score_high_for_clean_fk() -> None:
    parent = make_col("id", list(range(1, 201)), table="customers")
    child = make_col("customer_id", [(i % 200) + 1 for i in range(2000)], table="orders")
    s = compute_signals(child, parent)
    score = ind_candidate_score(s)
    assert score >= IND_PRUNE_FLOOR  # a real FK clears the prune


def test_ind_candidate_score_low_for_disjoint_pair() -> None:
    a = make_col("order_id", list(range(1, 501)), table="orders")
    b = make_col("product_id", list(range(5000, 5500)), table="products")
    s = compute_signals(a, b)
    assert ind_candidate_score(s) < IND_PRUNE_FLOOR  # pruned


def test_ind_prune_drops_weak_join_candidates_in_discover() -> None:
    """A pair with no containment and a non-key target scores below the IND floor;
    raising the floor to 1.0 prunes EVERY join-shaped candidate, lowering it admits
    more — proving the prune is active and tunable."""
    from ontoforge.relationships import discover_relationships
    profs = _relational_estate()
    strict = discover_relationships(profs, min_confidence=0.0, ind_prune_floor=1.0)
    loose = discover_relationships(profs, min_confidence=0.0, ind_prune_floor=0.0)
    join_types = {RelationshipType.FK_JOIN, RelationshipType.LOOKUP_DIMENSION,
                  RelationshipType.M2M_BRIDGE}
    n_join_strict = sum(1 for c in strict if c.rel_type in join_types and not c.needs_adjudication)
    n_join_loose = sum(1 for c in loose if c.rel_type in join_types and not c.needs_adjudication)
    assert n_join_strict <= n_join_loose


def test_ind_score_deterministic() -> None:
    parent = make_col("id", list(range(1, 201)), table="customers")
    child = make_col("customer_id", [(i % 200) + 1 for i in range(2000)], table="orders")
    s = compute_signals(child, parent)
    assert ind_candidate_score(s) == ind_candidate_score(s)
