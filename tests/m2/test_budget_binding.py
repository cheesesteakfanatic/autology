"""M2 acceptance: budget binding (whitepaper §8 economy profile, §11.2 M2).

With a token-charging fake ModelClient: spend NEVER exceeds the budget; once
the conservative reservation no longer fits, the spine does NOT call the model
and the decision comes back quarantined=True — never silently auto-decided.
"""

from __future__ import annotations

from ontoforge.contracts import DecisionKind, SpineProfile, Tier
from ontoforge.spine import DecisionSpine

from m2_helpers import ScriptedModelClient, heuristic_request


def _ambiguous_workload(n: int, kind=DecisionKind.ER):
    # Heuristic confidence 0.5: every decision lands in the escalation band.
    return [heuristic_request(kind, f"d{i:04d}", 0.5) for i in range(n)]


def test_spend_never_exceeds_budget_and_excess_is_quarantined() -> None:
    budget = 2_000
    client = ScriptedModelClient(t2=("yes", 0.99))
    spine = DecisionSpine(SpineProfile(name="economy", budget_tokens=budget), client)

    results = []
    for req in _ambiguous_workload(60):
        res = spine.decide(req)
        results.append(res)
        # Invariant: spend within budget after EVERY decision.
        assert spine.spent_tokens() <= budget, (
            f"spend {spine.spent_tokens()} exceeded budget {budget}"
        )

    escalated = [r for r in results if r.tier == Tier.T2 and not r.quarantined]
    quarantined = [r for r in results if r.quarantined]
    assert escalated, "budget should admit at least one T2 call"
    assert quarantined, "budget must eventually exhaust on this workload"
    # Budget binds monotonically: once the first quarantine happens, every
    # later ambiguous decision is also quarantined (spend never decreases).
    first_q = next(i for i, r in enumerate(results) if r.quarantined)
    assert all(r.quarantined for r in results[first_q:])
    # Quarantined decisions are NEVER silently auto-decided and carry no model cost.
    for r in quarantined:
        assert not r.auto_decided
        assert not r.deferred_to_human  # quarantine is its own fail-closed state
        assert r.cost_tokens == 0
        assert "quarantine" in r.rationale
    # The client was called exactly once per non-quarantined escalation:
    # quarantined decisions made NO model calls.
    assert len(client.calls) == len(escalated)


def test_blocked_t3_quarantines_after_t2() -> None:
    """T2 answers mid-band (forcing T3), but the budget only admits the T2
    call: the decision must quarantine at T2 rather than call T3."""
    from ontoforge.spine import build_prompt
    from ontoforge.spine.adjudicator import ADJUDICATE_MAX_TOKENS
    from ontoforge.spine.spine import CHARS_PER_TOKEN

    client = ScriptedModelClient(t2=("yes", 0.6), t3=("yes", 0.99))
    req = heuristic_request(DecisionKind.ER, "t3blocked", 0.5)
    # The spine's conservative reservation for one call; T2 and T3 prompts for
    # the same request have identical length, so reservation + 50 admits the
    # T2 call but cannot admit T3 after T2's real charge (>= 50 tokens) lands.
    reservation = len(build_prompt(req, "T2")) // CHARS_PER_TOKEN + ADJUDICATE_MAX_TOKENS
    budget = reservation + 50
    spine = DecisionSpine(SpineProfile(name="economy", budget_tokens=budget), client)
    res = spine.decide(req)
    assert client.calls_for("T2") == 1 and client.calls_for("T3") == 0
    assert res.quarantined and not res.auto_decided
    assert res.tier == Tier.T2  # tier-of-record: the last tier consulted
    assert spine.spent_tokens() <= budget


def test_zero_budget_blocks_all_calls() -> None:
    client = ScriptedModelClient()
    spine = DecisionSpine(SpineProfile(name="economy", budget_tokens=0), client)
    res = spine.decide(heuristic_request(DecisionKind.SM, "z0", 0.5))
    assert res.quarantined and not res.auto_decided
    assert client.calls == []
    assert spine.spent_tokens() == 0


def test_high_confidence_decisions_free_under_zero_budget() -> None:
    """Budget exhaustion must not affect decisions T0/T1 can settle."""
    client = ScriptedModelClient()
    spine = DecisionSpine(SpineProfile(name="economy", budget_tokens=0), client)
    accept = spine.decide(heuristic_request(DecisionKind.SM, "hi", 0.99))
    reject = spine.decide(heuristic_request(DecisionKind.SM, "lo", 0.01))
    assert accept.auto_decided and accept.outcome == "yes"
    assert reject.auto_decided and reject.outcome == "no"
    assert client.calls == [] and spine.spent_tokens() == 0


def test_spent_tokens_matches_client_charges() -> None:
    client = ScriptedModelClient(t2=("yes", 0.99))
    spine = DecisionSpine(SpineProfile(name="economy", budget_tokens=100_000), client)
    results = [spine.decide(req) for req in _ambiguous_workload(5)]
    assert spine.spent_tokens() == sum(r.cost_tokens for r in results)
    assert spine.spent_tokens() > 0
