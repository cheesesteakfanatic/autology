"""Reasoning-path typed-relationship voting (v2.1 §1.3/§1.4, CLOSED-CORE).

Exercises the second gate layered on top of the fire/hold Gate:

* unanimous paths → consensus commit;
* split paths → routed_to_human (no commit);
* SQL backward-validation that CONTRADICTS → not committed even if paths lean fire;
* a sound validation that AGREES → boosts confidence;
* confidence = MEDIAN of the path scores;
* should_vote fires only on the ambiguous band (a confident FK skips voting);
* determinism (same candidate ⇒ same verdict).

The existing fire/hold ensemble tests (test_gate.py / test_experts.py) are NOT
touched and must stay green — this is an additive gate.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ontoforge.contracts import (
    ColumnRef,
    EvidenceArtifact,
    JoinValidation,
    PathVote,
    ReasoningPath,
    RelationshipCandidate,
    RelationshipType,
    SignalKind,
)
from ontoforge.ensemble.paths import (
    BusinessLogicPath,
    SchemaPath,
    ValuePath,
    default_paths,
)
from ontoforge.ensemble.relgate import (
    AMBIGUOUS_BAND,
    CONSENSUS_THRESHOLD,
    TOP_CONFIDENCE_RATIO,
    TOP_K_COMMIT,
    RelationshipGate,
    should_vote,
)

RT = RelationshipType
SK = SignalKind


# --------------------------------------------------------------------------
# fixtures / helpers
# --------------------------------------------------------------------------


def _ev(kind: SK, value: float, *, weight: float = 1.0, fired: bool = True,
        conflicts: bool = False, detail: str = "") -> EvidenceArtifact:
    return EvidenceArtifact(kind=kind, value=value, weight=weight, fired=fired,
                            conflicts=conflicts, detail=detail)


def _cand(
    *,
    rel_type: RT = RT.FK_JOIN,
    confidence: float = 0.7,
    evidence: tuple[EvidenceArtifact, ...] = (),
    needs_adjudication: bool = False,
    left_table: str = "shipments",
    left_col: str = "parcel_id",
    right_table: str = "parcels",
    right_col: str = "parcel_id",
) -> RelationshipCandidate:
    return RelationshipCandidate(
        left=ColumnRef("src", left_table, left_col),
        right=ColumnRef("src", right_table, right_col),
        rel_type=rel_type,
        confidence=confidence,
        evidence=evidence,
        needs_adjudication=needs_adjudication,
    )


def _clean_fk_evidence() -> tuple[EvidenceArtifact, ...]:
    """A textbook FK: unique parent key, high containment, aligned distributions,
    fan-in cardinality. All three paths should converge on FK_JOIN."""
    return (
        _ev(SK.KEY_UNIQUENESS, 0.99),
        _ev(SK.CARDINALITY_RATIO, 8.0),
        _ev(SK.VALUE_CONTAINMENT, 0.97),
        _ev(SK.DISTRIBUTION_DIVERGENCE, 0.05),
        _ev(SK.ENTROPY, 0.8),
        _ev(SK.TYPE_COMPAT, 1.0),
        _ev(SK.NAME_SIMILARITY, 0.9),
    )


# fixed-vote path stub for exact, inspectable plurality behaviour
@dataclass(frozen=True, slots=True)
class _FixedPath:
    path: ReasoningPath
    rel_type: RT
    confidence: float = 0.8

    def vote(self, cand: RelationshipCandidate,
             validation: Optional[JoinValidation] = None) -> PathVote:
        return PathVote(self.path, self.rel_type, self.confidence, "fixed")


# --------------------------------------------------------------------------
# unanimous → consensus commit
# --------------------------------------------------------------------------


def test_unanimous_paths_commit() -> None:
    gate = RelationshipGate()
    cand = _cand(evidence=_clean_fk_evidence())
    v = gate.decide(cand)
    # all three real paths agree on FK_JOIN
    assert v.rel_type == RT.FK_JOIN
    assert {pv.rel_type for pv in v.votes} == {RT.FK_JOIN}
    assert v.consensus is True
    assert v.committed is True
    assert v.routed_to_human is False
    assert v.confidence >= CONSENSUS_THRESHOLD


def test_business_path_diverges_on_fact_to_dimension_naming() -> None:
    """Distinct reasoning, not noise: on an orders→customers pairing the schema
    and value paths read FK_JOIN from structure/data, while the business-logic
    path reads the fact→dimension naming role as LOOKUP_DIMENSION. The plurality
    (2 FK vs 1 lookup) still commits FK_JOIN — but the disagreement is REAL and
    recorded, proving the paths reason differently rather than echoing."""
    gate = RelationshipGate()
    cand = _cand(left_table="orders", left_col="customer_id",
                 right_table="customers", right_col="customer_id",
                 evidence=_clean_fk_evidence())
    v = gate.decide(cand)
    by_path = {pv.path: pv.rel_type for pv in v.votes}
    assert by_path[ReasoningPath.SCHEMA] == RT.FK_JOIN
    assert by_path[ReasoningPath.VALUE] == RT.FK_JOIN
    assert by_path[ReasoningPath.BUSINESS_LOGIC] == RT.LOOKUP_DIMENSION
    assert v.rel_type == RT.FK_JOIN          # 2-of-3 plurality
    assert v.committed is True


def test_unanimous_fixed_paths_commit() -> None:
    paths = [
        _FixedPath(ReasoningPath.SCHEMA, RT.LOOKUP_DIMENSION, 0.7),
        _FixedPath(ReasoningPath.VALUE, RT.LOOKUP_DIMENSION, 0.8),
        _FixedPath(ReasoningPath.BUSINESS_LOGIC, RT.LOOKUP_DIMENSION, 0.75),
    ]
    gate = RelationshipGate(paths)
    v = gate.decide(_cand())
    assert v.rel_type == RT.LOOKUP_DIMENSION
    assert v.committed is True
    # median of {0.7, 0.8, 0.75} = 0.75
    assert abs(v.confidence - 0.75) < 1e-9


# --------------------------------------------------------------------------
# split → routed_to_human
# --------------------------------------------------------------------------


def test_split_paths_route_to_human() -> None:
    """Three paths, three different types ⇒ no plurality ⇒ route, never commit."""
    paths = [
        _FixedPath(ReasoningPath.SCHEMA, RT.FK_JOIN, 0.9),
        _FixedPath(ReasoningPath.VALUE, RT.M2M_BRIDGE, 0.9),
        _FixedPath(ReasoningPath.BUSINESS_LOGIC, RT.DENORMALIZATION, 0.9),
    ]
    gate = RelationshipGate(paths)
    v = gate.decide(_cand())
    assert v.consensus is False
    assert v.committed is False
    assert v.routed_to_human is True


def test_tie_routes_to_human() -> None:
    """A 1-1 tie (with a third abstaining UNKNOWN) is not a strict plurality."""
    paths = [
        _FixedPath(ReasoningPath.SCHEMA, RT.FK_JOIN, 0.9),
        _FixedPath(ReasoningPath.VALUE, RT.DENORMALIZATION, 0.9),
        _FixedPath(ReasoningPath.BUSINESS_LOGIC, RT.UNKNOWN, 0.9),
    ]
    gate = RelationshipGate(paths)
    v = gate.decide(_cand())
    assert v.routed_to_human is True
    assert v.committed is False


def test_strict_plurality_two_of_three_commits() -> None:
    """A clean 2-of-3 plurality with high median ⇒ commit on the majority type."""
    paths = [
        _FixedPath(ReasoningPath.SCHEMA, RT.FK_JOIN, 0.8),
        _FixedPath(ReasoningPath.VALUE, RT.FK_JOIN, 0.8),
        _FixedPath(ReasoningPath.BUSINESS_LOGIC, RT.UNRELATED, 0.7),
    ]
    gate = RelationshipGate(paths)
    v = gate.decide(_cand())
    assert v.rel_type == RT.FK_JOIN
    assert v.committed is True


# --------------------------------------------------------------------------
# SQL backward-validation: booster / veto (§1.4)
# --------------------------------------------------------------------------


def _good_validation(verdict: RT = RT.FK_JOIN) -> JoinValidation:
    return JoinValidation(
        match_rate=0.98, orphan_rate=0.02, fanout_avg=1.0, fanout_max=3.0,
        null_key_rate=0.0, rows_left=10_000, rows_right=1_200, verdict=verdict, ok=True,
    )


def _bad_validation(verdict: RT = RT.UNRELATED) -> JoinValidation:
    return JoinValidation(
        match_rate=0.04, orphan_rate=0.96, fanout_avg=0.0, fanout_max=0.0,
        null_key_rate=0.3, rows_left=10_000, rows_right=1_200, verdict=verdict, ok=False,
    )


def test_validation_contradiction_blocks_commit_even_if_paths_lean_fire() -> None:
    """Paths unanimously lean FK_JOIN, but the join executed against real data
    fails (4% match, not ok). The data refuses the join ⇒ NOT committed, routed."""
    paths = [
        _FixedPath(ReasoningPath.SCHEMA, RT.FK_JOIN, 0.95),
        _FixedPath(ReasoningPath.VALUE, RT.FK_JOIN, 0.95),
        _FixedPath(ReasoningPath.BUSINESS_LOGIC, RT.FK_JOIN, 0.95),
    ]
    gate = RelationshipGate(paths)
    v = gate.decide(_cand(), validation=_bad_validation())
    assert v.rel_type == RT.FK_JOIN          # the paths' leaning is recorded…
    assert v.committed is False              # …but the validation veto wins
    assert v.routed_to_human is True


def test_validation_with_different_sound_verdict_blocks_commit() -> None:
    """The executed join types it differently AND that typing is sound ⇒ veto."""
    paths = [
        _FixedPath(ReasoningPath.SCHEMA, RT.FK_JOIN, 0.95),
        _FixedPath(ReasoningPath.VALUE, RT.FK_JOIN, 0.95),
        _FixedPath(ReasoningPath.BUSINESS_LOGIC, RT.FK_JOIN, 0.95),
    ]
    gate = RelationshipGate(paths)
    # validation executed cleanly but says DENORMALIZATION, not FK_JOIN
    contra = JoinValidation(
        match_rate=0.99, orphan_rate=0.0, fanout_avg=1.0, fanout_max=1.0,
        null_key_rate=0.0, rows_left=1_000, rows_right=1_000,
        verdict=RT.DENORMALIZATION, ok=True,
    )
    v = gate.decide(_cand(), validation=contra)
    assert v.committed is False
    assert v.routed_to_human is True


def test_sound_agreeing_validation_boosts_confidence() -> None:
    paths = [
        _FixedPath(ReasoningPath.SCHEMA, RT.FK_JOIN, 0.6),
        _FixedPath(ReasoningPath.VALUE, RT.FK_JOIN, 0.6),
        _FixedPath(ReasoningPath.BUSINESS_LOGIC, RT.FK_JOIN, 0.6),
    ]
    gate = RelationshipGate(paths)
    without = gate.decide(_cand())
    withval = gate.decide(_cand(), validation=_good_validation())
    assert withval.confidence > without.confidence
    assert withval.committed is True


# --------------------------------------------------------------------------
# median-of-path confidence
# --------------------------------------------------------------------------


def test_confidence_is_median_not_mean() -> None:
    """One wildly over-confident path must NOT drag the verdict up — the median
    is robust where a mean would not be."""
    paths = [
        _FixedPath(ReasoningPath.SCHEMA, RT.FK_JOIN, 0.50),
        _FixedPath(ReasoningPath.VALUE, RT.FK_JOIN, 0.55),
        _FixedPath(ReasoningPath.BUSINESS_LOGIC, RT.FK_JOIN, 0.99),  # over-confident outlier
    ]
    gate = RelationshipGate(paths)
    v = gate.decide(_cand())
    # median {0.50,0.55,0.99} = 0.55 (mean would be ~0.68)
    assert abs(v.confidence - 0.55) < 1e-9
    # and that median sits below the commit threshold ⇒ routed, not committed
    assert v.committed is False
    assert v.routed_to_human is True


# --------------------------------------------------------------------------
# should_vote — the scalpel
# --------------------------------------------------------------------------


def test_should_vote_skips_a_confident_decisive_fk() -> None:
    lo, hi = AMBIGUOUS_BAND
    confident = _cand(rel_type=RT.FK_JOIN, confidence=hi + 0.1,
                      evidence=_clean_fk_evidence())
    assert should_vote(confident) is False


def test_should_vote_fires_in_the_ambiguous_band() -> None:
    lo, hi = AMBIGUOUS_BAND
    mid = (lo + hi) / 2
    borderline = _cand(rel_type=RT.FK_JOIN, confidence=mid,
                       evidence=(_ev(SK.VALUE_CONTAINMENT, 0.6),))
    assert should_vote(borderline) is True


def test_should_vote_skips_confidently_low() -> None:
    lo, _ = AMBIGUOUS_BAND
    weak = _cand(rel_type=RT.UNRELATED, confidence=lo - 0.2,
                 evidence=(_ev(SK.VALUE_CONTAINMENT, 0.05),))
    assert should_vote(weak) is False


def test_should_vote_fires_on_needs_adjudication_flag() -> None:
    flagged = _cand(rel_type=RT.FK_JOIN, confidence=0.99,
                    evidence=_clean_fk_evidence(), needs_adjudication=True)
    assert should_vote(flagged) is True


def test_should_vote_fires_on_unknown_type() -> None:
    unk = _cand(rel_type=RT.UNKNOWN, confidence=0.99)
    assert should_vote(unk) is True


def test_should_vote_fires_on_conflicting_evidence() -> None:
    """Even a high proxy number is borderline if a fired signal conflicts."""
    conflicted = _cand(
        rel_type=RT.FK_JOIN, confidence=0.99,
        evidence=(
            _ev(SK.VALUE_CONTAINMENT, 0.9),
            _ev(SK.DISTRIBUTION_DIVERGENCE, 0.8, conflicts=True,
                detail="distributions diverge despite overlap"),
        ),
    )
    assert should_vote(conflicted) is True


# --------------------------------------------------------------------------
# the real paths reason DISTINCTLY (not temperature noise)
# --------------------------------------------------------------------------


def test_value_path_kills_looks_similar_isnt_related() -> None:
    """High overlap but diverging distributions ⇒ ValuePath says UNRELATED where a
    naive overlap heuristic (and SchemaPath, on a key) would say join."""
    vp = ValuePath()
    cand = _cand(evidence=(
        _ev(SK.VALUE_CONTAINMENT, 0.7),
        _ev(SK.VALUE_JACCARD, 0.7),
        _ev(SK.DISTRIBUTION_DIVERGENCE, 0.8),  # distributions diverge
    ))
    pv = vp.vote(cand)
    assert pv.rel_type == RT.UNRELATED


def test_schema_path_reasons_from_structure() -> None:
    sp = SchemaPath()
    # unique parent key + strong fan-in ⇒ FK by structure alone (no value signals)
    fk_cand = _cand(evidence=(
        _ev(SK.KEY_UNIQUENESS, 0.99),
        _ev(SK.CARDINALITY_RATIO, 10.0),
    ))
    assert sp.vote(fk_cand).rel_type == RT.FK_JOIN
    # non-unique right side, ratio≈1 ⇒ bridge shape
    bridge_cand = _cand(evidence=(
        _ev(SK.KEY_UNIQUENESS, 0.3),
        _ev(SK.CARDINALITY_RATIO, 1.0),
    ))
    assert sp.vote(bridge_cand).rel_type == RT.M2M_BRIDGE


def test_business_logic_path_reasons_from_meaning() -> None:
    bp = BusinessLogicPath()
    # a bridge/junction table role ⇒ M2M by meaning, regardless of values
    bridge = _cand(left_table="order_product_bridge", right_table="products",
                   left_col="product_id", right_col="product_id",
                   evidence=(_ev(SK.TYPE_COMPAT, 1.0),))
    assert bp.vote(bridge).rel_type == RT.M2M_BRIDGE
    # incompatible semantic types ⇒ unrelated by meaning
    incompat = _cand(evidence=(
        _ev(SK.TYPE_COMPAT, 0.0, conflicts=True, detail="currency vs date"),
    ))
    assert bp.vote(incompat).rel_type == RT.UNRELATED


def test_paths_are_distinct_objects_with_distinct_paths() -> None:
    paths = default_paths()
    kinds = [p.path for p in paths]
    assert set(kinds) == {ReasoningPath.SCHEMA, ReasoningPath.VALUE,
                          ReasoningPath.BUSINESS_LOGIC}
    assert len(kinds) == 3


# --------------------------------------------------------------------------
# determinism + provenance
# --------------------------------------------------------------------------


def test_determinism_same_candidate_same_verdict() -> None:
    g1, g2 = RelationshipGate(), RelationshipGate()
    cand = _cand(evidence=_clean_fk_evidence())
    val = _good_validation()
    v1 = g1.decide(cand, val)
    v2 = g2.decide(cand, val)
    assert RelationshipGate.to_provenance(v1) == RelationshipGate.to_provenance(v2)


def test_provenance_records_votes_and_validation() -> None:
    gate = RelationshipGate()
    cand = _cand(evidence=_clean_fk_evidence())
    v = gate.decide(cand, _good_validation())
    prov = RelationshipGate.to_provenance(v)
    assert prov["rel_type"] == RT.FK_JOIN.value
    assert len(prov["votes"]) == 3
    assert all("rationale" in row and "path" in row for row in prov["votes"])
    assert prov["validation"]["ok"] is True
    assert prov["committed"] is True


def test_gate_rejects_duplicate_paths() -> None:
    import pytest
    with pytest.raises(ValueError):
        RelationshipGate([SchemaPath(), SchemaPath()])
    with pytest.raises(ValueError):
        RelationshipGate([])


# --------------------------------------------------------------------------
# top-3-within-0.9 commit/abstain calibration (§3 — precision over recall)
# --------------------------------------------------------------------------


def _unanimous_paths(rt: RT, conf: float):
    return [
        _FixedPath(ReasoningPath.SCHEMA, rt, conf),
        _FixedPath(ReasoningPath.VALUE, rt, conf),
        _FixedPath(ReasoningPath.BUSINESS_LOGIC, rt, conf),
    ]


def test_calibration_constants_are_documented_defaults() -> None:
    assert TOP_K_COMMIT == 3
    assert TOP_CONFIDENCE_RATIO == 0.9


def test_calibration_commits_a_clear_winner() -> None:
    """One strong candidate, the rest far below the 0.9-of-top band ⇒ the leader
    commits (it is top-3, within ratio, consensus holds, no near-tie)."""
    gate = RelationshipGate(_unanimous_paths(RT.FK_JOIN, 0.95))
    cands = [
        _cand(confidence=0.95, right_table="parcels"),       # leader
        _cand(confidence=0.50, right_table="depots"),        # 0.50 < 0.9*0.95
        _cand(confidence=0.30, right_table="hubs"),
    ]
    verdicts = gate.calibrate_commits(cands)
    assert verdicts[0].committed is True
    assert verdicts[1].committed is False and verdicts[1].routed_to_human is True
    assert verdicts[2].committed is False


def test_calibration_borderline_second_within_0_9_routes_to_human() -> None:
    """The headline rule: a 2nd candidate within 0.9 of the top is a near-tie ⇒
    the field is AMBIGUOUS, so EVEN THE LEADER abstains and routes to a human."""
    gate = RelationshipGate(_unanimous_paths(RT.FK_JOIN, 0.95))
    cands = [
        _cand(confidence=0.95, right_table="parcels"),   # leader
        _cand(confidence=0.90, right_table="packages"),  # 0.90 >= 0.9*0.95=0.855 ⇒ near-tie
    ]
    verdicts = gate.calibrate_commits(cands)
    assert verdicts[0].committed is False
    assert verdicts[0].routed_to_human is True
    assert verdicts[1].committed is False


def test_calibration_outside_top_k_never_commits() -> None:
    """A 4th candidate, even with decent consensus, is outside the top-3 ⇒ route."""
    gate = RelationshipGate(_unanimous_paths(RT.FK_JOIN, 0.8))
    # leader clearly ahead (no near-tie), then three more well below the band
    cands = [
        _cand(confidence=0.90, right_table="a"),
        _cand(confidence=0.40, right_table="b"),
        _cand(confidence=0.35, right_table="c"),
        _cand(confidence=0.30, right_table="d"),  # 4th — outside top-3
    ]
    verdicts = gate.calibrate_commits(cands)
    assert verdicts[0].committed is True       # clear winner commits
    assert verdicts[3].committed is False      # outside top-k routes
    assert verdicts[3].routed_to_human is True


def test_calibration_no_consensus_blocks_commit_even_as_clear_leader() -> None:
    """A clear confidence leader with NO path consensus still must route — the
    calibration ANDs the field rule with the per-candidate consensus gate."""
    split = [
        _FixedPath(ReasoningPath.SCHEMA, RT.FK_JOIN, 0.9),
        _FixedPath(ReasoningPath.VALUE, RT.M2M_BRIDGE, 0.9),
        _FixedPath(ReasoningPath.BUSINESS_LOGIC, RT.DENORMALIZATION, 0.9),
    ]
    gate = RelationshipGate(split)
    cands = [_cand(confidence=0.95), _cand(confidence=0.20, right_table="z")]
    verdicts = gate.calibrate_commits(cands)
    assert verdicts[0].committed is False  # no plurality ⇒ no commit
    assert verdicts[0].routed_to_human is True


def test_calibration_validation_veto_still_blocks() -> None:
    """A clear leader whose executed join FAILS validation must not commit, even
    though it is the lone top candidate — the data veto composes with calibration."""
    gate = RelationshipGate(_unanimous_paths(RT.FK_JOIN, 0.95))
    cands = [_cand(confidence=0.95), _cand(confidence=0.30, right_table="z")]
    verdicts = gate.calibrate_commits(cands, [_bad_validation(), None])
    assert verdicts[0].committed is False
    assert verdicts[0].routed_to_human is True


def test_calibration_empty_and_alignment() -> None:
    import pytest
    gate = RelationshipGate()
    assert gate.calibrate_commits([]) == []
    with pytest.raises(ValueError):
        gate.calibrate_commits([_cand()], [None, None])  # misaligned validations


def test_calibration_is_deterministic() -> None:
    g1, g2 = (RelationshipGate(_unanimous_paths(RT.FK_JOIN, 0.95)) for _ in range(2))
    cands = [
        _cand(confidence=0.95, right_table="parcels"),
        _cand(confidence=0.50, right_table="depots"),
    ]
    v1 = g1.calibrate_commits(cands)
    v2 = g2.calibrate_commits(cands)
    assert [RelationshipGate.to_provenance(v) for v in v1] == \
           [RelationshipGate.to_provenance(v) for v in v2]
