"""The review loop (§4.8): GET /api/review queue, POST verdicts as append-only
ledger artifacts, and recalibration through the spine at 20 verdicts."""

from __future__ import annotations

import json

import pytest

from ontoforge.contracts import DecisionKind

ANSWERABLE = "How many work orders have component 'LANDING GEAR'?"
THRESHOLD = 20


class SpineRecorder:
    """Wraps the world's real spine; records recalibrate() calls, delegates everything."""

    def __init__(self, inner):
        self._inner = inner
        self.calls: list[tuple] = []

    def recalibrate(self, kind, samples):
        self.calls.append((kind, list(samples)))
        return self._inner.recalibrate(kind, samples)

    def __getattr__(self, name):
        return getattr(self._inner, name)


@pytest.fixture(scope="module", autouse=True)
def seeded(client):
    """Answering a question routes a QI decision through the ledger-recording
    spine — that decision is what the review queue surfaces."""
    out = client.post("/api/ask", json={"question": ANSWERABLE})
    assert out.status_code == 200


def test_queue_surfaces_the_deferred_qi_decision(client):
    out = client.get("/api/review")
    assert out.status_code == 200
    data = out.json()
    assert data["threshold"] == THRESHOLD
    qi = [it for it in data["items"] if it["kind"] == "qi"]
    assert qi, "the ask's low-confidence QI decision is queued for review"
    item = qi[0]
    assert item["decision_id"].startswith("qi")
    assert item["deferred_to_human"] or item["confidence"] < 0.7
    assert item["conformal_set"]
    assert item["outcome"] in item["conformal_set"]


def test_accept_appends_an_append_only_verdict_artifact(client, ledger_db):
    item = next(
        it for it in client.get("/api/review").json()["items"] if it["kind"] == "qi"
    )
    before = ledger_db.execute(
        "SELECT COUNT(*) FROM artifact WHERE kind = 'review-verdict'"
    ).fetchone()[0]

    out = client.post(
        f"/api/review/{item['decision_id']}",
        json={"verdict": "accept", "note": "looks right"},
    )
    assert out.status_code == 200
    v = out.json()
    assert v["verdict"] == "accept"
    assert v["kind"] == "qi"
    assert v["verdicts_for_kind"] >= 1
    assert v["threshold"] == THRESHOLD

    # REALLY persisted: a second sqlite connection sees the artifact
    rows = ledger_db.execute(
        "SELECT payload FROM artifact WHERE kind = 'review-verdict' ORDER BY seq"
    ).fetchall()
    assert len(rows) == before + 1
    payload = json.loads(rows[-1][0])
    assert payload["decision_id"] == item["decision_id"]
    assert payload["verdict"] == "accept"
    assert payload["true_outcome"] == item["outcome"]
    assert payload["atom_id"], "constraint H: the verdict is grounded in a minted atom"

    # the adjudicated decision leaves the queue
    remaining = {it["decision_id"] for it in client.get("/api/review").json()["items"]}
    assert item["decision_id"] not in remaining


def test_twenty_verdicts_trigger_spine_recalibration(client, world, ledger_db):
    # the ledger/spine already live on the TestClient portal thread (seeded);
    # wrap the spine so the recalibration call is directly observable
    recorder = SpineRecorder(world.spine)
    world._spine = recorder
    try:
        n = client.get("/api/review").json()["verdicts"].get("qi", 0)
        assert 0 < n < THRESHOLD
        (decision_id,) = ledger_db.execute(
            "SELECT decision_id FROM decision WHERE decision_id LIKE 'qi%' LIMIT 1"
        ).fetchone()

        last = None
        for i in range(THRESHOLD - n):
            verdict = "accept" if i % 2 == 0 else "reject"
            out = client.post(
                f"/api/review/{decision_id}",
                json={"verdict": verdict, "note": f"calibration sample {n + i + 1}"},
            )
            assert out.status_code == 200
            last = out.json()
            assert last["recalibrated"] is (last["verdicts_for_kind"] == THRESHOLD)

        assert last["verdicts_for_kind"] == THRESHOLD
        assert last["recalibrated"] is True
        assert last["recalibrations_for_kind"] == 1
    finally:
        world._spine = recorder._inner

    # observable through the wrapped spine: one replay of all 20 samples
    assert len(recorder.calls) == 1
    kind, samples = recorder.calls[0]
    assert kind == DecisionKind.QI
    assert len(samples) == THRESHOLD
    assert all(s.true_outcome for s in samples)
    assert all(s.kind == DecisionKind.QI for s in samples)

    # the calibrator for the kind now exists (a fit below MIN_FIT_SAMPLES=50
    # is a documented no-op, but the loop and its artifact are real)
    assert world.spine.calibrator(DecisionKind.QI) is not None

    # and the recalibration artifact is in the ledger, provenance-summed
    rows = ledger_db.execute(
        "SELECT payload, prov_ref FROM artifact WHERE kind = 'recalibration'"
    ).fetchall()
    assert len(rows) == 1
    payload = json.loads(rows[0][0])
    assert payload["kind"] == "qi"
    assert payload["n_samples"] == THRESHOLD
    assert rows[0][1], "the recalibration cites the verdict atoms"


def test_verdict_on_unknown_decision_is_404(client):
    out = client.post("/api/review/qi-not-a-real-decision", json={"verdict": "accept"})
    assert out.status_code == 404


def test_bad_verdict_value_is_422(client, ledger_db):
    (decision_id,) = ledger_db.execute(
        "SELECT decision_id FROM decision WHERE decision_id LIKE 'qi%' LIMIT 1"
    ).fetchone()
    out = client.post(f"/api/review/{decision_id}", json={"verdict": "maybe"})
    assert out.status_code == 422
