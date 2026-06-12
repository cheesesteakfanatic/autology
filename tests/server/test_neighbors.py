"""GET /api/entities/{uri}/neighbors — the inspector's link neighborhood:
[{predicate, direction, target_uri, target_label}] in both directions."""

from __future__ import annotations

import pytest


@pytest.fixture(scope="module")
def known_link(project):
    """(subject_uri, predicate, object_uri) of a real current link — read
    through a private Hearth handle (separate sqlite connection)."""
    from ontoforge.hearth import Hearth
    from ontoforge.ledger import SqliteLedger

    ledger = SqliteLedger(str(project / "ledger.sqlite"))
    try:
        hearth = Hearth(project / "hearth", ledger)
        for shard in hearth.links.link_shard_items():
            for link in shard.cells:
                if link.valid.open and link.system.open and link.predicate == "model":
                    return (link.subject_uri, link.predicate, link.object_uri)
        pytest.fail("the demo world committed aircraft->model links")
    finally:
        ledger.close()


def test_neighbors_contains_the_known_outgoing_link(client, known_link):
    subject, predicate, obj = known_link
    out = client.get(f"/api/entities/{subject}/neighbors")
    assert out.status_code == 200
    body = out.json()
    assert set(body) == {"links"}
    hits = [
        l for l in body["links"]
        if l["predicate"] == predicate and l["target_uri"] == obj
    ]
    assert hits, "the committed link is in the neighborhood"
    assert hits[0]["direction"] == "out"
    assert isinstance(hits[0]["target_label"], str) and hits[0]["target_label"], (
        "targets carry a human label for the graph view"
    )


def test_neighbors_sees_the_same_link_reversed(client, known_link):
    subject, predicate, obj = known_link
    out = client.get(f"/api/entities/{obj}/neighbors")
    assert out.status_code == 200
    hits = [
        l for l in out.json()["links"]
        if l["predicate"] == predicate and l["target_uri"] == subject
    ]
    assert hits and hits[0]["direction"] == "in"


def test_every_link_row_has_the_contract_shape(client, known_link):
    subject, _, _ = known_link
    links = client.get(f"/api/entities/{subject}/neighbors").json()["links"]
    assert links
    for l in links:
        assert set(l) == {"predicate", "direction", "target_uri", "target_label"}
        assert l["direction"] in {"out", "in"}
        assert l["target_uri"]
    # deterministic order: (predicate, direction, target_uri)
    keys = [(l["predicate"], l["direction"], l["target_uri"]) for l in links]
    assert keys == sorted(keys)


def test_unknown_entity_is_a_404(client):
    out = client.get("/api/entities/ent://nope/never-existed/neighbors")
    assert out.status_code == 404
