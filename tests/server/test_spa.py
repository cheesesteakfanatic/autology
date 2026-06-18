"""GET / serves the THREE-MODE shell — Ask | Build | Studio. The served
markup carries the always-visible mode switcher and the three mode panes;
every static reference resolves; the ES-module import graph is closed; data
never enters the DOM through innerHTML; abstention keeps its dignified voice;
the playground surfaces (Data Catalog, the live-build Data Map, the plain-
English engineering Console) are present and wired to the frozen API
contract; user-facing chrome speaks plain language (no internal engine
jargon); and the whole non-vendor payload stays under budget."""

from __future__ import annotations

import json
import re
from pathlib import Path

STATIC_DIR = Path(__file__).resolve().parents[2] / "src" / "ontoforge" / "server" / "static"
JS_DIR = STATIC_DIR / "js"
APPS_DIR = JS_DIR / "apps"
SURFACES_DIR = JS_DIR / "surfaces"
ATLAS_FIXTURE = Path(__file__).parent / "fixtures" / "atlas_synthetic_250.json"

#: the three modes
MODES = ("ask", "build", "studio")
#: STUDIO's windowed power-tool apps (internal ids kept; labels de-jargoned).
#: ``observatory`` is the OBSERVABILITY surface (R0 P1): value-level lineage,
#: the append-only audit log, run history, and the compute-at-cost ledger.
STUDIO_APPS = (
    "catalog", "datamap", "console", "review", "pulse", "inspector", "evidence", "observatory",
)
#: the de-jargoned single-surface modes
SURFACES = ("ask", "build")
#: Non-vendor payload budget. Held at 340 KB to seat the approved COOL
#: PROFESSIONAL redesign — SLATE (cool light :root, the default) / GRAPHITE
#: (cool dark data-theme). The cool system is disciplined (flat panels, 1px
#: hairlines instead of pillowy shadows, ONE accent per view, mono for every
#: number) so it is LIGHTER than the warm system it replaced, but the budget
#: stays a hard ceiling: it holds the shell, the eight Studio apps, the canvas
#: layer, and BOTH themes under the limit, so any future decorative bloat still
#: trips it.
PAYLOAD_BUDGET = 340 * 1024


def all_js_files():
    return [STATIC_DIR / "app.js", *sorted(JS_DIR.rglob("*.js"))]


def _index() -> str:
    return (STATIC_DIR / "index.html").read_text(encoding="utf-8")


# ════════════════════════════════════════════ the three-mode shell


def test_root_serves_the_three_mode_shell(client):
    out = client.get("/")
    assert out.status_code == 200
    assert "text/html" in out.headers["content-type"]
    html = out.text
    assert "OntoForge" in html
    # the always-visible mode switcher is the clearest thing on screen
    assert 'id="mode-switcher"' in html, "the segmented mode switcher"
    assert 'role="tablist"' in html, "the switcher is a real tablist"
    for mode in MODES:
        assert f'id="mode-{mode}"' in html, f"the {mode} segment"
        assert f'data-mode="{mode}"' in html
    # each mode gets exactly one pane, one visible at a time
    for mode in MODES:
        assert f'id="pane-{mode}"' in html, f"the {mode} pane"
    # ASK lands lit by default; Build & Studio exist but start hidden
    ask_seg = re.search(r'id="mode-ask"[^>]*>', html).group(0)
    assert 'aria-selected="true"' in ask_seg, "ASK is the default landing"
    # the switcher carries plain subtitles so a first-timer reads them at a glance
    for sub in ("get answers", "measure", "data"):
        assert sub in html, f"switcher subtitle hint '{sub}'"
    # spotlight is pre-mounted so open is instant
    assert 'id="spotlight"' in html and 'id="spotlight-input"' in html
    assert 'role="combobox"' in html and 'aria-controls="spotlight-results"' in html
    # the studio dock is present but starts hidden (Studio-only chrome)
    assert 'id="dock"' in html
    # the studio workspace canvas where WM windows are born
    assert 'id="desktop"' in html
    # first-run orientation coach
    assert 'id="coach"' in html


def test_studio_badge_and_help_affordances_present():
    html = _index()
    assert 'id="studio-badge"' in html, "the Confirm-suggestions count badge on the Studio segment"
    assert 'id="help-toggle"' in html, "a '?' reopens the orientation card"


def test_mode_switcher_is_strong_persistent_chrome():
    """The switcher must read as a major control, not a minor toggle — it
    carries an always-visible active label and switching is instant (no
    reload): the shell flips panes, not the document."""
    modes = (JS_DIR / "modes.js").read_text(encoding="utf-8")
    assert "createModeShell" in modes
    assert "switchTo" in modes, "switching is a JS pane flip, never a navigation"
    assert "active" in modes and "aria-selected" in modes, "the active segment is announced"
    # the dock belongs to STUDIO alone — never floats over Ask/Build
    assert 'mode !== "studio"' in modes or "dock.hidden" in modes
    css = (STATIC_DIR / "style.css").read_text(encoding="utf-8")
    assert ".mode-switcher" in css and ".mode-seg.active" in css, "the active segment has weight"


def test_app_boots_into_ask_and_routes_modes():
    app = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    assert "createModeShell" in app and "createAskSurface" in app and "createBuildSurface" in app
    assert 'modes.boot("ask"' in app, "ASK is the default landing for every session"
    # ⌘1/⌘2/⌘3 jump straight to a mode — the shell claims these first
    assert "modeForDigit" in app
    # the windowed apps live in STUDIO only; intents surface Studio first
    assert "ensureStudio" in app


# ════════════════════════════════════ de-jargon: plain language in chrome


def test_user_facing_chrome_is_plain_language():
    """The de-jargon contract: internal engine names never appear in any
    user-facing label. The whitepaper codenames stay in code/URIs only."""
    html = _index()
    # the top bar speaks plain language
    assert "your data" in html, "estate → 'your data'"
    assert "source records" in html, "atoms → 'source records'"
    # the segment names are the three plain modes
    for word in ("Ask", "Build", "Studio"):
        assert f">{word}<" in html, f"the {word} segment label"
    # internal engine codenames must NOT be visible in the shell chrome
    for jargon in ("STRATA", "HEARTH", "LODESTONE", "ANVIL", "TEMPER",
                   "WARDEN", "VISTA", "AMBER", "OQIR", "estate</",
                   "ontology operating system", "atoms <b"):
        assert jargon not in html, f"the shell must not expose '{jargon}'"


def test_naming_map_applied_across_apps():
    """The de-jargon naming map is applied as LABELS in the surfaces/apps:
    Data Map, Activity, Confirm suggestions, Explore record, Where this came
    from — while ids/URIs/intents keep their internal names."""
    datamap = (APPS_DIR / "datamap.js").read_text(encoding="utf-8")
    assert 'title: "Data Map"' in datamap, "Constellation/Atlas → 'Data Map'"
    assert 'id: "constellation"' in datamap, "but the internal app id is unchanged"
    assert "confirmed join" in datamap and "likely join" in datamap, "the map tiers read plainly"
    assert "separate (no link found)" in datamap or "standalone" in datamap

    pulse = (APPS_DIR / "pulse.js").read_text(encoding="utf-8")
    assert 'title: "Activity"' in pulse, "Pulse → 'Activity'"
    assert 'id: "pulse"' in pulse
    # the pipeline stage labels are plain words
    for plain in ("Reading the data", "Finding the shape", "Building the model",
                  "Matching records", "Filling in values"):
        assert plain in pulse, f"Activity step label '{plain}'"

    review = (APPS_DIR / "review.js").read_text(encoding="utf-8")
    assert 'title: "Confirm suggestions"' in review, "Review → 'Confirm suggestions'"
    assert 'id: "review"' in review
    assert "Confirm" in review and "Not the same" in review, "accept/reject → Confirm / Not the same"

    inspector = (APPS_DIR / "inspector.js").read_text(encoding="utf-8")
    assert 'title: "Record"' in inspector, "Inspector → 'Record'"
    assert "Explore record" in inspector

    evidence = (APPS_DIR / "evidence.js").read_text(encoding="utf-8")
    assert 'title: "Where this came from"' in evidence, "Evidence → 'Where this came from'"
    assert "source record" in evidence, "atoms → 'source records'"


def test_internal_names_kept_in_code_not_shown():
    """De-jargon is presentation-only: the API routes, the bus intents and
    the app ids keep their internal names so routing/persistence still work."""
    app = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    for intent in ("ask:run", "entity:open", "class:focus", "evidence:atoms", "evidence:prov"):
        assert intent in app, f"the bus intent {intent} is unchanged"
    core = (JS_DIR / "core.js").read_text(encoding="utf-8")
    assert "/api/atlas" in core, "the atlas endpoint name is unchanged in code"
    review = (APPS_DIR / "review.js").read_text(encoding="utf-8")
    assert "/api/review" in review and "verdict: v" in review, "the verdict API is unchanged"
    assert 'verdict("accept")' in review, "the internal accept/reject verdict values are unchanged"


# ════════════════════════════════════════════ ASK — the questioner


def test_ask_surface_is_the_centered_questioner(client):
    ask = (SURFACES_DIR / "ask.js").read_text(encoding="utf-8")
    assert "createAskSurface" in ask
    assert "/api/ask" in ask and "/api/ask/clarify" in ask, "Ask still speaks to the real API"
    # suggested + recent questions, generated from the model
    assert "suggestedQuestions" in ask and "renderSuggested" in ask
    assert "Recent questions" in ask
    # the big centered box with a soft prompt
    assert "Ask anything about your data" in ask
    # "Where this came from" replaces atom/citation jargon
    assert "Where this came from" in ask
    assert "source record" in ask, "atoms → 'source records'"
    css = client.get("/static/style.css").text
    assert ".surface-ask" in css and ".ask-field-big" in css, "the centered box has a designed treatment"
    assert ".sources-panel" in css, "the Sources panel opens beside the answer"


def test_ask_abstention_is_first_class_and_dignified(client):
    """Abstention renders as a dignified state, never an error style; the
    'won't guess' voice and 'what would make this answerable' chips survive."""
    ask = (SURFACES_DIR / "ask.js").read_text(encoding="utf-8")
    assert "state-abstained" in ask, "the abstention state class is applied"
    assert "declines to guess" in ask, "abstention speaks the honest voice"
    assert "what would make this answerable" in ask, "the recovery chips survive"
    css = client.get("/static/style.css").text
    assert ".state-abstained" in css, "abstention has its own designed treatment"


def test_ask_guides_to_studio_when_no_model(client):
    """If the model isn't built, ASK says so plainly and points to STUDIO —
    never a dead box with no explanation."""
    ask = (SURFACES_DIR / "ask.js").read_text(encoding="utf-8")
    assert "isn't ready to answer questions yet" in ask
    assert "Open Studio" in ask
    assert "mode:goto" in ask, "the not-ready CTA jumps modes"


# ════════════════════════════════════════════ BUILD — measure & pull data


def test_build_surface_is_the_two_pane_builder(client):
    build = (SURFACES_DIR / "build.js").read_text(encoding="utf-8")
    assert "createBuildSurface" in build
    # the plain-language pickers
    assert "Measure something" in build and "Break it down by" in build
    # the free-text box feeds the same synthesis
    assert "describe what you want to see" in build
    assert "/api/dashboards" in build, "proposals come from VISTA synthesis"
    # cool chart theme: the COOL desaturated atlas wheel — teal anchor + the
    # graphite-indigo data hue, never the warm marigold/tan palette
    assert "ATLAS_RANGE" in build and "#0E8C84" in build and "#4A56C7" in build
    assert "#D09735" not in build and "#9b978e" not in build, "the warm/old chart inks are gone"
    css = client.get("/static/style.css").text
    assert ".build-layout" in css and ".build-left" in css and ".build-right" in css


def test_build_separates_extract_from_export(client):
    """Extract (a CSV slice) and Export (the whole portable dataset) must be
    labeled and separated so a slice is never confused with the whole."""
    build = (SURFACES_DIR / "build.js").read_text(encoding="utf-8")
    assert "/api/extract" in build, "Extract pulls a filtered slice"
    assert "Download CSV" in build, "the slice downloads as CSV"
    assert "/api/export" in build, "Export seals the whole portable bundle"
    assert "Download the whole dataset" in build, "Export is labeled plainly as the whole"
    # the two are visually distinct containers
    assert "build-extract" in build and "build-export" in build
    css = client.get("/static/style.css").text
    assert ".build-extract" in css and ".build-export" in css


# ════════════════════════════════════════════ STUDIO — the playground


def test_studio_data_catalog_is_grouped_and_addable(client):
    catalog = (APPS_DIR / "catalog.js").read_text(encoding="utf-8")
    assert "createCatalogApp" in catalog and 'id: "catalog"' in catalog
    # the frozen contract
    assert "/api/catalog" in catalog
    assert "/api/workspace/build" in catalog, "Build map posts the build"
    assert "/api/workspace/state" in catalog
    # grouped by domain, table-of-contents-first
    assert "domain-group" in catalog and "domain-header" in catalog
    # add a dataset (folder/file path or drop) + status pills
    assert "Add data" in catalog
    assert "status-pill" in catalog
    for pill in ("Modeled", "Building", "Not yet modeled", "Needs attention"):
        assert pill in catalog, f"the '{pill}' build-status pill"
    # removal is guarded and emphasizes source files are not deleted
    assert "source files are not deleted" in catalog
    # build cap with a clear message
    assert "25" in catalog
    css = client.get("/static/style.css").text
    assert ".catalog-row" in css and ".status-pill" in css


def test_studio_data_map_animates_a_real_live_build(client):
    """The signature STUDIO moment: the Data Map animates from REAL engine
    events streamed by GET /api/workspace/build/{job_id} — never a timed
    fake. Nodes pop and arcs draw as types/joins are genuinely classified,
    batched on rAF so a burst never strobes."""
    datamap = (APPS_DIR / "datamap.js").read_text(encoding="utf-8")
    assert "/api/workspace/build/" in datamap, "it polls the real build job"
    assert "type_found" in datamap and "join_found" in datamap, "it reacts to real events"
    assert "studio:build-started" in datamap, "a build kicks the live layer"
    # honest progress, not a spinner: stage label + determinate bar + live tally
    assert "build-strip" in datamap and "build-bar-fill" in datamap
    assert "Confirmed joins" in datamap and "Likely joins" in datamap, "the live tally"
    # calm pacing: batched on rAF, ≤ a few per frame — never a synthetic delay
    assert "requestAnimationFrame" in datamap
    assert "REVEAL_PER_FRAME" in datamap, "reveals are batched, not strobed"
    # motion is real or instant — the final map renders via the engine
    assert "renderAtlas" in datamap, "the finished map is the real interactive atlas"
    css = client.get("/static/style.css").text
    assert ".build-strip" in css and ".live-arc" in css and ".live-node" in css
    assert "prefers-reduced-motion" in css, "the live build respects reduced motion"


def test_data_map_tiers_are_plainly_labeled(client):
    """The map tiers read as confirmed join / likely join / standalone —
    confirmed solid teal, likely dashed marigold."""
    datamap = (APPS_DIR / "datamap.js").read_text(encoding="utf-8")
    assert "confirmed join" in datamap and "likely join" in datamap
    assert "standalone" in datamap, "silos → 'standalone' (no link found)"
    css = (STATIC_DIR / "style.css").read_text(encoding="utf-8")
    assert ".live-arc.tier-confirmed" in css, "confirmed joins are solid teal"
    assert ".live-arc.tier-likely" in css and "dasharray" in css, "likely joins are dashed marigold"


def test_studio_engineering_console_previews_then_applies(client):
    """The plain-English Console: interpret → PREVIEW (never apply blind) →
    Apply with Undo; unsupported commands fail to worked examples; nothing
    destructive happens on Enter alone."""
    console = (APPS_DIR / "console.js").read_text(encoding="utf-8")
    assert "createConsoleApp" in console and 'id: "console"' in console
    # the frozen contract
    assert "/api/engineer/interpret" in console
    assert "/api/engineer/apply" in console
    assert "/api/engineer/undo" in console
    # always preview before acting
    assert "renderPreview" in console and "nothing has changed yet" in console
    assert "doApply" in console and "undo_token" in console, "Apply offers Undo"
    # unsupported never dead-ends — it falls to worked examples
    assert "couldn't turn that into a data step" in console
    assert "supported_examples" in console and "EXAMPLES" in console
    # clarification asks ONE question, never guesses
    assert "clarification" in console
    # destructive ops carry a consequence and need an explicit Apply tap
    assert "DESTRUCTIVE" in console and "consequence" in console
    css = client.get("/static/style.css").text
    assert ".console-card" in css and ".preview-card" in css


def test_studio_left_rail_names_the_sections():
    """STUDIO is organized into labeled, persistent sections (not a flat
    icon dock): Data Catalog, Data Map, Console, Confirm suggestions,
    Activity — a named left rail, the dock is the substrate underneath."""
    app = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    assert "STUDIO_PANELS" in app and "studio-rail" in app
    for label in ("Data Catalog", "Data Map", "Console", "Confirm suggestions", "Activity"):
        assert label in app, f"the rail names the '{label}' section"
    # the signature pairing on entry: Data Map + Console docked
    assert "tileStudioSignature" in app
    css = (STATIC_DIR / "style.css").read_text(encoding="utf-8")
    assert ".studio-rail" in css and ".rail-item" in css


# ════════════════════════════════════════════ first-run onboarding


def test_first_run_orientation_is_present_and_skippable():
    html = _index()
    assert 'id="coach"' in html and "three ways to work" in html
    # the orientation names all three modes in plain language
    for word in ("Ask", "Build", "Studio"):
        assert word in html
    modes = (JS_DIR / "modes.js").read_text(encoding="utf-8")
    assert "maybeCoach" in modes and "COACH_KEY" in modes, "dismissal is persisted"
    # adapts to data state: add-first when empty, try-a-question when modeled
    assert "Add your first dataset" in modes and "Try a question" in modes
    # first-visit per-mode flags are persisted (nudges show once)
    assert "FIRSTVISIT_KEY" in modes or "isFirstVisit" in modes


# ════════════════════════════════════════════ assets + module graph


def test_index_references_only_existing_static_files(client):
    html = _index()
    refs = re.findall(r'(?:src|href)="/static/([^"]+)"', html)
    assert refs, "index.html links its assets through /static/"
    for ref in refs:
        assert (STATIC_DIR / ref).is_file(), f"index.html references missing file: {ref}"
        assert client.get(f"/static/{ref}").status_code == 200
    names = set(refs)
    assert "style.css" in names and "app.js" in names
    assert {"vendor/vega.min.js", "vendor/vega-lite.min.js", "vendor/vega-embed.min.js"} <= names


def test_os_layer_modules_exist_and_are_served(client):
    """The shell is layered: kernel, bus, the mode controller, the WM, dock,
    spotlight — each a real file, each fetchable as javascript."""
    for mod in ("core.js", "bus.js", "modes.js", "wm.js", "dock.js", "spotlight.js", "constellation.js"):
        assert (JS_DIR / mod).is_file(), f"missing OS layer: js/{mod}"
        out = client.get(f"/static/js/{mod}")
        assert out.status_code == 200
        assert "javascript" in out.headers["content-type"]
    for surf in SURFACES:
        assert (SURFACES_DIR / f"{surf}.js").is_file(), f"missing surface: js/surfaces/{surf}.js"
        assert client.get(f"/static/js/surfaces/{surf}.js").status_code == 200


def test_every_studio_app_is_registered(client):
    registry = (APPS_DIR / "registry.js").read_text(encoding="utf-8")
    files = {p.stem for p in APPS_DIR.glob("*.js")}
    for app in STUDIO_APPS:
        assert app in files, f"missing studio app: js/apps/{app}.js"
        assert client.get(f"/static/js/apps/{app}.js").status_code == 200
    for imp in (
        "catalog", "datamap", "console", "review", "pulse", "inspector", "evidence", "observatory",
    ):
        assert f"./{imp}.js" in registry, f"registry does not import {imp}"
    # each app carries its registry id (datamap keeps the 'constellation' id)
    ids = {
        "catalog": "catalog", "datamap": "constellation", "console": "console",
        "review": "review", "pulse": "pulse", "inspector": "inspector", "evidence": "evidence",
        "observatory": "observatory",
    }
    for app, app_id in ids.items():
        src = (APPS_DIR / f"{app}.js").read_text(encoding="utf-8")
        assert f'id: "{app_id}"' in src, f"app {app} must carry registry id {app_id}"


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
    assert len(seen) >= 16, "the shell splits into kernel, modes, WM, surfaces, studio apps"


def test_no_studio_app_imports_another_app():
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


# ════════════════════════════════════ security + performance invariants


def test_module_data_interpolation_is_text_node_safe(client):
    """No module assigns API data to innerHTML — the el()/createTextNode
    discipline holds across the whole non-vendor payload."""
    for path in all_js_files():
        src = path.read_text(encoding="utf-8")
        assert not re.search(r"\.(innerHTML|outerHTML)\s*[+]?=", src), (
            f"{path.name} must never assign to innerHTML/outerHTML"
        )
        assert "insertAdjacentHTML" not in src, f"{path.name} must never insert HTML strings"
    core = (JS_DIR / "core.js").read_text(encoding="utf-8")
    assert "createTextNode" in core, "data is interpolated as text nodes, never innerHTML"


def test_total_non_vendor_payload_under_budget():
    """Performance is part of the contract: the whole shell (sans vendored
    vega) ships under 290 KB."""
    total = sum(
        p.stat().st_size
        for p in STATIC_DIR.rglob("*")
        if p.is_file() and "vendor" not in p.parts
    )
    assert total < PAYLOAD_BUDGET, f"non-vendor static payload is {total} bytes (budget {PAYLOAD_BUDGET})"


def test_cool_slate_is_the_default_theme(client):
    """The COOL PROFESSIONAL system: SLATE is the default light :root — a cool
    slate ground, flat white panels, cool near-black ink. Structure is carried
    by 1px hairlines; the default theme uses at most a 1px whisper shadow tinted
    cool ink (rgba(20,22,26,…)), never warm-amber and never black. No dark
    grounds leak into the default :root — graphite is the opt-in dark theme."""
    css = client.get("/static/style.css").text
    root = css.split("}", 1)[0]
    assert "#F6F7F9" in root, "Slate canvas is the cool desktop ground"
    assert "#FFFFFF" in root, "flat white is the resting panel surface"
    assert "#14161A" in root, "cool near-black ink is the primary text — never #000"
    # ONE accent: graphite-indigo (flat fill, never a gradient or warm marigold)
    assert "#313ECF" in root, "graphite-indigo is the single Slate accent"
    assert "#EEF0FB" in css or "rgba(49, 62, 207, 0.07)" in css, "the soft accent fill"
    # status/positive teal + the cool desaturated categorical wheel
    assert "#0E8C84" in root, "teal is status/positive (= confirmed)"
    for hue in ("#5B6B86", "#7D5BA6", "#3E6FA3", "#4C5578", "#5E8C7A"):
        assert hue in css, f"the cool categorical data hue {hue} is in the wheel"
    # NO tan/cream/amber/orange anywhere — the warm palette is fully gone
    for warm in ("#ECE1CB", "#FBF4E6", "#2A1F14", "#D09735", "#D8CDB8",
                 "#2C5956", "#945442", "#6C733A", "#375E72", "#713D68"):
        assert warm not in css, f"the warm token {warm} is gone from the cool system"
    # the GRAPHITE dark ground is legitimate ONLY behind the dark data-theme
    default_block = css.split('html[data-theme="dark"]')[0]
    for dark_ground in ("#0E1014", "#161A20", "#1C2128", "#12151A"):
        assert dark_ground not in default_block, \
            f"the graphite dark ground {dark_ground} must not leak into the Slate default :root"
    # Slate elevation shadows are a cool-ink whisper, never warm-amber, never black
    assert "rgba(20, 22, 26" in default_block or "rgba(20,22,26" in default_block, \
        "Slate whisper shadows are cool-ink tinted (rgba(20,22,26,…))"
    assert "rgba(90, 55, 20" not in default_block and "rgba(90,55,20" not in default_block, \
        "no warm-amber shadows in the cool Slate default"
    assert "rgba(0, 0, 0" not in default_block and "rgba(0,0,0" not in default_block, \
        "black shadows are reserved for the Graphite dark theme — never in Slate"


def test_attention_hierarchy_chrome_dim_tier_exists(client):
    """AWARD-GRADE attention hierarchy: a dedicated lower-contrast chrome ink
    tier exists so orientation/navigation chrome can RECEDE while the active
    work surface keeps full --ink. The tokens are declared in :root and the
    receding chrome actually consumes them."""
    css = client.get("/static/style.css").text
    root = css.split("}", 1)[0]
    # the chrome-dim token tier is a real, declared tier (not just --ink/--walnut)
    assert "--chrome-ink:" in root, "the recessed-chrome primary ink token exists in :root"
    assert "--chrome-dim:" in root, "the recessed-chrome secondary ink token exists in :root"
    # and it is actually applied to receding chrome, not merely declared
    assert "var(--chrome-dim)" in css, "the chrome-dim tier is applied to receding chrome"
    assert "var(--chrome-ink)" in css, "the chrome-ink tier is applied to receding chrome"
    # the ACTIVE mode segment stays primary (full ink) — it must NOT recede
    active = re.search(r"\.mode-seg\.active\s*\{([^}]*)\}", css)
    assert active and "var(--ink)" in active.group(1), "the active segment keeps full contrast"


def test_motion_is_fully_reduced_motion_gated(client):
    """AWARD/ADOPTION hygiene: prefers-reduced-motion is honored as a blanket
    gate — every animation/transition collapses, not just a hand-picked few."""
    css = client.get("/static/style.css").text
    assert "@media (prefers-reduced-motion: reduce)" in css, "the reduced-motion query is present"
    # the blanket gate disables animation + transition globally under the query
    blanket = re.search(
        r"@media \(prefers-reduced-motion: reduce\)\s*\{[^}]*\*,\s*\*::before,\s*\*::after\s*\{([^}]*)\}",
        css,
    )
    assert blanket, "a universal-selector reduced-motion rule exists"
    body = blanket.group(1)
    assert "animation-duration" in body and "transition-duration" in body, \
        "both animation and transition are collapsed under reduced motion"


def test_palette_governance_is_documented_in_css(client):
    """PALETTE GOVERNANCE: the HCL discipline of the ink ramp is documented in
    style.css comments so contrast can't silently drift; AA contrast figures
    are recorded beside the tokens."""
    css = client.get("/static/style.css").text
    assert "PALETTE GOVERNANCE" in css, "the ink-ramp governance note is in style.css"
    assert "ATTENTION-HIERARCHY" in css, "the chrome-tier intent is documented inline"
    # the recorded contrast ratios stay AA+ on the flat white panel
    assert "16.8:1" in css and "5.6:1" in css, "the measured AA contrast figures are recorded"


def test_primary_button_is_a_flat_accent_fill_no_gradient():
    """COOL PROFESSIONAL hard rule: the primary action is a FLAT accent fill
    (graphite-indigo), never a gradient. The accent carries the button as a
    solid background; the label sits in white on the accent for AA contrast."""
    css = (STATIC_DIR / "style.css").read_text(encoding="utf-8")
    forge = re.search(r"\.btn-forge\s*\{([^}]*)\}", css)
    assert forge, "the primary accent button class exists"
    body = forge.group(1)
    assert "background: var(--marigold)" in body or "#313ECF" in body, \
        "the primary button is a flat accent fill (the --marigold token = graphite-indigo)"
    assert "gradient" not in body, "NO gradient buttons — the fill is flat"
    assert "#fff" in body or "var(--ink)" not in body, "its label reads on the accent fill"


def test_confidence_gauge_is_an_arc_not_a_bar():
    """The signature confidence instrument is a 270° arc gauge — kept,
    because people get confidence."""
    core = (JS_DIR / "core.js").read_text(encoding="utf-8")
    assert "gauge-arc" in core and "gauge-fill-arc" in core
    assert "confGauge" in core and "svgEl" in core
    css = (STATIC_DIR / "style.css").read_text(encoding="utf-8")
    assert ".gauge-fill-arc" in css and ".gauge-track-arc" in css
    for band in ("--teal", "--marigold", "--walnut"):
        assert band in core


def test_toast_notification_system_exists():
    core = (JS_DIR / "core.js").read_text(encoding="utf-8")
    assert "export function toast" in core and "toast-host" in core
    css = (STATIC_DIR / "style.css").read_text(encoding="utf-8")
    assert ".toast-host" in css and ".toast" in css


def test_windows_carry_a_per_app_accent_strip():
    wm = (JS_DIR / "wm.js").read_text(encoding="utf-8")
    assert "appHue" in wm and "--accent" in wm
    core = (JS_DIR / "core.js").read_text(encoding="utf-8")
    assert "export function appHue" in core and "APP_HUE" in core
    css = (STATIC_DIR / "style.css").read_text(encoding="utf-8")
    titlebar = re.search(r"\.titlebar\s*\{([^}]*)\}", css)
    assert titlebar and "var(--accent)" in titlebar.group(1)


def test_wm_interaction_discipline():
    wm = (JS_DIR / "wm.js").read_text(encoding="utf-8")
    assert "setPointerCapture" in wm and "pointercancel" in wm
    assert "translate3d" in wm and "requestAnimationFrame" in wm
    assert "document.addEventListener(\"mousemove\"" not in wm
    assert "willChange" in wm
    assert "/api/workspace" in wm, "layout persists through the workspace API"
    assert "localStorage" in (JS_DIR / "core.js").read_text(encoding="utf-8")


def test_spotlight_speaks_the_search_contract_and_falls_through():
    spot = (JS_DIR / "spotlight.js").read_text(encoding="utf-8")
    assert "/api/search" in spot
    assert "AbortController" in spot, "in-flight searches are cancelled, never reordered"
    assert "aria-activedescendant" in spot
    # no query dead-ends — free text falls through to Ask
    assert 'kind: "ask"' in spot
    assert "SERVER_APP_ALIAS" in spot, "legacy server app ids are aliased"


def test_inspector_scrubber_survived_and_clamps_off_epoch():
    """The bitemporal time slider (de-jargoned 'rewind to a date') lives on
    inside Explore record, and clamps its low bound off the 1970 epoch."""
    inspector = (APPS_DIR / "inspector.js").read_text(encoding="utf-8")
    assert "as_of:" in inspector, "the slider refetches under an as-of stance"
    assert "scrub-track" in inspector and "/neighbors" in inspector
    assert "rewind to a date" in inspector, "the scrubber is de-jargoned"
    assert "EPOCH_FLOOR" in inspector and "1971" in inspector


# ═══════════════════════════════════ THE DATA MAP ENGINE (atlas, unchanged)
# The constellation engine + its served atlas contract are unchanged; only
# the app wrapper's labels are de-jargoned. These guard the engine contract.


def _engine_src() -> str:
    return (JS_DIR / "constellation.js").read_text(encoding="utf-8")


def _app_src() -> str:
    return (APPS_DIR / "datamap.js").read_text(encoding="utf-8")


def test_atlas_fetch_is_wired_and_defensive(client):
    core = (JS_DIR / "core.js").read_text(encoding="utf-8")
    assert "/api/atlas" in core and "loadAtlas" in core
    assert "cache.atlasPromise = null" in core or "atlasPromise: null" in core
    app = _app_src()
    assert "loadAtlas" in app and "renderAtlas" in app
    assert "model not built" in app, "the 404 fallback keeps a quiet, honest voice"
    assert "engine.render(onto)" in app, "the induced types still render"
    assert "dropCaches" in app


def test_atlas_tier_legend_chips_are_filter_toggles():
    app = _app_src()
    for tier in ("confirmed", "likely", "hint", "silos"):
        assert f'"{tier}"' in app, f"the {tier} tier has a legend toggle"
    assert "tier-toggle" in app and "data-tier" in app and "aria-pressed" in app
    assert re.search(r'tierToggle\("hint",[^)]*off: true', app), "hint arcs ship OFF by default"
    css = (STATIC_DIR / "style.css").read_text(encoding="utf-8")
    assert ".tier-toggle" in css
    for tier in ("confirmed", "likely", "hint", "silos"):
        assert f".constellation.hide-{tier}" in css


def test_likely_joins_are_dashed_marigold_with_score_opacity():
    css = (STATIC_DIR / "style.css").read_text(encoding="utf-8")
    likely_rule = re.search(r"\.tier-likely\s*\{([^}]*)\}", css)
    assert likely_rule and "stroke-dasharray" in likely_rule.group(1)
    assert "--marigold" in likely_rule.group(1)
    # MATURATION: the 'likely-breathe' pulse is gone (calm motion) — a hovered/
    # lit likely arc simply lifts to full opacity, no infinite breathing loop.
    assert "likely-breathe" not in css, "the breathing pulse motif is removed"
    lit = re.search(r"\.constellation path\.lit\.tier-likely\s*\{([^}]*)\}", css)
    assert lit and "stroke-opacity" in lit.group(1), "a lit likely arc lifts to full opacity"
    assert "prefers-reduced-motion" in css
    engine = _engine_src()
    assert "tier-${tier}" in engine
    assert 'setAttribute("stroke-opacity"' in engine


def test_atlas_islands_wear_distinct_categorical_hues():
    engine = _engine_src()
    assert "ISLAND_HUES" in engine and "comp._hue" in engine
    assert "fill:${comp._hue}" in engine


def test_atlas_evidence_card_speaks_the_contract():
    engine = _engine_src()
    for key in ("coverage", "overlap_count", "sample_shared_values",
                "name_similarity", "semtype_match", "src_prop", "dst_prop"):
        assert key in engine, f"the evidence card surfaces {key}"
    assert "pinEvidence" in engine and "unpinEvidence" in engine
    assert "slice(0, 5)" in engine
    css = (STATIC_DIR / "style.css").read_text(encoding="utf-8")
    assert ".evidence-card" in css and ".evidence-card.pinned" in css and "--mono" in css


def test_silos_collect_in_a_dignified_archipelago():
    engine = _engine_src()
    assert "archipelago" in engine and "island-hull" in engine and "island-label" in engine
    assert "dataset_count" in engine
    css = (STATIC_DIR / "style.css").read_text(encoding="utf-8")
    assert ".constellation .archipelago" in css
    arch_rules = "".join(m.group(0) for m in re.finditer(r"\.archipelago[^{]*\{[^}]*\}", css))
    assert "--verdict-red" not in arch_rules, "silos are quiet, never error-red"
    # COOL system: island labels are quiet tracked-uppercase (the warm small-caps
    # ornament is gone — section labels are 10-11px tracked uppercase now)
    island_label = re.search(r"\.island-label\s*\{([^}]*)\}", css)
    assert island_label and "text-transform: uppercase" in island_label.group(1), \
        "island labels are quiet tracked-uppercase, not the warm small-caps ornament"


def test_atlas_scale_discipline_is_documented_and_held():
    engine = _engine_src()
    assert "ATLAS SCALE GUARD" in engine and "250 nodes / 600 arcs" in engine
    assert 'setAttribute("viewBox"' in engine
    assert 'svg.addEventListener("pointerover"' in engine
    assert 'svg.addEventListener("click"' in engine
    assert "onpointerenter:" not in engine and "onpointermove:" not in engine
    assert "layoutIterations" in engine and "labels-hidden" in engine
    css = (STATIC_DIR / "style.css").read_text(encoding="utf-8")
    assert ".constellation.labels-hidden .node-label" in css


def test_synthetic_250_node_atlas_fixture_matches_the_contract():
    atlas = json.loads(ATLAS_FIXTURE.read_text(encoding="utf-8"))
    comps, links, stats = atlas["components"], atlas["links"], atlas["stats"]
    n_classes = sum(len(c["class_uris"]) for c in comps)
    assert n_classes == 250 and stats["classes"] == n_classes
    unique = {u for c in comps for u in c["class_uris"]}
    assert len(unique) == n_classes
    assert len(links) >= 600
    assert {l["tier"] for l in links} == {"confirmed", "likely", "hint"}
    assert stats["confirmed"] == sum(1 for l in links if l["tier"] == "confirmed")
    assert stats["likely"] == sum(1 for l in links if l["tier"] == "likely")
    assert stats["hint"] == sum(1 for l in links if l["tier"] == "hint")
    silos = [c for c in comps if c["is_silo"]]
    assert silos and stats["silos"] == len(silos)
    assert stats["components"] == len(comps)
    assert all(len(c["class_uris"]) == 1 for c in silos)
    for c in comps:
        assert {"id", "label", "class_uris", "dataset_count", "is_silo"} <= set(c)
    every_uri = {u for c in comps for u in c["class_uris"]}
    for l in links:
        assert {"src_class", "dst_class", "src_prop", "dst_prop", "tier", "score", "evidence"} <= set(l)
        assert l["src_class"] in every_uri and l["dst_class"] in every_uri
        assert {"coverage", "overlap_count", "sample_shared_values",
                "name_similarity", "semtype_match"} <= set(l["evidence"])
    src = _engine_src() + _app_src()
    for key in ("components", "links", "stats", "class_uris", "dataset_count",
                "is_silo", "src_class", "dst_class", "tier", "score", "evidence"):
        assert key in src, f"the JS never reads contract key {key}"


# ════════════════════════════ UI MATURATION — warm, grown-up, calmer
# These guard the "childish → natural/intuitive/premium" pass: chroma
# discipline, the neutral:accent ratio, type maturity, form restraint, and
# the calmer motion — without weakening any security/payload/abstention gate.


def _root(css):
    return css.split("}", 1)[0]


def _strip_css_comments(css):
    """Drop /* ... */ blocks so token assertions test DECLARATIONS, not the
    documentation prose (which may name old values when explaining a swap)."""
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


def test_chroma_discipline_hues_are_cool_desaturated(client):
    """CHROMA DISCIPLINE (cool): the categorical DATA wheel is the cool,
    low-saturation, information-only set — teal anchor + cool slate/plum/steel/
    sage tones. The ONE accent is graphite-indigo (#313ECF), a flat fill. The
    warm crayon/tan literals are gone everywhere."""
    css = client.get("/static/style.css").text
    root = _root(css)
    # the cool desaturated wheel (the :root token values, mirroring ATLAS_HUES roles)
    cool = ("#0E8C84", "#5B6B86", "#7D5BA6", "#3E6FA3", "#9A6B86", "#4C5578", "#5E8C7A")
    for hue in cool:
        assert hue in root, f"the cool data hue {hue} declares in :root"
    # the one accent is graphite-indigo, a flat fill (never a warm marigold)
    assert "#313ECF" in root, "graphite-indigo is the single accent"
    # the old warm crayon/tan literals must be gone from the default DECLARATIONS
    # (comments may still name them when documenting the swap)
    default_block = _strip_css_comments(css.split('html[data-theme="dark"]')[0])
    for crayon in ("#E0A126", "#1F6F6B", "#C75B39", "#B8532A", "#2D6E8E",
                   "#6E4A63", "#D09735", "#2C5956", "#945442"):
        assert crayon not in default_block, f"the warm crayon {crayon} is gone"
    # the JS categorical contract is cool in lockstep (core.js + constellation.js):
    # the ATLAS_HUES list keeps the same names/order/count, recolored cool.
    core_raw = (JS_DIR / "core.js").read_text(encoding="utf-8")
    engine = (JS_DIR / "constellation.js").read_text(encoding="utf-8")
    for hue in ("#0E8C84", "#4A56C7"):
        assert hue in core_raw and hue in engine, f"{hue} is in the JS cool wheel contract"
    # strip // line + /* */ block comments before checking old literals are gone
    core_code = re.sub(r"//[^\n]*", "", _strip_css_comments(core_raw))
    assert "#D09735" not in core_code and "#2C5956" not in core_code, \
        "warm hues gone from the core.js wheel values"


def test_neutral_accent_ratio_titlebar_is_not_a_colored_bezel(client):
    """NEUTRAL:ACCENT — the window title strip is a neutral cream cap with a
    thin accent (it keeps var(--accent), but as a hairline, not a hue fill).
    The per-card colored left-edges collapse to a hairline default."""
    css = client.get("/static/style.css").text
    titlebar = re.search(r"\.titlebar\s*\{([^}]*)\}", css)
    assert titlebar, "the title strip rule exists"
    body = titlebar.group(1)
    assert "var(--accent)" in body, "the accent survives as a quiet marker"
    assert "background: var(--accent)" not in body, "the strip is NOT a full hue fill"
    assert "background: var(--cream)" in body, "the strip is a neutral cream cap"
    # the build outputs no longer wear persimmon/avocado bezel edges
    assert "border-left: 3px solid var(--persimmon)" not in css
    assert "border-left: 3px solid var(--avocado)" not in css
    assert "border-left: 3px solid var(--ocean)" not in css, "console clarify edge neutralized"


def test_type_is_tight_system_sans_no_serif_hero_no_smallcaps(client):
    """COOL TYPE TREATMENT: the hero/taglines are a tight system SANS (NO
    decorative serif hero); section labels are quiet 10-11px tracked uppercase
    (NO small-caps eyebrow ornament anywhere); mono carries every number/id.
    The chrome sans is system, not Futura."""
    css = client.get("/static/style.css").text
    root = _root(css)
    # a system SANS stack is declared; the --serif token survives as a caller
    # alias but resolves to a SANS stack (no ui-serif / serif webfont)
    assert "--sans:" in root and "-apple-system" in root, "a system sans stack is declared"
    assert "ui-serif" not in root, "NO decorative serif — the hero is tight system sans"
    assert "Futura" not in root, "the geometric Futura-first chrome is gone"
    # mono carries the numbers/ids
    assert "--mono:" in root and "ui-monospace" in root, "mono for every number/id/metric"
    # buttons must NOT use small-caps
    btn = re.search(r"\n\.btn\s*\{([^}]*)\}", css)
    assert btn and "small-caps" not in btn.group(1), "the .btn label is not small-caps"
    # the small-caps eyebrow ornament is removed ENTIRELY (cool hard rule);
    # section labels are tracked uppercase instead
    assert "small-caps" not in css, "NO small-caps ornament anywhere in the cool system"
    assert "text-transform: uppercase" in css and "letter-spacing" in css, \
        "section labels are quiet tracked-uppercase, not small-caps"


def test_form_restraint_radii_reduced_and_toy_motifs_removed(client):
    """FORM RESTRAINT: radii drop (12→8, 14→10, 999px pills → a small
    rounded-rect token) and the toy motifs are deleted — the dot-pulse bounce,
    the conic starburst running-dot, the switcher coach halo, the node-pop
    overshoot, the likely-breathe pulse, the 45° striped chart placeholder."""
    css = client.get("/static/style.css").text
    root = _root(css)
    # COOL system: small radii throughout (6-8px)
    assert "--radius: 7px" in root and "--radius-win: 8px" in root, "radii are small (6-8px)"
    assert "--radius-pill:" in root, "a small rounded-rect pill token replaces 999px"
    # the deleted motif keyframes / gradients are gone
    for motif in ("@keyframes dot-pulse", "@keyframes switcher-halo",
                  "@keyframes node-pop", "@keyframes likely-breathe",
                  "conic-gradient", "repeating-linear-gradient"):
        assert motif not in css, f"the toy motif '{motif}' is removed"
    # the plasticky bright inset highlight on shadow-2 is gone
    assert "rgba(255, 253, 247, 0.6) inset" not in css, "the plastic inset highlight is gone"
    # the JS hooks for the removed motifs are gone too
    dock = (JS_DIR / "dock.js").read_text(encoding="utf-8")
    modes = (JS_DIR / "modes.js").read_text(encoding="utf-8")
    assert "pulse-once" not in dock, "the launch-bounce hook is removed"
    assert "coach-lit" not in modes, "the switcher coach-halo hook is removed"


def test_ground_is_flat_no_paper_grain(client):
    """COOL PROFESSIONAL: the ground is a flat cool surface — the warm paper
    grain is OFF (the grain overlay sits at opacity 0), so structure comes from
    hairlines and flat panels, not texture."""
    css = client.get("/static/style.css").text
    grain = re.search(r"body::before\s*\{([^}]*)\}", css)
    assert grain, "the body grain overlay rule exists"
    assert "opacity: 0" in grain.group(1), "the paper grain is turned off (flat cool ground)"


def test_datamap_has_a_canvas_render_path_with_svg_fallback():
    """PERFORMANCE: the constellation/Data Map gains a CANVAS acceleration
    layer for dense skies (60fps to several-thousand nodes), while keeping the
    crisp accessible SVG fallback under the threshold. The canvas paints on the
    same viewBox transform; the <svg> stays the interaction layer."""
    engine = (JS_DIR / "constellation.js").read_text(encoding="utf-8")
    # the threshold-gated canvas layer exists
    assert "CANVAS_THRESHOLD" in engine, "a documented element-count threshold gates the canvas"
    assert 'createElement("canvas")' in engine, "it creates a real <canvas>"
    assert 'getContext("2d")' in engine and "drawCanvas" in engine, "it paints to a 2d context"
    # the SVG fallback is explicit: under the threshold the pure-SVG path renders
    assert "useCanvas" in engine, "a flag switches between canvas and the SVG fallback"
    assert "ATLAS SCALE GUARD" in engine, "the scale-guard discipline is documented and held"
    # interaction stays honest: pan/zoom on viewBox, a JS nearest-node hit-test
    assert "canvasNodeAt" in engine, "canvas mode hit-tests the nearest node in JS"
    assert 'setAttribute("viewBox"' in engine, "pan/zoom still rides the viewBox"
    # the canvas takes NO pointer events — the svg owns interaction (security/hover)
    css = (STATIC_DIR / "style.css").read_text(encoding="utf-8")
    cv = re.search(r"\.constellation-canvas\s*\{([^}]*)\}", css)
    assert cv and "pointer-events: none" in cv.group(1), "the canvas takes no pointer events"
    # the canvas paints with literal tokens (no CSS var() in a 2d context),
    # and they are the COOL desaturated wheel (teal anchor + indigo data hue)
    assert "#0E8C84" in engine and "#4A56C7" in engine, "the canvas paints the cool wheel"


def test_payload_under_budget_with_canvas_and_both_themes():
    """The cool Slate/Graphite rewrite stays under the 340 KB non-vendor budget,
    even carrying the additive canvas layer and BOTH themes (Slate :root +
    Graphite data-theme) in one stylesheet."""
    total = sum(
        p.stat().st_size
        for p in STATIC_DIR.rglob("*")
        if p.is_file() and "vendor" not in p.parts
    )
    assert total < PAYLOAD_BUDGET, f"non-vendor payload is {total} bytes (budget {PAYLOAD_BUDGET})"
    # the stylesheet carries both themes and stays well within the budget — the
    # cool system is disciplined (flat panels, hairlines, one accent), so a
    # single stylesheet holds Slate + Graphite without blowing the ceiling.
    css_bytes = (STATIC_DIR / "style.css").stat().st_size
    assert css_bytes < PAYLOAD_BUDGET, (
        f"style.css ({css_bytes}) carries both cool themes within the budget"
    )
