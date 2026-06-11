"""M2: economy vs CRUCIBLE profiles (whitepaper §8).

Same workload, both profiles: CRUCIBLE escalates STRICTLY more (the band
widens to 'any non-trivial ambiguity'), never quarantines (budget shadow
price ~0), consults T2 AND T3 on every escalation, and uses agreement to
boost confidence; disagreement routes to the human queue.
"""

from __future__ import annotations

from ontoforge.contracts import DecisionKind, SpineProfile, Tier
from ontoforge.spine import DecisionSpine

from m2_helpers import ScriptedModelClient, heuristic_request

N = 99


def _workload():
    # Heuristic confidences sweep (0, 1): includes sure cases, the economy
    # band (0.30, 0.92), and the crucible-only margins (e.g. 0.93..0.97).
    return [heuristic_request(DecisionKind.ER, f"w{i:03d}", (i + 1) / (N + 1)) for i in range(N)]


def _run(profile: SpineProfile) -> tuple[list, ScriptedModelClient]:
    client = ScriptedModelClient(t2=("yes", 0.99), t3=("yes", 0.99))
    spine = DecisionSpine(profile, client)
    return [spine.decide(r) for r in _workload()], client


def _escalated_ids(results) -> set[str]:
    return {
        r.decision_id
        for r in results
        if r.tier in (Tier.T2, Tier.T3, Tier.HUMAN) or r.quarantined
    }


def test_crucible_escalates_strictly_more_and_never_quarantines() -> None:
    eco_results, eco_client = _run(SpineProfile(name="economy", budget_tokens=10_000_000))
    cru_results, cru_client = _run(SpineProfile(name="crucible", budget_tokens=10_000_000))

    eco_esc, cru_esc = _escalated_ids(eco_results), _escalated_ids(cru_results)
    assert eco_esc < cru_esc, "crucible escalations must be a strict superset"
    assert len(cru_esc) > len(eco_esc)

    assert all(not r.quarantined for r in cru_results)
    # CRUCIBLE consults BOTH tiers on every escalation.
    assert cru_client.calls_for("T2") == len(cru_esc)
    assert cru_client.calls_for("T3") == len(cru_esc)
    # Economy with a confident T2 never needed T3.
    assert eco_client.calls_for("T3") == 0


def test_crucible_ignores_budget_economy_quarantines() -> None:
    """Identical workload under a ZERO budget: economy fail-closes without a
    single model call; crucible still escalates everything in its band."""
    eco_results, eco_client = _run(SpineProfile(name="economy", budget_tokens=0))
    cru_results, cru_client = _run(SpineProfile(name="crucible", budget_tokens=0))

    assert any(r.quarantined for r in eco_results)
    assert eco_client.calls == []  # fail-closed: blocked calls are never made
    assert all(not r.quarantined for r in cru_results)
    assert cru_client.calls != []
    assert _escalated_ids(eco_results) < _escalated_ids(cru_results)


def test_crucible_spend_is_still_metered() -> None:
    client = ScriptedModelClient()
    spine = DecisionSpine(SpineProfile(name="crucible", budget_tokens=0), client)
    res = spine.decide(heuristic_request(DecisionKind.ER, "meter", 0.5))
    assert res.cost_tokens > 0
    assert spine.spent_tokens() == res.cost_tokens  # shadow price 0, not unmetered


def test_agreement_boosts_confidence() -> None:
    """T2 and T3 each at 0.9 (below the crucible tau_high of 0.98) — but they
    AGREE, so the independent-error boost 1-(1-.9)^2 = 0.99 clears the bar."""
    client = ScriptedModelClient(t2=("yes", 0.9), t3=("yes", 0.9))
    spine = DecisionSpine(SpineProfile(name="crucible"), client)
    res = spine.decide(heuristic_request(DecisionKind.ER, "agree", 0.5))
    assert res.auto_decided and res.tier == Tier.T3 and res.outcome == "yes"
    assert res.confidence >= 0.98
    assert "agreement" in res.rationale


def test_disagreement_routes_to_human() -> None:
    client = ScriptedModelClient(t2=("yes", 0.95), t3=("no", 0.95))
    spine = DecisionSpine(SpineProfile(name="crucible"), client)
    res = spine.decide(heuristic_request(DecisionKind.ER, "fight", 0.5))
    assert res.deferred_to_human and res.tier == Tier.HUMAN
    assert not res.quarantined
    assert "disagreement" in res.rationale


def test_weak_agreement_still_defers() -> None:
    """Agreement at low confidence must NOT clear the widened crucible band."""
    client = ScriptedModelClient(t2=("yes", 0.6), t3=("yes", 0.6))
    spine = DecisionSpine(SpineProfile(name="crucible"), client)
    res = spine.decide(heuristic_request(DecisionKind.ER, "weak", 0.5))
    assert res.deferred_to_human  # boost = 1 - 0.4^2 = 0.84 < 0.98


def test_crucible_band_escalates_what_economy_accepts() -> None:
    """A 0.95-confidence case: economy auto-accepts at T1; crucible escalates."""
    eco_client = ScriptedModelClient()
    eco = DecisionSpine(SpineProfile(name="economy"), eco_client)
    r_eco = eco.decide(heuristic_request(DecisionKind.ER, "edge", 0.95))
    assert r_eco.tier == Tier.T1 and eco_client.calls == []

    cru_client = ScriptedModelClient()
    cru = DecisionSpine(SpineProfile(name="crucible"), cru_client)
    r_cru = cru.decide(heuristic_request(DecisionKind.ER, "edge", 0.95))
    assert r_cru.tier != Tier.T1 and len(cru_client.calls) == 2
