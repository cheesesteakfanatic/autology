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


def test_the_instrument_shell_markup(client):
    """The signature surfaces are part of the served shell, not late-built."""
    html = client.get("/").text
    assert 'id="cmdk"' in html, "the command palette (Cmd+K)"
    assert 'id="evidence-rail"' in html, "the right-side evidence rail container"
    assert '<svg id="constellation"' in html, "the ontology constellation is an svg"
    assert 'id="time-scrubber"' in html, "the bitemporal as-of scrubber container"


def test_abstention_is_a_first_class_state(client):
    """Abstention renders as a dignified state, never an error style."""
    js = client.get("/static/app.js").text
    assert "state-abstained" in js, "the abstention state class is applied to answers"
    assert "declines to guess" in js, "abstention speaks in the product's honest voice"
    css = client.get("/static/style.css").text
    assert ".state-abstained" in css, "abstention has its own designed treatment"


def test_es_modules_exist_and_are_served(client):
    """app.js is an ES module; every module it imports is fetchable."""
    js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    mods = re.findall(r'from\s+"\./(js/[^"]+)"', js)
    assert mods, "the app is split into ES modules under static/js/"
    for mod in mods:
        assert (STATIC_DIR / mod).is_file(), f"app.js imports missing module: {mod}"
        out = client.get(f"/static/{mod}")
        assert out.status_code == 200
        assert "javascript" in out.headers["content-type"]


def test_module_data_interpolation_is_text_node_safe(client):
    """No module assigns API data to innerHTML — the el()/createTextNode
    discipline holds across the whole non-vendor payload."""
    for path in [STATIC_DIR / "app.js", *sorted((STATIC_DIR / "js").glob("*.js"))]:
        src = path.read_text(encoding="utf-8")
        assert not re.search(r"\.(innerHTML|outerHTML)\s*[+]?=", src), (
            f"{path.name} must never assign to innerHTML/outerHTML"
        )
        assert "insertAdjacentHTML" not in src, f"{path.name} must never insert HTML strings"


def test_total_non_vendor_payload_under_budget():
    """Performance is part of the contract: the whole app (sans vendored
    vega) ships in under 120 KB."""
    total = sum(
        p.stat().st_size
        for p in STATIC_DIR.rglob("*")
        if p.is_file() and "vendor" not in p.parts
    )
    assert total < 120 * 1024, f"non-vendor static payload is {total} bytes (budget 120 KB)"
