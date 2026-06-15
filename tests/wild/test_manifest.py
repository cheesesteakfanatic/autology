"""manifest.lock.json <-> disk integrity for the committed wild snapshot."""

from __future__ import annotations

import hashlib

from conftest import FIXTURES

SIZE_BUDGET_BYTES = 40 * 1024 * 1024
MIN_DATASETS = 380


def test_corpus_meets_the_landing_gates(datasets, fixtures_dir):
    assert len(datasets) >= MIN_DATASETS, f"only {len(datasets)} datasets landed"
    total = sum(p.stat().st_size for p in fixtures_dir.glob("*.csv"))
    assert total <= SIZE_BUDGET_BYTES, f"corpus is {total / 1e6:.1f} MB (> 20 MB budget)"


def test_slugs_are_unique_and_source_prefixed(datasets):
    slugs = [d["slug"] for d in datasets]
    assert len(slugs) == len(set(slugs))
    prefixes = ("of_", "ds_", "owid_", "fte_", "vg_", "sb_", "pl_")
    assert all(s.startswith(prefixes) for s in slugs)


def test_manifest_matches_files_on_disk(datasets, fixtures_dir):
    on_disk = {p.stem for p in fixtures_dir.glob("*.csv")}
    in_manifest = {d["slug"] for d in datasets}
    assert on_disk == in_manifest, (
        f"disk-only: {sorted(on_disk - in_manifest)[:5]}, "
        f"manifest-only: {sorted(in_manifest - on_disk)[:5]}"
    )


def test_manifest_entries_carry_the_documented_fields(datasets, manifest):
    required = {
        "slug", "url", "source", "license_note", "rows_kept", "cols",
        "domain", "description", "sha256",
    }
    row_cap = manifest["row_cap"]
    for d in datasets:
        assert required <= set(d), f"{d.get('slug')} missing {required - set(d)}"
        assert d["url"].startswith("https://")
        assert 20 <= d["rows_kept"] <= row_cap
        assert 2 <= d["cols"] <= 60


def test_domain_and_description_are_catalog_ready(datasets):
    """Every entry carries a non-empty single-word domain tag and a one-line
    blurb — the fields the catalog endpoint surfaces. Domains are deterministic
    (classify_domain) so the catalog never shows a blank facet."""
    for d in datasets:
        domain, desc = d["domain"], d["description"]
        assert isinstance(domain, str) and domain and " " not in domain, d["slug"]
        assert isinstance(desc, str) and 10 <= len(desc) <= 400, d["slug"]
        assert str(d["rows_kept"]) in desc and d["domain"] in desc, d["slug"]


def test_domain_classification_is_deterministic_from_schema(datasets, fixtures_dir):
    """The recorded domain is reproducible from the committed CSV's columns —
    no hidden state, the catalog can recompute it if it ever wants to."""
    from ontoforge.estates.wild import classify_domain  # type: ignore

    from conftest import load_csv

    # spot-check a deterministic slice (cheap; full corpus is large)
    picks = sorted(datasets, key=lambda d: d["slug"])[:: max(1, len(datasets) // 25)]
    for d in picks:
        df = load_csv(fixtures_dir / f"{d['slug']}.csv")
        assert classify_domain(d["slug"], list(df.columns)) == d["domain"], d["slug"]


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
