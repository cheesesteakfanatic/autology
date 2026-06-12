"""POST /api/ask — cited answers, abstention as a first-class state, caching,
and the /api/atoms resolution of per-cell citations."""

from __future__ import annotations

# a question the M12 competency suite proves answerable over this estate
ANSWERABLE = "How many work orders have component 'LANDING GEAR'?"
# ungroundable terms -> abstention (no unicorns in aviation maintenance)
UNANSWERABLE = "What is the average lifespan of a unicorn?"
# the M12 scope ambiguity: 'events' = NTSB AccidentEvent | any SafetyEvent
AMBIGUOUS = "How many events are recorded for DELTA AIR LINES INC?"


def test_ask_returns_a_cited_answer(client):
    out = client.post("/api/ask", json={"question": ANSWERABLE})
    assert out.status_code == 200
    a = out.json()
    assert a["abstained"] is False
    assert a["clarification"] is None
    assert a["columns"] and a["rows"]
    assert 0.0 < a["confidence"] <= 1.0
    assert a["citations"], "every answer cell cites its source atoms"
    cit = a["citations"][0]
    assert cit["column"] in a["columns"]
    assert cit["atom_ids"]


def test_citation_atom_ids_resolve_via_the_atoms_endpoint(client):
    a = client.post("/api/ask", json={"question": ANSWERABLE}).json()
    atom_id = a["citations"][0]["atom_ids"][0]
    out = client.get(f"/api/atoms/{atom_id}")
    assert out.status_code == 200
    atom = out.json()
    assert atom["atom_id"] == atom_id
    assert atom["uri"].startswith("atom://"), "atoms carry stable source URIs"
    assert atom["value"] is not None


def test_repeat_question_is_served_from_the_cache(client):
    first = client.post("/api/ask", json={"question": ANSWERABLE}).json()
    again = client.post("/api/ask", json={"question": ANSWERABLE}).json()
    assert again["cached"] is True
    assert again["rows"] == first["rows"]


def test_unanswerable_question_abstains_with_a_reason(client):
    out = client.post("/api/ask", json={"question": UNANSWERABLE})
    assert out.status_code == 200
    a = out.json()
    assert a["abstained"] is True
    assert a["abstain_reason"], "abstention always explains itself"
    assert a["rows"] == []
    assert a["citations"] == []


def test_ambiguous_question_asks_one_clarification(client):
    out = client.post("/api/ask", json={"question": AMBIGUOUS})
    assert out.status_code == 200
    a = out.json()
    assert a["abstained"] is False
    assert a["clarification"], "scope ambiguity surfaces as a question, not a guess"
    assert len(a["clarification_options"]) >= 2
    joined = " ".join(a["clarification_options"])
    assert "AccidentEvent" in joined and "SafetyEvent" in joined
    assert a["rows"] == []
    assert a["cached"] is False, "clarification answers are never cached"


def test_clarify_resolves_the_ambiguity_to_a_cited_answer(client):
    first = client.post("/api/ask", json={"question": AMBIGUOUS}).json()
    assert first["clarification"]
    out = client.post(
        "/api/ask/clarify", json={"question": AMBIGUOUS, "choice": "AccidentEvent"}
    )
    assert out.status_code == 200
    a = out.json()
    assert a["clarification"] is None
    assert a["abstained"] is False
    assert a["rows"], "the chosen reading executes to a real answer"
    assert a["citations"] and all(c["atom_ids"] for c in a["citations"])

    # the OTHER branch carries information: a different (wider) count
    again = client.post("/api/ask", json={"question": AMBIGUOUS}).json()
    assert again["clarification"]
    b = client.post(
        "/api/ask/clarify", json={"question": AMBIGUOUS, "choice": "SafetyEvent"}
    ).json()
    assert b["abstained"] is False
    assert b["rows"] != a["rows"], "structurally different readings, different answers"


def test_clarify_with_an_unoffered_choice_abstains(client):
    assert client.post("/api/ask", json={"question": AMBIGUOUS}).json()["clarification"]
    out = client.post("/api/ask/clarify", json={"question": AMBIGUOUS, "choice": "teapot"})
    assert out.status_code == 200
    a = out.json()
    assert a["abstained"] is True
    assert "option" in a["abstain_reason"]


def test_clarify_without_ambiguity_just_answers(client):
    # the question resolves on re-ask; the endpoint answers rather than erroring
    out = client.post("/api/ask/clarify", json={"question": ANSWERABLE, "choice": 0})
    assert out.status_code == 200
    a = out.json()
    assert a["abstained"] is False or a["abstain_reason"]


def test_empty_question_is_rejected(client):
    assert client.post("/api/ask", json={"question": ""}).status_code == 422


def test_unknown_atom_is_404(client):
    assert client.get("/api/atoms/ffffffffffffffff").status_code == 404
