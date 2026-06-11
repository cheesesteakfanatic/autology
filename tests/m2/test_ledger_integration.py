"""M2 x M0: every decide() appends to the ledger and records cost.

Uses the COMPLETE M0 implementation (ontoforge.ledger.SqliteLedger) per the
module brief — the spine treats it through the contracts.ledger protocol.
"""

from __future__ import annotations

import json

from ontoforge.contracts import DecisionKind, SpineProfile, Tier
from ontoforge.ledger import SqliteLedger
from ontoforge.spine import DecisionSpine

from m2_helpers import ScriptedModelClient, heuristic_request


def test_every_decision_is_ledgered_with_cost() -> None:
    with SqliteLedger(":memory:") as ledger:
        client = ScriptedModelClient(t2=("yes", 0.99))
        spine = DecisionSpine(SpineProfile(name="economy"), client, ledger=ledger)

        reqs = [
            heuristic_request(DecisionKind.ER, "led-hi", 0.99),    # T1 accept
            heuristic_request(DecisionKind.ER, "led-lo", 0.02),    # T1 reject
            heuristic_request(DecisionKind.ER, "led-mid", 0.55),   # escalates to T2
        ]
        results = [spine.decide(r) for r in reqs]

        rows = ledger.connection.execute(
            "SELECT decision_id, outcome, tier, cost_tokens, quarantined FROM decision "
            "ORDER BY decision_id"
        ).fetchall()
        assert len(rows) == 3
        by_id = {r[0]: r for r in rows}
        for res in results:
            row = by_id[res.decision_id]
            assert row[1] == res.outcome
            assert row[2] == int(res.tier.value)
            assert row[3] == res.cost_tokens
            assert row[4] == int(res.quarantined)
        assert by_id["led-mid"][2] == int(Tier.T2.value)

        # Cost is recorded per decide() under the spine task name.
        cost_rows = ledger.connection.execute(
            "SELECT task, tokens FROM cost WHERE task LIKE 'spine.decide.%'"
        ).fetchall()
        assert len(cost_rows) == 3
        assert ledger.total_cost_tokens() == sum(r.cost_tokens for r in results)
        assert sum(t for _, t in cost_rows) == spine.spent_tokens()


def test_quarantined_decisions_are_ledgered_too() -> None:
    """Fail-closed states must still be auditable (§8: every decision is a ledger row)."""
    with SqliteLedger(":memory:") as ledger:
        client = ScriptedModelClient()
        spine = DecisionSpine(SpineProfile(name="economy", budget_tokens=0), client, ledger=ledger)
        res = spine.decide(heuristic_request(DecisionKind.SM, "q1", 0.5))
        assert res.quarantined

        row = ledger.connection.execute(
            "SELECT quarantined, deferred_to_human, rationale FROM decision "
            "WHERE decision_id = 'q1'"
        ).fetchone()
        assert row is not None and row[0] == 1 and row[1] == 0
        assert "quarantine" in row[2]


def test_prov_atoms_carried_into_ledger() -> None:
    with SqliteLedger(":memory:") as ledger:
        spine = DecisionSpine(SpineProfile(name="economy"), ledger=ledger)
        req = heuristic_request(DecisionKind.ER, "prov1", 0.99)
        req = type(req)(
            kind=req.kind,
            decision_id=req.decision_id,
            candidates=req.candidates,
            features=req.features,
            context=req.context,
            impact=req.impact,
            prov_atoms=("atom:a1", "atom:b2"),
        )
        spine.decide(req)
        row = ledger.connection.execute(
            "SELECT prov_atoms FROM decision WHERE decision_id = 'prov1'"
        ).fetchone()
        assert json.loads(row[0]) == ["atom:a1", "atom:b2"]


def test_no_ledger_is_fine() -> None:
    spine = DecisionSpine(SpineProfile(name="economy"))
    res = spine.decide(heuristic_request(DecisionKind.ER, "nl", 0.99))
    assert res.auto_decided
