"""POST /api/export + GET /api/exports — AMBER snapshots from the UI.

The produced bundle is verified with amber.verify (manifest hashes, parquet
counts, provenance closure) — the endpoint must emit a REAL bundle, not a
plausible-looking directory."""

from __future__ import annotations

import json
from pathlib import Path

from ontoforge.amber import MANIFEST_NAME, verify


def test_export_produces_a_verifiable_amber_bundle(client, project):
    out = client.post("/api/export")
    assert out.status_code == 200
    b = out.json()
    assert set(b) == {"bundle_dir", "manifest_path", "files", "total_bytes"}

    bundle_dir = Path(b["bundle_dir"])
    assert bundle_dir.parent == project / "exports", "bundles land in <project>/exports/<n>/"
    assert bundle_dir.name.isdigit()
    assert Path(b["manifest_path"]) == bundle_dir / MANIFEST_NAME
    assert b["files"] > 0
    assert b["total_bytes"] > 0

    report = verify(b["bundle_dir"])
    assert report["ok"] is True, f"amber.verify must pass: {report['errors']}"
    assert report["errors"] == []

    # the summary measures the real bundle on disk
    real_files = [p for p in bundle_dir.rglob("*") if p.is_file()]
    assert b["files"] == len(real_files)
    assert b["total_bytes"] == sum(p.stat().st_size for p in real_files)


def test_repeat_exports_get_fresh_numbered_directories(client):
    first = client.post("/api/export").json()
    second = client.post("/api/export").json()
    assert first["bundle_dir"] != second["bundle_dir"]
    assert int(Path(second["bundle_dir"]).name) > int(Path(first["bundle_dir"]).name)


def test_exports_endpoint_lists_past_bundles(client):
    made = client.post("/api/export").json()
    out = client.get("/api/exports")
    assert out.status_code == 200
    exports = out.json()["exports"]
    assert len(exports) >= 1
    by_dir = {e["bundle_dir"]: e for e in exports}
    assert made["bundle_dir"] in by_dir
    listed = by_dir[made["bundle_dir"]]
    assert listed["files"] == made["files"]
    assert listed["total_bytes"] == made["total_bytes"]
    for e in exports:
        assert set(e) == {"bundle_dir", "manifest_path", "files", "total_bytes"}
        assert json.loads(Path(e["manifest_path"]).read_text())["format"] == "amber-bundle"


def test_export_accepts_a_caller_named_out_dir_under_the_project(client, project):
    out = client.post("/api/export", json={"out_dir": "exports/named-bundle"})
    assert out.status_code == 200
    b = out.json()
    assert Path(b["bundle_dir"]) == (project / "exports" / "named-bundle").resolve()
    assert verify(b["bundle_dir"])["ok"] is True
    # and the named bundle shows up in the listing too
    dirs = [e["bundle_dir"] for e in client.get("/api/exports").json()["exports"]]
    assert b["bundle_dir"] in dirs


def test_export_refuses_paths_escaping_the_project(client):
    out = client.post("/api/export", json={"out_dir": "../escapee"})
    assert out.status_code == 409


def test_export_refuses_a_non_empty_target(client):
    made = client.post("/api/export").json()
    rel = Path(made["bundle_dir"]).relative_to(Path(made["bundle_dir"]).parents[1])
    out = client.post("/api/export", json={"out_dir": str(rel)})
    assert out.status_code == 409, "amber refuses to overwrite an existing bundle"
