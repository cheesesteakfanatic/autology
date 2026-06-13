"""GET /api/atlas + /api/atlas/link over a project directory.

The endpoints serve <project>/atlas.json verbatim through the pydantic
contract the SHIPPED UI consumes (js/apps/constellation.js); the committed
250-class synthetic fixture is parsed through the same schema — THE
compatibility test between this API and the already-built front end.
Zero network: TestClient drives the ASGI app in-process.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ontoforge.server import schemas as S
from ontoforge.server.app import create_app

ATLAS_FIXTURE = Path(__file__).parent / "fixtures" / "atlas_synthetic_250.json"


def mini_atlas() -> dict:
    """A hand-written atlas in the exact UI contract shape."""
    ev = {
        "coverage": 1.0, "overlap_count": 40,
        "sample_shared_values": ["AP01", "AP02"],
        "name_similarity": 0.0, "semtype_match": False,
    }
    return {
        "components": [
            {"id": "c0", "label": "Flight",
             "class_uris": ["onto://class/flight", "onto://class/airport"],
             "dataset_count": 2, "is_silo": False},
            {"id": "c1", "label": "Staff",
             "class_uris": ["onto://class/staff"], "dataset_count": 1, "is_silo": True},
            {"id": "c2", "label": "routes",
             "class_uris": ["table://of_routes"], "dataset_count": 1, "is_silo": True},
        ],
        "links": [
            {"src_class": "onto://class/flight", "dst_class": "onto://class/airport",
             "src_prop": "origin", "dst_prop": "airport_id",
             "tier": "confirmed", "score": 0.8, "evidence": ev},
            {"src_class": "table://of_routes", "dst_class": "onto://class/airport",
             "src_prop": "src_apt", "dst_prop": "airport_id",
             "tier": "likely", "score": 0.55,
             "evidence": {"coverage": 0.61, "overlap_count": 9,
                          "sample_shared_values": ["AP03"],
                          "name_similarity": 0.2, "semtype_match": True}},
            {"src_class": "onto://class/staff", "dst_class": "onto://class/airport",
             "src_prop": "base", "dst_prop": "airport_id",
             "tier": "hint", "score": 0.3,
             "evidence": {"coverage": 0.0, "overlap_count": 0,
                          "sample_shared_values": [],
                          "name_similarity": 0.0, "semtype_match": True}},
        ],
        "stats": {"classes": 4, "components": 3, "silos": 2,
                  "confirmed": 1, "likely": 1, "hint": 1},
    }


@pytest.fixture()
def atlas_project(tmp_path) -> Path:
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "config.json").write_text(
        json.dumps({"estate": "generic", "source_dir": str(tmp_path),
                    "ledger": "ledger.sqlite", "hearth_root": "hearth"}),
        encoding="utf-8",
    )
    (proj / "atlas.json").write_text(json.dumps(mini_atlas()), encoding="utf-8")
    return proj


@pytest.fixture()
def atlas_client(atlas_project):
    with TestClient(create_app(atlas_project)) as c:
        yield c, atlas_project


# ----------------------------------------------------------------- GET /atlas


def test_get_atlas_serves_the_persisted_payload(atlas_client):
    client, _ = atlas_client
    r = client.get("/api/atlas")
    assert r.status_code == 200
    assert r.json() == mini_atlas()


def test_get_atlas_404_with_rebuild_instructions(atlas_client):
    client, proj = atlas_client
    (proj / "atlas.json").unlink()
    client.post("/api/reload")
    r = client.get("/api/atlas")
    assert r.status_code == 404
    detail = r.json()["detail"]
    assert "atlas not built" in detail
    assert f"python -m ontoforge.pipeline.atlas {proj}" in detail


def test_get_atlas_404_on_a_project_never_built(client):
    """The session aviation project (tests/server/conftest.py) has no
    atlas.json — the endpoint degrades exactly like the UI expects."""
    r = client.get("/api/atlas")
    assert r.status_code == 404
    assert "atlas not built" in r.json()["detail"]


def test_reload_drops_the_atlas_cache(atlas_client):
    client, proj = atlas_client
    p = proj / "atlas.json"
    assert client.get("/api/atlas").json()["stats"]["hint"] == 1

    # rewrite the file but FORCE the old mtime: only /api/reload can bust now
    st = p.stat()
    changed = mini_atlas()
    changed["stats"]["hint"] = 99
    p.write_text(json.dumps(changed), encoding="utf-8")
    os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns))
    assert client.get("/api/atlas").json()["stats"]["hint"] == 1, "cached"

    assert client.post("/api/reload").json() == {"reloaded": True}
    assert client.get("/api/atlas").json()["stats"]["hint"] == 99, "reload refreshed"


def test_changed_file_refreshes_without_reload(atlas_client):
    """A rebuilt atlas.json (new mtime) is picked up on the next request."""
    client, proj = atlas_client
    changed = mini_atlas()
    changed["stats"]["likely"] = 7
    p = proj / "atlas.json"
    p.write_text(json.dumps(changed), encoding="utf-8")
    os.utime(p)  # ensure the mtime moves even on coarse filesystems
    assert client.get("/api/atlas").json()["stats"]["likely"] == 7


# ------------------------------------------------------------ GET /atlas/link


def test_atlas_link_returns_matching_links_with_full_evidence(atlas_client):
    client, _ = atlas_client
    r = client.get("/api/atlas/link", params={
        "src": "onto://class/flight", "dst": "onto://class/airport",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["src"] == "onto://class/flight"
    assert body["dst"] == "onto://class/airport"
    assert len(body["links"]) == 1
    lk = body["links"][0]
    assert lk["tier"] == "confirmed"
    assert lk["evidence"]["sample_shared_values"] == ["AP01", "AP02"]
    assert set(lk["evidence"]) == {
        "coverage", "overlap_count", "sample_shared_values",
        "name_similarity", "semtype_match",
    }


def test_atlas_link_matches_either_direction_and_pseudo_classes(atlas_client):
    client, _ = atlas_client
    r = client.get("/api/atlas/link", params={
        "src": "onto://class/airport", "dst": "table://of_routes",  # reversed
    })
    assert r.status_code == 200
    links = r.json()["links"]
    assert len(links) == 1 and links[0]["tier"] == "likely"


def test_atlas_link_unknown_pair_is_empty_not_an_error(atlas_client):
    client, _ = atlas_client
    r = client.get("/api/atlas/link", params={"src": "onto://x", "dst": "onto://y"})
    assert r.status_code == 200
    assert r.json()["links"] == []


def test_atlas_link_404_when_not_built(atlas_client):
    client, proj = atlas_client
    (proj / "atlas.json").unlink()
    client.post("/api/reload")
    r = client.get("/api/atlas/link", params={"src": "a", "dst": "b"})
    assert r.status_code == 404


# --------------------------------------------- UI fixture compatibility (THE)


def test_schema_parses_the_shipped_ui_fixture():
    """The 250-class synthetic atlas the UI was built and scale-tested
    against parses through the API's pydantic contract — fields, tiers,
    evidence and all — and survives a serialization round-trip."""
    fixture = json.loads(ATLAS_FIXTURE.read_text(encoding="utf-8"))
    parsed = S.AtlasOut(**fixture)
    assert len(parsed.components) == len(fixture["components"])
    assert len(parsed.links) == len(fixture["links"])
    assert parsed.stats.classes == fixture["stats"]["classes"]
    assert {lk.tier for lk in parsed.links} == {"confirmed", "likely", "hint"}

    dumped = parsed.model_dump()
    assert dumped["stats"] == fixture["stats"]
    for got, want in zip(dumped["links"], fixture["links"]):
        assert got == want
    for got, want in zip(dumped["components"], fixture["components"]):
        assert got == want


def test_pipeline_report_payload_parses_through_the_api_schema():
    """What build_atlas persists is exactly what the endpoint will emit:
    AtlasReport.to_payload() validates against AtlasOut field-for-field."""
    from ontoforge.pipeline.atlas import (
        AtlasComponent, AtlasEvidence, AtlasLink, AtlasReport,
    )

    report = AtlasReport(
        components=[AtlasComponent(id="c0", label="X", class_uris=("u1",),
                                   dataset_count=1, is_silo=True)],
        links=[AtlasLink(src_class="u1", dst_class="u2", src_prop="a",
                         dst_prop="b", tier="hint", score=0.3,
                         evidence=AtlasEvidence(coverage=0.0, overlap_count=0))],
        stats={"classes": 1, "components": 1, "silos": 1,
               "confirmed": 0, "likely": 0, "hint": 1},
    )
    parsed = S.AtlasOut(**report.to_payload())
    assert parsed.links[0].evidence.semtype_match is False
    assert parsed.components[0].is_silo is True
