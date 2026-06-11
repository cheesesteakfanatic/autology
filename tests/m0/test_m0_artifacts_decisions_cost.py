"""Append-only ledgers: ARTIFACT (constraint H), DECISION, COST (§1.3, §11.2 M0)."""

import json
import sqlite3

import pytest

from ontoforge.contracts.atoms import make_cell_atom
from ontoforge.contracts.decisions import DecisionResult, Tier
from ontoforge.contracts.provenance import ONE, ZERO, leaf, prov_prod
from ontoforge.ledger import LedgerCostMeter, SqliteLedger


# ------------------------------------------------------------ constraint H


def test_append_artifact_with_zero_provenance_raises():
    led = SqliteLedger()
    zref = led.intern(ZERO)
    with pytest.raises(ValueError, match="constraint H"):
        led.append_artifact("bad", "fact", "{}", zref)
    # nothing was written
    n = led.connection.execute("SELECT COUNT(*) FROM artifact").fetchone()[0]
    assert n == 0


def test_append_artifact_with_uninterned_ref_raises():
    led = SqliteLedger()
    with pytest.raises(KeyError):
        led.append_artifact("bad", "fact", "{}", "ffffffffffffffff")


def test_append_artifact_one_provenance_is_allowed():
    """ONE = axiomatic/empty product is non-ZERO and therefore legal."""
    led = SqliteLedger()
    led.append_artifact("axiom", "config", "{}", led.intern(ONE))
    n = led.connection.execute("SELECT COUNT(*) FROM artifact").fetchone()[0]
    assert n == 1


def test_append_artifact_persists_and_is_invalidatable():
    led = SqliteLedger()
    a, b = make_cell_atom("s", "t", "r1", "c", 1), make_cell_atom("s", "t", "r2", "c", 2)
    led.register_atoms([a, b])
    ref = led.intern(prov_prod([leaf(a.atom_id), leaf(b.atom_id)]))
    led.append_artifact("art-1", "entity-cell", '{"v": 3}', ref)
    row = led.connection.execute(
        "SELECT artifact_id, kind, payload, prov_ref FROM artifact"
    ).fetchone()
    assert row == ("art-1", "entity-cell", '{"v": 3}', ref)
    assert led.invalidate([a.atom_id]) == {"art-1"}


# ------------------------------------------------------------- append-only


def test_ledger_tables_reject_update_and_delete():
    led = SqliteLedger()
    led.record_cost("t", 5)
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        led.connection.execute("UPDATE cost SET tokens = 0")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        led.connection.execute("DELETE FROM cost")
    a = make_cell_atom("s", "t", "r", "c", 1)
    led.register_atoms([a])
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        led.connection.execute("UPDATE atom SET value_repr = 'tampered'")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        led.connection.execute("DELETE FROM atom")


# --------------------------------------------------------------- decisions


def test_append_decision_persists_full_record():
    led = SqliteLedger()
    a = make_cell_atom("s", "t", "r", "c", "x")
    led.register_atoms([a])
    result = DecisionResult(
        decision_id="er:42",
        outcome="yes",
        confidence=0.97,
        conformal_set=("yes",),
        tier=Tier.T1,
        cost_tokens=12,
        deferred_to_human=False,
        quarantined=False,
        rationale="fs score above tau_high",
    )
    led.append_decision(result, prov_atoms=[a.atom_id])
    row = led.connection.execute(
        "SELECT decision_id, outcome, confidence, conformal_set, tier, cost_tokens, "
        "deferred_to_human, quarantined, rationale, prov_atoms FROM decision"
    ).fetchone()
    assert row[0] == "er:42"
    assert row[1] == "yes"
    assert row[2] == pytest.approx(0.97)
    assert json.loads(row[3]) == ["yes"]
    assert row[4] == Tier.T1.value
    assert row[5] == 12
    assert (row[6], row[7]) == (0, 0)
    assert row[8] == "fs score above tau_high"
    assert json.loads(row[9]) == [a.atom_id]


def test_decisions_append_not_overwrite():
    led = SqliteLedger()
    r1 = DecisionResult("d1", "no", 0.4, ("no", "yes"), Tier.T0)
    r2 = DecisionResult("d1", "yes", 0.95, ("yes",), Tier.T3, cost_tokens=900)
    led.append_decision(r1)
    led.append_decision(r2)  # supersession = a second row, never an update
    n = led.connection.execute("SELECT COUNT(*) FROM decision WHERE decision_id='d1'").fetchone()[0]
    assert n == 2


# -------------------------------------------------------------------- cost


def test_record_cost_and_total():
    led = SqliteLedger()
    assert led.total_cost_tokens() == 0
    led.record_cost("er.match", 120)
    led.record_cost("er.match", 80)
    led.record_cost("strata.name", 45)
    assert led.total_cost_tokens() == 245
    n = led.connection.execute("SELECT COUNT(*) FROM cost").fetchone()[0]
    assert n == 3


def test_file_backed_ledger_persists_across_reopen(tmp_path):
    path = str(tmp_path / "ledger.db")
    a = make_cell_atom("s", "t", "r1", "c", 99)
    with SqliteLedger(path) as led:
        led.register_atoms([a])
        ref = led.intern(prov_prod([leaf(a.atom_id)]))
        led.append_artifact("art-x", "k", "{}", ref)
        led.record_cost("task", 7)
    with SqliteLedger(path) as led2:
        assert led2.get_atom(a.atom_id).value == 99
        assert led2.resolve(ref) == leaf(a.atom_id)
        assert led2.invalidate([a.atom_id]) == {"art-x"}
        assert led2.total_cost_tokens() == 7


def test_ledger_cost_meter_writes_through():
    led = SqliteLedger()
    meter = LedgerCostMeter(led)
    meter.record("anvil.synthesize", 300)
    meter.record("anvil.synthesize", 150)
    meter.record("lodestone.answer", 50)
    # in-memory counters (contracts.CostMeter behavior)
    assert meter.total_tokens == 500
    assert meter.tokens_by_task["anvil.synthesize"] == 450
    assert meter.calls_by_task["anvil.synthesize"] == 2
    # durable COST table agrees
    assert led.total_cost_tokens() == 500
    per_task = dict(
        led.connection.execute("SELECT task, SUM(tokens) FROM cost GROUP BY task").fetchall()
    )
    assert per_task == {"anvil.synthesize": 450, "lodestone.answer": 50}
