"""Wave 1 cross-module seam test (whitepaper §18.1 integration gate).

One pass over the aviation hero estate exercising every boundary built in
Wave 1, with real implementations on both sides of each seam:

    estates (fixtures)  ->  cdc.CsvConnector  ->  ledger.SqliteLedger   (M1 x M0)
    estates (dataframe) ->  profiling.profile_table                     (estate x M3)
    spine.DecisionSpine ->  ledger.HeuristicAdapter + SqliteLedger      (M2 x M0)

No fakes, no network: the ModelClient is the deterministic HeuristicAdapter
from M0 and one real SqliteLedger is shared across the CDC and spine legs,
so the spine decision's prov_atoms reference atoms genuinely registered by
ingestion.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ontoforge.cdc import CsvConnector, ingest
from ontoforge.contracts import DecisionKind, DecisionRequest, SpineProfile, Tier
from ontoforge.estates import load_estate
from ontoforge.estates.aviation import TABLES, default_fixtures_dir
from ontoforge.ledger import HeuristicAdapter, SqliteLedger
from ontoforge.profiling import profile_table
from ontoforge.spine import DecisionSpine

FAA_TABLE = "faa_master"
N_NUMBER = "N-NUMBER"  # actual fixture column name (FAA layout)


# --------------------------------------------------------------------------
# shared fixtures: load once, ingest once, profile once (module scope)
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def estate():
    return load_estate()


@pytest.fixture(scope="module")
def ledger_and_batch(estate):
    """Real SqliteLedger holding the cold-pull ingestion of faa_master."""
    meta = TABLES[FAA_TABLE]
    connector = CsvConnector(
        source_id=meta["source_id"],
        path=default_fixtures_dir() / meta["file"],
        key_columns=meta["key_columns"],
        object_name=FAA_TABLE,
    )
    ledger = SqliteLedger(":memory:")
    batch, state = ingest(connector, ledger, None)
    yield ledger, batch, state
    ledger.close()


@pytest.fixture(scope="module")
def faa_profile(estate):
    """M3 profile of the wart-preserving estate dataframe (strings, blanks kept)."""
    df = estate["tables"][FAA_TABLE]
    return profile_table(df, TABLES[FAA_TABLE]["source_id"], FAA_TABLE)


# --------------------------------------------------------------------------
# (a) estate loads
# --------------------------------------------------------------------------


def test_estate_loads_all_tables_with_gold(estate):
    assert estate["name"] == "aviation"
    assert set(estate["tables"]) == set(TABLES)
    for name, df in estate["tables"].items():
        assert len(df) > 0, f"{name} is empty"
    df = estate["tables"][FAA_TABLE]
    assert N_NUMBER in df.columns
    assert len(df) == 2500
    # wart preservation: all-string load, blanks kept, no NaN coercion
    assert not df.isna().any().any()
    assert df.map(lambda v: isinstance(v, str)).all().all()
    for artifact in estate["metadata"]["gold"].values():
        assert Path(artifact).is_file(), f"missing gold artifact {artifact}"


# --------------------------------------------------------------------------
# (b) CDC ingestion into the real ledger (M1 x M0)
# --------------------------------------------------------------------------


def test_cold_pull_registers_every_cell_atom(estate, ledger_and_batch):
    ledger, batch, state = ledger_and_batch
    df = estate["tables"][FAA_TABLE]
    n_cells = df.shape[0] * df.shape[1]

    assert batch.cycle == 1
    assert batch.source_id == TABLES[FAA_TABLE]["source_id"]
    assert len(batch.deltas) == n_cells
    assert all(d.kind == "insert" for d in batch.deltas)

    (count,) = ledger.connection.execute("SELECT COUNT(*) FROM atom").fetchone()
    assert count == n_cells  # every cell URI is distinct -> no dedup collapse

    # round-trip one atom through the ledger
    first = batch.deltas[0].atom
    got = ledger.get_atom(first.atom_id)
    assert got is not None
    assert got.uri == first.uri

    # new_state is JSON-able and replayable (caller persists it)
    json.dumps(state)
    assert state["cycle"] == 1


def test_warm_pull_of_unchanged_csv_is_empty_and_adds_no_atoms(ledger_and_batch):
    ledger, _, state = ledger_and_batch
    meta = TABLES[FAA_TABLE]
    connector = CsvConnector(
        source_id=meta["source_id"],
        path=default_fixtures_dir() / meta["file"],
        key_columns=meta["key_columns"],
        object_name=FAA_TABLE,
    )
    (before,) = ledger.connection.execute("SELECT COUNT(*) FROM atom").fetchone()
    batch2, state2 = ingest(connector, ledger, state)
    assert batch2.cycle == 2
    assert batch2.deltas == []
    (after,) = ledger.connection.execute("SELECT COUNT(*) FROM atom").fetchone()
    assert after == before


# --------------------------------------------------------------------------
# (c) M3 profiling of the estate dataframe
# --------------------------------------------------------------------------


def test_profile_candidate_keys_include_n_number(faa_profile):
    tp = faa_profile
    assert tp.row_count == 2500
    assert N_NUMBER in tp.columns

    n_number_keys = [key for key in tp.candidate_keys if N_NUMBER in key]
    assert n_number_keys, (
        f"no candidate key involves {N_NUMBER!r}; keys={tp.candidate_keys!r}"
    )
    # the estate plants 8 reused tail numbers (temporal reuse trap), so
    # N-NUMBER alone must NOT be unique — every key through it is composite.
    assert (N_NUMBER,) not in tp.candidate_keys
    assert all(len(key) >= 2 for key in n_number_keys)


# --------------------------------------------------------------------------
# (d) spine decision through HeuristicAdapter, landing in the same ledger
# --------------------------------------------------------------------------


def test_spine_decision_via_heuristic_adapter_lands_in_ledger(ledger_and_batch):
    ledger, batch, _ = ledger_and_batch
    calls: list[str] = []

    def adjudicate_er(req):
        calls.append(req.task)
        return {"choice": "yes", "confidence": 0.99}

    client = HeuristicAdapter({"spine.adjudicate.er": adjudicate_er})
    spine = DecisionSpine(SpineProfile(name="economy"), client, ledger=ledger)

    prov = (batch.deltas[0].atom.atom_id, batch.deltas[1].atom.atom_id)
    req = DecisionRequest(
        kind=DecisionKind.ER,
        decision_id="wave1-seam-er-1",
        candidates=("no", "yes"),
        features=(("s", 0.5),),  # uncalibrated heuristic -> ambiguous -> escalate
        context=(("note", "wave1 seam: do these registry rows co-refer?"),),
        prov_atoms=prov,
    )
    result = spine.decide(req)

    # escalation really went through the M0 HeuristicAdapter
    assert calls == ["spine.adjudicate.er"]
    assert result.outcome == "yes"
    assert result.tier == Tier.T2
    assert not result.deferred_to_human
    assert not result.quarantined
    assert result.confidence >= 0.92  # cleared tau_high at T2

    row = ledger.connection.execute(
        "SELECT outcome, tier, cost_tokens, quarantined, prov_atoms FROM decision "
        "WHERE decision_id = ?",
        (req.decision_id,),
    ).fetchone()
    assert row is not None, "decision did not land in the ledger decision table"
    assert row[0] == result.outcome
    assert row[1] == int(result.tier.value)
    assert row[2] == result.cost_tokens
    assert row[3] == int(result.quarantined)
    assert json.loads(row[4]) == list(prov)

    cost_rows = ledger.connection.execute(
        "SELECT task, tokens FROM cost WHERE task = 'spine.decide.er'"
    ).fetchall()
    assert len(cost_rows) == 1
    assert cost_rows[0][1] == result.cost_tokens == spine.spent_tokens()
