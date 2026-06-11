"""Generator determinism + pinned-fixture drift guard (whitepaper §17.6, §18.4).

Two generator runs must be byte-identical, and the committed fixtures must be
exactly what the generator (seed=42) produces from the pinned seeds — so the
repository state is reproducible from `scripts/build_aviation_fixtures.py`.
No network is touched: generation reads only the pinned `_seed/` CSVs.
"""

from __future__ import annotations

from pathlib import Path

from conftest import FIXTURES

ARTIFACTS = [
    "faa_master.csv",
    "faa_acftref.csv",
    "asrs_reports.csv",
    "ntsb_events.csv",
    "maintenance_erp.csv",
    "gold/er_gold_pairs.csv",
    "gold/mini_ontology.json",
    "gold/competency_questions.yaml",
]


def _generate(generator, out: Path) -> None:
    generator.build_all(out, FIXTURES / "_seed")


def test_two_runs_byte_identical(generator, tmp_path):
    a, b = tmp_path / "run_a", tmp_path / "run_b"
    _generate(generator, a)
    _generate(generator, b)
    for rel in ARTIFACTS:
        fa, fb = a / rel, b / rel
        assert fa.exists() and fb.exists(), rel
        assert fa.read_bytes() == fb.read_bytes(), f"non-deterministic output: {rel}"


def test_committed_fixtures_match_regeneration(generator, tmp_path):
    out = tmp_path / "regen"
    _generate(generator, out)
    for rel in ARTIFACTS:
        committed = (FIXTURES / rel).read_bytes()
        regenerated = (out / rel).read_bytes()
        assert committed == regenerated, (
            f"committed fixture {rel} drifted from generator output — "
            f"re-run scripts/build_aviation_fixtures.py"
        )


def test_total_fixture_size_under_budget():
    total = sum(p.stat().st_size for p in FIXTURES.rglob("*") if p.is_file())
    assert total < 5 * 1024 * 1024, f"fixtures exceed 5 MB budget: {total} bytes"


def test_row_counts_in_spec_range(estate):
    t = estate["tables"]
    assert 2400 <= len(t["faa_master"]) <= 2600
    assert 100 <= len(t["faa_acftref"]) <= 140
    assert len(t["asrs_reports"]) == 350
    assert len(t["ntsb_events"]) == 200
    assert len(t["maintenance_erp"]) == 600


def test_pinned_seed_manifest_documents_downloads():
    """AMD-0006: real downloads that succeeded are pinned with hashes; the
    blocked ones are documented."""
    import json

    manifest = json.loads((FIXTURES / "_seed" / "MANIFEST.json").read_text())
    assert set(manifest["sources"]) == {"airports.dat", "planes.dat"}
    for src in manifest["sources"].values():
        assert len(src["sha256"]) == 64
        assert src["bytes"] > 0
    assert (FIXTURES / "_seed" / "airports_us.csv").exists()
    assert (FIXTURES / "_seed" / "planes.csv").exists()
    assert "403" in manifest["notes"]  # FAA registry block documented


def test_estate_metadata_shape(estate):
    md = estate["metadata"]
    assert md["estate"] == "aviation"
    assert set(md["tables"]) == {
        "faa_master", "faa_acftref", "asrs_reports", "ntsb_events", "maintenance_erp"
    }
    for tmeta in md["tables"].values():
        assert tmeta["source_id"]
        assert tmeta["key_columns"]
    assert Path(md["gold"]["ontology"]).exists()
    assert Path(md["gold"]["competency_questions"]).exists()
    assert Path(md["gold"]["er_pairs"]).exists()
