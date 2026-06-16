"""The answer-cache layer of CachedWorkStore — the close-the-loop primitives
(v2.1 §4). These are the STORE-level mechanics (normalization, fingerprinting,
write-back, validity-gated lookup, tenant isolation); the full loop through
LODESTONE is exercised in tests/m12/test_flywheel.py.

Deterministic, keyless, zero-network.
"""

from __future__ import annotations

from ontoforge.discovery import (
    CachedAnswer,
    CachedWorkStore,
    WorkKind,
    describe_work,
    fingerprint_atoms,
    normalize_question,
)


# --------------------------------------------------------------- normalize


def test_normalize_question_is_order_and_stopword_insensitive() -> None:
    a = normalize_question("Which manufacturer built the aircraft N4669X?")
    b = normalize_question("the aircraft N4669X was built by which manufacturer")
    assert a == b
    # the load-bearing identifier survives normalization
    assert "n4669x" in a


def test_normalize_question_distinguishes_different_content() -> None:
    assert normalize_question("revenue by region 2024") != normalize_question(
        "revenue by region 2023"
    )


# ------------------------------------------------------------- fingerprint


def test_fingerprint_is_deterministic_and_set_based() -> None:
    assert fingerprint_atoms(["a", "b"]) == fingerprint_atoms(["b", "a", "a"])
    assert fingerprint_atoms([]) == "fp:0:empty"
    # adding a NEW atom (provenance changed) moves the fingerprint
    assert fingerprint_atoms(["a", "b"]) != fingerprint_atoms(["a", "b", "c"])
    # dropping a cited atom also moves it
    assert fingerprint_atoms(["a", "b"]) != fingerprint_atoms(["a"])


# ----------------------------------------------------------- describe ask


def test_describe_ask_surfaces_question_and_shape() -> None:
    desc = describe_work(
        WorkKind.ASK,
        {"question": "total revenue by region", "columns": ["region", "rev"],
         "n_rows": 3, "confidence": 0.91},
    )
    assert "total revenue by region" in desc
    assert "region" in desc and "rev" in desc
    assert "3 result row(s)" in desc


# ------------------------------------------------------ write-back / lookup


def _store_with_answer(fp_atoms=("atom://x", "atom://y")) -> CachedWorkStore:
    s = CachedWorkStore()
    s.cache_answer(
        "Who manufactured the aircraft with tail number N4669X?",
        columns=["manufacturer"],
        rows=[["GULFSTREAM AEROSPACE"]],
        citations=[{"row": 0, "column": "manufacturer",
                    "value": "GULFSTREAM AEROSPACE", "atom_ids": list(fp_atoms)}],
        atom_ids=fp_atoms,
        oqir="Traverse(Select(...), 'model')",
        confidence=0.93,
        tenant_id="acme",
    )
    return s


def test_cache_answer_then_lookup_serves_same_answer() -> None:
    s = _store_with_answer()
    fp = fingerprint_atoms(("atom://x", "atom://y"))
    # a reordered phrasing with the same content tokens hits the same cache key
    hit = s.lookup_answer(
        "The aircraft with tail number N4669X was manufactured by who?",
        tenant_id="acme",
        current_fingerprint=fp,
    )
    assert isinstance(hit, CachedAnswer)
    assert hit.rows == (("GULFSTREAM AEROSPACE",),)
    assert hit.confidence == 0.93
    # referenceable downstream: an id + an auto-generated description
    assert hit.object_id.endswith("@v1")
    assert "N4669X" in hit.description or "n4669x" in hit.description.lower()


def test_lookup_invalidates_when_fingerprint_moves() -> None:
    """A provenance change (different atom set) makes the cached answer STALE:
    lookup returns None so the caller recomputes — never a wrong cached answer."""
    s = _store_with_answer()
    moved = fingerprint_atoms(("atom://x", "atom://z"))  # y -> z, provenance moved
    assert s.is_stale(
        "Who manufactured the aircraft with tail number N4669X?",
        moved, tenant_id="acme",
    )
    hit = s.lookup_answer(
        "Who manufactured the aircraft with tail number N4669X?",
        tenant_id="acme", current_fingerprint=moved,
    )
    assert hit is None


def test_lookup_without_fingerprint_serves_unconditionally() -> None:
    """When no live fingerprint is supplied the lookup is a plain retrieval (the
    LODESTONE driver always supplies one; this is the bare-store contract)."""
    s = _store_with_answer()
    hit = s.lookup_answer(
        "Who manufactured the aircraft with tail number N4669X?", tenant_id="acme"
    )
    assert hit is not None


def test_answer_cache_is_tenant_scoped() -> None:
    """§1.5 isolation: a cached Ask never crosses the tenant boundary."""
    s = _store_with_answer()  # tenant acme
    fp = fingerprint_atoms(("atom://x", "atom://y"))
    assert s.lookup_answer(
        "Who manufactured the aircraft with tail number N4669X?",
        tenant_id="globex", current_fingerprint=fp,
    ) is None
    assert s.lookup_answer(
        "Who manufactured the aircraft with tail number N4669X?",
        tenant_id="acme", current_fingerprint=fp,
    ) is not None


def test_recompute_writes_a_new_version_and_keeps_history() -> None:
    s = _store_with_answer()
    # re-answer the same question (provenance moved -> recomputed answer)
    obj = s.cache_answer(
        "Who manufactured the aircraft with tail number N4669X?",
        columns=["manufacturer"],
        rows=[["GULFSTREAM AEROSPACE CORP"]],
        atom_ids=("atom://x", "atom://z"),
        tenant_id="acme",
    )
    assert obj.version == 2
    norm = normalize_question("Who manufactured the aircraft with tail number N4669X?")
    hist = s.history(f"ask:acme:{norm}")
    assert [o.version for o in hist] == [1, 2]
    # the latest version is what lookup serves
    fp2 = fingerprint_atoms(("atom://x", "atom://z"))
    hit = s.lookup_answer(
        "Who manufactured the aircraft with tail number N4669X?",
        tenant_id="acme", current_fingerprint=fp2,
    )
    assert hit is not None and hit.rows == (("GULFSTREAM AEROSPACE CORP",),)


def test_cached_ask_is_retrievable_by_search() -> None:
    """§4 step 3: the written-back object is referenceable downstream via search,
    carrying its description + provenance."""
    s = _store_with_answer()
    hits = s.search("manufacturer aircraft N4669X", tenant_id="acme", kind=WorkKind.ASK)
    assert hits
    top = hits[0].obj
    assert top.kind is WorkKind.ASK
    assert top.description  # auto-generated description present
    assert "fingerprint" in top.payload  # provenance/validity metadata carried
