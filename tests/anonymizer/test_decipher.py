"""decipher: scalar / tree / Answer round-trips, key-bound, non-mutating."""

from __future__ import annotations

import pytest

from ontoforge.anonymizer import anonymize, decipher, decipher_value
from ontoforge.anonymizer.keymap import DecipherError
from ontoforge.contracts import Answer, CitedCell

KEY = b"customer-secret-key-001"


def _anon_simple():
    tables = {"people": {"email": ["alice@acme.com", "bob@acme.com", "carol@acme.com"]}}
    return tables, anonymize(tables, KEY)


def test_decipher_scalar_token() -> None:
    tables, (anon, enc) = _anon_simple()
    tok = anon["people"]["email"][0]
    assert decipher(tok, enc, KEY) == "alice@acme.com"


def test_decipher_list_and_dict_tree() -> None:
    tables, (anon, enc) = _anon_simple()
    toks = anon["people"]["email"]
    obj = {"rows": [[toks[0]], [toks[1]]], "note": "passthrough"}
    out = decipher(obj, enc, KEY)
    assert out["rows"] == [["alice@acme.com"], ["bob@acme.com"]]
    assert out["note"] == "passthrough"  # non-token left untouched


def test_decipher_answer_rows_and_citations() -> None:
    tables, (anon, enc) = _anon_simple()
    toks = anon["people"]["email"]
    ans = Answer(
        columns=["email"],
        rows=[[toks[0]], [toks[1]]],
        citations=[CitedCell(row=0, column="email", value=toks[0], atom_ids=("a1",))],
        confidence=0.9,
    )
    out = decipher(ans, enc, KEY)
    assert out.rows == [["alice@acme.com"], ["bob@acme.com"]]
    assert out.citations[0].value == "alice@acme.com"
    assert out.citations[0].atom_ids == ("a1",)  # provenance preserved


def test_decipher_does_not_mutate_input() -> None:
    tables, (anon, enc) = _anon_simple()
    toks = list(anon["people"]["email"])
    original = list(toks)
    _ = decipher(toks, enc, KEY)
    assert toks == original


def test_decipher_requires_the_key() -> None:
    tables, (anon, enc) = _anon_simple()
    tok = anon["people"]["email"][0]
    with pytest.raises(DecipherError):
        decipher(tok, enc, b"wrong-key")


def test_decipher_value_passes_through_unknown() -> None:
    assert decipher_value("not-a-token", {"OFX_x": "raw"}) == "not-a-token"
    assert decipher_value(None, {}) is None
