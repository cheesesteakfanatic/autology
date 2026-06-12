"""GET /api/search — the frozen federated-search contract: five kinds, ranked
exact-prefix > word-prefix > substring > fuzzy, interleaved purely by score;
plus question recording through /api/ask (persists across server restarts).

Hearth introspection uses a PRIVATE Hearth/SqliteLedger pair (sqlite allows a
second connection) so the session world's thread-affine connection is never
touched from the test thread.
"""

from __future__ import annotations

import pytest

from ontoforge.contracts import Layer
from ontoforge.server.search import match_score

DISTINCTIVE_QUESTION = "Which mechanics certified the most landing gear repairs?"


@pytest.fixture(scope="module")
def private_hearth(project):
    from ontoforge.hearth import Hearth
    from ontoforge.ledger import SqliteLedger

    ledger = SqliteLedger(str(project / "ledger.sqlite"))
    try:
        yield Hearth(project / "hearth", ledger)
    finally:
        ledger.close()


@pytest.fixture(scope="module")
def known_tail(private_hearth):
    """(tail_number, entity_uri) of a real aircraft in the demo world."""
    shard = next(
        s
        for s in private_hearth.value_shard_items()
        if s.layer is Layer.ENTITY and s.class_uri.endswith("/Aircraft")
    )
    pairs = sorted(
        (str(shard.cells[max(seqs)].value), uri)
        for (uri, prop), seqs in shard.open_by_key.items()
        if prop == "tail_number" and seqs
    )
    assert pairs, "the demo world committed tail numbers"
    return pairs[0]


# ----------------------------------------------------------------- the shape


def test_search_contract_shape(client):
    out = client.get("/api/search", params={"q": "aircraft"})
    assert out.status_code == 200
    body = out.json()
    assert set(body) == {"results"}
    assert body["results"], "the demo world matches 'aircraft'"
    for r in body["results"]:
        assert set(r) == {"kind", "title", "subtitle", "ref", "score"}
        assert r["kind"] in {"class", "entity", "property", "question", "app"}
        assert isinstance(r["title"], str) and r["title"]
        assert isinstance(r["subtitle"], str)
        assert isinstance(r["ref"], str) and r["ref"]
        assert isinstance(r["score"], float) and 0.0 < r["score"] <= 1.0


def test_empty_query_returns_no_results(client):
    assert client.get("/api/search", params={"q": "  "}).json() == {"results": []}


def test_limit_caps_the_result_count(client):
    out = client.get("/api/search", params={"q": "a", "limit": 5}).json()
    assert len(out["results"]) <= 5
    full = client.get("/api/search", params={"q": "a"}).json()
    assert len(full["results"]) <= 20, "default limit is 20"


# ------------------------------------------------------------------- ranking


def test_score_tiers_are_strictly_ordered():
    exact = match_score("aircraft", "aircraft")
    exact_prefix = match_score("air", "aircraft")
    word_prefix = match_score("gear", "landing gear")
    substring = match_score("craft", "aircraft")
    fuzzy = match_score("aircarft", "aircraft")  # transposition
    assert exact == 1.0
    assert 1.0 > exact_prefix > word_prefix > substring > fuzzy > 0.0
    assert exact_prefix > 0.75
    assert word_prefix <= 0.70
    assert substring <= 0.45
    assert fuzzy < 0.30
    assert match_score("zzz", "aircraft") == 0.0


def test_exact_class_name_beats_substring_entity(client):
    out = client.get("/api/search", params={"q": "Aircraft"}).json()["results"]
    top = out[0]
    assert top["kind"] == "class"
    assert top["title"] == "Aircraft"
    assert top["score"] == 1.0
    entity_hits = [r for r in out if r["kind"] == "entity"]
    assert entity_hits, "entity uris contain 'aircraft' as a substring"
    assert all(r["score"] < top["score"] for r in entity_hits)


def test_kinds_interleave_by_score(client):
    out = client.get("/api/search", params={"q": "aircraft", "limit": 20}).json()["results"]
    kinds = {r["kind"] for r in out}
    assert {"class", "property", "entity"} <= kinds, "multiple kinds interleave"
    scores = [r["score"] for r in out]
    assert scores == sorted(scores, reverse=True), "ordering is purely by score"


def test_property_synonyms_match(client):
    # Aircraft.tail_number carries the gold synonym 'registration number'
    out = client.get("/api/search", params={"q": "registration number"}).json()["results"]
    hits = [r for r in out if r["kind"] == "property" and r["title"] == "tail_number"]
    assert hits, "property search matches synonyms, not just names"
    assert hits[0]["ref"].endswith("#tail_number")
    assert "#" in hits[0]["ref"], "property ref is class_uri#prop"


def test_app_registry_is_searchable(client):
    out = client.get("/api/search", params={"q": "review"}).json()["results"]
    apps = [r for r in out if r["kind"] == "app"]
    assert apps and apps[0]["ref"] == "review"
    for app_id in ("ask", "constellation", "entities", "dashboards", "status", "export"):
        hits = client.get("/api/search", params={"q": app_id}).json()["results"]
        assert any(r["kind"] == "app" and r["ref"] == app_id for r in hits)


# ------------------------------------------------------------------ entities


def test_entity_search_finds_a_known_tail_number(client, known_tail):
    tail, uri = known_tail
    out = client.get("/api/search", params={"q": tail}).json()["results"]
    hits = [r for r in out if r["kind"] == "entity"]
    assert hits, f"search finds tail number {tail}"
    top = hits[0]
    assert top["ref"] == uri, "entity ref is the entity uri"
    assert top["score"] > 0.75, "an exact tail-number match lands in the top band"
    assert tail in top["subtitle"], "the subtitle shows the matched value"


def test_entity_search_survives_reload(client, known_tail):
    tail, uri = known_tail
    assert client.post("/api/reload").status_code == 200
    out = client.get("/api/search", params={"q": tail}).json()["results"]
    assert any(r["kind"] == "entity" and r["ref"] == uri for r in out), (
        "the value index rebuilds lazily after /api/reload"
    )


# ----------------------------------------------------------------- questions


def test_ask_records_the_question_in_the_ledger(client, ledger_db):
    out = client.post("/api/ask", json={"question": DISTINCTIVE_QUESTION})
    assert out.status_code == 200
    rows = ledger_db.execute(
        "SELECT payload, prov_ref FROM artifact WHERE kind = 'question'"
    ).fetchall()
    assert any(DISTINCTIVE_QUESTION in payload for payload, _ in rows), (
        "every /api/ask persists the question text as a 'question' artifact"
    )
    assert all(prov_ref for _, prov_ref in rows), "constraint H: questions carry provenance"


def test_search_finds_a_previously_asked_question(client):
    client.post("/api/ask", json={"question": DISTINCTIVE_QUESTION})
    out = client.get("/api/search", params={"q": "mechanics certified"}).json()["results"]
    hits = [r for r in out if r["kind"] == "question"]
    assert hits
    assert hits[0]["ref"] == DISTINCTIVE_QUESTION, "question ref is the question text"
    assert hits[0]["title"] == DISTINCTIVE_QUESTION


def test_questions_survive_a_server_restart(project, client):
    """A brand-new app over the same project still finds the asked question —
    the recording lives in the ledger, not in server memory."""
    from fastapi.testclient import TestClient

    from ontoforge.server import create_app

    client.post("/api/ask", json={"question": DISTINCTIVE_QUESTION})
    with TestClient(create_app(project)) as restarted:
        out = restarted.get("/api/search", params={"q": "mechanics certified"})
        hits = [r for r in out.json()["results"] if r["kind"] == "question"]
        assert any(r["ref"] == DISTINCTIVE_QUESTION for r in hits)


def test_repeat_asks_do_not_duplicate_the_question_artifact(client, ledger_db):
    client.post("/api/ask", json={"question": DISTINCTIVE_QUESTION})
    client.post("/api/ask", json={"question": DISTINCTIVE_QUESTION})
    (n,) = ledger_db.execute(
        "SELECT COUNT(*) FROM artifact WHERE kind = 'question' AND payload LIKE ?",
        (f"%{DISTINCTIVE_QUESTION[:30]}%",),
    ).fetchone()
    assert n == 1, "question recording is idempotent per question text"
