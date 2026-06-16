"""LargeCsvConnector: chunked, constant-memory streaming with delta correctness.

Generates CSVs and asserts: (1) chunked streaming actually chunks (chunk count is
ceil(rows/chunk_size); a chunk never exceeds chunk_size); (2) output is identical
to CsvConnector at any chunk_size (atoms, atom_ids, deltas); (3) deltas reconstruct
the new snapshot exactly across mutation cycles; (4) the RAW mirror round-trips.
Fully offline — only generated local files.
"""

from __future__ import annotations

import csv
import json
import random

import pytest

from ontoforge.cdc import CsvConnector, LargeCsvConnector, RawMirror, ingest
from ontoforge.contracts import Atom, cell_uri


def _write_csv(path, header, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def _gen_csv(path, n, header=("id", "a", "b")):
    rows = [[str(i), f"a{i}", f"b{i}"] for i in range(n)]
    _write_csv(path, list(header), rows)
    return rows


def test_streaming_chunks_bound_size(tmp_path):
    path = tmp_path / "big.csv"
    _gen_csv(path, 100)
    conn = LargeCsvConnector("s", path, key_columns=["id"], chunk_size=15)
    chunks = list(conn.stream_chunks())
    # 100 rows / 15 per chunk -> 7 chunks (6x15 + 1x10)
    assert len(chunks) == 7
    assert all(len(c[1]) <= 15 for c in chunks)
    assert sum(len(c[1]) for c in chunks) == 100
    # every chunk reports the same column order
    assert all(c[0] == ["id", "a", "b"] for c in chunks)


def test_single_chunk_when_chunk_size_exceeds_rows(tmp_path):
    path = tmp_path / "small.csv"
    _gen_csv(path, 10)
    conn = LargeCsvConnector("s", path, key_columns=["id"], chunk_size=10_000)
    chunks = list(conn.stream_chunks())
    assert len(chunks) == 1 and len(chunks[0][1]) == 10


@pytest.mark.parametrize("chunk_size", [1, 7, 100, 10_000])
def test_output_identical_to_csv_connector_at_any_chunk_size(tmp_path, chunk_size):
    path = tmp_path / "data.csv"
    _gen_csv(path, 123)
    large, _ = LargeCsvConnector("s", path, key_columns=["id"], chunk_size=chunk_size).pull(None)
    small, _ = CsvConnector("s", path, key_columns=["id"]).pull(None)
    assert {(d.kind, d.atom.uri, d.atom.atom_id, d.atom.value) for d in large.deltas} == {
        (d.kind, d.atom.uri, d.atom.atom_id, d.atom.value) for d in small.deltas
    }


def test_first_pull_all_inserts(tmp_path):
    path = tmp_path / "data.csv"
    _gen_csv(path, 50)
    batch, _ = LargeCsvConnector("s", path, key_columns=["id"], chunk_size=8).pull(None)
    assert len(batch.deltas) == 50 * 3
    assert all(d.kind == "insert" for d in batch.deltas)


def test_noop_repull_and_single_cell_update(tmp_path):
    path = tmp_path / "data.csv"
    _gen_csv(path, 30)
    conn = LargeCsvConnector("s", path, key_columns=["id"], chunk_size=8)
    batch1, state1 = conn.pull(None)
    old = {d.atom.uri: d.atom for d in batch1.deltas}

    b_noop, s_noop = conn.pull(state1)
    assert b_noop.deltas == []
    assert b_noop.cycle == 2

    rows = [[str(i), f"a{i}", f"b{i}"] for i in range(30)]
    rows[17][1] = "CHANGED"
    _write_csv(path, ["id", "a", "b"], rows)
    batch2, _ = conn.pull(s_noop)
    assert len(batch2.deltas) == 1
    d = batch2.deltas[0]
    assert d.kind == "update"
    assert d.atom.uri == cell_uri("s", "data", "17", "a")
    assert d.atom.value == "CHANGED"
    assert d.superseded_atom_id == old[d.atom.uri].atom_id


def test_row_delete_tombstones_each_cell(tmp_path):
    path = tmp_path / "data.csv"
    _gen_csv(path, 10)
    conn = LargeCsvConnector("s", path, key_columns=["id"], chunk_size=4)
    _, state = conn.pull(None)

    rows = [[str(i), f"a{i}", f"b{i}"] for i in range(10) if i != 3]
    _write_csv(path, ["id", "a", "b"], rows)
    batch, _ = conn.pull(state)
    assert len(batch.deltas) == 3
    assert all(d.kind == "delete" and d.atom.value is None for d in batch.deltas)
    assert all("/3#" in d.atom.uri for d in batch.deltas)


def _expected_cell_map(path):
    with open(path, newline="", encoding="utf-8") as f:
        recs = [r for r in csv.reader(f) if r]
    header = recs[0]
    out = {}
    for rec in recs[1:]:
        row = {c: (rec[i] if i < len(rec) else None) for i, c in enumerate(header)}
        rk = row["id"]
        for c in header:
            out[cell_uri("fuzz", "data", rk, c)] = row[c]
    return out


def _apply(old_map, batch):
    new_map = dict(old_map)
    for d in batch.deltas:
        if d.kind == "insert":
            assert d.atom.uri not in new_map
            new_map[d.atom.uri] = d.atom.value
        elif d.kind == "update":
            assert d.atom.uri in new_map
            old_value = new_map[d.atom.uri]
            assert d.superseded_atom_id == Atom(uri=d.atom.uri, value=old_value).atom_id
            new_map[d.atom.uri] = d.atom.value
        elif d.kind == "delete":
            old_value = new_map.pop(d.atom.uri)
            assert d.superseded_atom_id == Atom(uri=d.atom.uri, value=old_value).atom_id
    return new_map


@pytest.mark.parametrize("seed", [0, 1, 2])
def test_mutation_fuzzing_reconstructs_state(tmp_path, seed):
    rng = random.Random(seed)
    path = tmp_path / "data.csv"
    header = ["id", "a", "b", "c"]

    def rand_val():
        return rng.choice(["", "x", "y", "zz", "1.5", "name,with,commas"])

    rows = {f"k{i}": [rand_val() for _ in header[1:]] for i in range(rng.randint(5, 12))}
    next_id = len(rows)

    def flush():
        _write_csv(path, header, [[k, *v] for k, v in rows.items()])

    flush()
    conn = LargeCsvConnector("fuzz", path, key_columns=["id"], chunk_size=rng.choice([1, 3, 7]))
    batch, state = conn.pull(None)
    cell_map = _apply({}, batch)
    assert cell_map == _expected_cell_map(path)

    for _ in range(6):
        for _ in range(rng.randint(0, 3)):
            rows[f"k{next_id}"] = [rand_val() for _ in header[1:]]
            next_id += 1
        for _ in range(rng.randint(0, 4)):
            if rows:
                rows[rng.choice(sorted(rows))][rng.randrange(len(header) - 1)] = rand_val()
        for _ in range(rng.randint(0, 2)):
            if rows:
                rows.pop(rng.choice(sorted(rows)))
        flush()
        batch, state = conn.pull(state)
        cell_map = _apply(cell_map, batch)
        assert cell_map == _expected_cell_map(path), f"divergence cycle {batch.cycle}"
        state = json.loads(json.dumps(state))


def test_raw_mirror_round_trip_and_byte_stable(tmp_path):
    from cdc_helpers import FakeLedger

    path = tmp_path / "data.csv"
    _gen_csv(path, 20)
    conn = LargeCsvConnector("s", path, key_columns=["id"], chunk_size=6)
    mirror = RawMirror(tmp_path / "raw")
    ledger = FakeLedger()

    _, state = ingest(conn, ledger, None, mirror=mirror, pulled_at="t1")
    back = mirror.read_snapshot("s", "data")
    assert back.num_rows == 20
    assert back.column_names == ["id", "a", "b"]
    assert back.to_pylist()[0] == {"id": "0", "a": "a0", "b": "b0"}

    obj_dir = tmp_path / "raw" / "raw" / "s" / "data"
    files1 = sorted(p.name for p in obj_dir.glob("*.parquet"))
    bytes1 = (obj_dir / files1[0]).read_bytes()

    # unchanged repull -> byte-identical mirror file (content addressing)
    ingest(conn, ledger, state, mirror=mirror, pulled_at="t2")
    files2 = sorted(p.name for p in obj_dir.glob("*.parquet"))
    assert files2 == files1
    assert (obj_dir / files1[0]).read_bytes() == bytes1


def test_empty_csv_yields_no_deltas(tmp_path):
    path = tmp_path / "empty.csv"
    path.write_text("", encoding="utf-8")
    batch, state = LargeCsvConnector("s", path, key_columns=["id"]).pull(None)
    assert batch.deltas == []
