"""GET /api/status — real ledger counters, stages, cost; POST /api/reload."""

from __future__ import annotations


def test_status_counts_match_the_ledger(client, ledger_db):
    out = client.get("/api/status")
    assert out.status_code == 200
    s = out.json()
    assert s["estate"] == "aviation"
    assert s["ledger_exists"] is True
    assert s["limit"] == 150
    (real_atoms,) = ledger_db.execute("SELECT COUNT(*) FROM atom").fetchone()
    assert s["atoms"] == real_atoms > 0
    assert isinstance(s["cost_tokens"], int)


def test_status_reports_pipeline_stages(client):
    s = client.get("/api/status").json()
    assert "materialize" in s["stages"]
    assert s["materialized"]["ontology"] == "gold"
    assert s["materialized"]["entities"] > 0


def test_status_decision_and_artifact_tallies_are_dicts(client):
    s = client.get("/api/status").json()
    for tier, counts in s["decisions_by_tier"].items():
        assert int(tier) >= 0
        assert counts["count"] >= counts["deferred"] >= 0
    for kind, n in s["decisions_by_kind"].items():
        assert isinstance(kind, str) and n >= 1
    for kind, n in s["artifacts"].items():
        assert isinstance(kind, str) and n >= 1


def test_cors_admits_localhost_dev_origins(client):
    out = client.get("/api/status", headers={"Origin": "http://localhost:5173"})
    assert out.headers.get("access-control-allow-origin") == "http://localhost:5173"
    out = client.get("/api/status", headers={"Origin": "https://evil.example.com"})
    assert "access-control-allow-origin" not in out.headers


def test_reload_reopens_the_project(client):
    out = client.post("/api/reload")
    assert out.status_code == 200
    assert out.json() == {"reloaded": True}
    # the world re-opens lazily and still answers
    assert client.get("/api/status").json()["ledger_exists"] is True
