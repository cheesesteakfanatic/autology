"""GET / serves the SPA; every static reference in index.html exists on disk
and is fetchable — no broken script/style/vendor links."""

from __future__ import annotations

import re
from pathlib import Path

STATIC_DIR = Path(__file__).resolve().parents[2] / "src" / "ontoforge" / "server" / "static"


def test_root_serves_the_spa_markup(client):
    out = client.get("/")
    assert out.status_code == 200
    assert "text/html" in out.headers["content-type"]
    html = out.text
    assert "OntoForge" in html
    for panel in ("panel-ask", "panel-ontology", "panel-review", "panel-dashboards", "panel-status"):
        assert f'id="{panel}"' in html
    assert "#/ask" in html, "hash-routed tabs"


def test_index_references_only_existing_static_files(client):
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    refs = re.findall(r'(?:src|href)="/static/([^"]+)"', html)
    assert refs, "index.html links its assets through /static/"
    for ref in refs:
        assert (STATIC_DIR / ref).is_file(), f"index.html references missing file: {ref}"
        assert client.get(f"/static/{ref}").status_code == 200

    # the app shell is complete: styles, module script, vega vendor trio
    names = set(refs)
    assert "style.css" in names
    assert "app.js" in names
    assert {"vendor/vega.min.js", "vendor/vega-lite.min.js", "vendor/vega-embed.min.js"} <= names


def test_static_assets_have_sane_content(client):
    css = client.get("/static/style.css").text
    assert "--amber" in css and "--hairline" in css, "the design system tokens"
    js = client.get("/static/app.js").text
    assert "createTextNode" in js, "data is interpolated as text nodes, never innerHTML"
    assert "/api/ask" in js and "/api/review" in js
