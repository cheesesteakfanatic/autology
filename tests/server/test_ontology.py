"""GET /api/ontology and /api/ontology/class/{uri} — the served world model."""

from __future__ import annotations


def test_ontology_tree_has_aircraft(client):
    out = client.get("/api/ontology")
    assert out.status_code == 200
    onto = out.json()
    names = {c["name"] for c in onto["classes"]}
    assert "Aircraft" in names
    assert onto["version"] >= 0
    assert len(onto["classes"]) >= 5


def test_ontology_edges_connect_known_classes(client):
    onto = client.get("/api/ontology").json()
    uris = {c["uri"] for c in onto["classes"]}
    assert onto["edges"], "the aviation gold ontology has link properties"
    for e in onto["edges"]:
        assert e["source"] in uris
        assert e["target"] in uris
        assert e["link"]


def test_class_detail_round_trips_by_uri(client):
    onto = client.get("/api/ontology").json()
    aircraft = next(c for c in onto["classes"] if c["name"] == "Aircraft")
    out = client.get(f"/api/ontology/class/{aircraft['uri']}")
    assert out.status_code == 200
    detail = out.json()
    assert detail["name"] == "Aircraft"
    assert detail["properties"], "Aircraft has properties"
    prop = detail["properties"][0]
    assert {"uri", "name", "datatype", "is_link"} <= prop.keys()


def test_class_properties_carry_units_and_event_flags(client):
    onto = client.get("/api/ontology").json()
    all_props = [p for c in onto["classes"] for p in c["properties"]]
    assert any(p["unit"] for p in all_props), "gold ontology declares units"
    assert all(isinstance(c["is_event"], bool) for c in onto["classes"])


def test_unknown_class_is_404(client):
    out = client.get("/api/ontology/class/onto://nope/NotAClass")
    assert out.status_code == 404
