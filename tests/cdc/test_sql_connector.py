"""SqlConnector against a REAL in-memory SQLite engine (fully offline, stdlib sqlite3 + sqlalchemy).

A known schema yields the expected cell atoms (same URI grammar as CsvConnector),
and a second pull after a mutation emits exactly the right delta. Also covers
introspected primary keys, chunked pulls, keyless tables, typed values, the RAW
mirror round-trip, and the missing-driver error path.
"""

from __future__ import annotations

import json

import pytest

sa = pytest.importorskip("sqlalchemy")

from ontoforge.cdc import RawMirror, SqlConnector, ingest  # noqa: E402
from ontoforge.contracts import Atom, cell_uri  # noqa: E402


def _engine():
    """A single shared in-memory SQLite engine (StaticPool => one DB across connections)."""
    return sa.create_engine("sqlite://", poolclass=sa.pool.StaticPool)


def _exec(engine, *statements: str) -> None:
    with engine.begin() as conn:
        for s in statements:
            conn.execute(sa.text(s))


def _seed_items(engine) -> None:
    _exec(
        engine,
        "CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT, qty INTEGER)",
        "INSERT INTO items VALUES (1, 'bolt', 10)",
        "INSERT INTO items VALUES (2, 'nut', 20)",
    )


def test_first_pull_expected_atoms_with_cell_uris():
    engine = _engine()
    _seed_items(engine)
    conn = SqlConnector("db1", "sqlite://", "items", key_columns=["id"], engine=engine)
    batch, state = conn.pull(None)

    assert batch.source_id == "db1"
    assert batch.cycle == 1
    assert len(batch.deltas) == 6  # 2 rows x 3 cols
    assert all(d.kind == "insert" for d in batch.deltas)

    by_uri = {d.atom.uri: d.atom.value for d in batch.deltas}
    assert by_uri[cell_uri("db1", "items", "1", "name")] == "bolt"
    assert by_uri[cell_uri("db1", "items", "2", "qty")] == 20  # typed int preserved
    # row key uses value_repr(int) == "1", so URIs match CSV/Parquet for the same key
    assert cell_uri("db1", "items", "1", "id") in by_uri


def test_second_pull_emits_delta_on_change():
    engine = _engine()
    _seed_items(engine)
    conn = SqlConnector("db1", "sqlite://", "items", key_columns=["id"], engine=engine)
    batch1, state1 = conn.pull(None)
    old = {d.atom.uri: d.atom for d in batch1.deltas}

    # no-op repull: zero deltas, cycle advances
    batch_noop, state_noop = conn.pull(state1)
    assert batch_noop.deltas == []
    assert batch_noop.cycle == 2

    # mutate one cell
    _exec(engine, "UPDATE items SET qty = 25 WHERE id = 2")
    batch2, _ = conn.pull(state_noop)
    assert len(batch2.deltas) == 1
    d = batch2.deltas[0]
    assert d.kind == "update"
    assert d.atom.uri == cell_uri("db1", "items", "2", "qty")
    assert d.atom.value == 25
    assert d.superseded_atom_id == old[d.atom.uri].atom_id
    assert d.atom.atom_id != old[d.atom.uri].atom_id


def test_insert_and_delete_rows():
    engine = _engine()
    _seed_items(engine)
    conn = SqlConnector("db1", "sqlite://", "items", key_columns=["id"], engine=engine)
    _, state = conn.pull(None)

    _exec(engine, "INSERT INTO items VALUES (3, 'washer', 7)", "DELETE FROM items WHERE id = 1")
    batch, _ = conn.pull(state)
    kinds = sorted(d.kind for d in batch.deltas)
    assert kinds == ["delete", "delete", "delete", "insert", "insert", "insert"]
    inserts = {d.atom.uri for d in batch.deltas if d.kind == "insert"}
    deletes = {d.atom.uri for d in batch.deltas if d.kind == "delete"}
    assert all("/3#" in u for u in inserts)
    assert all("/1#" in u for u in deletes)
    assert all(d.atom.value is None for d in batch.deltas if d.kind == "delete")


def test_introspected_primary_key_used_when_keys_omitted():
    engine = _engine()
    _seed_items(engine)
    # key_columns omitted entirely -> introspect PK ('id')
    conn = SqlConnector("db1", "sqlite://", "items", engine=engine)
    batch, _ = conn.pull(None)
    assert conn.key_columns == ["id"]
    uris = {d.atom.uri for d in batch.deltas}
    assert cell_uri("db1", "items", "1", "name") in uris  # keyed by id, not content-addressed


def test_keyless_table_falls_back_to_content_addressed_rows():
    engine = _engine()
    _exec(
        engine,
        "CREATE TABLE log (event TEXT, level TEXT)",
        "INSERT INTO log VALUES ('boot', 'info')",
        "INSERT INTO log VALUES ('crash', 'error')",
    )
    conn = SqlConnector("db1", "sqlite://", "log", engine=engine)
    batch, _ = conn.pull(None)
    assert conn.key_columns == []  # no PK introspected
    assert all("/row-" in d.atom.uri for d in batch.deltas)


def test_typed_values_and_nulls():
    engine = _engine()
    _exec(
        engine,
        "CREATE TABLE m (id INTEGER PRIMARY KEY, ratio REAL, note TEXT)",
        "INSERT INTO m VALUES (1, 1.5, NULL)",
        "INSERT INTO m VALUES (2, 2.25, '')",
    )
    conn = SqlConnector("db1", "sqlite://", "m", engine=engine)
    batch, _ = conn.pull(None)
    vals = {d.atom.uri: d.atom.value for d in batch.deltas}
    assert vals[cell_uri("db1", "m", "1", "ratio")] == 1.5
    assert vals[cell_uri("db1", "m", "1", "note")] is None
    assert vals[cell_uri("db1", "m", "2", "note")] == ""
    # null vs "" are distinct atoms at congruent coordinates
    a_null = Atom(uri=cell_uri("db1", "m", "1", "note"), value=None)
    a_empty = Atom(uri=cell_uri("db1", "m", "2", "note"), value="")
    assert a_null.atom_id != a_empty.atom_id


def test_chunked_pull_is_chunk_size_invariant():
    engine = _engine()
    _exec(engine, "CREATE TABLE big (id INTEGER PRIMARY KEY, v TEXT)")
    with engine.begin() as conn_:
        for i in range(250):
            conn_.execute(sa.text("INSERT INTO big VALUES (:id, :v)"), {"id": i, "v": f"r{i}"})

    a = SqlConnector("db1", "sqlite://", "big", engine=engine, chunk_size=7)
    b = SqlConnector("db1", "sqlite://", "big", engine=engine, chunk_size=1000)
    ba, _ = a.pull(None)
    bb, _ = b.pull(None)
    assert len(ba.deltas) == 250 * 2
    assert {(d.atom.uri, d.atom.atom_id) for d in ba.deltas} == {
        (d.atom.uri, d.atom.atom_id) for d in bb.deltas
    }


def test_state_is_json_roundtrippable():
    engine = _engine()
    _seed_items(engine)
    conn = SqlConnector("db1", "sqlite://", "items", key_columns=["id"], engine=engine)
    _, state = conn.pull(None)
    state_rt = json.loads(json.dumps(state))
    batch2, _ = conn.pull(state_rt)
    assert batch2.deltas == []


def test_raw_mirror_round_trip(tmp_path):
    from cdc_helpers import FakeLedger

    engine = _engine()
    _seed_items(engine)
    conn = SqlConnector("db1", "sqlite://", "items", key_columns=["id"], engine=engine)
    mirror = RawMirror(tmp_path / "data")
    ingest(conn, FakeLedger(), None, mirror=mirror, pulled_at="2026-06-15T00:00:00Z")

    back = mirror.read_snapshot("db1", "items")
    assert back.column_names == ["id", "name", "qty"]
    assert back.to_pylist() == [
        {"id": 1, "name": "bolt", "qty": 10},
        {"id": 2, "name": "nut", "qty": 20},
    ]


def test_raw_mirror_byte_stable_for_unchanged_data(tmp_path):
    from cdc_helpers import FakeLedger

    engine = _engine()
    _seed_items(engine)
    conn = SqlConnector("db1", "sqlite://", "items", key_columns=["id"], engine=engine)
    mirror = RawMirror(tmp_path / "data")
    ledger = FakeLedger()

    _, state = ingest(conn, ledger, None, mirror=mirror, pulled_at="t1")
    obj_dir = tmp_path / "data" / "raw" / "db1" / "items"
    files1 = sorted(p.name for p in obj_dir.glob("*.parquet"))
    bytes1 = (obj_dir / files1[0]).read_bytes()

    ingest(conn, ledger, state, mirror=mirror, pulled_at="t2")
    files2 = sorted(p.name for p in obj_dir.glob("*.parquet"))
    assert files2 == files1  # content-addressed: unchanged data is never rewritten
    assert (obj_dir / files1[0]).read_bytes() == bytes1
    entries = mirror.manifest("db1", "items")
    assert [e["cycle"] for e in entries] == [1, 2]
    assert entries[0]["content_hash"] == entries[1]["content_hash"]


def test_missing_table_raises_clear_error():
    engine = _engine()
    conn = SqlConnector("db1", "sqlite://", "nope", engine=engine)
    with pytest.raises((ValueError, sa.exc.SQLAlchemyError)):
        conn.pull(None)


def test_missing_sqlalchemy_raises_actionable_error(monkeypatch):
    import builtins

    import ontoforge.cdc.sql as sqlmod

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "sqlalchemy" or name.startswith("sqlalchemy."):
            raise ImportError("no sqlalchemy")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(ImportError, match="connectors"):
        sqlmod._require_sqlalchemy()
