"""POST /api/dashboards — VISTA top-3 with data filled through the
lodestone-backed OQIR executor; GET /api/dashboards — saved proposals."""

from __future__ import annotations

import json


def test_propose_returns_three_vega_lite_dashboards(client):
    out = client.post("/api/dashboards", json={"utterance": "maintenance cost overview"})
    assert out.status_code == 200
    data = out.json()
    assert data["utterance"] == "maintenance cost overview"
    assert len(data["dashboards"]) == 3

    for d in data["dashboards"]:
        assert d["title"]
        assert d["charts"], "every proposal carries charts"
        for chart in d["charts"]:
            spec = chart["vega"]
            assert "vega-lite" in spec["$schema"]
            assert isinstance(spec["data"]["values"], list), "data filled by the executor"

    # the executor really lowered OQIR through the world: some chart has rows
    n_rows = sum(
        len(c["vega"]["data"]["values"])
        for d in data["dashboards"]
        for c in d["charts"]
    )
    assert n_rows > 0


def test_proposals_are_ranked_by_score(client):
    data = client.post("/api/dashboards", json={"utterance": "work orders by status"}).json()
    scores = [d["score"] for d in data["dashboards"]]
    assert scores == sorted(scores, reverse=True)


def test_saved_dashboards_round_trip(client, project):
    # nothing saved yet
    assert client.get("/api/dashboards").json()["dashboards"] == []

    # save one in the CLI's bundle format (dashboards/dashboard_N.json + .vl.json)
    d = project / "dashboards"
    vega = {
        "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
        "mark": "bar",
        "data": {"values": [{"status": "OPEN", "n": 3}]},
    }
    (d / "dashboard_1_chart_0.vl.json").write_text(json.dumps(vega), encoding="utf-8")
    (d / "dashboard_1.json").write_text(
        json.dumps(
            {
                "title": "Work order mix",
                "score": 0.91,
                "rationale": "saved by the CLI",
                "charts": [{"title": "by status", "vega_file": "dashboard_1_chart_0.vl.json"}],
            }
        ),
        encoding="utf-8",
    )

    saved = client.get("/api/dashboards").json()["dashboards"]
    assert len(saved) == 1
    assert saved[0]["title"] == "Work order mix"
    assert saved[0]["charts"][0]["vega"]["mark"] == "bar"
    assert saved[0]["charts"][0]["vega"]["data"]["values"]


def test_empty_utterance_is_rejected(client):
    assert client.post("/api/dashboards", json={"utterance": ""}).status_code == 422
