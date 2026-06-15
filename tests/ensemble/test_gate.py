"""The WMA DE-decision gate: weighted-majority firing, the execution-grounded
veto (confidently-wrong guard), Littlestone-Warmuth weight updates from a human
verdict, threshold gating, TURN/Soft-SC, and determinism."""

from __future__ import annotations

from dataclasses import dataclass

from ontoforge.ensemble.experts import (
    ActionContext,
    CoverageExpert,
    Expert,
    Vote,
    default_experts,
)
from ontoforge.ensemble.gate import (
    DEFAULT_THRESHOLD,
    Gate,
    soft_self_consistency,
    turn_temperature,
)


# a couple of fixed-vote experts to make WMA behaviour exact and inspectable
@dataclass(frozen=True, slots=True)
class _FixedExpert:
    name: str
    decision: str
    confidence: float = 0.9

    def vote(self, ctx: ActionContext) -> Vote:
        return Vote(self.decision, self.confidence, "fixed", self.name)


def _high_cov_ctx() -> ActionContext:
    return ActionContext(
        action="join", coverage=0.98, value_overlap=0.98,
        left_name="order customer_id", right_name="customer customer_id",
        left_type="string", right_type="string",
    )


def _low_cov_ctx() -> ActionContext:
    return ActionContext(
        action="join", coverage=0.05, value_overlap=0.05,
        left_name="order qty", right_name="customer id",
        left_type="integer", right_type="string",
    )


# ----------------------------------------------------------------- firing


def test_high_coverage_join_fires() -> None:
    gate = Gate(default_experts())
    dec = gate.decide(_high_cov_ctx())
    assert dec.fire is True
    assert dec.tally["fire"] > dec.tally["hold"]
    assert dec.soft_score >= DEFAULT_THRESHOLD


def test_low_coverage_join_does_not_fire() -> None:
    gate = Gate(default_experts())
    dec = gate.decide(_low_cov_ctx())
    assert dec.fire is False
    assert dec.tally["hold"] >= dec.tally["fire"]


# ----------------------------------------------- execution-grounded veto


def test_subcoverage_join_vetoed_despite_unanimous_fire() -> None:
    """The confidently-wrong guard for the gate: even if EVERY expert votes
    'fire', a verifier that rejects on sub-coverage VETOES the action. The data
    refusing a join overrides the votes."""
    unanimous = [
        _FixedExpert("e1", "fire", 0.99),
        _FixedExpert("e2", "fire", 0.99),
        _FixedExpert("e3", "fire", 0.99),
    ]
    gate = Gate(unanimous, threshold=0.0)

    def _verifier(ctx: ActionContext):
        return (False, "coverage 5% below the 35% floor (execution-grounded veto)")

    dec = gate.decide(_high_cov_ctx(), verifier=_verifier)
    assert dec.fire is False
    assert dec.vetoed is True
    assert "floor" in dec.veto_reason
    # the votes were unanimous fire, but the veto won
    assert all(v["decision"] == "fire" for v in dec.votes)


def test_verifier_ok_lets_votes_decide() -> None:
    gate = Gate(default_experts())
    dec = gate.decide(_high_cov_ctx(), verifier=lambda c: (True, ""))
    assert dec.vetoed is False
    assert dec.fire is True


# ------------------------------------------------- threshold gating (Soft-SC)


def test_threshold_gates_a_weak_majority() -> None:
    """A bare 'fire' majority with LOW per-expert confidence is held back by the
    calibrated Soft-Self-Consistency threshold (sparse-action gating)."""
    experts = [
        _FixedExpert("e1", "fire", 0.40),
        _FixedExpert("e2", "fire", 0.40),
        _FixedExpert("e3", "hold", 0.20),
    ]
    high = Gate(experts, threshold=0.6)   # demands strong fire confidence
    low = Gate(experts, threshold=0.3)    # lenient
    ctx = _high_cov_ctx()
    assert high.decide(ctx).fire is False  # soft score 0.40 < 0.6 -> held
    assert low.decide(ctx).fire is True    # 0.40 >= 0.3 and majority fire


# -------------------------------------------- Littlestone-Warmuth weight update


def test_epsilon_matches_sqrt_lnN_over_T() -> None:
    import math
    gate = Gate(default_experts(), horizon=64)
    n = len(gate.experts)
    expected = math.sqrt(math.log(n) / 64)
    assert abs(gate.epsilon - expected) < 1e-9


def test_confirm_reject_penalizes_disagreeing_experts() -> None:
    """A human Confirm/Reject verdict multiplicatively penalizes experts who
    voted against the confirmed outcome (WMA self-improvement, wired to review)."""
    experts = [
        _FixedExpert("right", "fire", 0.9),
        _FixedExpert("wrong", "hold", 0.9),
    ]
    gate = Gate(experts, horizon=16)
    ctx = _high_cov_ctx()
    before = dict(gate.weights)
    # reviewer confirms the action SHOULD have fired
    after = gate.update_weights(ctx, confirmed_fire=True)
    # the 'wrong' expert (voted hold) is penalized relative to 'right'
    assert after["wrong"] < before["wrong"] or after["wrong"] < after["right"]
    assert after["right"] > after["wrong"]
    # mean weight preserved (renormalized)
    assert abs(sum(after.values()) / len(after) - 1.0) < 1e-9


def test_regret_drives_decision_over_rounds() -> None:
    """Over repeated rounds where a noisy expert keeps disagreeing with the
    confirmed truth, its weight decays so the ensemble's decision converges to
    the correct side — the provable-regret behaviour."""
    experts = [
        _FixedExpert("good", "fire", 0.8),
        _FixedExpert("noisy", "hold", 0.95),  # loud but wrong
    ]
    gate = Gate(experts, threshold=0.0, horizon=8)
    ctx = _high_cov_ctx()
    # initially the loud wrong expert can tie/dominate the tally
    w0 = dict(gate.weights)
    # 10 rounds of "should have fired" feedback
    for _ in range(10):
        gate.update_weights(ctx, confirmed_fire=True)
    # the noisy (wrong) expert's weight has decayed below the good expert's
    assert gate.weights["noisy"] < gate.weights["good"]
    assert gate.weights["noisy"] < w0["noisy"]
    # and the gate now fires (the good expert's confidence-weighted vote wins)
    assert gate.decide(ctx).fire is True


# ----------------------------------------------------------------- determinism


def test_determinism_same_inputs_same_decision() -> None:
    g1, g2 = Gate(default_experts()), Gate(default_experts())
    ctx = _high_cov_ctx()
    d1, d2 = g1.decide(ctx), g2.decide(ctx)
    assert d1.to_provenance() == d2.to_provenance()


# ----------------------------------------------------------------- TURN / Soft-SC


def test_turn_temperature_responds_to_spread() -> None:
    tight = turn_temperature([0.9, 0.91, 0.9])
    spread = turn_temperature([0.1, 0.9, 0.5])
    assert tight < spread  # confident agreement -> lower temp; dispersion -> higher


def test_soft_self_consistency_modes() -> None:
    cs = [0.8, 0.6, 0.9]
    assert soft_self_consistency(cs, "min") == 0.6
    assert abs(soft_self_consistency(cs, "mean") - (sum(cs) / 3)) < 1e-9
    assert abs(soft_self_consistency(cs, "product") - (0.8 * 0.6 * 0.9)) < 1e-9
    assert soft_self_consistency([]) == 0.0


def test_gate_requires_unique_named_experts() -> None:
    import pytest
    with pytest.raises(ValueError):
        Gate([CoverageExpert(), CoverageExpert()])  # duplicate name
    with pytest.raises(ValueError):
        Gate([])  # empty
