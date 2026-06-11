"""DocConnector: paragraph span atoms, snapshot diff, and the move-stability
guarantee (unchanged paragraph keeps its atom_id even when it moves)."""

from __future__ import annotations

from ontoforge.cdc import DocConnector
from ontoforge.cdc.docs import normalize_text, split_paragraphs


def state_atoms(state, rel):
    return [p["atom_id"] for p in state["docs"][rel]["paras"]]


def test_split_paragraphs_offsets_and_text():
    text = "First para line one.\nline two.\n\n\nSecond para.\n   \nThird.\n"
    paras = split_paragraphs(text)
    assert [p[2] for p in paras] == ["First para line one.\nline two.", "Second para.", "Third."]
    for start, end, ptext in paras:
        assert text[start:end] == ptext  # offsets index into the normalized text


def test_first_pull_all_inserts_span_uris(tmp_path):
    (tmp_path / "a.txt").write_text("Alpha one.\n\nBeta two.\n", encoding="utf-8")
    conn = DocConnector("docs1", tmp_path)
    batch, state = conn.pull(None)
    assert batch.cycle == 1
    assert [d.kind for d in batch.deltas] == ["insert", "insert"]
    uris = sorted(d.atom.uri for d in batch.deltas)
    assert uris[0] == "atom://docs1/a.txt#span:0-10"
    assert uris[1] == "atom://docs1/a.txt#span:12-21"
    assert {d.atom.value for d in batch.deltas} == {"Alpha one.", "Beta two."}


def test_unchanged_doc_repull_zero_deltas(tmp_path):
    (tmp_path / "a.md").write_text("One.\n\nTwo.\n", encoding="utf-8")
    conn = DocConnector("docs1", tmp_path)
    _, s1 = conn.pull(None)
    b2, s2 = conn.pull(s1)
    assert b2.deltas == []
    assert state_atoms(s2, "a.md") == state_atoms(s1, "a.md")


def test_edit_one_paragraph_exactly_one_update(tmp_path):
    p = tmp_path / "a.txt"
    p.write_text("Stable head.\n\nMutable middle.\n\nStable tail.\n", encoding="utf-8")
    conn = DocConnector("docs1", tmp_path)
    b1, s1 = conn.pull(None)
    old_mid = next(d.atom for d in b1.deltas if d.atom.value == "Mutable middle.")

    p.write_text("Stable head.\n\nMutable middle, edited.\n\nStable tail.\n", encoding="utf-8")
    b2, s2 = conn.pull(s1)
    assert len(b2.deltas) == 1
    d = b2.deltas[0]
    assert d.kind == "update"
    assert d.atom.value == "Mutable middle, edited."
    assert d.superseded_atom_id == old_mid.atom_id
    assert d.atom.atom_id != old_mid.atom_id


def test_insert_above_shifts_offsets_but_keeps_atom_ids(tmp_path):
    p = tmp_path / "a.txt"
    p.write_text("Body paragraph.\n\nClosing paragraph.\n", encoding="utf-8")
    conn = DocConnector("docs1", tmp_path)
    b1, s1 = conn.pull(None)
    ids_before = state_atoms(s1, "a.txt")

    p.write_text("NEW INTRO.\n\nBody paragraph.\n\nClosing paragraph.\n", encoding="utf-8")
    b2, s2 = conn.pull(s1)
    # exactly one insert; the shifted-but-unchanged paragraphs emit nothing
    assert [d.kind for d in b2.deltas] == ["insert"]
    assert b2.deltas[0].atom.value == "NEW INTRO."
    ids_after = state_atoms(s2, "a.txt")
    assert ids_after[1:] == ids_before  # identities preserved despite new offsets
    # ...even though current offsets (hence uris) moved in the state
    assert s2["docs"]["a.txt"]["paras"][1]["start"] != s1["docs"]["a.txt"]["paras"][0]["start"]


def test_pure_move_emits_no_deltas_and_atom_ids_stable(tmp_path):
    p = tmp_path / "a.txt"
    p.write_text("Para A.\n\nPara B.\n\nPara C.\n", encoding="utf-8")
    conn = DocConnector("docs1", tmp_path)
    _, s1 = conn.pull(None)
    ids1 = set(state_atoms(s1, "a.txt"))

    p.write_text("Para B.\n\nPara A.\n\nPara C.\n", encoding="utf-8")  # reorder only
    b2, s2 = conn.pull(s1)
    assert b2.deltas == [], [(d.kind, d.atom.value) for d in b2.deltas]
    assert set(state_atoms(s2, "a.txt")) == ids1


def test_move_plus_edit_touches_only_the_edited_paragraph(tmp_path):
    p = tmp_path / "a.txt"
    p.write_text("Para A.\n\nPara B.\n\nPara C.\n", encoding="utf-8")
    conn = DocConnector("docs1", tmp_path)
    b1, s1 = conn.pull(None)
    atoms1 = {d.atom.value: d.atom for d in b1.deltas}

    # B moves up (unchanged), A is edited. The moved paragraph must emit NOTHING;
    # the edited one is superseded — either as one update or a delete+insert pair
    # (documented degradation when the edit also moves relative to its neighbors).
    p.write_text("Para B.\n\nPara A, edited.\n\nPara C.\n", encoding="utf-8")
    b2, s2 = conn.pull(s1)

    touched_values = {d.atom.value for d in b2.deltas}
    assert "Para B." not in touched_values and "Para C." not in touched_values
    superseded = {d.superseded_atom_id for d in b2.deltas if d.superseded_atom_id}
    assert superseded == {atoms1["Para A."].atom_id}  # exactly the edited paragraph
    new_values = {d.atom.value for d in b2.deltas if d.kind in ("insert", "update")}
    assert new_values == {"Para A, edited."}
    # moved-but-unchanged paragraphs keep their identities in state
    kept = set(state_atoms(s2, "a.txt"))
    assert atoms1["Para B."].atom_id in kept and atoms1["Para C."].atom_id in kept


def test_delete_doc_tombstones_every_paragraph(tmp_path):
    p = tmp_path / "gone.txt"
    p.write_text("One.\n\nTwo.\n", encoding="utf-8")
    conn = DocConnector("docs1", tmp_path)
    b1, s1 = conn.pull(None)
    first_ids = {d.atom.atom_id for d in b1.deltas}
    p.unlink()
    b2, s2 = conn.pull(s1)
    assert [d.kind for d in b2.deltas] == ["delete", "delete"]
    assert {d.superseded_atom_id for d in b2.deltas} == first_ids
    assert all(d.atom.value is None for d in b2.deltas)
    assert s2["docs"] == {}


def test_new_file_inserts_only_its_paragraphs(tmp_path):
    (tmp_path / "a.txt").write_text("Existing.\n", encoding="utf-8")
    conn = DocConnector("docs1", tmp_path)
    _, s1 = conn.pull(None)
    (tmp_path / "b.md").write_text("Fresh doc.\n\nMore.\n", encoding="utf-8")
    b2, _ = conn.pull(s1)
    assert sorted(d.atom.value for d in b2.deltas) == ["Fresh doc.", "More."]
    assert all(d.kind == "insert" for d in b2.deltas)


def test_duplicate_paragraphs_get_per_occurrence_identity(tmp_path):
    p = tmp_path / "dup.txt"
    p.write_text("Same text.\n\nOther.\n\nSame text.\n", encoding="utf-8")
    conn = DocConnector("docs1", tmp_path)
    b1, s1 = conn.pull(None)
    dup_ids = [d.atom.atom_id for d in b1.deltas if d.atom.value == "Same text."]
    assert len(dup_ids) == 2 and dup_ids[0] != dup_ids[1]

    # dropping one duplicate deletes exactly one occurrence; the other survives
    p.write_text("Same text.\n\nOther.\n", encoding="utf-8")
    b2, s2 = conn.pull(s1)
    assert [d.kind for d in b2.deltas] == ["delete"]
    assert b2.deltas[0].superseded_atom_id in dup_ids
    assert set(dup_ids) & set(state_atoms(s2, "dup.txt"))


def test_crlf_and_bom_doc_same_atoms_as_plain(tmp_path):
    plain = tmp_path / "p"
    plain.mkdir()
    (plain / "d.txt").write_text("Para one.\n\nPara two.\n", encoding="utf-8")
    crlf = tmp_path / "c"
    crlf.mkdir()
    (crlf / "d.txt").write_bytes("Para one.\r\n\r\nPara two.\r\n".encode("utf-8-sig"))

    b_plain, _ = DocConnector("docsX", plain).pull(None)
    b_crlf, _ = DocConnector("docsX", crlf).pull(None)
    # CRLF normalization + BOM stripping: identical atoms (uri AND id)
    assert {(d.atom.uri, d.atom.atom_id) for d in b_plain.deltas} == {
        (d.atom.uri, d.atom.atom_id) for d in b_crlf.deltas
    }


def test_latin1_doc_fallback(tmp_path):
    (tmp_path / "l.txt").write_bytes(b"Caf\xe9 narrative.\n")  # invalid UTF-8
    batch, _ = DocConnector("docs1", tmp_path).pull(None)
    assert [d.atom.value for d in batch.deltas] == ["Café narrative."]


def test_normalize_text_handles_lone_cr():
    assert normalize_text("a\r\nb\rc\n") == "a\nb\nc\n"
