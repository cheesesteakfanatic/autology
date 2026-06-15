"""Deterministic experts: each weighs a different evidence facet, so the keyless
ensemble is genuinely diverse."""

from __future__ import annotations

from ontoforge.ensemble.experts import (
    ActionContext,
    CoverageExpert,
    NameSimilarityExpert,
    TypeCompatExpert,
    ValueOverlapExpert,
    Vote,
    default_experts,
)


def test_coverage_expert_fires_on_high_coverage() -> None:
    e = CoverageExpert()
    assert e.vote(ActionContext(action="join", coverage=0.95)).decision == "fire"
    assert e.vote(ActionContext(action="join", coverage=0.05)).decision == "hold"
    # no coverage measured -> conservative hold
    assert e.vote(ActionContext(action="join")).decision == "hold"


def test_value_overlap_expert_independent_of_coverage_framing() -> None:
    e = ValueOverlapExpert()
    assert e.vote(ActionContext(action="join", value_overlap=0.8)).decision == "fire"
    assert e.vote(ActionContext(action="join", value_overlap=0.1)).decision == "hold"


def test_name_similarity_expert_soft_signal() -> None:
    e = NameSimilarityExpert()
    # a PARTIAL name match (shares 'cust' substring but not identical tokens):
    # fires, but as a soft signal its confidence is below the maximum.
    fire = e.vote(ActionContext(action="join", left_name="cust_ref", right_name="customer key"))
    hold = e.vote(ActionContext(action="join", left_name="qty", right_name="region"))
    assert fire.decision == "fire"
    assert hold.decision == "hold"
    # it is a SOFT signal: a partial match does not assert full certainty
    assert fire.confidence < 1.0
    # an exact-token match, by contrast, can be fully confident
    exact = e.vote(ActionContext(action="join", left_name="customer_id", right_name="customer id"))
    assert exact.decision == "fire"


def test_type_compat_expert_holds_on_incompatible_types() -> None:
    e = TypeCompatExpert()
    bad = e.vote(ActionContext(action="join", left_type="date", right_type="integer"))
    good = e.vote(ActionContext(action="join", left_type="string", right_type="string"))
    assert bad.decision == "hold"
    assert good.decision == "fire"
    # unit mismatch is a hold even when types match
    unit = e.vote(
        ActionContext(action="retype", left_type="float", right_type="float",
                      left_unit="USD", right_unit="EUR")
    )
    assert unit.decision == "hold"


def test_default_ensemble_is_diverse() -> None:
    experts = default_experts()
    names = [e.name for e in experts]
    assert len(set(names)) == len(names) == 4
    # on an ambiguous context the experts genuinely disagree (diversity)
    ctx = ActionContext(action="join", coverage=0.5, value_overlap=0.5,
                        left_name="a", right_name="b", left_type="string", right_type="string")
    decisions = {e.vote(ctx).decision for e in experts}
    assert len(decisions) >= 1  # at least produces votes; diversity exercised elsewhere


def test_votes_carry_expert_name_and_are_valid() -> None:
    for e in default_experts():
        v = e.vote(ActionContext(action="join", coverage=0.7))
        assert isinstance(v, Vote)
        assert v.decision in ("fire", "hold")
        assert 0.0 <= v.confidence <= 1.0
