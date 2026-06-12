"""Generator determinism + corpus hygiene.

The committed fixtures/meridian corpus must regenerate byte-identically from
``ontoforge.estates.meridian_gen`` (seed 7), stay under the 8 MB budget, use
real-world identifier schemes (valid GS1 GTIN-14 check digits, valid ISO 6346
container check digits, SAP-style 45* PO numbers, zero-padded vendor ids), and
keep its intended candidate keys free of accidental near-unique competitors.
"""

from __future__ import annotations

import hashlib

from ontoforge.estates import meridian_gen

from meridian_helpers import FIXTURES


def _tree_hashes(base) -> dict[str, str]:
    return {
        str(p.relative_to(base)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(base.rglob("*"))
        if p.is_file()
    }


def test_generator_is_byte_deterministic(tmp_path):
    m1 = meridian_gen.build_corpus(tmp_path / "a")
    m2 = meridian_gen.build_corpus(tmp_path / "b")
    assert m1 == m2
    assert _tree_hashes(tmp_path / "a") == _tree_hashes(tmp_path / "b")


def test_committed_fixtures_match_the_generator(tmp_path):
    """fixtures/meridian IS the seed-7 output — no hand edits can drift in."""
    meridian_gen.build_corpus(tmp_path / "regen")
    assert _tree_hashes(tmp_path / "regen") == _tree_hashes(FIXTURES)


def test_size_and_shape_budget(frames):
    total = sum(p.stat().st_size for p in FIXTURES.rglob("*") if p.is_file())
    assert total < meridian_gen.MAX_TOTAL_BYTES
    assert len(frames) == 10
    assert {f.replace(".csv", "") for f in meridian_gen.FIXTURE_FILES} == set(frames)
    for name, df in frames.items():
        assert 290 <= len(df) <= 1600, f"{name}: {len(df)} rows out of budget"


def test_intended_keys_have_no_unique_competitors(frames):
    """A stray near-unique measure column would hijack key choice (and with it
    row identity); the generator enforces this — re-assert it on the artifact."""
    for table, df in frames.items():
        intended = set(meridian_gen._INTENDED_KEYS[table])
        for col in df.columns:
            if col in intended or (table == "products" and col == "PRODUCT_NAME"):
                continue
            assert df[col].nunique() < 0.98 * len(df), f"{table}.{col}"


def test_gtin14_check_digits_are_valid(frames):
    for gtin in frames["products"]["GTIN"]:
        digits = [int(c) for c in gtin]
        assert len(digits) == 14
        total = sum(d * (3 if i % 2 == 0 else 1) for i, d in enumerate(digits[:13]))
        assert digits[13] == (10 - total % 10) % 10, gtin


def test_iso6346_container_check_digits_are_valid(frames):
    letters = meridian_gen._ISO6346_LETTER
    for cn in frames["shipments"]["CONTAINER_NUMBER"]:
        assert len(cn) == 11, cn
        total = sum(
            (letters[ch] if ch.isalpha() else int(ch)) * (2**i)
            for i, ch in enumerate(cn[:10])
        )
        assert int(cn[10]) == (total % 11) % 10, cn


def test_identifier_schemes(frames):
    po = frames["purchase_order_lines"]
    assert po["PO_NUMBER"].str.fullmatch(r"45\d{8}").all()
    assert frames["supplier_contracts"]["SUPPLIER_ID"].str.fullmatch(r"\d{10}").all()
    assert frames["supplier_contracts"]["DUNS_NUMBER"].str.fullmatch(r"\d{9}").all()
    assert frames["quality_notifications"]["NOTIFICATION_ID"].str.fullmatch(r"QN-\d{3}-\d{5}").all()
    assert frames["shipments"]["ORIGIN_PORT"].isin(
        ["CNSHA", "CNYTN", "TWKHH", "VNSGN", "KRPUS", "JPNGO", "CNXMN", "TWTPE"]
    ).all()
    assert frames["retail_pos_sales"]["FISCAL_QUARTER"].str.fullmatch(r"FY\d{4}-Q[1-4]").all()


def test_gold_answers_are_computed_not_hardcoded(frames, gold):
    """questions.yaml pins exactly what compute_gold derives from the corpus."""
    computed = meridian_gen.compute_gold(frames)
    pinned = {q["id"]: q["answer"] for q in gold["questions"]}
    for qid, expected in computed.items():
        if expected is None:
            assert pinned[qid] is None
        else:
            assert abs(float(pinned[qid]) - float(expected)) <= 1e-6 * max(1.0, abs(float(expected)))


def test_gold_spec_shape(gold):
    qs = gold["questions"]
    assert len(qs) == 12
    assert sum(1 for q in qs if q["answerable"]) == 9
    assert sum(1 for q in qs if q["expected_behavior"] == "abstain") == 2
    assert sum(1 for q in qs if q["expected_behavior"] == "reject_unit_mismatch") == 1
    assert gold["generator_seed"] == meridian_gen.SEED
    # every wart class ships a deterministic recovery rule
    conv = gold["conventions"]
    for key in ("vendor_id_recovery", "area_uom_recovery", "weight_uom_recovery",
                "date_locales", "null_tokens", "kpc_trap", "supplier_resolution"):
        assert conv.get(key), key
