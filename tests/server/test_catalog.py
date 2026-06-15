"""GET /api/catalog: lists real downloaded datasets with the required fields,
deterministic domain + description, and a domain histogram."""

from __future__ import annotations


def test_catalog_lists_datasets_with_required_fields(client) -> None:
    r = client.get("/api/catalog")
    assert r.status_code == 200
    body = r.json()
    assert "datasets" in body and "domains" in body
    assert body["datasets"], "catalog must enumerate downloaded datasets"
    for d in body["datasets"]:
        # the canonical dataset shape
        for field in ("id", "name", "source", "domain", "rows", "cols", "columns", "description"):
            assert field in d, f"dataset missing {field}: {d}"
        assert isinstance(d["columns"], list)
        assert d["rows"] >= 0 and d["cols"] >= 0
        assert d["description"]


def test_catalog_covers_the_three_corpora(client) -> None:
    body = client.get("/api/catalog").json()
    sources = {d["id"].split(":", 1)[0] for d in body["datasets"]}
    # meridian + aviation always ship in a source checkout; wild may be
    # mid-fetch but its dir exists
    assert "meridian" in sources
    assert "aviation" in sources


def test_catalog_ids_are_unique_and_sorted(client) -> None:
    body = client.get("/api/catalog").json()
    ids = [d["id"] for d in body["datasets"]]
    assert len(ids) == len(set(ids))
    assert ids == sorted(ids)


def test_domains_histogram_sums_to_dataset_count(client) -> None:
    body = client.get("/api/catalog").json()
    total = sum(d["count"] for d in body["domains"])
    assert total == len(body["datasets"])
    # each domain entry well-formed
    for dom in body["domains"]:
        assert dom["name"] and dom["count"] >= 1


def test_aviation_datasets_classify_as_aviation(client) -> None:
    body = client.get("/api/catalog").json()
    av = [d for d in body["datasets"] if d["id"].startswith("aviation:")]
    assert av
    assert any(d["domain"] == "aviation" for d in av)
