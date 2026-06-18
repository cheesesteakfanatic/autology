"""POST /api/agent + GET /api/agent/opener — the agent-loop turn.

The conversation-first shell talks to ONE endpoint: an utterance is
deterministically classified into {question, chart, engineer-op, confirm-joins,
show-model, build} and dispatched to the EXISTING engine path, returning a
narration + typed inline artifacts. Ambiguity surfaces as a clarification (one
question, never a guess) because the downstream endpoint adjudicates it. Keyless,
offline, deterministic — same utterance, same envelope.

These run over the session aviation project (see conftest): a materialized world
WITHOUT a built atlas, so show-model / confirm-joins exercise the unbuilt-world
narration paths while question routing exercises the real ask engine.
"""

from __future__ import annotations

from ontoforge.server.agent import classify

# the M12 competency suite proves these over the aviation estate (see test_ask)
ANSWERABLE = "How many work orders have component 'LANDING GEAR'?"
UNANSWERABLE = "What is the average lifespan of a unicorn?"
AMBIGUOUS = "How many events are recorded for DELTA AIR LINES INC?"


# ------------------------------------------------------ pure classification

def test_classify_is_deterministic_and_total():
    cases = {
        "link orders to customers on customer_id": "engineer-op",
        "treat amount as currency": "engineer-op",
        "rename qty to quantity": "engineer-op",
        "merge duplicate suppliers": "engineer-op",
        "show me the model": "show-model",
        "draw the ontology": "show-model",
        "what joins should I confirm?": "confirm-joins",
        "review the pending links": "confirm-joins",
        "confirm all joins above 0.9": "confirm-joins",
        "add more data to the catalog": "build",
        "wire up the sales dataset": "build",
        "chart revenue by month": "chart",
        "plot the trend over time": "chart",
        "how many work orders are there?": "question",
        "what is the total spend": "question",
    }
    for utt, intent in cases.items():
        assert classify(utt) == intent, utt
        # determinism: identical input → identical output
        assert classify(utt) == classify(utt)


def test_classify_engineer_op_wins_over_chart():
    # 'link ... on ...' is an engineer cue even though it could read as analytic
    assert classify("link orders to products on sku") == "engineer-op"


def test_classify_empty_is_question():
    assert classify("") == "question"
    assert classify("   ") == "question"


# ------------------------------------------------------------- question path

def test_agent_answers_a_grounded_question(client):
    r = client.post("/api/agent", json={"utterance": ANSWERABLE})
    assert r.status_code == 200
    env = r.json()
    assert env["intent"] == "question"
    assert env["clarification"] is None
    answer = next(a for a in env["artifacts"] if a["kind"] == "answer")
    assert answer["columns"] and answer["rows"]
    assert 0.0 < answer["confidence"] <= 1.0
    assert answer["citations"] and answer["citations"][0]["atom_ids"]


def test_agent_question_carries_a_scalar_value_for_a_count(client):
    env = client.post("/api/agent", json={"utterance": ANSWERABLE}).json()
    answer = next(a for a in env["artifacts"] if a["kind"] == "answer")
    # a 1x1 count answer exposes the scalar so the UI renders a big-number card
    if len(answer["rows"]) == 1 and len(answer["rows"][0]) == 1:
        assert answer["value"] == answer["rows"][0][0]


def test_agent_abstains_honestly_as_text(client):
    env = client.post("/api/agent", json={"utterance": UNANSWERABLE}).json()
    assert env["intent"] == "question"
    assert env["artifacts"] == [] or env["artifacts"][0]["kind"] == "text"
    # no fabricated answer artifact on an abstention
    assert all(a["kind"] != "answer" for a in env["artifacts"])
    assert env["narration"]


def test_agent_ambiguous_question_asks_one_clarification(client):
    env = client.post("/api/agent", json={"utterance": AMBIGUOUS}).json()
    assert env["intent"] == "question"
    assert env["clarification"], "ambiguity becomes a question, never a guess"
    assert len(env["followups"]) >= 2
    # the clarification is text, NOT a confident answer
    assert all(a["kind"] != "answer" for a in env["artifacts"])


# --------------------------------------------------------- engineer-op path

def test_agent_engineer_op_returns_a_preview_or_clarification(client):
    env = client.post(
        "/api/agent", json={"utterance": "link Aircraft to WorkOrder on tail_number"}
    ).json()
    assert env["intent"] == "engineer-op"
    kinds = {a["kind"] for a in env["artifacts"]}
    # either a real op preview, or an honest clarification/text — never a guess
    assert kinds <= {"op_preview", "text"}
    if "op_preview" in kinds:
        op_art = next(a for a in env["artifacts"] if a["kind"] == "op_preview")
        assert op_art["op"]["kind"] == "link"
        assert op_art["preview"] is not None


def test_agent_unsupported_engineer_verb_is_text_with_examples(client):
    # 'merge' is a cue (engineer-op) but the slot won't resolve → clarification;
    # use a sentence that trips the cue table yet can't ground.
    env = client.post(
        "/api/agent", json={"utterance": "merge duplicate dragons"}
    ).json()
    assert env["intent"] == "engineer-op"
    assert env["clarification"] or env["artifacts"][0]["kind"] == "text"


# ----------------------------------------------------------- show-model path

def test_agent_show_model_on_unbuilt_atlas_is_an_honest_nudge(client):
    # the session project has no atlas.json → the data-map turn nudges, no 404
    env = client.post("/api/agent", json={"utterance": "show me the data map"}).json()
    assert env["intent"] == "show-model"
    assert env["artifacts"][0]["kind"] == "text"
    assert env["narration"]


# --------------------------------------------------------- confirm-joins path

def test_agent_confirm_joins_returns_a_confirm_artifact(client):
    env = client.post(
        "/api/agent", json={"utterance": "what joins should I confirm?"}
    ).json()
    assert env["intent"] == "confirm-joins"
    art = next(a for a in env["artifacts"] if a["kind"] == "confirm_joins")
    assert "items" in art and "likely_joins" in art


def test_agent_confirm_batch_parses_the_threshold(client):
    env = client.post(
        "/api/agent", json={"utterance": "confirm all joins above 0.9"}
    ).json()
    assert env["intent"] == "confirm-joins"
    art = next(a for a in env["artifacts"] if a["kind"] == "confirm_joins")
    assert art["threshold"] == 0.9


# --------------------------------------------------------------- build path

def test_agent_build_is_non_destructive_text(client):
    env = client.post(
        "/api/agent", json={"utterance": "add more data to the catalog"}
    ).json()
    assert env["intent"] == "build"
    assert env["artifacts"][0]["kind"] == "text"
    assert env["followups"], "build narration offers next actions"


# --------------------------------------------------------------- chart path

def test_agent_chart_routes_to_view(client):
    # the view engine adjudicates: a real chart, a clarification, or an honest
    # abstention — the router only routes, so this is never confidently wrong.
    env = client.post(
        "/api/agent", json={"utterance": "chart work orders by component"}
    ).json()
    assert env["intent"] == "chart"
    kinds = {a["kind"] for a in env["artifacts"]}
    assert kinds <= {"chart", "text"}


# ----------------------------------------------------------- determinism

def test_agent_turn_is_deterministic(client):
    a = client.post("/api/agent", json={"utterance": ANSWERABLE}).json()
    b = client.post("/api/agent", json={"utterance": ANSWERABLE}).json()
    assert a["intent"] == b["intent"]
    assert a["artifacts"] == b["artifacts"]


def test_agent_rejects_empty_utterance(client):
    assert client.post("/api/agent", json={"utterance": ""}).status_code == 422


# --------------------------------------------------------------- the opener

def test_opener_summarizes_the_active_world(client):
    r = client.get("/api/agent/opener")
    assert r.status_code == 200
    op = r.json()
    assert op["narration"]
    assert "stats" in op and "entities" in op["stats"]
    assert isinstance(op["followups"], list)


def test_opener_is_deterministic(client):
    a = client.get("/api/agent/opener").json()
    b = client.get("/api/agent/opener").json()
    assert a == b
