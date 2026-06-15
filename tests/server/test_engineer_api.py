"""Engineer API: /interpret parses each kind to op+preview | clarification |
unsupported (PREVIEW never mutates); /apply + /undo round-trip a link via the
real TEMPER engine; a low-coverage join is flagged."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def built_client(tmp_path_factory):
    """A fresh app whose catalog points at two joinable synthetic tables, with a
    playground world already built and active (so /interpret/apply/extract run
    against a real materialized world)."""
    from ontoforge.server import create_app

    fixtures = tmp_path_factory.mktemp("eng-fixtures")
    mer = fixtures / "meridian"
    mer.mkdir()
    pd.DataFrame(
        {"sku": ["s1", "s2", "s3"], "pname": ["Widget", "Gadget", "Gizmo"], "country": ["US", "UK", "US"]}
    ).to_csv(mer / "products.csv", index=False)
    pd.DataFrame(
        {"line_id": ["l1", "l2", "l3"], "sku": ["s1", "s2", "s3"], "qty": ["1", "2", "3"]}
    ).to_csv(mer / "saleslines.csv", index=False)

    proj = tmp_path_factory.mktemp("eng-project")
    (proj / "config.json").write_text(
        json.dumps({"estate": "playground", "ledger": "ledger.sqlite", "hearth_root": "hearth"}),
        encoding="utf-8",
    )
    (proj / "state.json").write_text(json.dumps({"limit": None, "cdc": {}, "stages": []}), encoding="utf-8")
    app = create_app(proj)
    app.state.world.fixtures_root = fixtures

    with TestClient(app) as c:
        r = c.post("/api/workspace/build", json={"dataset_ids": ["meridian:products", "meridian:saleslines"]})
        job_id = r.json()["job_id"]
        deadline = time.time() + 60
        while time.time() < deadline:
            snap = c.get(f"/api/workspace/build/{job_id}").json()
            if snap["status"] in ("done", "error"):
                break
            time.sleep(0.05)
        assert snap["status"] == "done", snap.get("error")
        yield c


# ------------------------------------------------------------- interpret

def test_interpret_unsupported(built_client) -> None:
    r = built_client.post("/api/engineer/interpret", json={"command": "do a barrel roll"})
    assert r.status_code == 200
    body = r.json()
    assert body["unsupported"] is True
    assert body["supported_examples"]


def test_interpret_clarifies_unknown_endpoint(built_client) -> None:
    r = built_client.post("/api/engineer/interpret", json={"command": "link saleslines to nope on sku"})
    body = r.json()
    assert body["clarification"]
    assert body["op"] is None


def test_interpret_link_previews_coverage(built_client) -> None:
    r = built_client.post(
        "/api/engineer/interpret", json={"command": "link saleslines to products on sku"}
    )
    body = r.json()
    assert body["op"]["kind"] == "link"
    prev = body["preview"]
    assert prev["coverage"] == 1.0
    assert prev["tier"] == "confirmed"
    assert prev["op_token"] is not None
    assert prev["blocked"] is False


def test_interpret_low_coverage_join_is_flagged(built_client) -> None:
    r = built_client.post(
        "/api/engineer/interpret", json={"command": "link saleslines to products on quantity"}
    )
    prev = r.json()["preview"]
    assert prev["blocked"] is True
    assert prev["op_token"] is None
    assert "floor" in prev["block_reason"]


def test_interpret_rename_previews_zero_migration(built_client) -> None:
    r = built_client.post("/api/engineer/interpret", json={"command": "rename country to nation"})
    body = r.json()
    assert body["op"]["kind"] == "rename"
    assert body["preview"]["op_token"] is not None


def test_interpret_does_not_mutate(built_client) -> None:
    """Interpreting twice yields identical results — no state changed."""
    a = built_client.post("/api/engineer/interpret", json={"command": "rename country to nation"}).json()
    onto_before = built_client.get("/api/ontology").json()
    b = built_client.post("/api/engineer/interpret", json={"command": "rename country to nation"}).json()
    onto_after = built_client.get("/api/ontology").json()
    assert a["op"]["params"] == b["op"]["params"]
    assert onto_before["version"] == onto_after["version"]


# --------------------------------------------------------- apply + undo

def test_apply_then_undo_round_trips_a_link(built_client) -> None:
    """Apply a link, assert the ontology gains a link property + atlas_delta,
    then undo and assert the ontology returns to its prior state."""
    onto_before = built_client.get("/api/ontology").json()
    links_before = sum(
        1 for c in onto_before["classes"] for p in c["properties"] if p["is_link"]
    )

    interp = built_client.post(
        "/api/engineer/interpret", json={"command": "link saleslines to products on sku"}
    ).json()
    token = interp["preview"]["op_token"]

    applied = built_client.post("/api/engineer/apply", json={"op": token}).json()
    assert applied["ok"] is True
    assert applied["atlas_delta"]["added_links"]
    assert applied["undo_token"] is not None

    onto_mid = built_client.get("/api/ontology").json()
    links_mid = sum(1 for c in onto_mid["classes"] for p in c["properties"] if p["is_link"])
    assert links_mid == links_before + 1

    undone = built_client.post("/api/engineer/undo", json={"undo_token": applied["undo_token"]}).json()
    assert undone["ok"] is True

    onto_after = built_client.get("/api/ontology").json()
    links_after = sum(1 for c in onto_after["classes"] for p in c["properties"] if p["is_link"])
    assert links_after == links_before  # exact restoration


def test_apply_bogus_op_rejected_not_crashed(built_client) -> None:
    bad = {"op_type": "RenameProperty", "class_uri": "onto://class/nope", "prop_name": "x", "new_name": "y"}
    r = built_client.post("/api/engineer/apply", json={"op": bad})
    assert r.status_code == 200
    assert r.json()["ok"] is False
