"""ObjectStoreConnector against a LOCAL-FILESYSTEM fake bucket (fully offline).

A tmp directory stands in for an S3 bucket; CSV and Parquet objects are read via
the local fallback adapter (and via fsspec's LocalFileSystem when fsspec is present).
Atoms are byte-identical to the equivalent CsvConnector / ParquetConnector output,
and a second pull after the object is rewritten emits the right delta.
"""

from __future__ import annotations

import datetime
import json

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from ontoforge.cdc import (
    CsvConnector,
    ObjectStoreConnector,
    ParquetConnector,
    RawMirror,
    ingest,
)
from ontoforge.contracts import cell_uri


def _write_csv(path, header, rows):
    import csv

    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def _bucket(tmp_path):
    """A tmp dir standing in for an S3 bucket — never a live endpoint."""
    b = tmp_path / "bucket"
    b.mkdir()
    return b


def test_csv_object_first_pull_matches_file_connector(tmp_path):
    bucket = _bucket(tmp_path)
    key = bucket / "items.csv"
    _write_csv(key, ["id", "name", "qty"], [["1", "bolt", "10"], ["2", "nut", "20"]])

    obj = ObjectStoreConnector("os1", str(key), key_columns=["id"])
    file_conn = CsvConnector("os1", key, key_columns=["id"])
    bo, _ = obj.pull(None)
    bf, _ = file_conn.pull(None)
    assert {(d.atom.uri, d.atom.atom_id, d.atom.value) for d in bo.deltas} == {
        (d.atom.uri, d.atom.atom_id, d.atom.value) for d in bf.deltas
    }
    uris = {d.atom.uri for d in bo.deltas}
    assert cell_uri("os1", "items", "1", "name") in uris


def test_file_url_scheme_supported(tmp_path):
    bucket = _bucket(tmp_path)
    key = bucket / "items.csv"
    _write_csv(key, ["id", "v"], [["1", "a"]])
    obj = ObjectStoreConnector("os1", key.as_uri(), key_columns=["id"])  # file:// URL
    batch, _ = obj.pull(None)
    vals = {d.atom.uri: d.atom.value for d in batch.deltas}
    assert vals[cell_uri("os1", "items", "1", "v")] == "a"


def test_parquet_object_typed_values_match_file_connector(tmp_path):
    bucket = _bucket(tmp_path)
    key = bucket / "inv.parquet"
    table = pa.table(
        {
            "id": pa.array([1, 2], type=pa.int64()),
            "name": pa.array(["bolt", None], type=pa.string()),
            "qty": pa.array([1.5, 2.0], type=pa.float64()),
            "day": pa.array([datetime.date(2026, 1, 1), datetime.date(2026, 6, 15)], type=pa.date32()),
        }
    )
    pq.write_table(table, key)

    obj = ObjectStoreConnector("os1", str(key), key_columns=["id"])
    file_conn = ParquetConnector("os1", key, key_columns=["id"])
    bo, _ = obj.pull(None)
    bf, _ = file_conn.pull(None)
    assert {(d.atom.uri, d.atom.atom_id) for d in bo.deltas} == {
        (d.atom.uri, d.atom.atom_id) for d in bf.deltas
    }
    vals = {d.atom.uri: d.atom.value for d in bo.deltas}
    assert vals[cell_uri("os1", "inv", "1", "qty")] == 1.5
    assert vals[cell_uri("os1", "inv", "2", "name")] is None
    assert vals[cell_uri("os1", "inv", "2", "day")] == datetime.date(2026, 6, 15)


def test_second_pull_emits_delta_after_object_rewrite(tmp_path):
    bucket = _bucket(tmp_path)
    key = bucket / "items.csv"
    _write_csv(key, ["id", "name", "qty"], [["1", "bolt", "10"], ["2", "nut", "20"]])
    obj = ObjectStoreConnector("os1", str(key), key_columns=["id"])
    batch1, state1 = obj.pull(None)
    old = {d.atom.uri: d.atom for d in batch1.deltas}

    # no-op repull
    b_noop, s_noop = obj.pull(state1)
    assert b_noop.deltas == []

    # rewrite the object: change one cell
    _write_csv(key, ["id", "name", "qty"], [["1", "bolt", "10"], ["2", "nut", "25"]])
    batch2, _ = obj.pull(s_noop)
    assert len(batch2.deltas) == 1
    d = batch2.deltas[0]
    assert d.kind == "update"
    assert d.atom.uri == cell_uri("os1", "items", "2", "qty")
    assert d.atom.value == "25"
    assert d.superseded_atom_id == old[d.atom.uri].atom_id


def test_explicit_fmt_overrides_suffix(tmp_path):
    bucket = _bucket(tmp_path)
    key = bucket / "data"  # no suffix
    _write_csv(key, ["id", "v"], [["1", "a"]])
    obj = ObjectStoreConnector("os1", str(key), key_columns=["id"], fmt="csv", object_name="data")
    batch, _ = obj.pull(None)
    assert {d.atom.uri for d in batch.deltas} == {cell_uri("os1", "data", "1", c) for c in ("id", "v")}


def test_unknown_suffix_without_fmt_raises(tmp_path):
    bucket = _bucket(tmp_path)
    key = bucket / "data.bin"
    key.write_bytes(b"x")
    with pytest.raises(ValueError, match="infer format"):
        ObjectStoreConnector("os1", str(key))


def test_s3_url_without_fsspec_raises_actionable(monkeypatch, tmp_path):
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "fsspec" or name.startswith("fsspec."):
            raise ImportError("no fsspec")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    obj = ObjectStoreConnector("os1", "s3://bucket/key.csv", key_columns=["id"])
    with pytest.raises(ImportError, match="connectors"):
        obj.pull(None)


def test_raw_mirror_round_trip_local_bucket(tmp_path):
    from cdc_helpers import FakeLedger

    bucket = _bucket(tmp_path)
    key = bucket / "items.csv"
    # null vs empty distinction preserved through the mirror
    key.write_text('id,name,qty\n1,bolt,""\n2,nut\n', encoding="utf-8")
    obj = ObjectStoreConnector("os1", str(key), key_columns=["id"])
    mirror = RawMirror(tmp_path / "data")
    ingest(obj, FakeLedger(), None, mirror=mirror, pulled_at="2026-06-15T00:00:00Z")

    back = mirror.read_snapshot("os1", "items")
    assert back.to_pylist() == [
        {"id": "1", "name": "bolt", "qty": ""},
        {"id": "2", "name": "nut", "qty": None},
    ]


def test_state_json_roundtrippable(tmp_path):
    bucket = _bucket(tmp_path)
    key = bucket / "items.csv"
    _write_csv(key, ["id", "v"], [["1", "a"]])
    obj = ObjectStoreConnector("os1", str(key), key_columns=["id"])
    _, state = obj.pull(None)
    state_rt = json.loads(json.dumps(state))
    b2, _ = obj.pull(state_rt)
    assert b2.deltas == []
