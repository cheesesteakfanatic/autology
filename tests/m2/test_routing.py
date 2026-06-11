"""M2: two-threshold selective routing (MVP plan §2 escalation contract).

High-confidence cases never escalate (counted on a fake client that fails the
test if called); ambiguous-band cases reach T2; T3 fires only when T2's
confidence itself lands in the band; exhausted tiers defer to the human queue.
T0 rules pre-empt everything for kinds they can settle.
"""

from __future__ import annotations

import pytest

from ontoforge.contracts import (
    DecisionKind,
    DecisionRequest,
    SpineProfile,
    Tier,
    TierScore,
)
from ontoforge.spine import DecisionSpine

from m2_helpers import (
    CANDS,
    ExplodingModelClient,
    GaussianWorld,
    ScriptedModelClient,
    gaussian_samples,
    heuristic_request,
)


def test_high_confidence_never_escalates() -> None:
    spine = DecisionSpine(SpineProfile(name="economy"), ExplodingModelClient())
    for i, s in enumerate((0.93, 0.97, 0.999, 0.07, 0.01)):
        res = spine.decide(heuristic_request(DecisionKind.ER, f"hc{i}", s))
        assert res.auto_decided and res.tier == Tier.T1
        assert res.outcome == ("yes" if s >= 0.5 else "no")
        assert res.confidence >= 0.92
    assert spine.spent_tokens() == 0


def test_calibrated_high_confidence_never_escalates() -> None:
    spine = DecisionSpine(SpineProfile(name="economy"), ExplodingModelClient())
    spine.recalibrate(DecisionKind.ER, gaussian_samples(DecisionKind.ER, seed=1, n=20_000))
    world = GaussianWorld()
    res = spine.decide(
        DecisionRequest(
            kind=DecisionKind.ER,
            decision_id="far",
            candidates=CANDS,
            features=world.features(4.0, 2.0),
        )
    )
    assert res.auto_decided and res.tier == Tier.T1 and res.outcome == "yes"
    assert "uncalibrated" not in res.rationale


def test_ambiguous_band_reaches_t2_only() -> None:
    client = ScriptedModelClient(t2=("yes", 0.99))
    spine = DecisionSpine(SpineProfile(name="economy"), client)
    res = spine.decide(heuristic_request(DecisionKind.ER, "mid", 0.6))
    assert res.tier == Tier.T2 and res.auto_decided and res.outcome == "yes"
    assert client.calls_for("T2") == 1
    assert client.calls_for("T3") == 0  # T2 was confident: no frontier call


def test_t3_fires_only_when_t2_in_band() -> None:
    client = ScriptedModelClient(t2=("yes", 0.6), t3=("yes", 0.99))
    spine = DecisionSpine(SpineProfile(name="economy"), client)
    res = spine.decide(heuristic_request(DecisionKind.ER, "deep", 0.6))
    assert client.calls_for("T2") == 1 and client.calls_for("T3") == 1
    assert res.tier == Tier.T3 and res.auto_decided and res.outcome == "yes"


def test_t2_auto_reject_band() -> None:
    """A confidently-negative T2 verdict crosses tau_low: auto-reject at T2."""
    client = ScriptedModelClient(t2=("no", 0.95))
    spine = DecisionSpine(SpineProfile(name="economy"), client)
    res = spine.decide(heuristic_request(DecisionKind.ER, "neg", 0.6))
    assert res.tier == Tier.T2 and res.auto_decided and res.outcome == "no"
    assert client.calls_for("T3") == 0


def test_exhausted_tiers_defer_to_human() -> None:
    client = ScriptedModelClient(t2=("yes", 0.5), t3=("yes", 0.5))
    spine = DecisionSpine(SpineProfile(name="economy"), client)
    res = spine.decide(heuristic_request(DecisionKind.ER, "hard", 0.6))
    assert res.deferred_to_human and res.tier == Tier.HUMAN
    assert not res.auto_decided and not res.quarantined
    assert client.calls_for("T2") == 1 and client.calls_for("T3") == 1


def test_no_client_defers_ambiguous() -> None:
    spine = DecisionSpine(SpineProfile(name="economy"))
    res = spine.decide(heuristic_request(DecisionKind.ER, "noclient", 0.6))
    assert res.deferred_to_human and res.tier == Tier.HUMAN


def test_malformed_model_output_degrades_safely() -> None:
    """Garbage from T2 AND T3 must not crash or auto-decide: abstention -> human."""
    client = ScriptedModelClient(malformed_tiers=frozenset({"T2", "T3"}))
    spine = DecisionSpine(SpineProfile(name="economy"), client)
    res = spine.decide(heuristic_request(DecisionKind.ER, "garbage", 0.6))
    assert res.deferred_to_human and not res.auto_decided


def test_t0_rule_preempts_all_tiers() -> None:
    client = ExplodingModelClient()
    spine = DecisionSpine(SpineProfile(name="economy"), client)

    def exact_key_rule(req: DecisionRequest) -> TierScore | None:
        if dict(req.context).get("exact_key_match"):
            return TierScore(scores={"yes": 1.0, "no": 0.0})
        return None

    spine.register_rule(DecisionKind.ER, exact_key_rule)
    res = spine.decide(
        DecisionRequest(
            kind=DecisionKind.ER,
            decision_id="t0hit",
            candidates=CANDS,
            features=(("s", 0.5),),  # would otherwise escalate
            context=(("exact_key_match", True),),
        )
    )
    assert res.tier == Tier.T0 and res.auto_decided and res.outcome == "yes"
    assert res.confidence >= 0.92 and res.cost_tokens == 0


def test_t0_abstention_falls_through_to_t1() -> None:
    spine = DecisionSpine(SpineProfile(name="economy"), ExplodingModelClient())
    spine.register_rule(DecisionKind.SM, lambda req: None)  # always abstains
    res = spine.decide(heuristic_request(DecisionKind.SM, "fallthru", 0.99))
    assert res.tier == Tier.T1 and res.auto_decided


def test_impact_widens_escalation_band() -> None:
    """conf 0.93 auto-accepts at impact 1 (tau_high 0.92) but escalates at
    impact 2 (tau_high widened to 0.94): high-impact decisions escalate more
    readily (contracts.decisions.DecisionRequest.impact)."""
    client = ScriptedModelClient(t2=("yes", 0.99))
    spine = DecisionSpine(SpineProfile(name="economy"), client)
    low = spine.decide(heuristic_request(DecisionKind.ER, "imp1", 0.93, impact=1.0))
    assert low.tier == Tier.T1 and client.calls == []
    high = spine.decide(heuristic_request(DecisionKind.ER, "imp2", 0.93, impact=2.0))
    assert high.tier == Tier.T2 and client.calls_for("T2") == 1


def test_multiclass_routing() -> None:
    """Non-binary kinds: argmax must clear tau_high or escalate (no reject side)."""
    client = ScriptedModelClient(by_decision={"mc1": {"T2": ("b", 0.97)}})
    spine = DecisionSpine(SpineProfile(name="economy"), client)
    req = DecisionRequest(
        kind=DecisionKind.QI,
        decision_id="mc1",
        candidates=("a", "b", "c"),
        features=(("a", 0.4), ("b", 0.35), ("c", 0.25)),
    )
    res = spine.decide(req)
    assert res.tier == Tier.T2 and res.outcome == "b" and res.auto_decided

    clear = DecisionRequest(
        kind=DecisionKind.QI,
        decision_id="mc2",
        candidates=("a", "b", "c"),
        features=(("a", 0.95), ("b", 0.03), ("c", 0.02)),
    )
    res2 = spine.decide(clear)
    assert res2.tier == Tier.T1 and res2.outcome == "a"


def test_fewer_than_two_candidates_rejected() -> None:
    spine = DecisionSpine(SpineProfile())
    with pytest.raises(ValueError):
        spine.decide(
            DecisionRequest(kind=DecisionKind.ER, decision_id="bad", candidates=("only",))
        )
