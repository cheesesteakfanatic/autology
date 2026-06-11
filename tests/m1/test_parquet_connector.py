"""ParquetConnector: same delta contract as CSV, over typed Parquet data."""

from __future__ import annotations

import datetime

import pyarrow as pa
import pyarrow.parquet as pq

from ontoforge.cdc import ParquetConnector
from ontoforge.contracts import cell_uri


def write_parquet(path, ids, names, qtys, dates):
    table = pa.table(
        {
            "id": pa.array(ids, type=pa.int64()),
            "name": pa.array(names, type=pa.string()),
            "qty": pa.array(qtys, type=pa.float64()),
            "day": pa.array(dates, type=pa.date32()),
        }
    )
    pq.write_table(table, path)
    return table


def test_first_pull_inserts_typed_values(tmp_path):
    path = tmp_path / "inv.parquet"
    write_parquet(
        path,
        [1, 2],
        ["bolt", None],
        [1.5, 2.0],
        [datetime.date(2026, 1, 1), datetime.date(2026, 6, 11)],
    )
    conn = ParquetConnector("psrc", path, key_columns=["id"])
    batch, state = conn.pull(None)

    assert batch.cycle == 1
    assert len(batch.deltas) == 8
    assert all(d.kind == "insert" for d in batch.deltas)
    vals = {d.atom.uri: d.atom.value for d in batch.deltas}
    assert vals[cell_uri("psrc", "inv", "1", "qty")] == 1.5
    assert vals[cell_uri("psrc", "inv", "2", "name")] is None
    assert vals[cell_uri("psrc", "inv", "2", "day")] == datetime.date(2026, 6, 11)


def test_single_typed_cell_change_one_update(tmp_path):
    path = tmp_path / "inv.parquet"
    write_parquet(path, [1, 2], ["bolt", "nut"], [1.5, 2.0], [datetime.date(2026, 1, 1)] * 2)
    conn = ParquetConnector("psrc", path, key_columns=["id"])
    batch1, state1 = conn.pull(None)
    old = {d.atom.uri: d.atom for d in batch1.deltas}

    write_parquet(path, [1, 2], ["bolt", "nut"], [1.5, 2.25], [datetime.date(2026, 1, 1)] * 2)
    batch2, _ = conn.pull(state1)
    assert len(batch2.deltas) == 1
    d = batch2.deltas[0]
    assert d.kind == "update"
    assert d.atom.uri == cell_uri("psrc", "inv", "2", "qty")
    assert d.atom.value == 2.25
    assert d.superseded_atom_id == old[d.atom.uri].atom_id


def test_none_and_empty_string_distinct(tmp_path):
    path = tmp_path / "s.parquet"
    table = pa.table(
        {
            "id": pa.array([1, 2], type=pa.int64()),
            "note": pa.array([None, ""], type=pa.string()),
        }
    )
    pq.write_table(table, path)
    conn = ParquetConnector("psrc", path, key_columns=["id"])
    batch, _ = conn.pull(None)
    vals = {d.atom.uri: d.atom.value for d in batch.deltas}
    assert vals[cell_uri("psrc", "s", "1", "note")] is None
    assert vals[cell_uri("psrc", "s", "2", "note")] == ""
    atoms = {d.atom.uri: d.atom.atom_id for d in batch.deltas}
    assert atoms[cell_uri("psrc", "s", "1", "note")] != atoms[cell_uri("psrc", "s", "2", "note")]

    # null -> "" flip at the SAME cell is a real update
    table2 = pa.table(
        {"id": pa.array([1, 2], type=pa.int64()), "note": pa.array(["", ""], type=pa.string())}
    )
    pq.write_table(table2, path)
    _, state1 = ParquetConnector("psrc", path, key_columns=["id"]).pull(None)
    pq.write_table(table, path)
    batch3, _ = ParquetConnector("psrc", path, key_columns=["id"]).pull(state1)
    assert [d.kind for d in batch3.deltas] == ["update"]
    assert batch3.deltas[0].atom.value is None


def test_repull_stability_parquet(tmp_path):
    path = tmp_path / "inv.parquet"
    write_parquet(path, [1], ["bolt"], [1.5], [datetime.date(2026, 1, 1)])
    c1 = ParquetConnector("psrc", path, key_columns=["id"])
    batch1, state1 = c1.pull(None)
    batch2, _ = c1.pull(state1)
    assert batch2.deltas == []
    batch3, _ = ParquetConnector("psrc", path, key_columns=["id"]).pull(None)
    assert {(d.atom.uri, d.atom.atom_id) for d in batch3.deltas} == {
        (d.atom.uri, d.atom.atom_id) for d in batch1.deltas
    }
