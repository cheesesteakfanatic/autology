"""Ingest driver: registers batch atoms into anything implementing register_atoms
(contracts Ledger protocol, accepted structurally), tombstones excluded."""

from __future__ import annotations

from ontoforge.cdc import CsvConnector, DocConnector, RawMirror, ingest
from m1_helpers import write_csv


def test_ingest_registers_insert_and_update_atoms(tmp_path, fake_ledger):
    src = tmp_path / "items.csv"
    write_csv(src, ["id", "v"], [["1", "a"], ["2", "b"]])
    conn = CsvConnector("s1", src, key_columns=["id"])

    batch1, state = ingest(conn, fake_ledger, None)
    assert fake_ledger.register_calls == 1
    assert set(fake_ledger.atoms) == {d.atom.atom_id for d in batch1.deltas}
    # registered atoms carry their source values (the provenance leaves, constraint H)
    assert sorted(a.value for a in fake_ledger.atoms.values()) == ["1", "2", "a", "b"]

    # update one cell, delete one row: update atom registered, tombstone NOT
    write_csv(src, ["id", "v"], [["1", "A2"]])
    batch2, state = ingest(conn, fake_ledger, state)
    kinds = sorted(d.kind for d in batch2.deltas)
    assert kinds == ["delete", "delete", "update"]
    update_atom = next(d.atom for d in batch2.deltas if d.kind == "update")
    assert update_atom.atom_id in fake_ledger.atoms
    tombstone_ids = {d.atom.atom_id for d in batch2.deltas if d.kind == "delete"}
    assert not tombstone_ids & set(fake_ledger.atoms)
    # invalidation key set = superseded ids of the update and both deleted cells
    assert len(batch2.changed_atom_ids) == 3


def test_ingest_noop_pull_registers_nothing(tmp_path, fake_ledger):
    src = tmp_path / "items.csv"
    write_csv(src, ["id", "v"], [["1", "a"]])
    conn = CsvConnector("s1", src, key_columns=["id"])
    _, state = ingest(conn, fake_ledger, None)
    calls_before = fake_ledger.register_calls
    batch2, _ = ingest(conn, fake_ledger, state)
    assert batch2.deltas == []
    assert fake_ledger.register_calls == calls_before  # no empty register call


def test_ingest_doc_connector_and_mirror(tmp_path, fake_ledger):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "n.txt").write_text("Narrative para.\n\nSecond para.\n", encoding="utf-8")
    conn = DocConnector("d1", docs)
    mirror = RawMirror(tmp_path / "data")

    batch, _ = ingest(conn, fake_ledger, None, mirror=mirror, pulled_at="t0")
    assert {a.value for a in fake_ledger.atoms.values()} == {"Narrative para.", "Second para."}
    snap = mirror.read_snapshot("d1", "docs")
    assert snap.column_names == ["doc_path", "start", "end", "content"]
    assert [r["content"] for r in snap.to_pylist()] == ["Narrative para.", "Second para."]
    entries = mirror.manifest("d1", "docs")
    assert entries[0]["cycle"] == batch.cycle == 1 and entries[0]["pulled_at"] == "t0"


def test_dedup_on_content_reregistration_is_noop(tmp_path, fake_ledger):
    src = tmp_path / "items.csv"
    write_csv(src, ["id", "v"], [["1", "a"]])
    # two fresh connectors, no state: same atoms registered twice -> dedup to same ids
    ingest(CsvConnector("s1", src, key_columns=["id"]), fake_ledger, None)
    n = len(fake_ledger.atoms)
    ingest(CsvConnector("s1", src, key_columns=["id"]), fake_ledger, None)
    assert len(fake_ledger.atoms) == n
