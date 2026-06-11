"""CSV encoding robustness: utf-8-sig BOM, CRLF, latin-1 fallback, quoted newlines."""

from __future__ import annotations

from ontoforge.cdc import CsvConnector
from ontoforge.contracts import cell_uri


def test_utf8_bom_does_not_pollute_header(tmp_path):
    src = tmp_path / "b.csv"
    src.write_bytes("id,name\n1,café\n".encode("utf-8-sig"))
    batch, _ = CsvConnector("s", src, key_columns=["id"]).pull(None)
    uris = {d.atom.uri for d in batch.deltas}
    assert cell_uri("s", "b", "1", "id") in uris  # 'id' clean, no ﻿ prefix
    vals = {d.atom.uri: d.atom.value for d in batch.deltas}
    assert vals[cell_uri("s", "b", "1", "name")] == "café"


def test_crlf_values_have_no_trailing_cr(tmp_path):
    src = tmp_path / "c.csv"
    src.write_bytes(b"id,name\r\n1,bolt\r\n2,nut\r\n")
    batch, _ = CsvConnector("s", src, key_columns=["id"]).pull(None)
    vals = {d.atom.uri: d.atom.value for d in batch.deltas}
    assert vals[cell_uri("s", "c", "1", "name")] == "bolt"
    assert vals[cell_uri("s", "c", "2", "name")] == "nut"
    assert not any(isinstance(v, str) and "\r" in v for v in vals.values())


def test_crlf_and_lf_files_yield_identical_atoms(tmp_path):
    a = tmp_path / "x.csv"
    b = tmp_path / "y.csv"
    a.write_bytes(b"id,v\n1,p\n")
    b.write_bytes(b"id,v\r\n1,p\r\n")
    ba, _ = CsvConnector("s", a, key_columns=["id"], object_name="t").pull(None)
    bb, _ = CsvConnector("s", b, key_columns=["id"], object_name="t").pull(None)
    assert {(d.atom.uri, d.atom.atom_id) for d in ba.deltas} == {
        (d.atom.uri, d.atom.atom_id) for d in bb.deltas
    }


def test_latin1_fallback(tmp_path):
    src = tmp_path / "l.csv"
    src.write_bytes(b"id,name\n1,Jos\xe9\n")  # \xe9 invalid as UTF-8 here
    batch, _ = CsvConnector("s", src, key_columns=["id"]).pull(None)
    vals = {d.atom.uri: d.atom.value for d in batch.deltas}
    assert vals[cell_uri("s", "l", "1", "name")] == "José"


def test_quoted_embedded_newline_and_comma(tmp_path):
    src = tmp_path / "q.csv"
    src.write_bytes(b'id,note\r\n1,"line one\r\nline two, with comma"\r\n')
    batch, _ = CsvConnector("s", src, key_columns=["id"]).pull(None)
    vals = {d.atom.uri: d.atom.value for d in batch.deltas}
    assert vals[cell_uri("s", "q", "1", "note")] == "line one\r\nline two, with comma"
