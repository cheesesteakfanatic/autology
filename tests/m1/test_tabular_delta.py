"""CSV connector delta semantics: first-pull inserts, per-cell update granularity,
deletes, keyless rows, URI stability, JSON-able state, and mutation fuzzing
(whitepaper §11.2 M1 tests: delta completeness, atom-URI stability fuzzing)."""

from __future__ import annotations

import json
import random

import pytest

from ontoforge.cdc import CsvConnector
from ontoforge.contracts import Atom, cell_uri

from m1_helpers import write_csv


def make_csv(tmp_path, rows, header=("id", "name", "qty")):
    path = tmp_path / "items.csv"
    write_csv(path, list(header), rows)
    return path


def test_first_pull_all_inserts_with_expected_uris(tmp_path):
    path = make_csv(tmp_path, [["1", "bolt", "10"], ["2", "nut", "20"]])
    conn = CsvConnector("src1", path, key_columns=["id"])
    batch, state = conn.pull(None)

    assert batch.source_id == "src1"
    assert batch.cycle == 1
    assert len(batch.deltas) == 6  # 2 rows x 3 columns
    assert all(d.kind == "insert" for d in batch.deltas)
    assert all(d.superseded_atom_id == "" for d in batch.deltas)

    uris = {d.atom.uri for d in batch.deltas}
    assert cell_uri("src1", "items", "1", "name") in uris
    assert cell_uri("src1", "items", "2", "qty") in uris
    # values land on the right atoms
    by_uri = {d.atom.uri: d.atom.value for d in batch.deltas}
    assert by_uri[cell_uri("src1", "items", "1", "name")] == "bolt"
    assert by_uri[cell_uri("src1", "items", "2", "qty")] == "20"


def test_unchanged_repull_emits_nothing_and_atom_ids_stable(tmp_path):
    path = make_csv(tmp_path, [["1", "bolt", "10"], ["2", "nut", "20"]])
    conn = CsvConnector("src1", path, key_columns=["id"])
    batch1, state1 = conn.pull(None)

    # re-pull with state: zero deltas
    batch2, state2 = conn.pull(state1)
    assert batch2.deltas == []
    assert batch2.cycle == 2

    # re-pull from scratch (fresh connector, no state): identical atom_ids and uris
    conn_b = CsvConnector("src1", path, key_columns=["id"])
    batch3, _ = conn_b.pull(None)
    assert {(d.atom.uri, d.atom.atom_id) for d in batch3.deltas} == {
        (d.atom.uri, d.atom.atom_id) for d in batch1.deltas
    }


def test_single_cell_change_emits_exactly_one_update(tmp_path):
    path = make_csv(tmp_path, [["1", "bolt", "10"], ["2", "nut", "20"]])
    conn = CsvConnector("src1", path, key_columns=["id"])
    batch1, state1 = conn.pull(None)
    old_atom = next(
        d.atom for d in batch1.deltas if d.atom.uri == cell_uri("src1", "items", "2", "qty")
    )

    write_csv(path, ["id", "name", "qty"], [["1", "bolt", "10"], ["2", "nut", "25"]])
    batch2, _ = conn.pull(state1)

    assert len(batch2.deltas) == 1
    d = batch2.deltas[0]
    assert d.kind == "update"
    assert d.atom.uri == cell_uri("src1", "items", "2", "qty")
    assert d.atom.value == "25"
    assert d.superseded_atom_id == old_atom.atom_id
    assert d.atom.atom_id != old_atom.atom_id  # changed value -> NEW atom id
    assert batch2.changed_atom_ids == [old_atom.atom_id]


def test_row_delete_emits_tombstones_superseding_each_cell(tmp_path):
    path = make_csv(tmp_path, [["1", "bolt", "10"], ["2", "nut", "20"]])
    conn = CsvConnector("src1", path, key_columns=["id"])
    batch1, state1 = conn.pull(None)
    row2_atoms = {d.atom.uri: d.atom for d in batch1.deltas if "/2#" in d.atom.uri}

    write_csv(path, ["id", "name", "qty"], [["1", "bolt", "10"]])
    batch2, state2 = conn.pull(state1)

    assert len(batch2.deltas) == 3
    assert all(d.kind == "delete" for d in batch2.deltas)
    for d in batch2.deltas:
        assert d.atom.value is None  # tombstone
        assert d.superseded_atom_id == row2_atoms[d.atom.uri].atom_id
    assert "2" not in state2["rows"]


def test_row_insert_emits_only_new_cells(tmp_path):
    path = make_csv(tmp_path, [["1", "bolt", "10"]])
    conn = CsvConnector("src1", path, key_columns=["id"])
    _, state1 = conn.pull(None)

    write_csv(path, ["id", "name", "qty"], [["1", "bolt", "10"], ["3", "washer", "7"]])
    batch2, _ = conn.pull(state1)
    assert len(batch2.deltas) == 3
    assert all(d.kind == "insert" for d in batch2.deltas)
    assert all("/3#" in d.atom.uri for d in batch2.deltas)


def test_empty_string_and_missing_field_are_distinct_atoms(tmp_path):
    # row 1: qty present but empty (""); row 2 written raw with a short record (qty absent -> None)
    path = tmp_path / "items.csv"
    path.write_text('id,name,qty\n1,bolt,""\n2,nut\n', encoding="utf-8")
    conn = CsvConnector("src1", path, key_columns=["id"])
    batch, _ = conn.pull(None)
    vals = {d.atom.uri: d.atom.value for d in batch.deltas}
    assert vals[cell_uri("src1", "items", "1", "qty")] == ""
    assert vals[cell_uri("src1", "items", "2", "qty")] is None
    # distinct content addresses for "" vs None at congruent coordinates
    a_empty = Atom(uri=cell_uri("src1", "items", "1", "qty"), value="")
    a_null = Atom(uri=cell_uri("src1", "items", "1", "qty"), value=None)
    assert a_empty.atom_id != a_null.atom_id


def test_keyless_rows_content_addressed_and_edit_becomes_delete_insert(tmp_path):
    path = tmp_path / "log.csv"
    write_csv(path, ["event", "level"], [["boot", "info"], ["crash", "error"]])
    conn = CsvConnector("src1", path, key_columns=[])  # no keys
    batch1, state1 = conn.pull(None)
    assert all(d.kind == "insert" for d in batch1.deltas)
    assert all("/row-" in d.atom.uri for d in batch1.deltas)

    # documented limitation: editing a keyless row is delete(old)+insert(new)
    write_csv(path, ["event", "level"], [["boot", "info"], ["crash", "fatal"]])
    batch2, _ = conn.pull(state1)
    kinds = sorted(d.kind for d in batch2.deltas)
    assert kinds == ["delete", "delete", "insert", "insert"]  # 2 cells out, 2 cells in


def test_duplicate_keyless_rows_get_occurrence_suffix(tmp_path):
    path = tmp_path / "log.csv"
    write_csv(path, ["event"], [["boot"], ["boot"]])
    conn = CsvConnector("src1", path)
    batch, _ = conn.pull(None)
    uris = sorted(d.atom.uri for d in batch.deltas)
    assert len(uris) == 2 and uris[0] != uris[1]
    assert uris[1].split("#")[0].endswith("~2")


def test_column_add_and_drop_touch_only_that_column(tmp_path):
    path = tmp_path / "t.csv"
    write_csv(path, ["id", "a"], [["1", "x"], ["2", "y"]])
    conn = CsvConnector("s", path, key_columns=["id"])
    _, s1 = conn.pull(None)

    write_csv(path, ["id", "a", "b"], [["1", "x", "n1"], ["2", "y", "n2"]])
    b2, s2 = conn.pull(s1)
    assert sorted((d.kind, d.atom.value) for d in b2.deltas) == [
        ("insert", "n1"),
        ("insert", "n2"),
    ]
    assert all(d.atom.uri.endswith("#b") for d in b2.deltas)

    write_csv(path, ["id", "b"], [["1", "n1"], ["2", "n2"]])
    b3, _ = conn.pull(s2)
    assert sorted(d.kind for d in b3.deltas) == ["delete", "delete"]
    assert all(d.atom.uri.endswith("#a") and d.atom.value is None for d in b3.deltas)


def test_state_is_json_roundtrippable(tmp_path):
    path = make_csv(tmp_path, [["1", "bolt", "10"]])
    conn = CsvConnector("src1", path, key_columns=["id"])
    _, state1 = conn.pull(None)
    state_rt = json.loads(json.dumps(state1))
    batch2, _ = conn.pull(state_rt)
    assert batch2.deltas == []


def test_key_values_with_uri_reserved_chars_are_quoted(tmp_path):
    path = tmp_path / "t.csv"
    write_csv(path, ["id", "v"], [["a/b#c", "1"], ["a%b", "2"]])
    conn = CsvConnector("s", path, key_columns=["id"])
    batch, _ = conn.pull(None)
    for d in batch.deltas:
        body = d.atom.uri.removeprefix("atom://")
        path_part, _, frag = body.partition("#")
        assert "#" not in frag  # exactly one fragment separator
        assert path_part.count("/") == 2  # source/table/rowkey only
    # two distinct rows -> disjoint uri sets
    assert len({d.atom.uri for d in batch.deltas}) == 4


# --------------------------------------------------------------- mutation fuzzing


def _expected_cell_map(path) -> dict[str, str | None]:
    """Independent oracle: parse the CSV directly and build uri -> value."""
    import csv as _csv

    with open(path, newline="", encoding="utf-8") as f:
        recs = [r for r in _csv.reader(f) if r]
    header = recs[0]
    out: dict[str, str | None] = {}
    for rec in recs[1:]:
        row = {c: (rec[i] if i < len(rec) else None) for i, c in enumerate(header)}
        rk = row["id"]  # fuzz keys are alphanumeric -> quoting is identity
        for c in header:
            out[cell_uri("fuzz", "data", rk, c)] = row[c]
    return out


def _apply_deltas(old_map: dict, batch) -> dict:
    """Replay a DeltaBatch onto the previous cell map, asserting every delta is genuine."""
    new_map = dict(old_map)
    for d in batch.deltas:
        if d.kind == "insert":
            assert d.atom.uri not in new_map, f"phantom insert {d.atom.uri}"
            assert d.superseded_atom_id == ""
            new_map[d.atom.uri] = d.atom.value
        elif d.kind == "update":
            assert d.atom.uri in new_map, f"update of unknown uri {d.atom.uri}"
            old_value = new_map[d.atom.uri]
            assert old_value != d.atom.value, f"phantom update {d.atom.uri}"
            # superseded id must be the content address of the PREVIOUS atom
            assert d.superseded_atom_id == Atom(uri=d.atom.uri, value=old_value).atom_id
            new_map[d.atom.uri] = d.atom.value
        elif d.kind == "delete":
            assert d.atom.uri in new_map, f"phantom delete {d.atom.uri}"
            old_value = new_map.pop(d.atom.uri)
            assert d.superseded_atom_id == Atom(uri=d.atom.uri, value=old_value).atom_id
        else:  # pragma: no cover
            raise AssertionError(d.kind)
    return new_map


@pytest.mark.parametrize("seed", [0, 1, 2, 3, 4])
def test_mutation_fuzzing_delta_reconstructs_state_exactly(tmp_path, seed):
    """Random insert/update/delete between pulls; the delta stream must reconstruct
    the new snapshot exactly from the old — no missed, no phantom changes."""
    rng = random.Random(seed)
    path = tmp_path / "data.csv"
    header = ["id", "a", "b", "c"]

    def rand_val():
        return rng.choice(["", "x", "y", "zz", "1.5", "0", "name,with,commas", 'q"uote'])

    rows: dict[str, list] = {}
    next_id = 0
    for _ in range(rng.randint(3, 8)):
        rows[f"k{next_id}"] = [rand_val() for _ in header[1:]]
        next_id += 1

    def flush():
        write_csv(path, header, [[k, *v] for k, v in rows.items()])

    flush()
    conn = CsvConnector("fuzz", path, key_columns=["id"])
    batch, state = conn.pull(None)
    cell_map = _apply_deltas({}, batch)
    assert cell_map == _expected_cell_map(path)

    for _cycle in range(8):
        # random mutations
        for _ in range(rng.randint(0, 3)):  # inserts
            rows[f"k{next_id}"] = [rand_val() for _ in header[1:]]
            next_id += 1
        for _ in range(rng.randint(0, 4)):  # cell updates (sometimes a no-op value)
            if rows:
                k = rng.choice(sorted(rows))
                rows[k][rng.randrange(len(header) - 1)] = rand_val()
        for _ in range(rng.randint(0, 2)):  # deletes
            if rows:
                rows.pop(rng.choice(sorted(rows)))
        flush()

        batch, state = conn.pull(state)
        cell_map = _apply_deltas(cell_map, batch)
        assert cell_map == _expected_cell_map(path), f"divergence at cycle {batch.cycle}"
        # state stays JSON-able every cycle
        state = json.loads(json.dumps(state))
