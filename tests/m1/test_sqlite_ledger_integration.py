"""M1 <-> M0 integration: ingest a real CSV into the real SqliteLedger.

The other M1 tests use a FakeLedger because M0 was being built in parallel;
this file exercises the completed ``ontoforge.ledger.SqliteLedger`` end to end:
dedup-on-content across two cold pulls of unchanged data, value/uri fidelity
through ``get_atom``, supersession on change, and tombstone exclusion.
"""

from __future__ import annotations

import pytest

from ontoforge.cdc import CsvConnector, ingest
from ontoforge.contracts import cell_uri
from ontoforge.ledger import SqliteLedger

from m1_helpers import write_csv


@pytest.fixture
def ledger() -> SqliteLedger:
    led = SqliteLedger()  # in-memory: deterministic, no network, no files
    yield led
    led.close()


def _atom_count(led: SqliteLedger) -> int:
    return led.connection.execute("SELECT COUNT(*) FROM atom").fetchone()[0]


def test_two_unchanged_pulls_dedup_on_content(tmp_path, ledger):
    src = tmp_path / "fleet.csv"
    write_csv(src, ["tail", "model", "hours"], [["N100", "A320", "1200"], ["N200", "B737", ""]])

    # pull 1: cold (state=None) -> 6 inserts, 6 atom rows in the ledger
    batch1, _ = ingest(CsvConnector("fleet", src, key_columns=["tail"]), ledger, None)
    assert len(batch1.deltas) == 6
    assert _atom_count(ledger) == 6

    # pull 2: cold AGAIN over unchanged data (fresh connector, no state) ->
    # the same 6 atoms are re-registered; content addressing must dedup them.
    batch2, _ = ingest(CsvConnector("fleet", src, key_columns=["tail"]), ledger, None)
    assert {d.atom.atom_id for d in batch2.deltas} == {d.atom.atom_id for d in batch1.deltas}
    assert _atom_count(ledger) == 6  # no new rows: dedup-on-content (M0 invariant)

    # round-trip fidelity through the real ledger, incl. the empty-string cell
    hours_id = next(
        d.atom.atom_id for d in batch1.deltas
        if d.atom.uri == cell_uri("fleet", "fleet", "N200", "hours")
    )
    stored = ledger.get_atom(hours_id)
    assert stored is not None
    assert stored.uri == cell_uri("fleet", "fleet", "N200", "hours")
    assert stored.value == ""  # empty string preserved, not collapsed to NULL
    assert stored.atom_id == hours_id


def test_warm_pull_change_appends_only_the_new_atom(tmp_path, ledger):
    src = tmp_path / "fleet.csv"
    write_csv(src, ["tail", "hours"], [["N100", "1200"], ["N200", "900"]])
    conn = CsvConnector("fleet", src, key_columns=["tail"])
    batch1, state = ingest(conn, ledger, None)
    assert _atom_count(ledger) == 4

    # warm no-op pull: zero deltas, zero new ledger rows
    batch2, state = ingest(conn, ledger, state)
    assert batch2.deltas == []
    assert _atom_count(ledger) == 4

    # one cell changes + one row vanishes: exactly ONE new atom row (the update);
    # delete tombstones are never registered (they carry no value)
    write_csv(src, ["tail", "hours"], [["N100", "1300"]])
    batch3, _ = ingest(conn, ledger, state)
    assert sorted(d.kind for d in batch3.deltas) == ["delete", "delete", "update"]
    assert _atom_count(ledger) == 5

    upd = next(d for d in batch3.deltas if d.kind == "update")
    assert ledger.get_atom(upd.atom.atom_id).value == "1300"
    # the superseded atom is still in the ledger, append-only (M0 invariant)
    old = ledger.get_atom(upd.superseded_atom_id)
    assert old is not None and old.value == "1200"
    # changed_atom_ids (the invalidation key set) all resolve in the ledger
    assert all(ledger.get_atom(a) is not None for a in batch3.changed_atom_ids)
