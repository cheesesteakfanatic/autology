"""Contract acceptance for typed relationship inference (v2.1 §1.1–§1.5, M-REL).

Pure-data contracts: construct each type, verify enum membership, immutability
(frozen + slots), round-trippability (asdict → re-construct), and that the module
pulls in NO heavy deps (deterministic, zero-network — importable on its own).
"""

from __future__ import annotations

import dataclasses

import pytest

import ontoforge.contracts as c
from ontoforge.contracts.relationships import (
    ColumnRef,
    EvidenceArtifact,
    JoinValidation,
    PathVote,
    ReasoningPath,
    RelationshipCandidate,
    RelationshipType,
    RelationshipVerdict,
    ScoutPayload,
    SignalKind,
    TenantPrior,
)


# ------------------------------------------------------------------- fixtures


def _left() -> ColumnRef:
    return ColumnRef(source_id="crm", table="orders", column="customer_id")


def _right() -> ColumnRef:
    return ColumnRef(source_id="crm", table="customers", column="id")


def _evidence() -> EvidenceArtifact:
    return EvidenceArtifact(
        kind=SignalKind.VALUE_CONTAINMENT,
        value=0.98,
        weight=0.4,
        fired=True,
        conflicts=False,
        detail="values(orders.customer_id) ⊆ values(customers.id)",
    )


def _validation() -> JoinValidation:
    return JoinValidation(
        match_rate=0.98,
        orphan_rate=0.02,
        fanout_avg=1.0,
        fanout_max=1.0,
        null_key_rate=0.0,
        rows_left=1000,
        rows_right=200,
        verdict=RelationshipType.FK_JOIN,
        ok=True,
        detail="clean fk join",
    )


# ------------------------------------------------------------------- enums


def test_relationship_type_membership():
    names = {m.name for m in RelationshipType}
    assert names == {
        "FK_JOIN",
        "LOOKUP_DIMENSION",
        "M2M_BRIDGE",
        "DENORMALIZATION",
        "DERIVED_FIELD",
        "UNRELATED",
        "UNKNOWN",
    }
    # str-enum: the value is a usable string and the member IS a str
    assert RelationshipType.FK_JOIN == "fk_join"
    assert isinstance(RelationshipType.UNRELATED, str)
    assert RelationshipType("unrelated") is RelationshipType.UNRELATED


def test_signal_kind_membership():
    names = {m.name for m in SignalKind}
    assert names == {
        "VALUE_CONTAINMENT",
        "VALUE_JACCARD",
        "INFREQUENT_TOKEN",
        "DISTRIBUTION_DIVERGENCE",
        "CARDINALITY_RATIO",
        "KEY_UNIQUENESS",
        "ENTROPY",
        "NAME_SIMILARITY",
        "TYPE_COMPAT",
        "SAMPLED_ROW",
        "FANOUT",
    }
    assert SignalKind.DISTRIBUTION_DIVERGENCE == "distribution_divergence"
    assert isinstance(SignalKind.ENTROPY, str)


def test_reasoning_path_membership():
    assert {m.name for m in ReasoningPath} == {"SCHEMA", "VALUE", "BUSINESS_LOGIC"}
    assert ReasoningPath.BUSINESS_LOGIC == "business_logic"
    assert isinstance(ReasoningPath.SCHEMA, str)


# ------------------------------------------------------------------- construction


def test_construct_column_ref():
    ref = _left()
    assert (ref.source_id, ref.table, ref.column) == ("crm", "orders", "customer_id")


def test_construct_evidence_artifact():
    e = _evidence()
    assert e.kind is SignalKind.VALUE_CONTAINMENT
    assert e.fired is True and e.conflicts is False
    assert e.value == pytest.approx(0.98)


def test_construct_relationship_candidate():
    cand = RelationshipCandidate(
        left=_left(),
        right=_right(),
        rel_type=RelationshipType.FK_JOIN,
        confidence=0.87,
        evidence=(_evidence(),),
        rationale="containment + key uniqueness",
        needs_adjudication=False,
    )
    assert cand.rel_type is RelationshipType.FK_JOIN
    assert cand.evidence[0].kind is SignalKind.VALUE_CONTAINMENT
    # defaults are present and sane
    bare = RelationshipCandidate(
        left=_left(), right=_right(), rel_type=RelationshipType.UNKNOWN, confidence=0.0
    )
    assert bare.evidence == () and bare.rationale == "" and bare.needs_adjudication is False


def test_construct_scout_payload_carries_evidence_not_bulk():
    payload = ScoutPayload(
        left=_left(),
        right=_right(),
        hypothesis="orders.customer_id is a FK into customers.id",
        signals_fired=(_evidence(),),
        signals_conflicted=(),
        left_samples=("C-001", "C-002"),
        right_samples=("C-001", "C-002", "C-003"),
        shared_samples=("C-001", "C-002"),
        candidate_types=(RelationshipType.FK_JOIN, RelationshipType.LOOKUP_DIMENSION),
    )
    assert payload.candidate_types[0] is RelationshipType.FK_JOIN
    assert all(isinstance(s, str) for s in payload.left_samples)
    # defaults: everything but the addresses + hypothesis is optional
    bare = ScoutPayload(left=_left(), right=_right(), hypothesis="?")
    assert bare.signals_fired == () and bare.shared_samples == () and bare.candidate_types == ()


def test_construct_join_validation():
    v = _validation()
    assert v.verdict is RelationshipType.FK_JOIN
    assert v.ok is True
    assert v.match_rate == pytest.approx(0.98)
    bare = JoinValidation(
        match_rate=0.1,
        orphan_rate=0.9,
        fanout_avg=0.0,
        fanout_max=0.0,
        null_key_rate=0.0,
        rows_left=10,
        rows_right=10,
        verdict=RelationshipType.UNRELATED,
    )
    assert bare.ok is False and bare.detail == ""


def test_construct_path_vote_and_verdict():
    votes = (
        PathVote(path=ReasoningPath.SCHEMA, rel_type=RelationshipType.FK_JOIN, confidence=0.9),
        PathVote(path=ReasoningPath.VALUE, rel_type=RelationshipType.FK_JOIN, confidence=0.85),
        PathVote(
            path=ReasoningPath.BUSINESS_LOGIC,
            rel_type=RelationshipType.FK_JOIN,
            confidence=0.8,
            rationale="customer_id naming convention",
        ),
    )
    verdict = RelationshipVerdict(
        left=_left(),
        right=_right(),
        rel_type=RelationshipType.FK_JOIN,
        confidence=0.85,
        consensus=True,
        votes=votes,
        validation=_validation(),
        committed=True,
        routed_to_human=False,
        prov_ref="prov:abc123",
    )
    assert verdict.consensus is True and verdict.committed is True
    assert len(verdict.votes) == 3
    assert verdict.validation is not None and verdict.validation.ok is True
    # default verdict: no validation, not committed, not routed
    bare = RelationshipVerdict(
        left=_left(),
        right=_right(),
        rel_type=RelationshipType.UNKNOWN,
        confidence=0.0,
        consensus=False,
    )
    assert bare.validation is None and bare.committed is False and bare.routed_to_human is False
    assert bare.prov_ref == "" and bare.votes == ()


def test_construct_tenant_prior():
    prior = TenantPrior(
        tenant_id="acme",
        kind="name_convention",
        key="suffix:_id",
        value="fk_likely",
        weight=0.7,
        observations=12,
    )
    assert prior.tenant_id == "acme" and prior.observations == 12
    bare = TenantPrior(tenant_id="acme", kind="join_history", key="orders->customers", value="accepted")
    assert bare.weight == 0.0 and bare.observations == 0


# ------------------------------------------------------------------- immutability (frozen + slots)


@pytest.mark.parametrize(
    "obj, attr, newval",
    [
        (_left(), "column", "x"),
        (_evidence(), "value", 0.0),
        (_validation(), "match_rate", 0.0),
        (TenantPrior(tenant_id="t", kind="k", key="k", value="v"), "weight", 1.0),
    ],
)
def test_frozen_blocks_mutation(obj, attr, newval):
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(obj, attr, newval)


@pytest.mark.parametrize(
    "obj",
    [
        _left(),
        _evidence(),
        _validation(),
        PathVote(path=ReasoningPath.SCHEMA, rel_type=RelationshipType.FK_JOIN, confidence=0.5),
        TenantPrior(tenant_id="t", kind="k", key="k", value="v"),
    ],
)
def test_slots_block_new_attributes(obj):
    # slots=True => no __dict__, cannot add arbitrary attributes
    assert not hasattr(obj, "__dict__")
    with pytest.raises((AttributeError, dataclasses.FrozenInstanceError)):
        object.__setattr__(obj, "surprise_attr", 1)


def test_frozen_types_are_hashable():
    # frozen + slots with hashable fields => usable as dict keys / in sets
    assert len({_left(), _left()}) == 1
    assert hash(_evidence()) == hash(_evidence())
    assert hash(_validation()) == hash(_validation())


# ------------------------------------------------------------------- round-trip


@pytest.mark.parametrize(
    "obj, cls",
    [
        (_left(), ColumnRef),
        (_evidence(), EvidenceArtifact),
        (_validation(), JoinValidation),
        (
            RelationshipCandidate(
                left=_left(),
                right=_right(),
                rel_type=RelationshipType.FK_JOIN,
                confidence=0.8,
                evidence=(_evidence(),),
                rationale="r",
                needs_adjudication=True,
            ),
            RelationshipCandidate,
        ),
        (
            ScoutPayload(
                left=_left(),
                right=_right(),
                hypothesis="h",
                signals_fired=(_evidence(),),
                candidate_types=(RelationshipType.FK_JOIN,),
            ),
            ScoutPayload,
        ),
        (PathVote(path=ReasoningPath.VALUE, rel_type=RelationshipType.M2M_BRIDGE, confidence=0.6), PathVote),
        (
            RelationshipVerdict(
                left=_left(),
                right=_right(),
                rel_type=RelationshipType.FK_JOIN,
                confidence=0.85,
                consensus=True,
                votes=(PathVote(path=ReasoningPath.SCHEMA, rel_type=RelationshipType.FK_JOIN, confidence=0.9),),
                validation=_validation(),
                committed=True,
                prov_ref="p",
            ),
            RelationshipVerdict,
        ),
        (TenantPrior(tenant_id="t", kind="name_convention", key="k", value="v", weight=0.5, observations=3), TenantPrior),
    ],
)
def test_round_trippable(obj, cls):
    # field-level reconstruction (shallow) reproduces an equal object
    rebuilt = cls(**{f.name: getattr(obj, f.name) for f in dataclasses.fields(obj)})
    assert rebuilt == obj
    # asdict produces a plain JSON-ish dict of primitives/containers (enums are str)
    d = dataclasses.asdict(obj)
    assert isinstance(d, dict)


# ------------------------------------------------------------------- re-exports


def test_all_types_reexported_from_package():
    for name in (
        "RelationshipType",
        "SignalKind",
        "EvidenceArtifact",
        "ColumnRef",
        "RelationshipCandidate",
        "ScoutPayload",
        "JoinValidation",
        "ReasoningPath",
        "PathVote",
        "RelationshipVerdict",
        "TenantPrior",
    ):
        assert hasattr(c, name), f"{name} not re-exported from ontoforge.contracts"
        assert name in c.__all__
    # the package symbol IS the module symbol (same object)
    assert c.RelationshipType is RelationshipType
    assert c.ScoutPayload is ScoutPayload
    assert c.RelationshipVerdict is RelationshipVerdict


# ------------------------------------------------------------------- no heavy deps / zero network


def test_module_imports_no_heavy_or_network_deps():
    import ontoforge.contracts.relationships as rel

    src = rel.__file__
    assert src and src.endswith("relationships.py")
    # the contract module's own imports must be stdlib-only (pure data, deterministic)
    import ast

    with open(src, "r", encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    imported_roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported_roots.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:  # absolute import
                imported_roots.add(node.module.split(".")[0])
    forbidden = {
        "duckdb",
        "pandas",
        "numpy",
        "pyarrow",
        "xxhash",
        "requests",
        "httpx",
        "urllib",
        "socket",
        "aiohttp",
        "openai",
        "anthropic",
        "ontoforge",
    }
    assert not (imported_roots & forbidden), f"heavy/network/intra-pkg import leaked: {imported_roots & forbidden}"
    # only stdlib roots remain
    assert imported_roots <= {"dataclasses", "enum", "typing", "__future__"}
