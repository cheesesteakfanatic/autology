"""manifest.lock.json <-> disk integrity for the committed wild snapshot."""

from __future__ import annotations

import hashlib

from conftest import FIXTURES

SIZE_BUDGET_BYTES = 20 * 1024 * 1024
MIN_DATASETS = 150


def test_corpus_meets_the_landing_gates(datasets, fixtures_dir):
    assert len(datasets) >= MIN_DATASETS, f"only {len(datasets)} datasets landed"
    total = sum(p.stat().st_size for p in fixtures_dir.glob("*.csv"))
    assert total <= SIZE_BUDGET_BYTES, f"corpus is {total / 1e6:.1f} MB (> 20 MB budget)"


def test_slugs_are_unique_and_source_prefixed(datasets):
    slugs = [d["slug"] for d in datasets]
    assert len(slugs) == len(set(slugs))
    prefixes = ("of_", "ds_", "fte_", "vg_", "sb_")
    assert all(s.startswith(prefixes) for s in slugs)


def test_manifest_matches_files_on_disk(datasets, fixtures_dir):
    on_disk = {p.stem for p in fixtures_dir.glob("*.csv")}
    in_manifest = {d["slug"] for d in datasets}
    assert on_disk == in_manifest, (
        f"disk-only: {sorted(on_disk - in_manifest)[:5]}, "
        f"manifest-only: {sorted(in_manifest - on_disk)[:5]}"
    )


def test_manifest_entries_carry_the_documented_fields(datasets, manifest):
    required = {"slug", "url", "source", "license_note", "rows_kept", "cols", "sha256"}
    row_cap = manifest["row_cap"]
    for d in datasets:
        assert required <= set(d), f"{d.get('slug')} missing {required - set(d)}"
        assert d["url"].startswith("https://")
        assert 20 <= d["rows_kept"] <= row_cap
        assert 2 <= d["cols"] <= 60


def test_sha256_spot_check_20(datasets):
    """20 deterministic spot checks: the committed bytes ARE the snapshot."""
    picks = sorted(datasets, key=lambda d: d["slug"])[:: max(1, len(datasets) // 20)][:20]
    assert len(picks) == 20
    for d in picks:
        digest = hashlib.sha256((FIXTURES / f"{d['slug']}.csv").read_bytes()).hexdigest()
        assert digest == d["sha256"], f"sha256 drift in {d['slug']}.csv"


def test_fetch_stats_recorded(manifest):
    stats = manifest["stats"]
    assert stats["datasets_kept"] == len(manifest["datasets"])
    assert stats["github_api_calls"] <= 15
    assert set(stats["per_source"]) >= {
        "openflights", "datasets-org", "fivethirtyeight", "vega", "seaborn"
    }
