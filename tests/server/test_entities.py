"""GET /api/entities/{uri}?stance=... — the time-travel read: property card
under a temporal stance + full per-property bitemporal history (HEARTH §4.4).
"""

from __future__ import annotations

import pytest

from ontoforge.contracts import Layer


@pytest.fixture(scope="module")
def aircraft_uri(client, world) -> str:
    """A real aircraft entity from the materialized world — one that carries a
    registration-windowed tail_number (the time-travel showcase)."""
    onto = client.get("/api/ontology").json()
    class_uri = next(c["uri"] for c in onto["classes"] if c["name"] == "Aircraft")
    with world.lock:
        shard = next(
            s
            for s in world.hearth.value_shard_items()
            if s.layer is Layer.ENTITY and s.class_uri == class_uri
        )
        uris = sorted(shard.by_entity)
    assert uris, "the world build committed aircraft entities"
    return uris[0]


def test_entity_card_under_the_current_stance(client, aircraft_uri):
    out = client.get(f"/api/entities/{aircraft_uri}")
    assert out.status_code == 200
    e = out.json()
    assert e["uri"] == aircraft_uri
    assert e["stance"] == "current"
    assert any(c.endswith("Aircraft") for c in e["classes"])
    assert e["properties"], "the card carries current property values"
    assert "serial_number" in e["properties"]


def test_entity_history_is_bitemporal_and_provenance_grounded(client, aircraft_uri):
    e = client.get(f"/api/entities/{aircraft_uri}").json()
    assert e["history"], "every property exposes its audit trail"
    for prop, cells in e["history"].items():
        assert cells, f"history for {prop} is non-empty"
        for c in cells:
            assert c["prov_ref"], "constraint H: no cell without provenance"
            assert c["system_from"], "system time is always bounded below"
            assert isinstance(c["confidence"], float)
            assert isinstance(c["src_rank"], int)
            assert isinstance(c["is_current"], bool)


def test_as_of_stance_time_travels_the_card(client, aircraft_uri):
    e = client.get(f"/api/entities/{aircraft_uri}").json()
    # tail_number is committed under the FAA registration window: bounded
    # valid interval -> absent from the CURRENT card, present under as_of
    tn_cells = e["history"]["tail_number"]
    window_start = tn_cells[0]["valid_from"]
    assert window_start, "the registration window is bounded below"
    assert "tail_number" not in e["properties"]

    out = client.get(
        f"/api/entities/{aircraft_uri}", params={"stance": f"as_of:{window_start}"}
    )
    assert out.status_code == 200
    then = out.json()
    assert then["stance"].startswith("as_of:")
    assert then["properties"]["tail_number"] == tn_cells[0]["value"]


def test_as_of_before_all_validity_yields_an_empty_card(client, aircraft_uri):
    out = client.get(
        f"/api/entities/{aircraft_uri}", params={"stance": "as_of:1950-01-01T00:00:00"}
    )
    assert out.status_code == 200
    e = out.json()
    assert e["properties"] == {}
    assert e["history"], "history is stance-independent: the full audit trail"


def test_entity_prov_refs_resolve_through_the_provenance_endpoint(client, aircraft_uri):
    e = client.get(f"/api/entities/{aircraft_uri}").json()
    prov_ref = next(c["prov_ref"] for cells in e["history"].values() for c in cells)
    out = client.get(f"/api/provenance/{prov_ref}")
    assert out.status_code == 200
    assert out.json()["n_atoms"] >= 1


def test_unknown_entity_is_404(client):
    assert client.get("/api/entities/ent://nope/not-a-thing").status_code == 404


def test_bad_stance_is_422(client, aircraft_uri):
    assert (
        client.get(f"/api/entities/{aircraft_uri}", params={"stance": "lunchtime"}).status_code
        == 422
    )
    assert (
        client.get(
            f"/api/entities/{aircraft_uri}", params={"stance": "as_of:not-a-date"}
        ).status_code
        == 422
    )
