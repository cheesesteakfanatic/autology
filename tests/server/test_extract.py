"""POST /api/extract: filtered entity rows + per-cell citations; ?format=csv
streams a CSV download."""

from __future__ import annotations

import csv
import io
import json
import time
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def extract_client(tmp_path_factory):
    from ontoforge.server import create_app

    fixtures = tmp_path_factory.mktemp("ex-fixtures")
    mer = fixtures / "meridian"
    mer.mkdir()
    pd.DataFrame(
        {"sku": ["s1", "s2", "s3", "s4"], "pname": ["Widget", "Gadget", "Gizmo", "Doohickey"],
         "country": ["US", "UK", "US", "DE"]}
    ).to_csv(mer / "products.csv", index=False)
    pd.DataFrame(
        {"line_id": ["l1", "l2", "l3"], "sku": ["s1", "s2", "s3"], "qty": ["1", "2", "3"]}
    ).to_csv(mer / "saleslines.csv", index=False)

    proj = tmp_path_factory.mktemp("ex-project")
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


def _products_uri(client) -> str:
    onto = client.get("/api/ontology").json()
    for c in onto["classes"]:
        if c["name"].lower().startswith("product"):
            return c["uri"]
    # fall back: the class with a 'country' property
    for c in onto["classes"]:
        if any(p["name"] == "country" for p in c["properties"]):
            return c["uri"]
    raise AssertionError("no products class")


def test_extract_returns_rows_and_citations(extract_client) -> None:
    uri = _products_uri(extract_client)
    r = extract_client.post(
        "/api/extract", json={"type_uri": uri, "filters": [], "columns": [], "limit": 100}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["columns"]
    assert len(body["rows"]) == 4  # four products
    # every cell with a value carries citation atoms
    assert body["citations"]
    cited = [c for c in body["citations"] if c["value"] is not None]
    assert cited
    assert all(c["atom_ids"] for c in cited)


def test_extract_filter_narrows_rows(extract_client) -> None:
    uri = _products_uri(extract_client)
    r = extract_client.post(
        "/api/extract",
        json={"type_uri": uri, "filters": [{"prop": "country", "op": "==", "value": "US"}],
              "columns": ["country", "pname"], "limit": 100},
    )
    body = r.json()
    assert body["columns"] == ["country", "pname"]
    assert len(body["rows"]) == 2  # two US products
    country_idx = body["columns"].index("country")
    assert all(row[country_idx] == "US" for row in body["rows"])


def test_extract_limit_caps_rows(extract_client) -> None:
    uri = _products_uri(extract_client)
    body = extract_client.post(
        "/api/extract", json={"type_uri": uri, "filters": [], "columns": [], "limit": 1}
    ).json()
    assert len(body["rows"]) == 1


def test_extract_csv_variant_streams_csv(extract_client) -> None:
    uri = _products_uri(extract_client)
    r = extract_client.post(
        "/api/extract",
        params={"format": "csv"},
        json={"type_uri": uri, "filters": [], "columns": ["sku", "country"], "limit": 100},
    )
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers.get("content-disposition", "")
    rows = list(csv.reader(io.StringIO(r.text)))
    assert rows[0] == ["sku", "country"]
    assert len(rows) == 5  # header + 4 products


def test_extract_unknown_type_404(extract_client) -> None:
    r = extract_client.post(
        "/api/extract", json={"type_uri": "onto://class/ghost", "filters": [], "columns": [], "limit": 10}
    )
    assert r.status_code == 404
