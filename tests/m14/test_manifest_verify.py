"""M14 gates — manifest hash verification + tamper detection + bundle layout."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from ontoforge.amber import CAPABILITY_LOSS, AmberError, snapshot, verify


def _copy(bundle: Path, tmp_path: Path) -> Path:
    dst = tmp_path / "tampered"
    shutil.copytree(bundle, dst)
    return dst


def test_fresh_bundle_verifies(bundle):
    report = verify(bundle)
    assert report["ok"], report["errors"]
    assert report["checks"]["files_verified"] > 10
    assert report["checks"]["prov_refs_resolved"] == report["checks"]["distinct_prov_refs"] > 0
    assert report["checks"]["transforms_readable"] == 2
    assert report["checks"]["decisions"] == 1
    assert report["checks"]["morphisms"] == 1


def test_bundle_layout_complete(bundle):
    manifest = json.loads((bundle / "manifest.json").read_text())
    for section in (
        "ontology/ontology.ttl",
        "ontology/ontology.json",
        "rdf/data_current.ttl",
        "data/manifest.json",
        "decisions/decisions.jsonl",
        "morphisms/morphisms.jsonl",
        "provenance/prov_terms.jsonl",
        "provenance/prov_shapes.jsonl",
        "provenance/atoms.jsonl",
        "docs/README.md",
    ):
        assert section in manifest["files"], section
    assert any(f.startswith("transforms/") and f.endswith(".sql") for f in manifest["files"])
    assert any(f.startswith("data/values/") for f in manifest["files"])
    assert any(f.startswith("data/links/") for f in manifest["files"])
    # §7 capability-loss declaration: exactly L, verbatim
    assert tuple(manifest["capability_loss"]) == CAPABILITY_LOSS


def test_flipped_byte_fails_verification(bundle, tmp_path):
    dst = _copy(bundle, tmp_path)
    victim = next(p for p in sorted(dst.rglob("*.parquet")))
    blob = bytearray(victim.read_bytes())
    blob[len(blob) // 2] ^= 0xFF
    victim.write_bytes(bytes(blob))
    report = verify(dst)
    assert not report["ok"]
    rel = victim.relative_to(dst).as_posix()
    assert any("sha256 mismatch" in e and rel in e for e in report["errors"]), report["errors"]


def test_deleted_file_fails_verification(bundle, tmp_path):
    dst = _copy(bundle, tmp_path)
    (dst / "provenance" / "atoms.jsonl").unlink()
    report = verify(dst)
    assert not report["ok"]
    assert any("missing file: provenance/atoms.jsonl" in e for e in report["errors"])


def test_inserted_file_fails_verification(bundle, tmp_path):
    dst = _copy(bundle, tmp_path)
    (dst / "docs" / "EXTRA.md").write_text("not in the manifest")
    report = verify(dst)
    assert not report["ok"]
    assert any("file not in manifest: docs/EXTRA.md" in e for e in report["errors"])


def test_tampered_ttl_fails_verification(bundle, tmp_path):
    dst = _copy(bundle, tmp_path)
    ttl = dst / "ontology" / "ontology.ttl"
    ttl.write_text(ttl.read_text().replace("Aircraft", "Spacecraft"))
    report = verify(dst)
    assert not report["ok"]


def test_snapshot_refuses_nonempty_target(world, bundle, tmp_path):
    target = tmp_path / "occupied"
    target.mkdir()
    (target / "junk").write_text("x")
    with pytest.raises(AmberError, match="not empty"):
        snapshot(target, world["hearth"], world["ontology"], world["ledger"])


def test_snapshot_rejects_unknown_scope(world, tmp_path):
    with pytest.raises(AmberError, match="scope"):
        snapshot(tmp_path / "s", world["hearth"], world["ontology"], world["ledger"], scope="raw")


def test_generated_docs_describe_the_estate(bundle, world):
    text = (bundle / "docs" / "README.md").read_text()
    for c in world["ontology"].iter_classes():
        assert f"### {c.name}" in text
    assert "Capability loss" in text
    assert str(world["n_aircraft"]) in text or "value_cells" in text
