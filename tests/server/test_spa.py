"""GET / serves the OS shell — the ontology operating system. The served
markup carries the window-manager surface (menubar, desktop, dock,
spotlight); every static reference resolves; the ES-module import graph is
closed; data never enters the DOM through innerHTML; abstention keeps its
dignified voice; and the whole non-vendor payload stays under budget."""

from __future__ import annotations

import json
import re
from pathlib import Path

STATIC_DIR = Path(__file__).resolve().parents[2] / "src" / "ontoforge" / "server" / "static"
JS_DIR = STATIC_DIR / "js"
APPS_DIR = JS_DIR / "apps"
ATLAS_FIXTURE = Path(__file__).parent / "fixtures" / "atlas_synthetic_250.json"

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


# ═══════════════════════════════════ THE ATLAS (constellation, evolved)


def _engine_src() -> str:
    return (JS_DIR / "constellation.js").read_text(encoding="utf-8")


def _app_src() -> str:
    return (APPS_DIR / "constellation.js").read_text(encoding="utf-8")


def test_atlas_fetch_is_wired_and_defensive(client):
    """The app rides GET /api/atlas through the shared cache, and falls
    back to the plain ontology sky with a quiet note while the endpoint's
    crew lands (it may 404 — that must never throw into the DOM)."""
    core = (JS_DIR / "core.js").read_text(encoding="utf-8")
    assert "/api/atlas" in core, "the atlas endpoint is fetched through the kernel cache"
    assert "loadAtlas" in core
    assert "cache.atlasPromise = null" in core or "atlasPromise: null" in core
    app = _app_src()
    assert "loadAtlas" in app, "the constellation app asks for the atlas"
    assert "renderAtlas" in app, "and renders it through the engine"
    assert "atlas not built" in app, "the 404 fallback keeps a quiet, honest voice"
    assert "engine.render(onto)" in app, "the original ontology sky still renders"
    # a reload drops the atlas cache with the rest of the world
    assert "dropCaches" in app


def test_atlas_tier_legend_chips_are_filter_toggles():
    """confirmed / likely / hint / silos render as toggle chips carrying
    counts from the served stats block."""
    app = _app_src()
    for tier in ("confirmed", "likely", "hint", "silos"):
        assert f'"{tier}"' in app, f"the {tier} tier has a legend toggle"
    assert "tier-toggle" in app, "legend chips are buttons, not decorations"
    assert "data-tier" in app
    assert "aria-pressed" in app, "toggles speak their state"
    assert "stats.confirmed" in app and "stats.likely" in app, "counts come from stats"
    assert re.search(r'tierToggle\("hint",[^)]*off: true', app), "hint arcs ship OFF by default"
    css = (STATIC_DIR / "style.css").read_text(encoding="utf-8")
    assert ".tier-toggle" in css
    for tier in ("confirmed", "likely", "hint", "silos"):
        assert f".constellation.hide-{tier}" in css, f"toggling {tier} hides that tier in CSS"


def test_likely_joins_are_dashed_amber_with_score_opacity():
    """A LIKELY join must read as a hypothesis: dashed amber, weighted by
    its own score, breathing only on hover."""
    css = (STATIC_DIR / "style.css").read_text(encoding="utf-8")
    likely_rule = re.search(r"\.tier-likely\s*\{([^}]*)\}", css)
    assert likely_rule, "the dashed-likely CSS class exists"
    assert "stroke-dasharray" in likely_rule.group(1), "likely arcs are dashed"
    assert "--amber" in likely_rule.group(1), "likely arcs are amber"
    assert "likely-breathe" in css, "likely arcs breathe on hover"
    assert "prefers-reduced-motion" in css
    engine = _engine_src()
    assert "tier-${tier}" in engine, "every arc carries its tier class"
    assert 'setAttribute("stroke-opacity"' in engine, "opacity is set per arc, proportional to score"


def test_atlas_evidence_card_speaks_the_contract():
    """Hovering a likely arc shows WHY: score, coverage, overlap, the two
    column names, sample shared values in mono; click pins the card."""
    engine = _engine_src()
    for key in ("coverage", "overlap_count", "sample_shared_values",
                "name_similarity", "semtype_match", "src_prop", "dst_prop"):
        assert key in engine, f"the evidence card surfaces {key}"
    assert "pinEvidence" in engine and "unpinEvidence" in engine, "click pins, click-away releases"
    assert "slice(0, 5)" in engine, "at most five sample shared values"
    app = _app_src()
    assert "evidence-card" in app or "evidence-card" in engine, "the evidence-card container exists"
    css = (STATIC_DIR / "style.css").read_text(encoding="utf-8")
    assert ".evidence-card" in css
    assert ".evidence-card.pinned" in css
    assert "--mono" in css  # samples and column names are mono (design tokens present)


def test_silos_collect_in_a_dignified_archipelago():
    """Singleton components band along the bottom — dimmer, labeled, and
    never styled like an error."""
    engine = _engine_src()
    assert "archipelago" in engine, "the archipelago band is real markup"
    assert "island-hull" in engine and "island-label" in engine, "islands carry hulls and labels"
    assert "dataset_count" in engine, "island labels carry their dataset counts"
    css = (STATIC_DIR / "style.css").read_text(encoding="utf-8")
    assert ".constellation .archipelago" in css
    assert ".constellation .island-hull" in css
    assert ".constellation .island-label" in css
    arch_rules = "".join(m.group(0) for m in re.finditer(r"\.archipelago[^{]*\{[^}]*\}", css))
    assert "--verdict-red" not in arch_rules, "silos are quiet, never error-red"
    assert "small-caps" in css, "island labels are small-caps serif"


def test_atlas_scale_discipline_is_documented_and_held():
    """250 nodes / 600 arcs: settle once, render static SVG, pan/zoom by
    viewBox only, hover by delegation, labels yield below the zoom
    threshold. The guard comment marks the contract in the source."""
    engine = _engine_src()
    assert "ATLAS SCALE GUARD" in engine, "the node-count guard comment survives"
    assert "250 nodes / 600 arcs" in engine
    # pan/zoom touch only the viewBox
    assert 'setAttribute("viewBox"' in engine
    # hover/click ride one delegated listener set — never per-node handlers
    assert 'svg.addEventListener("pointerover"' in engine
    assert 'svg.addEventListener("click"' in engine
    assert "onpointerenter:" not in engine, "no per-node inline hover handlers"
    assert "onpointermove:" not in engine
    # iterations shrink as islands grow — one big island cannot stall render
    assert "layoutIterations" in engine
    # class labels hide when zoomed out; island labels stay readable
    assert "labels-hidden" in engine
    css = (STATIC_DIR / "style.css").read_text(encoding="utf-8")
    assert ".constellation.labels-hidden .node-label" in css


def test_atlas_titlebar_and_spotlight_focus():
    """When the atlas is present the window announces itself —
    'Atlas — N islands · M silos' — and a class search flies to its island."""
    app = _app_src()
    assert "setTitle" in app and "Atlas — " in app
    assert "islands" in app and "silos" in app
    engine = _engine_src()
    assert "zoomToIsland" in engine, "island labels zoom to fit their island"
    assert "focusClass" in engine, "spotlight focus lands on the engine"
    assert "engine.focusClass" in app, "the app routes class:focus through the island zoom"


def test_synthetic_250_node_atlas_fixture_matches_the_contract():
    """The committed wild-atlas fixture exercises the full contract at the
    scale the engine guards: 250 classes, 600+ links, every tier, real
    silos — and the JS consumes exactly these keys."""
    atlas = json.loads(ATLAS_FIXTURE.read_text(encoding="utf-8"))
    comps, links, stats = atlas["components"], atlas["links"], atlas["stats"]

    n_classes = sum(len(c["class_uris"]) for c in comps)
    assert n_classes == 250, "the fixture is the 250-node scale test"
    assert stats["classes"] == n_classes
    unique = {u for c in comps for u in c["class_uris"]}
    assert len(unique) == n_classes, "class URIs are globally unique across components"
    assert len(links) >= 600, "the fixture is the 600-arc scale test"
    assert {l["tier"] for l in links} == {"confirmed", "likely", "hint"}
    assert stats["confirmed"] == sum(1 for l in links if l["tier"] == "confirmed")
    assert stats["likely"] == sum(1 for l in links if l["tier"] == "likely")
    assert stats["hint"] == sum(1 for l in links if l["tier"] == "hint")

    silos = [c for c in comps if c["is_silo"]]
    assert silos, "the fixture has honest silos"
    assert stats["silos"] == len(silos)
    assert stats["components"] == len(comps)
    assert all(len(c["class_uris"]) == 1 for c in silos), "silos are singletons"

    for c in comps:
        assert {"id", "label", "class_uris", "dataset_count", "is_silo"} <= set(c)
    every_uri = {u for c in comps for u in c["class_uris"]}
    for l in links:
        assert {"src_class", "dst_class", "src_prop", "dst_prop",
                "tier", "score", "evidence"} <= set(l)
        assert l["src_class"] in every_uri and l["dst_class"] in every_uri
        assert {"coverage", "overlap_count", "sample_shared_values",
                "name_similarity", "semtype_match"} <= set(l["evidence"])

    # the render path consumes this exact contract — every key it needs
    # appears in the engine/app source (static proof the code paths exist)
    src = _engine_src() + _app_src()
    for key in ("components", "links", "stats", "class_uris", "dataset_count",
                "is_silo", "src_class", "dst_class", "tier", "score", "evidence"):
        assert key in src, f"the JS never reads contract key {key}"
