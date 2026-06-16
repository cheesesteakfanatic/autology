"""Per-tenant pattern learning (v2.1 §1.5; CLOSED CORE).

These gates pin the four load-bearing behaviours:
  * priors LEARN a tenant's key convention ('_id') and RAISE a matching candidate;
  * a historically-REJECTED shape LOWERS a similar candidate;
  * ISOLATION — two tenants with different ids never see each other's priors;
  * the BOUNDED NUDGE cannot flip a distribution-disagreeing candidate to a join;
  * DETERMINISM.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from ontoforge.contracts import (
    ColumnProfile,
    ColumnRef,
    Datatype,
    EvidenceArtifact,
    PathVote,
    ReasoningPath,
    RelationshipCandidate,
    RelationshipType,
    RelationshipVerdict,
    SignalKind,
    TableProfile,
)
from ontoforge.tenant import MAX_NUDGE, TenantPriors, shape_key
from ontoforge.tenant.priors import KIND_NAME_CONVENTION


# --------------------------------------------------------------------- fixtures


def _col(table: str, name: str, sem: str = "", sem_conf: float = 0.0) -> ColumnProfile:
    return ColumnProfile(
        source_id="s",
        table=table,
        column=name,
        inferred_type=Datatype.STRING,
        row_count=100,
        null_count=0,
        distinct_estimate=100,
        semantic_type=sem,
        semantic_confidence=sem_conf,
    )


def _table(table: str, *cols: ColumnProfile) -> TableProfile:
    return TableProfile(
        source_id="s",
        table=table,
        row_count=100,
        columns={c.column: c for c in cols},
    )


def _candidate(
    left_col: str,
    right_col: str,
    rel_type: RelationshipType = RelationshipType.FK_JOIN,
    confidence: float = 0.5,
    evidence: tuple[EvidenceArtifact, ...] = (),
    left_table: str = "orders",
    right_table: str = "customers",
) -> RelationshipCandidate:
    return RelationshipCandidate(
        left=ColumnRef("s", left_table, left_col),
        right=ColumnRef("s", right_table, right_col),
        rel_type=rel_type,
        confidence=confidence,
        evidence=evidence,
        rationale="base",
    )


def _verdict(
    left_col: str,
    right_col: str,
    rel_type: RelationshipType = RelationshipType.FK_JOIN,
    left_table: str = "orders",
    right_table: str = "customers",
) -> RelationshipVerdict:
    return RelationshipVerdict(
        left=ColumnRef("s", left_table, left_col),
        right=ColumnRef("s", right_table, right_col),
        rel_type=rel_type,
        confidence=0.8,
        consensus=True,
        votes=(PathVote(ReasoningPath.SCHEMA, rel_type, 0.8),),
    )


def _id_schema() -> list[TableProfile]:
    # a tenant that consistently names keys with the '_id' suffix and 'cust_' prefix
    return [
        _table("orders", _col("orders", "order_id"), _col("orders", "cust_id"), _col("orders", "qty")),
        _table("customers", _col("customers", "cust_id"), _col("customers", "cust_name")),
        _table("invoices", _col("invoices", "invoice_id"), _col("invoices", "cust_id")),
    ]


# --------------------------------------------------------------------- learning conventions


def test_learns_id_key_convention_and_raises_matching_candidate() -> None:
    tp = TenantPriors("acme")
    tp.observe_schema(_id_schema())

    # the '_id' suffix recurs across order_id/cust_id/invoice_id -> a learned convention
    keys = {p.key for p in tp.priors() if p.kind == KIND_NAME_CONVENTION}
    assert "_id" in keys

    cand = _candidate("cust_id", "cust_id")
    adjusted = tp.adjust_candidate(cand)

    assert adjusted.confidence > cand.confidence
    assert "convention" in adjusted.rationale
    assert "_id" in adjusted.rationale
    # nudge is bounded
    assert adjusted.confidence - cand.confidence <= MAX_NUDGE + 1e-9


def test_unrecognised_name_gets_no_convention_nudge() -> None:
    tp = TenantPriors("acme")
    tp.observe_schema(_id_schema())
    # neither side carries a learned tenant affix
    cand = _candidate("qty", "region", left_table="orders", right_table="geo")
    adjusted = tp.adjust_candidate(cand)
    assert adjusted.confidence == pytest.approx(cand.confidence)


# --------------------------------------------------------------------- join history


def test_rejected_history_lowers_similar_candidate() -> None:
    tp = TenantPriors("acme")
    # the tenant repeatedly REJECTS joining a status code to a customer id (a
    # 'looks similar isn't related' shape they keep declining)
    for _ in range(3):
        tp.observe_verdict(_verdict("status_code", "status_code", RelationshipType.FK_JOIN), accepted=False)

    cand = _candidate("status_code", "status_code", RelationshipType.FK_JOIN, confidence=0.5)
    adjusted = tp.adjust_candidate(cand)

    assert adjusted.confidence < cand.confidence
    assert "rejected" in adjusted.rationale


def test_accepted_history_raises_similar_candidate() -> None:
    tp = TenantPriors("acme")
    for _ in range(3):
        tp.observe_verdict(_verdict("cust_id", "cust_id", RelationshipType.FK_JOIN), accepted=True)

    # a DIFFERENT concrete pair that shares the learned shape
    cand = _candidate("cust_id", "cust_id", RelationshipType.FK_JOIN, confidence=0.5,
                      left_table="invoices", right_table="customers")
    adjusted = tp.adjust_candidate(cand)

    assert adjusted.confidence > cand.confidence
    assert "accepted" in adjusted.rationale


def test_single_verdict_below_min_observations_does_not_nudge() -> None:
    tp = TenantPriors("acme")
    tp.observe_verdict(_verdict("cust_id", "cust_id"), accepted=True)  # only ONE observation
    cand = _candidate("cust_id", "cust_id", confidence=0.5)
    adjusted = tp.adjust_candidate(cand)
    # one stray verdict must not move future inference
    assert adjusted.confidence == pytest.approx(cand.confidence)


def test_history_shape_is_order_insensitive() -> None:
    assert shape_key("cust_id", "order_id", RelationshipType.FK_JOIN) == shape_key(
        "order_id", "cust_id", RelationshipType.FK_JOIN
    )


# --------------------------------------------------------------------- ISOLATION


def test_two_tenants_never_see_each_others_priors() -> None:
    acme = TenantPriors("acme")
    globex = TenantPriors("globex")

    # acme learns an '_id' convention + an accepted join shape
    acme.observe_schema(_id_schema())
    for _ in range(3):
        acme.observe_verdict(_verdict("cust_id", "cust_id"), accepted=True)

    # globex learns a totally different convention ('dim_' prefix) + nothing accepted
    globex.observe_schema(
        [
            _table("sales", _col("sales", "dim_region"), _col("sales", "dim_product")),
            _table("ref", _col("ref", "dim_region"), _col("ref", "dim_product")),
        ]
    )

    acme_keys = {(p.kind, p.key) for p in acme.priors()}
    globex_keys = {(p.kind, p.key) for p in globex.priors()}

    # DISJOINT: neither tenant's priors leak into the other
    assert acme_keys.isdisjoint(globex_keys)
    assert all(p.tenant_id == "acme" for p in acme.priors())
    assert all(p.tenant_id == "globex" for p in globex.priors())

    # behaviourally: acme's accepted shape raises an acme candidate but NOT globex's
    cand = _candidate("cust_id", "cust_id", confidence=0.5)
    assert acme.adjust_candidate(cand).confidence > cand.confidence
    # globex never saw cust_id/_id history -> unchanged
    assert globex.adjust_candidate(cand).confidence == pytest.approx(cand.confidence)


def test_shared_sqlite_file_keeps_tenants_isolated(tmp_path) -> None:
    # even sharing ONE physical store file, the tenant_id namespaces every row
    store = tmp_path / "priors.sqlite"
    acme = TenantPriors("acme", store_path=store)
    globex = TenantPriors("globex", store_path=store)

    acme.observe_schema(_id_schema())
    globex.observe_schema(
        [_table("a", _col("a", "dim_x"), _col("a", "dim_y")),
         _table("b", _col("b", "dim_x"), _col("b", "dim_y"))]
    )
    acme.close()
    globex.close()

    # reopen each tenant: it loads ONLY its own rows
    acme2 = TenantPriors("acme", store_path=store)
    globex2 = TenantPriors("globex", store_path=store)
    acme_keys = {p.key for p in acme2.priors()}
    globex_keys = {p.key for p in globex2.priors()}

    assert "_id" in acme_keys
    assert "_id" not in globex_keys  # acme's convention never leaks into globex
    assert "dim_" in globex_keys
    assert "dim_" not in acme_keys
    acme2.close()
    globex2.close()


# --------------------------------------------------------------------- BOUNDED NUDGE / hard evidence


def _diverge_evidence() -> tuple[EvidenceArtifact, ...]:
    # a FIRED, CONFLICTING distribution-divergence signal: distributions disagree
    return (
        EvidenceArtifact(
            kind=SignalKind.DISTRIBUTION_DIVERGENCE,
            value=0.81,
            weight=1.0,
            fired=True,
            conflicts=True,
            detail="JSD=0.81 distributions diverge",
        ),
    )


def test_prior_cannot_flip_distribution_disagreeing_candidate() -> None:
    tp = TenantPriors("acme")
    tp.observe_schema(_id_schema())
    for _ in range(5):
        tp.observe_verdict(_verdict("cust_id", "cust_id"), accepted=True)

    # this candidate LOOKS like a learned-good shape (cust_id↔cust_id) BUT the
    # distributions provably disagree (fired+conflicting divergence signal)
    cand = _candidate(
        "cust_id", "cust_id",
        RelationshipType.FK_JOIN,
        confidence=0.30,  # below the 0.35 likely-join floor
        evidence=_diverge_evidence(),
    )
    adjusted = tp.adjust_candidate(cand)

    # the nudge is SUPPRESSED: distribution-disagreement wins, no lift at all
    assert adjusted.confidence == pytest.approx(cand.confidence)
    assert adjusted.confidence < 0.35  # still below the join floor
    assert "suppressed" in adjusted.rationale


def test_unrelated_typed_candidate_is_never_raised() -> None:
    tp = TenantPriors("acme")
    tp.observe_schema(_id_schema())
    for _ in range(5):
        tp.observe_verdict(_verdict("cust_id", "cust_id"), accepted=True)
    cand = _candidate("cust_id", "cust_id", RelationshipType.UNRELATED, confidence=0.4)
    adjusted = tp.adjust_candidate(cand)
    assert adjusted.confidence == pytest.approx(cand.confidence)


def test_nudge_is_bounded_even_with_all_priors_firing() -> None:
    tp = TenantPriors("acme")
    tp.observe_schema(_id_schema())
    for _ in range(20):
        tp.observe_verdict(_verdict("cust_id", "cust_id"), accepted=True)
    # both sides carry the learned '_id' convention AND the accepted shape AND
    # (after observing semtypes) a shared semtype — every prior wants to raise.
    tp.observe_schema(
        [
            _table("orders", _col("orders", "cust_id", sem="customer_key")),
            _table("customers", _col("customers", "cust_id", sem="customer_key")),
        ]
    )
    cand = _candidate("cust_id", "cust_id", confidence=0.5)
    adjusted = tp.adjust_candidate(cand)
    # total lift never exceeds MAX_NUDGE
    assert adjusted.confidence - cand.confidence <= MAX_NUDGE + 1e-9
    assert adjusted.confidence <= 1.0


def test_single_nudge_cannot_cross_join_floor_alone() -> None:
    # a candidate just below the 0.35 likely-join floor with a maxed-out prior
    # must NOT be lifted across the floor by the prior alone.
    from ontoforge.engineer.operators import JOIN_LIKELY_FLOOR

    tp = TenantPriors("acme")
    tp.observe_schema(_id_schema())
    for _ in range(30):
        tp.observe_verdict(_verdict("cust_id", "cust_id"), accepted=True)
    # confidence sits one MAX_NUDGE below the floor: prior can't reach it
    cand = _candidate("cust_id", "cust_id", confidence=JOIN_LIKELY_FLOOR - MAX_NUDGE - 0.01)
    adjusted = tp.adjust_candidate(cand)
    assert adjusted.confidence < JOIN_LIKELY_FLOOR


# --------------------------------------------------------------------- semtype map


def test_shared_semtype_raises_candidate() -> None:
    tp = TenantPriors("acme")
    # tenant consistently types both names as 'customer_key' across tables
    tp.observe_schema(
        [
            _table("orders", _col("orders", "buyer", sem="customer_key")),
            _table("returns", _col("returns", "buyer", sem="customer_key")),
            _table("customers", _col("customers", "acct", sem="customer_key")),
            _table("loyalty", _col("loyalty", "acct", sem="customer_key")),
        ]
    )
    cand = _candidate("buyer", "acct", confidence=0.5, left_table="orders", right_table="customers")
    adjusted = tp.adjust_candidate(cand)
    assert adjusted.confidence > cand.confidence
    assert "typed" in adjusted.rationale


# --------------------------------------------------------------------- determinism / purity


def test_adjust_does_not_mutate_input() -> None:
    tp = TenantPriors("acme")
    tp.observe_schema(_id_schema())
    cand = _candidate("cust_id", "cust_id", confidence=0.5)
    before = replace(cand)
    _ = tp.adjust_candidate(cand)
    assert cand == before  # frozen dataclass, untouched


def test_determinism_same_inputs_same_output() -> None:
    def build_and_adjust() -> RelationshipCandidate:
        tp = TenantPriors("acme")
        tp.observe_schema(_id_schema())
        for _ in range(3):
            tp.observe_verdict(_verdict("cust_id", "cust_id"), accepted=True)
            tp.observe_verdict(_verdict("status_code", "status_code"), accepted=False)
        return tp.adjust_candidate(_candidate("cust_id", "cust_id", confidence=0.5))

    a = build_and_adjust()
    b = build_and_adjust()
    assert a == b


def test_priors_listing_is_sorted_and_deterministic() -> None:
    tp = TenantPriors("acme")
    tp.observe_schema(_id_schema())
    p1 = tp.export_json()
    p2 = tp.export_json()
    assert p1 == p2
    # sorted by (kind, key)
    pr = tp.priors()
    assert pr == sorted(pr, key=lambda p: (p.kind, p.key))


def test_persisted_priors_round_trip_identically(tmp_path) -> None:
    store = tmp_path / "p.sqlite"
    tp = TenantPriors("acme", store_path=store)
    tp.observe_schema(_id_schema())
    for _ in range(3):
        tp.observe_verdict(_verdict("cust_id", "cust_id"), accepted=True)
    snapshot = tp.export_json()
    tp.close()

    tp2 = TenantPriors("acme", store_path=store)
    assert tp2.export_json() == snapshot
    tp2.close()


def test_empty_tenant_id_rejected() -> None:
    with pytest.raises(ValueError):
        TenantPriors("")
    with pytest.raises(ValueError):
        TenantPriors("   ")
