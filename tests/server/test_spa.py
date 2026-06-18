"""GET / serves the CONVERSATION-FIRST agent shell — ONE thread where the user
talks in a persistent bottom composer ("Ask, build, or wire up your data —") and
the autonomous data-engineering agent responds with short narration + rich INLINE
ARTIFACTS (cited answer, Vega chart, confirm-join cards, op-preview, data map).
There are no Ask/Build/Studio modes: a slim thread-history rail, a calm
conversation reading column, the composer. The agent loop speaks to POST
/api/agent and opens proactively via GET /api/agent/opener.

The served markup carries the rail / conversation column / composer; every
static reference resolves; the ES-module import graph is closed; data never
enters the DOM through innerHTML; abstention keeps its dignified voice (now
adjudicated server-side and surfaced as a calm text turn); the REUSED renderers
(the answer card, the Vega chart, the confirm card, the op preview, the data
map) live on as inline artifact renderers wired to the frozen API contract;
user-facing chrome speaks plain language (no internal engine jargon); and the
whole non-vendor payload stays under budget."""

from __future__ import annotations

import json
import re
from pathlib import Path

STATIC_DIR = Path(__file__).resolve().parents[2] / "src" / "ontoforge" / "server" / "static"
JS_DIR = STATIC_DIR / "js"
APPS_DIR = JS_DIR / "apps"
ATLAS_FIXTURE = Path(__file__).parent / "fixtures" / "atlas_synthetic_250.json"

#: The reused engine apps that survive as inline-artifact renderers / spotlight
#: targets. The conversation shell no longer windows them, but the app code +
#: its endpoints are REUSED unchanged: the Data Map (constellation engine), the
#: Confirm queue (review), the engineering Console, plus the shared utilities.
#: ``observatory`` is the OBSERVABILITY surface (value-level lineage, the
#: append-only audit log, run history, the compute-at-cost ledger).
STUDIO_APPS = (
    "catalog", "datamap", "console", "review", "pulse", "inspector", "evidence", "observatory",
)
#: The inline-artifact KINDS the agent emits — each maps to a reused renderer
#: in js/artifacts.js (answer = ask.js card, chart = build.js Vega, confirm_joins
#: = review.js card, op_preview = console.js preview, datamap = constellation.js).
ARTIFACT_KINDS = ("answer", "chart", "confirm_joins", "op_preview", "datamap", "text")
#: Non-vendor payload budget. Held at 400 KB to seat the conversation shell plus
#: the reused Tableau-grade view/answer/chart/confirm/op/datamap renderers (now
#: in js/artifacts.js) and the real VENDORED-Vega charting. The user chose
#: CAPABILITY over bytes: those substantial renderers + the agent loop + the data
#: map engine + BOTH cool themes (SLATE light :root + GRAPHITE dark data-theme)
#: live under one hard ceiling, so any future decorative bloat still trips it.
PAYLOAD_BUDGET = 400 * 1024


def all_js_files():
    return [STATIC_DIR / "app.js", *sorted(JS_DIR.rglob("*.js"))]


def _index() -> str:
    return (STATIC_DIR / "index.html").read_text(encoding="utf-8")


# ════════════════════════════════════════ the conversation-first shell


def test_root_serves_the_conversation_shell(client):
    out = client.get("/")
    assert out.status_code == 200
    assert "text/html" in out.headers["content-type"]
    html = out.text
    assert "OntoForge" in html
    # ONE conversation: a thread-history rail, the reading column, the composer.
    # There is no mode switcher and no Ask/Build/Studio segments any more.
    assert 'id="mode-switcher"' not in html, "the three-mode switcher is gone"
    for seg in ('id="mode-ask"', 'id="mode-build"', 'id="mode-studio"',
                'id="pane-ask"', 'id="pane-build"', 'id="pane-studio"'):
        assert seg not in html, f"the three-mode chrome '{seg}' is gone"
    # the slim left thread-history rail
    assert 'id="rail"' in html and 'class="rail"' in html, "the thread-history rail"
    # the calm conversation reading column + the live thread log
    assert 'class="conv"' in html, "the conversation reading column"
    assert 'id="thread-col"' in html, "the conversation thread"
    assert 'role="log"' in html and 'aria-live="polite"' in html, "the thread is a live log"
    # ONE persistent bottom composer with the approved prompt
    assert 'class="composer"' in html, "the persistent bottom composer"
    assert 'id="composer-form"' in html and 'id="composer-input"' in html
    assert "Ask, build, or wire up your data" in html, "the approved composer prompt"
    # the composer is a real combobox with grounded typeahead
    inp = re.search(r'id="composer-input"[^>]*>', html).group(0)
    assert 'role="combobox"' in inp and 'aria-controls="composer-suggest"' in inp
    assert 'id="composer-suggest"' in html, "the grounded suggestion drop"
    # spotlight survives (⌘K jump to a thread / entity / question)
    assert 'id="spotlight"' in html and 'id="spotlight-input"' in html
    assert 'role="combobox"' in html and 'aria-controls="spotlight-results"' in html


def test_shell_has_no_cockpit_or_floating_window_chrome():
    """The conversation shell replaced the Studio cockpit + the WM desktop +
    the dock + the orientation coach — none of that chrome ships in the markup."""
    html = _index()
    for gone in ('id="cockpit"', "cockpit-grid", 'id="dock"', 'id="desktop"',
                 'id="coach"', 'id="studio-badge"', 'role="tablist"'):
        assert gone not in html, f"the old shell chrome '{gone}' is gone"


def test_app_boots_the_agent_shell_and_routes_intents():
    """app.js boots ONE agent shell (no mode controller). Every external 'ask'
    intent — spotlight free text, a recalled question, an entity/class jump —
    becomes a turn in the one thread, classified by the agent."""
    app = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    assert "createAgentShell" in app, "the conversation shell is the boot surface"
    assert "agent.boot(" in app, "the agent shell boots the session"
    # there is no mode controller / mode boot any more
    assert "createModeShell" not in app and 'modes.boot(' not in app, "no mode controller"
    # external intents fold into the one thread via agent.submit over the bus
    assert "agent.submit" in app, "external intents become a turn in the thread"
    for intent in ("ask:run", "entity:open", "class:focus"):
        assert intent in app, f"the bus intent {intent} still routes into the thread"


def test_agent_loop_module_speaks_the_agent_contract():
    """js/agent.js is the conversation loop: it POSTs /api/agent for each turn,
    opens proactively via GET /api/agent/opener, renders narration + INLINE
    ARTIFACT cards through the reused renderers (artifacts.js), and persists the
    thread history. Data enters the DOM only as text nodes — never innerHTML."""
    agent = (JS_DIR / "agent.js").read_text(encoding="utf-8")
    assert "createAgentShell" in agent
    assert "/api/agent" in agent, "each turn POSTs the agent endpoint"
    assert "/api/agent/opener" in agent, "the proactive opener is fetched on load"
    # narration + artifacts: the reused renderers are invoked per artifact
    assert "renderArtifact" in agent and "suggestionChips" in agent, "reused renderers drive the cards"
    assert "narration" in agent and "artifacts" in agent, "a turn = narration + artifacts"
    assert "clarification" in agent, "a clarify turn reads as a calm note, never a guess"
    # the thread-history rail is localStorage-backed
    assert "ontoforge.thread" in agent and "store" in agent, "thread history persists locally"
    # the composer + grounded typeahead reuse /api/suggest with an AbortController
    assert "/api/suggest" in agent and "AbortController" in agent, "grounded typeahead, cancellable"
    # the security discipline: data → text nodes, never innerHTML
    assert "createTextNode" in agent, "narration numbers are wrapped as text nodes"


# ════════════════════════════════════ INLINE ARTIFACTS — the reused renderers
# The signature reframe: the OLD surface render logic (ask.js answer card,
# build.js Vega chart) and the OLD app render logic (review confirm card,
# console op preview, constellation data map) live on UNCHANGED as inline
# artifact renderers in js/artifacts.js, mounted into each agent turn. These
# guard that the renderers are present and wired to the FROZEN API contract.


def test_artifacts_module_is_the_inline_renderer_hub(client):
    """js/artifacts.js exports one renderer per artifact kind and a dispatch
    table. Each renderer reuses the existing engine contract; charts use the
    VENDORED Vega only; provenance is one disclosure away."""
    art = (JS_DIR / "artifacts.js").read_text(encoding="utf-8")
    assert "export function renderArtifact" in art, "the dispatch entry point"
    for kind in ARTIFACT_KINDS:
        assert f'"{kind}"' in art, f"the dispatch table handles the '{kind}' artifact"
    for fn in ("renderAnswerArtifact", "renderChartArtifact", "renderConfirmArtifact",
               "renderOpPreviewArtifact", "renderDataMapArtifact", "renderTextArtifact"):
        assert f"export function {fn}" in art, f"the reused renderer {fn} is exported"
    # it is a real ES module on disk, served as javascript
    assert (JS_DIR / "artifacts.js").is_file()
    served = client.get("/static/js/artifacts.js")
    assert served.status_code == 200 and "javascript" in served.headers["content-type"]


def test_answer_artifact_reuses_the_ask_card_contract(client):
    """The ANSWER artifact is the reused ask.js answer card: a cited value /
    table with a confidence read and the lazy provenance disclosure (resolving
    atoms via /api/atoms), de-jargoned to 'where this came from' / 'source
    records'. The /api/ask call itself is dispatched server-side by the agent."""
    art = (JS_DIR / "artifacts.js").read_text(encoding="utf-8")
    assert "renderAnswerArtifact" in art
    assert "citations" in art and "confidence" in art and "plain_english" in art, "the AskOut shape"
    assert "/api/atoms/" in art, "provenance resolves atoms lazily"
    assert "where this came from" in art, "atoms/citation jargon → 'where this came from'"
    assert "source record" in art, "atoms → 'source records'"
    css = client.get("/static/style.css").text
    assert ".art" in css and ".arthead" in css, "the inline artifact card has a designed frame"


def test_abstention_stays_first_class_and_dignified():
    """Abstention/clarify is adjudicated by the engine (ask abstains below the
    soft floor, clarifies in the band) and surfaced HONESTLY in the thread —
    never a confident guess. The agent orchestrator routes an abstained answer
    to a plain text turn and a clarify to a one-question note."""
    agent_py = (STATIC_DIR.parent / "agent.py").read_text(encoding="utf-8")
    assert "abstain" in agent_py, "the orchestrator honours the engine's abstention"
    assert "clarification" in agent_py, "a band answer asks ONE question, never guesses"
    # the front-end renders the clarify turn as a calm note (not an error style)
    agent_js = (JS_DIR / "agent.js").read_text(encoding="utf-8")
    assert "clarify-inline" in agent_js or "clarification" in agent_js


def test_chart_artifact_reuses_the_vendored_vega(client):
    """The CHART artifact is the reused build.js Vega render path: it renders the
    executed view's rows through the VENDORED window.vegaEmbed with the cool
    chart theme, falling back to a table; it offers Extract-CSV (/api/extract)."""
    art = (JS_DIR / "artifacts.js").read_text(encoding="utf-8")
    assert "renderChartArtifact" in art
    assert "vegaEmbed" in art, "the chart uses the vendored vega-embed"
    assert "/api/extract" in art, "the chart offers an Extract-CSV slice"
    # the cool desaturated chart theme — teal anchor + the indigo data hue,
    # never the warm marigold/tan palette
    assert "#0E8C84" in art and "#4A56C7" in art, "the cool chart inks"
    assert "#D09735" not in art and "#9b978e" not in art, "the warm/old chart inks are gone"


def test_confirm_artifact_reuses_the_review_verdict(client):
    """The CONFIRM-JOINS artifact is the reused review.js confirm card: likely-
    join + flagged review items with Confirm / Not the same, posting the
    unchanged verdict to /api/review/{decision_id} with the internal verdict
    values preserved."""
    art = (JS_DIR / "artifacts.js").read_text(encoding="utf-8")
    assert "renderConfirmArtifact" in art
    assert "/api/review/" in art and 'verdict("accept")' in art, "the verdict API is unchanged"
    assert "Confirm" in art and "Not the same" in art, "accept/reject → Confirm / Not the same"


def test_op_preview_artifact_reuses_the_console_discipline(client):
    """The OP-PREVIEW artifact is the reused console.js discipline: interpret →
    PREVIEW (never apply blind) → Apply with Undo. The op_token echo and the
    'nothing has changed yet' voice are preserved verbatim; destructive ops
    carry a consequence and need an explicit Apply tap."""
    art = (JS_DIR / "artifacts.js").read_text(encoding="utf-8")
    assert "renderOpPreviewArtifact" in art
    assert "/api/engineer/apply" in art and "/api/engineer/undo" in art, "the frozen contract"
    assert "op_token" in art and "undo_token" in art, "the op_token echo + Undo discipline"
    assert "nothing has changed yet" in art, "the preview-before-acting voice survives"
    assert "DESTRUCTIVE" in art, "destructive ops are flagged before acting"


def test_datamap_artifact_reuses_the_constellation_engine(client):
    """The DATA-MAP artifact is the reused constellation.js engine: the agent
    hands it the atlas {components, links, stats} and it renders the real
    interactive map via engine.renderAtlas."""
    art = (JS_DIR / "artifacts.js").read_text(encoding="utf-8")
    assert "renderDataMapArtifact" in art
    assert "createConstellation" in art and "renderAtlas" in art, "the data map reuses the engine"
    assert 'from "./constellation.js"' in art, "it imports the constellation engine, not a copy"


# ════════════════════════════════════ de-jargon: plain language in chrome


def test_user_facing_chrome_is_plain_language():
    """The de-jargon contract: internal engine names never appear in any
    user-facing label. The whitepaper codenames stay in code/URIs only."""
    html = _index()
    # the top bar speaks plain language
    assert "your data" in html, "estate → 'your data'"
    assert "source records" in html, "atoms → 'source records'"
    # internal engine codenames must NOT be visible in the shell chrome
    for jargon in ("STRATA", "HEARTH", "LODESTONE", "ANVIL", "TEMPER",
                   "WARDEN", "VISTA", "AMBER", "OQIR", "estate</",
                   "ontology operating system", "atoms <b"):
        assert jargon not in html, f"the shell must not expose '{jargon}'"


def test_naming_map_applied_across_apps():
    """The de-jargon naming map is applied as LABELS in the reused apps:
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
    for intent in ("ask:run", "entity:open", "class:focus"):
        assert intent in app, f"the bus intent {intent} is unchanged"
    core = (JS_DIR / "core.js").read_text(encoding="utf-8")
    assert "/api/atlas" in core, "the atlas endpoint name is unchanged in code"
    review = (APPS_DIR / "review.js").read_text(encoding="utf-8")
    assert "/api/review" in review and "verdict: v" in review, "the verdict API is unchanged"
    assert 'verdict("accept")' in review, "the internal accept/reject verdict values are unchanged"


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


def test_index_modulepreloads_the_agent_layer(client):
    """The shell preloads the conversation layer: the kernel, the bus, the agent
    loop, the inline-artifact renderers — each a real file, each fetchable as
    javascript. (The reused data-map engine + app registry preload too.)"""
    html = _index()
    preloads = re.findall(r'modulepreload"\s+href="/static/([^"]+)"', html)
    for must in ("app.js", "js/core.js", "js/bus.js", "js/agent.js", "js/artifacts.js"):
        assert must in preloads, f"the shell preloads {must}"
    for mod in ("core.js", "bus.js", "agent.js", "artifacts.js", "spotlight.js", "constellation.js"):
        assert (JS_DIR / mod).is_file(), f"missing shell layer: js/{mod}"
        out = client.get(f"/static/js/{mod}")
        assert out.status_code == 200
        assert "javascript" in out.headers["content-type"]


def test_reused_engine_apps_exist_and_are_served(client):
    """The reused engine apps stay on disk and served — their render logic
    powers the inline artifacts (Data Map, Confirm, Console) and the spotlight
    targets; each carries its unchanged registry id (datamap keeps
    'constellation')."""
    registry = (APPS_DIR / "registry.js").read_text(encoding="utf-8")
    files = {p.stem for p in APPS_DIR.glob("*.js")}
    for app in STUDIO_APPS:
        assert app in files, f"missing reused app: js/apps/{app}.js"
        assert client.get(f"/static/js/apps/{app}.js").status_code == 200
    for imp in STUDIO_APPS:
        assert f"./{imp}.js" in registry, f"registry does not import {imp}"
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
    # the shell splits into kernel, agent loop, artifact renderers, the data-map
    # engine + the reused apps reachable through the registry
    assert len(seen) >= 14, "the shell splits into kernel, agent loop, renderers, reused apps"


def test_agent_shell_module_is_reachable_from_app(client):
    """The conversation loop + the inline renderers are reachable through the
    import graph from app.js — the shell really is wired to the agent layer."""
    seen: set[Path] = set()
    queue = [STATIC_DIR / "app.js"]
    while queue:
        path = queue.pop()
        if path in seen or not path.is_file():
            continue
        seen.add(path)
        src = path.read_text(encoding="utf-8")
        for imp in re.findall(r'from\s+"(\.[^"]+)"', src):
            queue.append((path.parent / imp).resolve())
    names = {p.name for p in seen}
    assert "agent.js" in names, "app.js reaches the agent loop"
    assert "artifacts.js" in names, "the agent loop reaches the inline renderers"
    assert "constellation.js" in names, "the data-map engine is reachable for the datamap artifact"


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


# ═══════════════════════════════ the agent API contract (POST /api/agent)


def test_agent_endpoints_are_registered():
    """The conversation shell stands on two new endpoints: POST /api/agent (one
    turn → narration + typed inline artifacts) and GET /api/agent/opener (the
    proactive 'I mapped N datasets into M entities…' opener). Both are wired."""
    app_py = (STATIC_DIR.parent / "app.py").read_text(encoding="utf-8")
    assert '@app.post("/api/agent"' in app_py, "POST /api/agent is registered"
    assert '@app.get("/api/agent/opener"' in app_py, "GET /api/agent/opener is registered"
    # the orchestrator reuses the SAME engine service functions — it never
    # duplicates ask/view/interpret logic
    assert "from . import agent as agent_loop" in app_py
    schemas = (STATIC_DIR.parent / "schemas.py").read_text(encoding="utf-8")
    for model in ("AgentIn", "AgentOut", "AgentArtifact", "AgentOpenerOut"):
        assert f"class {model}" in schemas, f"the {model} schema exists"


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
    vega) ships under the 400 KB non-vendor budget."""
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


def test_spotlight_speaks_the_search_contract_and_falls_through():
    spot = (JS_DIR / "spotlight.js").read_text(encoding="utf-8")
    assert "/api/search" in spot
    assert "AbortController" in spot, "in-flight searches are cancelled, never reordered"
    assert "aria-activedescendant" in spot
    # no query dead-ends — free text falls through to Ask (now a thread turn)
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
# The constellation engine + its served atlas contract are unchanged; it now
# powers the datamap inline artifact. These guard the engine contract.


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
    # the JS hook for the removed launch-bounce motif is gone too
    dock = (JS_DIR / "dock.js").read_text(encoding="utf-8")
    assert "pulse-once" not in dock, "the launch-bounce hook is removed"


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
    """The cool Slate/Graphite rewrite plus the conversation shell + the reused
    inline-artifact renderers stay under the 400 KB non-vendor budget, even
    carrying the additive canvas layer and BOTH themes (Slate :root + Graphite
    data-theme) in one stylesheet."""
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
