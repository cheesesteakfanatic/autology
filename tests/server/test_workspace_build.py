"""Live build API: POST /api/workspace/build over a tiny synthetic catalog
runs the real pipeline, streams join_found events, switches the active world.

A fresh app over a temp project, its catalog rooted at a synthetic fixtures
tree (two joinable 3-row tables). Fast + deterministic + zero network.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def play_fixtures(tmp_path_factory) -> Path:
    """A minimal fixtures/ tree the catalog can enumerate: one 'meridian'
    corpus dir with two joinable tables."""
    root = tmp_path_factory.mktemp("play-fixtures")
    mer = root / "meridian"
    mer.mkdir()
    pd.DataFrame(
        {"sku": ["s1", "s2", "s3"], "pname": ["Widget", "Gadget", "Gizmo"], "country": ["US", "UK", "US"]}
    ).to_csv(mer / "products.csv", index=False)
    pd.DataFrame(
        {"line_id": ["l1", "l2", "l3"], "sku": ["s1", "s2", "s3"], "qty": ["1", "2", "3"]}
    ).to_csv(mer / "saleslines.csv", index=False)
    return root


@pytest.fixture(scope="module")
def play_app(tmp_path_factory, play_fixtures):
    """A bare project (no pre-built world) whose catalog points at the
    synthetic fixtures; reads start as 'demo' (empty) and switch after a
    build."""
    from ontoforge.server import create_app

    proj = tmp_path_factory.mktemp("play-project")
    (proj / "config.json").write_text(
        json.dumps({"estate": "playground", "ledger": "ledger.sqlite", "hearth_root": "hearth"}),
        encoding="utf-8",
    )
    (proj / "state.json").write_text(json.dumps({"limit": None, "cdc": {}, "stages": []}), encoding="utf-8")
    app = create_app(proj)
    # root the catalog at the synthetic fixtures tree (instance override)
    app.state.world.fixtures_root = play_fixtures
    return app


@pytest.fixture(scope="module")
def play_client(play_app):
    with TestClient(play_app) as c:
        yield c


def _poll_to_done(client, job_id: str, timeout: float = 60.0) -> dict:
    """Poll the build status until terminal, accumulating events."""
    deadline = time.time() + timeout
    seen: list[dict] = []
    last = 0
    while time.time() < deadline:
        r = client.get(f"/api/workspace/build/{job_id}", params={"since": last})
        assert r.status_code == 200, r.text
        snap = r.json()
        seen.extend(snap["events"])
        last = snap["last_seq"]
        if snap["status"] in ("done", "error"):
            snap["events"] = seen
            return snap
        time.sleep(0.05)
    raise AssertionError("build did not finish in time")


def test_catalog_lists_the_synthetic_datasets(play_client) -> None:
    body = play_client.get("/api/catalog").json()
    ids = {d["id"] for d in body["datasets"]}
    assert {"meridian:products", "meridian:saleslines"} <= ids


def test_build_runs_emits_joins_and_switches_active_world(play_client) -> None:
    # before any build, the active world is the (empty) demo
    st0 = play_client.get("/api/workspace/state").json()
    assert st0["active_world"] == "demo"
    assert st0["built"] is False

    r = play_client.post(
        "/api/workspace/build",
        json={"dataset_ids": ["meridian:products", "meridian:saleslines"], "mode": "replace"},
    )
    assert r.status_code == 200, r.text
    job_id = r.json()["job_id"]

    snap = _poll_to_done(play_client, job_id)
    assert snap["status"] == "done", snap.get("error")
    kinds = {e["kind"] for e in snap["events"]}
    assert "join_found" in kinds
    assert "type_found" in kinds
    assert snap["result"]["stats"]["types"] >= 2

    # the active world flipped to the playground
    st1 = play_client.get("/api/workspace/state").json()
    assert st1["active_world"] == "playground"
    assert st1["built"] is True
    assert st1["stats"]["types"] >= 2

    # reads now serve the playground: ontology + atlas resolve
    onto = play_client.get("/api/ontology").json()
    assert len(onto["classes"]) >= 2
    atlas = play_client.get("/api/atlas").json()
    assert atlas["stats"]["classes"] >= 2


def test_join_event_precedes_type_event_over_the_wire(play_client) -> None:
    r = play_client.post(
        "/api/workspace/build",
        json={"dataset_ids": ["meridian:products", "meridian:saleslines"]},
    )
    job_id = r.json()["job_id"]
    snap = _poll_to_done(play_client, job_id)
    seqs = {e["kind"]: e["seq"] for e in snap["events"] if e["kind"] in ("join_found", "type_found")}
    first_join = min((e["seq"] for e in snap["events"] if e["kind"] == "join_found"), default=None)
    first_type = min((e["seq"] for e in snap["events"] if e["kind"] == "type_found"), default=None)
    assert first_join is not None
    if first_type is not None:
        assert first_join < first_type


def test_over_cap_selection_is_rejected_clearly(play_client) -> None:
    ids = [f"meridian:x{i}" for i in range(26)]
    r = play_client.post("/api/workspace/build", json={"dataset_ids": ids})
    assert r.status_code == 422
    assert "max" in r.json()["detail"].lower()


def test_unknown_dataset_id_rejected(play_client) -> None:
    r = play_client.post("/api/workspace/build", json={"dataset_ids": ["meridian:ghost"]})
    assert r.status_code == 422
    assert "unknown" in r.json()["detail"].lower()


def test_poll_unknown_job_is_404(play_client) -> None:
    assert play_client.get("/api/workspace/build/deadbeef").status_code == 404


def test_add_mode_unions_with_existing_selection(play_client) -> None:
    """A 'replace' build then an 'add' build: the add unions the new id with the
    already-built selection, and the workspace echoes the union."""
    r = play_client.post(
        "/api/workspace/build",
        json={"dataset_ids": ["meridian:products"], "mode": "replace"},
    )
    _poll_to_done(play_client, r.json()["job_id"])

    r2 = play_client.post(
        "/api/workspace/build",
        json={"dataset_ids": ["meridian:saleslines"], "mode": "add"},
    )
    snap = _poll_to_done(play_client, r2.json()["job_id"])
    assert snap["status"] == "done", snap.get("error")
    state = play_client.get("/api/workspace/state").json()
    assert set(state["datasets"]) == {"meridian:products", "meridian:saleslines"}
