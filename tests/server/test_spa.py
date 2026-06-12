"""GET / serves the OS shell — the ontology operating system. The served
markup carries the window-manager surface (menubar, desktop, dock,
spotlight); every static reference resolves; the ES-module import graph is
closed; data never enters the DOM through innerHTML; abstention keeps its
dignified voice; and the whole non-vendor payload stays under budget."""

from __future__ import annotations

import re
from pathlib import Path

STATIC_DIR = Path(__file__).resolve().parents[2] / "src" / "ontoforge" / "server" / "static"
JS_DIR = STATIC_DIR / "js"
APPS_DIR = JS_DIR / "apps"

#: every micro-app the OS ships, in registry order
APPS = ("ask", "constellation", "inspector", "evidence", "review", "dashboards", "pulse", "exporter")


def all_js_files():
    return [STATIC_DIR / "app.js", *sorted(JS_DIR.rglob("*.js"))]


# ───────────────────────────────────────────────────────── the shell


def test_root_serves_the_os_shell_markup(client):
    out = client.get("/")
    assert out.status_code == 200
    assert "text/html" in out.headers["content-type"]
    html = out.text
    assert "OntoForge" in html
    # the operating surface, pre-mounted: menubar, workspace, dock, spotlight
    assert 'id="menubar"' in html, "the menu-bar strip"
    assert 'id="desktop"' in html, "the workspace void where windows live"
    assert 'id="dock"' in html, "the dock"
    assert 'id="spotlight"' in html, "spotlight is pre-mounted so open is <100ms"
    assert 'id="spotlight-input"' in html
    assert 'id="spotlight-results"' in html
    # spotlight is a real combobox, not a div with vibes
    assert 'role="combobox"' in html
    assert 'aria-controls="spotlight-results"' in html
    # first-run: the epigraph holds the empty workspace
    assert 'id="epigraph"' in html
    assert "just type" in html, "the first-run hint"
    # live estate meta in the menubar
    for meta in ("meta-estate", "meta-atoms", "meta-cost"):
        assert f'id="{meta}"' in html


def test_index_references_only_existing_static_files(client):
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    refs = re.findall(r'(?:src|href)="/static/([^"]+)"', html)
    assert refs, "index.html links its assets through /static/"
    for ref in refs:
        assert (STATIC_DIR / ref).is_file(), f"index.html references missing file: {ref}"
        assert client.get(f"/static/{ref}").status_code == 200

    # the shell is complete: styles, boot module, vega vendor trio
    names = set(refs)
    assert "style.css" in names
    assert "app.js" in names
    assert {"vendor/vega.min.js", "vendor/vega-lite.min.js", "vendor/vega-embed.min.js"} <= names


def test_static_assets_have_sane_content(client):
    css = client.get("/static/style.css").text
    assert "--amber" in css and "--hairline" in css, "the design system tokens"
    js = client.get("/static/js/core.js").text
    assert "createTextNode" in js, "data is interpolated as text nodes, never innerHTML"
    # the apps still speak to the real API
    ask = (APPS_DIR / "ask.js").read_text(encoding="utf-8")
    assert "/api/ask" in ask
    review = (APPS_DIR / "review.js").read_text(encoding="utf-8")
    assert "/api/review" in review


# ─────────────────────────────────────────────────── the OS layers


def test_os_layer_modules_exist_and_are_served(client):
    """The shell is layered: kernel helpers, bus, WM, dock, spotlight —
    each a real file, each fetchable as javascript."""
    for mod in ("core.js", "bus.js", "wm.js", "dock.js", "spotlight.js", "constellation.js"):
        assert (JS_DIR / mod).is_file(), f"missing OS layer: js/{mod}"
        out = client.get(f"/static/js/{mod}")
        assert out.status_code == 200
        assert "javascript" in out.headers["content-type"]


def test_every_micro_app_is_registered(client):
    """Each micro-app is a window class in js/apps/*.js and the registry
    registers all of them."""
    registry = (APPS_DIR / "registry.js").read_text(encoding="utf-8")
    for app in APPS:
        assert (APPS_DIR / f"{app}.js").is_file(), f"missing micro-app: js/apps/{app}.js"
        assert client.get(f"/static/js/apps/{app}.js").status_code == 200
        assert f"./{app}.js" in registry, f"registry does not import {app}"
    for app in APPS:
        src = (APPS_DIR / f"{app}.js").read_text(encoding="utf-8")
        assert f'id: "{app}"' in src, f"app {app} must carry its registry id"


def test_es_module_import_graph_is_closed(client):
    """Every relative import, starting from app.js, resolves to a file on
    disk and is served as javascript — the graph has no dangling edges."""
    seen: set[Path] = set()
    queue = [STATIC_DIR / "app.js"]
    while queue:
        path = queue.pop()
        if path in seen:
            continue
        seen.add(path)
        assert path.is_file(), f"import graph reaches missing module: {path}"
        rel = path.relative_to(STATIC_DIR).as_posix()
        out = client.get(f"/static/{rel}")
        assert out.status_code == 200
        assert "javascript" in out.headers["content-type"]
        src = path.read_text(encoding="utf-8")
        for imp in re.findall(r'from\s+"(\.[^"]+)"', src):
            queue.append((path.parent / imp).resolve())
    assert len(seen) >= 14, "the OS is split into modules: kernel, WM, dock, spotlight, apps"


def test_wm_interaction_discipline():
    """The window manager uses the mechanics that make it feel native:
    pointer capture (never document mousemove), transform-only motion,
    rAF-coalesced writes, an explicit z stack, and FLIP minimize."""
    wm = (JS_DIR / "wm.js").read_text(encoding="utf-8")
    assert "setPointerCapture" in wm, "drags hold the pointer via capture"
    assert "pointercancel" in wm, "the cancel path releases the gesture too"
    assert "translate3d" in wm, "windows move on the compositor, not top/left"
    assert "requestAnimationFrame" in wm, "one style write per frame"
    assert "document.addEventListener(\"mousemove\"" not in wm
    assert "willChange" in wm, "layer promotion scoped to the gesture lifetime"
    assert "/api/workspace" in wm, "layout persists through the workspace API"
    assert "localStorage" in (JS_DIR / "core.js").read_text(encoding="utf-8"), "with a local fallback"


def test_spotlight_speaks_the_search_contract():
    spot = (JS_DIR / "spotlight.js").read_text(encoding="utf-8")
    assert "/api/search" in spot, "spotlight queries the search API"
    assert "AbortController" in spot, "in-flight searches are cancelled, never reordered"
    assert "aria-activedescendant" in spot, "combobox highlight is virtual"
    assert "Ask the estate" in spot, "no query dead-ends — free text falls through to ask"


# ───────────────────────────────────────────────── product invariants


def test_abstention_is_a_first_class_state(client):
    """Abstention renders as a dignified state, never an error style."""
    js = (APPS_DIR / "ask.js").read_text(encoding="utf-8")
    assert "state-abstained" in js, "the abstention state class is applied to answers"
    assert "declines to guess" in js, "abstention speaks in the product's honest voice"
    css = client.get("/static/style.css").text
    assert ".state-abstained" in css, "abstention has its own designed treatment"


def test_the_scrubber_survived_the_rebuild():
    """The bitemporal as-of scrubber lives on inside the Inspector app."""
    inspector = (APPS_DIR / "inspector.js").read_text(encoding="utf-8")
    assert "as_of:" in inspector, "the scrubber refetches under an as-of stance"
    assert "scrub-track" in inspector
    assert "/neighbors" in inspector, "the inspector walks the entity graph"


def test_module_data_interpolation_is_text_node_safe(client):
    """No module assigns API data to innerHTML — the el()/createTextNode
    discipline holds across the whole non-vendor payload."""
    for path in all_js_files():
        src = path.read_text(encoding="utf-8")
        assert not re.search(r"\.(innerHTML|outerHTML)\s*[+]?=", src), (
            f"{path.name} must never assign to innerHTML/outerHTML"
        )
        assert "insertAdjacentHTML" not in src, f"{path.name} must never insert HTML strings"


def test_no_app_imports_another_app():
    """Apps talk over the bus; the registry is the only module that may
    import them all."""
    for path in sorted(APPS_DIR.glob("*.js")):
        if path.name == "registry.js":
            continue
        src = path.read_text(encoding="utf-8")
        for imp in re.findall(r'from\s+"(\.[^"]+)"', src):
            target = (path.parent / imp).resolve()
            assert target.parent != APPS_DIR, (
                f"{path.name} imports a sibling app ({imp}) — intents go over the bus"
            )


def test_total_non_vendor_payload_under_budget():
    """Performance is part of the contract: the whole OS (sans vendored
    vega) ships in under 250 KB."""
    total = sum(
        p.stat().st_size
        for p in STATIC_DIR.rglob("*")
        if p.is_file() and "vendor" not in p.parts
    )
    assert total < 250 * 1024, f"non-vendor static payload is {total} bytes (budget 250 KB)"
