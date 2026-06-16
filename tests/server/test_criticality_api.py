"""GET /api/criticality over a project directory (Crew C, §6 integration).

The endpoint is a READ-ONLY view of the process-local criticality model, which
is fed ADDITIVELY by the existing handlers: /api/ask records a 'query' usage
event for the class uris an answer touched. These tests assert:

* an UNBUILT world (no ontology yet) returns an empty, well-formed list and
  NEVER raises;
* on a real demo world, after some asks, the endpoint returns a score-sorted,
  schema-valid list whose nodes are ontology class uris;
* recording is lazy + deterministic (a repeated GET is byte-identical);
* the existing endpoints (/api/ask, /api/ontology) are unchanged.

The criticality model is process-global, so each test uses its OWN TestClient
and calls ``usage.reset()`` to isolate state. Zero network: TestClient drives
the ASGI app in-process.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from ontoforge.server import schemas as S
from ontoforge.server import usage as criticality_usage
from ontoforge.server.app import create_app

ASKS = [
    "how many aircraft are there?",
    "list the airports",
    "what airlines operate flights?",
]


def test_unbuilt_world_returns_empty_without_raising(tmp_path: Path) -> None:
    """No ontology on disk -> empty elements, status 200, never a 500."""
    proj = tmp_path / "empty"
    proj.mkdir()
    (proj / "config.json").write_text(
        json.dumps(
            {
                "estate": "x",
                "ledger": "ledger.sqlite",
                "hearth_root": "hearth",
                "fixtures_dir": str(tmp_path / "nope"),
            }
        ),
        encoding="utf-8",
    )
    (proj / "state.json").write_text(
        json.dumps({"limit": None, "cdc": {}, "stages": []}), encoding="utf-8"
    )
    criticality_usage.reset()
    app = create_app(proj)
    try:
        with TestClient(app) as c:
            r = c.get("/api/criticality?top=5")
            assert r.status_code == 200
            body = r.json()
            assert body == {"elements": [], "total": 0}
            # validates against the contract
            S.CriticalityOut(**body)
    finally:
        criticality_usage.reset()


def test_criticality_is_score_sorted_after_asks(project) -> None:
    """On the demo world, asks feed the model and the endpoint returns a
    well-formed, score-descending list of ontology class nodes."""
    criticality_usage.reset()
    app = create_app(project)
    try:
        with TestClient(app) as c:
            # baseline: lazy model has scored nothing yet
            base = c.get("/api/criticality").json()
            assert base["total"] == 0
            assert base["elements"] == []

            for q in ASKS:
                ask = c.post("/api/ask", json={"question": q})
                assert ask.status_code == 200  # existing endpoint unchanged

            body = c.get("/api/criticality?top=8").json()
            out = S.CriticalityOut(**body)  # schema-valid
            assert out.total == len(out.elements)
            assert out.elements, "asks should have produced scored elements"

            scores = [e.score for e in out.elements]
            assert scores == sorted(scores, reverse=True), "must be score-sorted desc"

            # every node is a real ontology class uri with the right kind
            onto_uris = _ontology_uris(c)
            for e in out.elements:
                assert e.kind == "class"
                assert 0.0 <= e.score <= 1.0
                assert e.uri in onto_uris
                assert e.label  # non-empty human label
    finally:
        criticality_usage.reset()


def test_repeated_get_is_deterministic(project) -> None:
    """Lazy recompute is a no-op on a stable log: two GETs are byte-identical."""
    criticality_usage.reset()
    app = create_app(project)
    try:
        with TestClient(app) as c:
            for q in ASKS:
                c.post("/api/ask", json={"question": q})
            first = c.get("/api/criticality?top=10").json()
            second = c.get("/api/criticality?top=10").json()
            assert first == second
    finally:
        criticality_usage.reset()


def test_top_param_bounds_the_list(project) -> None:
    """?top=N caps the returned elements; top<=0 yields an empty list."""
    criticality_usage.reset()
    app = create_app(project)
    try:
        with TestClient(app) as c:
            for q in ASKS:
                c.post("/api/ask", json={"question": q})
            two = S.CriticalityOut(**c.get("/api/criticality?top=2").json())
            assert len(two.elements) <= 2
            zero = S.CriticalityOut(**c.get("/api/criticality?top=0").json())
            assert zero.elements == []
            assert zero.total == 0
    finally:
        criticality_usage.reset()


def _ontology_uris(client: TestClient) -> set[str]:
    onto = client.get("/api/ontology").json()
    return {cls["uri"] for cls in onto.get("classes", [])}
