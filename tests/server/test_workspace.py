"""GET/PUT /api/workspace — the window-layout blob, persisted atomically at
<project>/workspace.json (tmp + rename; a torn file is impossible)."""

from __future__ import annotations

import json

import pytest

LAYOUT = {
    "windows": [
        {"app": "ask", "x": 40, "y": 32, "w": 720, "h": 480, "z": 2},
        {"app": "constellation", "x": 800, "y": 60, "w": 560, "h": 520, "z": 1},
    ],
    "dock": ["ask", "entities", "review"],
    "theme": "amber",
}


def test_workspace_defaults_to_an_empty_object(client, project):
    (project / "workspace.json").unlink(missing_ok=True)
    out = client.get("/api/workspace")
    assert out.status_code == 200
    assert out.json() == {}


def test_workspace_put_get_round_trip(client, project):
    put = client.put("/api/workspace", json=LAYOUT)
    assert put.status_code == 200
    assert put.json() == LAYOUT, "PUT echoes what was stored"
    assert client.get("/api/workspace").json() == LAYOUT

    # and it is REALLY on disk where the spec says
    on_disk = json.loads((project / "workspace.json").read_text(encoding="utf-8"))
    assert on_disk == LAYOUT


def test_workspace_accepts_arbitrary_json(client):
    for blob in ([1, 2, 3], {"nested": {"deep": [True, None, 0.5]}}, "just a string"):
        assert client.put("/api/workspace", json=blob).status_code == 200
        assert client.get("/api/workspace").json() == blob


def test_workspace_rejects_non_json_bodies(client):
    out = client.put("/api/workspace", content=b"{not json", headers={"content-type": "application/json"})
    assert out.status_code == 422


def test_workspace_overwrite_leaves_no_tmp_file(client, project):
    for i in range(5):
        assert client.put("/api/workspace", json={"rev": i}).status_code == 200
    assert client.get("/api/workspace").json() == {"rev": 4}
    leftovers = [p.name for p in project.iterdir() if "workspace" in p.name and p.name != "workspace.json"]
    assert leftovers == [], "the atomic write cleans up its tmp file"


def test_atomic_write_never_tears_the_old_blob(client, project):
    """If serialization fails mid-write, the previously persisted blob is
    untouched — the write is all-or-nothing."""
    from ontoforge.server.world import write_json_atomic

    assert client.put("/api/workspace", json=LAYOUT).status_code == 200
    path = project / "workspace.json"
    before = path.read_text(encoding="utf-8")
    with pytest.raises(TypeError):
        write_json_atomic(path, {"bad": object()})  # not JSON-serializable
    assert path.read_text(encoding="utf-8") == before
    assert json.loads(before) == LAYOUT
