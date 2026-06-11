"""RAW mirror: lossless read-back (nulls vs empty strings distinct), byte-stability
for unchanged data, and (cycle, pulled_at) manifest metadata."""

from __future__ import annotations

import pyarrow as pa
import pyarrow.parquet as pq

from ontoforge.cdc import CsvConnector, ParquetConnector, RawMirror, ingest
from m1_helpers import FakeLedger, write_csv


def test_lossless_csv_mirror_nulls_vs_empty_distinct(tmp_path):
    src = tmp_path / "items.csv"
    # row 1: qty empty string; row 2: qty MISSING (short record -> None)
    src.write_text('id,name,qty\n1,bolt,""\n2,nut\n3,,5\n', encoding="utf-8")
    conn = CsvConnector("s1", src, key_columns=["id"])
    mirror = RawMirror(tmp_path / "data")
    ingest(conn, FakeLedger(), None, mirror=mirror, pulled_at="2026-06-11T00:00:00Z")

    table = mirror.read_snapshot("s1", "items")
    assert table.column_names == ["id", "name", "qty"]
    assert table.to_pylist() == [
        {"id": "1", "name": "bolt", "qty": ""},
        {"id": "2", "name": "nut", "qty": None},
        {"id": "3", "name": "", "qty": "5"},
    ]


def test_lossless_parquet_mirror_exact_table(tmp_path):
    src = tmp_path / "t.parquet"
    table = pa.table(
        {
            "id": pa.array([1, 2, 3], type=pa.int64()),
            "note": pa.array([None, "", "x"], type=pa.string()),
            "w": pa.array([0.5, None, 2.0], type=pa.float64()),
        }
    )
    pq.write_table(table, src)
    conn = ParquetConnector("s2", src, key_columns=["id"])
    mirror = RawMirror(tmp_path / "data")
    ingest(conn, FakeLedger(), None, mirror=mirror, pulled_at="2026-06-11T00:00:00Z")

    back = mirror.read_snapshot("s2", "t")
    assert back.equals(table)  # exact: schema, nulls, empty strings, values


def test_unchanged_data_is_byte_stable_single_file(tmp_path):
    src = tmp_path / "items.csv"
    write_csv(src, ["id", "v"], [["1", "a"], ["2", "b"]])
    conn = CsvConnector("s1", src, key_columns=["id"])
    mirror = RawMirror(tmp_path / "data")
    ledger = FakeLedger()

    _, state = ingest(conn, ledger, None, mirror=mirror, pulled_at="2026-06-11T00:00:00Z")
    obj_dir = tmp_path / "data" / "raw" / "s1" / "items"
    files1 = sorted(p.name for p in obj_dir.glob("*.parquet"))
    bytes1 = (obj_dir / files1[0]).read_bytes()

    # second pull, nothing changed: same single parquet file, byte-identical
    ingest(conn, ledger, state, mirror=mirror, pulled_at="2026-06-12T00:00:00Z")
    files2 = sorted(p.name for p in obj_dir.glob("*.parquet"))
    assert files2 == files1
    assert (obj_dir / files1[0]).read_bytes() == bytes1

    entries = mirror.manifest("s1", "items")
    assert [e["cycle"] for e in entries] == [1, 2]
    assert entries[0]["content_hash"] == entries[1]["content_hash"]
    assert entries[0]["pulled_at"] == "2026-06-11T00:00:00Z"
    assert entries[1]["pulled_at"] == "2026-06-12T00:00:00Z"


def test_changed_data_new_content_file_and_cycle_addressable(tmp_path):
    src = tmp_path / "items.csv"
    write_csv(src, ["id", "v"], [["1", "a"]])
    conn = CsvConnector("s1", src, key_columns=["id"])
    mirror = RawMirror(tmp_path / "data")
    ledger = FakeLedger()
    _, state = ingest(conn, ledger, None, mirror=mirror, pulled_at="t1")

    write_csv(src, ["id", "v"], [["1", "CHANGED"]])
    ingest(conn, ledger, state, mirror=mirror, pulled_at="t2")

    obj_dir = tmp_path / "data" / "raw" / "s1" / "items"
    assert len(list(obj_dir.glob("*.parquet"))) == 2
    snap1 = mirror.read_snapshot("s1", "items", cycle=1)
    snap2 = mirror.read_snapshot("s1", "items", cycle=2)
    assert snap1.to_pylist()[0]["v"] == "a"
    assert snap2.to_pylist()[0]["v"] == "CHANGED"
    # default read = latest
    assert mirror.read_snapshot("s1", "items").equals(snap2)
