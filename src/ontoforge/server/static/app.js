/* OntoForge — the three-mode shell. Boot module.
   Vanilla ES modules, no build chain, no framework.
   SECURITY INVARIANT: every piece of API data enters the DOM through el()/
   document.createTextNode — nothing interpolated is ever assigned to innerHTML.

   THREE MODES (one always-visible switcher): ASK the questioner, BUILD the
   dashboard/data builder, STUDIO the windowed data-engineering desktop. */

import { $, el, api, fmt, loadOntology, store, workspaceState, svgEl, clear, appHue } from "./js/core.js";
import { createBus } from "./js/bus.js";
import { createModeShell, MODES } from "./js/modes.js";
import { createAskSurface } from "./js/surfaces/ask.js";
import { createBuildSurface } from "./js/surfaces/build.js";
import { createWM } from "./js/wm.js";
import { createDock } from "./js/dock.js";
import { createSpotlight } from "./js/spotlight.js";
import { createRegistry } from "./js/apps/registry.js";

const registry = createRegistry();
const bus = createBus();

/* ════════════════════════════ STUDIO — the coherent COCKPIT.
   No longer a floating-window desktop: a left RAIL selects the CENTER
   STAGE; a fixed CONFIRM queue rides the right; the plain-English CONSOLE
   is a persistent bottom command bar. Each named app mounts ONCE into a
   fixed cockpit region through a tiny host that mimics the WM ctx; visibility
   toggles preserve state (and the Data Map live-build poller). The WM lives
   on ONLY for the transient Evidence overlay ("Where this came from"), which
   is genuinely a detached child of its citing element. */

const studioDesktop = $("#desktop");   // now the transient evidence-overlay layer
let dock = null;
let studioMounted = false;
let studioApi = null;

/* The WM is retained for the Evidence drill alone (multi+transient child
   overlays). dockTarget is a no-op since the dock no longer magnifies. */
const wm = createWM({
  desktop: studioDesktop, bus, registry,
  dockTarget: (win) => (dock ? dock.targetFor(win) : null),
  onWindows: (wins) => { if (dock) dock.update(wins); },
});
dock = createDock({ root: $("#dock"), registry, wm });

/* ─────────────────────────────── the cockpit region host.
   Mounts an app spec into a fixed region (NOT a floating window) with a ctx
   that mirrors the WM contract: root=region body, on/emit over the bus,
   addDisposer/setTitle/close/focus/openNear. The app code is unchanged. */
const regions = new Map();   // appId -> { el, body, api }

function mountRegion(appId, regionEl, params = {}) {
  if (regions.has(appId)) return regions.get(appId);
  const spec = registry.get(appId);
  if (!spec) return null;
  regionEl.style.setProperty("--accent", appHue(spec.id));
  const body = el("div", { class: "region-body" });
  regionEl.append(body);
  const disposers = [], subs = [];
  const ctx = {
    winId: `region-${appId}`,
    root: body,
    setTitle() { /* the cockpit names regions in chrome, not per-app */ },
    on(event, fn) { const off = bus.on(event, fn); subs.push(off); return off; },
    emit(event, payload = {}) { bus.emit(event, { sourceWinId: `region-${appId}`, ...payload }); },
    addDisposer(fn) { disposers.push(fn); },
    close() { /* fixed regions don't close */ },
    focus() { focusRegion(appId); },
    openNear(otherApp, otherParams, otherOpts = {}) {
      // an app asking to open a neighbour (Evidence) → the transient overlay
      return wm.open(otherApp, otherParams, otherOpts);
    },
  };
  const appApi = spec.mount(ctx, params) || {};
  const rec = { el: regionEl, body, api: appApi, subs, disposers };
  regions.set(appId, rec);
  return rec;
}

/* ─────────────────────────────────── intent routing policy (Studio).
   Apps emit; the shell decides which region answers. Apps never import or
   reference each other. Several intents must also surface STUDIO first so a
   region exists to receive them. */

function ensureStudio() {
  if (modes && modes.current() !== "studio") modes.switchTo("studio");
}

bus.on("app:launch", ({ app }) => {
  const spec = registry.get(app);
  if (!spec) return;
  ensureStudio();
  // center apps route to the stage; fixed-region apps just focus their region
  if (app === "evidence") { wm.open(app); return; }
  showPanel(app);
});

bus.on("entity:open", ({ uri }) => {
  if (!uri) return;
  ensureStudio();
  // the Record opens as a CENTER detail view, loading the requested entity
  showCenter("inspector");
  const rec = regions.get("inspector");
  if (rec && rec.api.load) rec.api.load(uri);
});

bus.on("class:focus", ({ uri, prop }) => {
  ensureStudio();
  showCenter("constellation");
  const rec = regions.get("constellation");
  if (uri && rec && rec.api.focusClass) rec.api.focusClass(uri, prop);
});

/* Evidence stays a genuinely-floating transient overlay over the stage
   (child of its citing element). It keeps using the WM. */
function routeEvidence(params, sourceWinId) {
  ensureStudio();
  const source = sourceWinId && wm.get ? wm.get(sourceWinId) : null;
  if (source) {
    const child = wm.find((w) => w.app.id === "evidence" && w.parentId === source.id);
    if (child) { if (child.appApi.show) child.appApi.show(params); wm.focus(child); return; }
    wm.open("evidence", params, { near: source, parentId: source.id });
  } else {
    wm.open("evidence", params);
  }
}
bus.on("evidence:atoms", ({ atomIds, label, sourceWinId }) => routeEvidence({ atomIds, label }, sourceWinId));
bus.on("evidence:prov", ({ provRef, label, sourceWinId }) => routeEvidence({ provRef, label }, sourceWinId));

// ask:run from spotlight (free text) lands in ASK mode
bus.on("ask:run", ({ question }) => {
  modes.switchTo("ask", { question });
});

// the STUDIO badge tracks pending confirm-suggestions
bus.on("review:count", ({ count }) => { if (modes) modes.setBadge(count); });

// a surface asks to jump modes (e.g. "Open Studio →", "Try a question →")
bus.on("mode:goto", ({ mode, panel }) => { if (modes) modes.switchTo(mode, panel ? { panel } : {}); });
bus.on("ask:suggest", () => { if (modes) modes.switchTo("ask", { suggest: true }); });

/* ════════════════════════════ STUDIO layout — the cockpit regions.
   A left rail names the sections; clicking one selects the CENTER stage (or
   focuses the always-present Confirm queue / Console bar). The Data Map is
   the default high-contrast centerpiece. */

const STUDIO_PANELS = [
  { id: "catalog", label: "Data Catalog" },
  { id: "constellation", label: "Data Map" },
  { id: "console", label: "Console" },
  { id: "review", label: "Confirm suggestions" },
  { id: "pulse", label: "Activity" },
  { id: "observatory", label: "Observatory" },
];

// which rail ids live in the CENTER stage (swapped) vs. fixed cockpit regions
const CENTER_APPS = ["constellation", "catalog", "pulse", "observatory", "inspector"];
const RAIL_GLYPHS = {
  catalog: "M3 3h7v7H3zM14 3h7v7h-7zM3 14h7v7H3zM14 14h7v7h-7z",
  constellation: "M6 6m-2.5 0a2.5 2.5 0 1 0 5 0a2.5 2.5 0 1 0 -5 0M18 9m-2.5 0a2.5 2.5 0 1 0 5 0a2.5 2.5 0 1 0 -5 0M9 18m-2.5 0a2.5 2.5 0 1 0 5 0a2.5 2.5 0 1 0 -5 0M8 7.5l8 .8M7.5 16l1.2-7M11 17l5.5-6",
  console: "M3 4h18v16H3zM7 9l3 3-3 3M13 15h4",
  review: "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18M8.5 12.5l2.3 2.3 4.7-5",
  pulse: "M3 12h4l2.5 7 5-14 2.5 7h4",
  observatory: "M11 5a6 6 0 1 0 0 12 6 6 0 0 0 0-12M15.5 15.5L21 21",
};
let centerHost = null;   // the stage region wrapper that holds the swapped apps

/* The cockpit shell: rail | center stage | confirm rail, console bar across
   the bottom. Built ONCE; the apps mount lazily into their regions. */
async function mountStudio() {
  if (studioMounted) return;
  studioMounted = true;

  const cockpit = $("#cockpit");

  // ── left rail of named sections ──
  const rail = el("nav", { class: "studio-rail", "aria-label": "studio sections" });
  for (const p of STUDIO_PANELS) {
    const icon = svgEl("svg", {
      class: "rail-ic", viewBox: "0 0 24 24", fill: "none", stroke: "currentColor",
      "stroke-width": "1.6", "stroke-linecap": "round", "stroke-linejoin": "round",
      "aria-hidden": "true",
    }, svgEl("path", { d: RAIL_GLYPHS[p.id] || "" }));
    const label = el("span", { class: "rail-label" }, p.label);
    const btn = el("button", {
      class: "rail-item", type: "button", dataset: { panel: p.id },
      onclick: () => showPanel(p.id),
    }, icon, label);
    if (p.id === "review") {
      btn.append(el("span", { class: "rail-badge", id: "rail-review-badge", hidden: "hidden" }));
    }
    rail.append(btn);
  }

  // ── center stage (the high-contrast centerpiece host) ──
  centerHost = el("div", { class: "cockpit-center map-stage", id: "cockpit-center" });

  // ── right rail: the Confirm queue (review app, always present) ──
  const confirm = el("aside", { class: "cockpit-confirm", "aria-label": "Confirm suggestions" },
    el("div", { class: "region-head" },
      el("span", { class: "region-kicker" }, "Confirm queue"),
      el("span", { class: "region-hint" }, "suggested joins & merges")));

  // ── bottom: the plain-English Console command bar (console app) ──
  const consoleBar = el("div", { class: "cockpit-console", "aria-label": "Console command bar" });

  // insert the regions BEFORE #desktop (the evidence overlay layer stays last)
  cockpit.insertBefore(rail, studioDesktop);
  cockpit.insertBefore(centerHost, studioDesktop);
  cockpit.insertBefore(confirm, studioDesktop);
  cockpit.insertBefore(consoleBar, studioDesktop);

  // mount the permanent regions: Confirm queue + Console + the live Data Map
  mountRegion("review", confirm);
  mountRegion("console", consoleBar);
  // the Data Map mounts up front so studio:build-started always animates,
  // even before the rail selects it
  const stageMap = el("div", { class: "stage-app", dataset: { app: "constellation" } });
  centerHost.append(stageMap);
  mountRegion("constellation", stageMap);

  // decide the entry view from the data state
  let ws = null;
  try { ws = await workspaceState(); } catch { ws = null; }
  let onto = null;
  try { onto = await loadOntology(); } catch { onto = null; }
  const hasModel = !!(onto && onto.classes && onto.classes.length) || !!(ws && ws.built);
  const hasData = !!(ws && ws.datasets && ws.datasets.length);

  // empty project → Data Catalog front and centre; else → the Data Map
  showPanel(hasData || hasModel ? "constellation" : "catalog");
  tileStudioSignature();

  studioApi = { showPanel };
}

/* The signature pairing is now structural (the cockpit grid puts the Data
   Map centre-stage with the Console docked along the bottom) — no hand-tiling
   of floating windows. Kept as a hook for entry + the test pin. */
function tileStudioSignature() {
  if (centerHost) centerHost.classList.add("signature");
}

/* ensure a center app is mounted into the stage host, show it, hide siblings */
function showCenter(appId) {
  if (!centerHost) return null;
  if (!CENTER_APPS.includes(appId)) return null;
  let host = centerHost.querySelector(`.stage-app[data-app="${appId}"]`);
  if (!host) {
    host = el("div", { class: "stage-app", dataset: { app: appId } });
    centerHost.append(host);
    mountRegion(appId, host);
  }
  for (const node of centerHost.querySelectorAll(".stage-app")) {
    node.hidden = node !== host;
  }
  return regions.get(appId);
}

/* a fixed-region rail item (Confirm / Console) just focuses its region */
function focusRegion(appId) {
  const rec = regions.get(appId);
  if (!rec) return;
  rec.el.classList.add("region-flash");
  setTimeout(() => rec.el.classList.remove("region-flash"), 700);
  const focusable = rec.body.querySelector("input, textarea, button, [tabindex]");
  if (focusable) focusable.focus({ preventScroll: true });
}

function showPanel(panelId) {
  ensureStudio();
  if (CENTER_APPS.includes(panelId)) showCenter(panelId);
  else focusRegion(panelId);     // review / console are always-present regions
  for (const b of document.querySelectorAll(".rail-item")) {
    b.classList.toggle("active", b.dataset.panel === panelId);
  }
}

bus.on("studio:show-map", () => { ensureStudio(); showPanel("constellation"); });
bus.on("studio:show-catalog", () => { ensureStudio(); showPanel("catalog"); });
bus.on("studio:build-started", () => { ensureStudio(); showPanel("constellation"); });
// mirror the review count into the studio rail badge too
bus.on("review:count", ({ count }) => {
  const b = $("#rail-review-badge");
  if (b) { if (count > 0) { b.hidden = false; b.textContent = String(count); } else { b.hidden = true; } }
});

/* ════════════════════════════ ASK + BUILD single surfaces */

const askSurface = createAskSurface({ bus });
const buildSurface = createBuildSurface({ bus });

/* ════════════════════════════ the mode shell */

const modes = createModeShell({
  bus,
  surfaces: {
    ask: askSurface,
    build: buildSurface,
    studio: {
      mount({ firstVisit }) { mountStudio(firstVisit); },
      enter({ panel }) { if (panel && studioApi) studioApi.showPanel(panel); },
      show({ panel }) { if (panel && studioApi) studioApi.showPanel(panel); },
    },
  },
});

/* ───────────────────────────────────────────────────────── spotlight */

const spotlight = createSpotlight({
  root: $("#spotlight"),
  input: $("#spotlight-input"),
  listEl: $("#spotlight-results"),
  countEl: $("#spotlight-count"),
  registry, wm, bus,
});
$("#spotlight-hint").addEventListener("click", () => spotlight.toggle());

/* ───────────────────────────────── theme (Slate light is the default) */
const THEME_KEY = "ontoforge.theme";
/* sun (shown in Graphite/dark → tap to go light) vs crescent moon
   (shown in Slate/light → tap to go dark), built XSS-safe via svgEl. */
function themeIcon(theme) {
  const ic = svgEl("svg", {
    viewBox: "0 0 24 24", fill: "none", stroke: "currentColor",
    "stroke-width": "1.6", "stroke-linecap": "round", "stroke-linejoin": "round",
    "aria-hidden": "true",
  });
  if (theme === "dark") {
    ic.append(svgEl("circle", { cx: "12", cy: "12", r: "4.2" }));
    ic.append(svgEl("path", { d: "M12 3v2M12 19v2M3 12h2M19 12h2M5.6 5.6l1.4 1.4M17 17l1.4 1.4M18.4 5.6L17 7M7 17l-1.4 1.4" }));
  } else {
    ic.append(svgEl("path", { d: "M20 14.5A8 8 0 1 1 9.5 4a6.3 6.3 0 0 0 10.5 10.5z" }));
  }
  return ic;
}
function applyTheme(theme) {
  if (theme === "dark") document.documentElement.setAttribute("data-theme", "dark");
  else document.documentElement.removeAttribute("data-theme");
  const t = $("#theme-toggle");
  if (t) clear(t).append(themeIcon(theme));
}
applyTheme(store.get(THEME_KEY, "light"));   // Slate light is the default
$("#theme-toggle").addEventListener("click", () => {
  const next = document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark";
  store.set(THEME_KEY, next);
  applyTheme(next);
});

/* ───────────────────────────────────────────────────────────── top bar */

async function refreshMeta() {
  try {
    const s = await api("/api/status");
    $("#meta-estate").textContent = s.estate;
    $("#meta-atoms").textContent = s.ledger_exists ? fmt(s.atoms) : "—";
  } catch {
    $("#meta-estate").textContent = "—";
  }
}
bus.on("world:reload", refreshMeta);
setInterval(refreshMeta, 60_000);

/* ──────────────────────────────────────────────────────────── keyboard */

document.addEventListener("keydown", (e) => {
  // ⌘K toggles Spotlight from anywhere
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
    e.preventDefault();
    spotlight.toggle();
    return;
  }
  if (spotlight.isOpen()) return;

  // ⌘1 / ⌘2 / ⌘3 jump straight to a mode — the shell claims these first
  if ((e.metaKey || e.ctrlKey) && /^[1-3]$/.test(e.key)) {
    const mode = modes.modeForDigit(Number(e.key));
    if (mode) { e.preventDefault(); modes.switchTo(mode); return; }
  }

  const a = document.activeElement;
  const typing = a && (a.tagName === "INPUT" || a.tagName === "TEXTAREA" || a.isContentEditable);

  if (e.key === "Escape" && !typing) {
    if (modes.current() === "studio") {
      // a transient evidence overlay closes first
      const top = wm.topWin();
      if (top && top.app.transient) wm.close(top);
    }
    return;
  }
  if (typing || e.metaKey || e.ctrlKey || e.altKey) return;

  // '/' summons Spotlight
  if (e.key === "/") { e.preventDefault(); spotlight.open(); return; }

  // mode-specific key handling
  if (modes.current() === "ask") {
    if (askSurface.onKey && askSurface.onKey(e)) return;
  }
  // STUDIO cockpit: keys aimed at a region route to that region's app.
  // j/k/a/r belong to the Confirm queue (review); other regions get keys
  // when the event originates inside them.
  if (modes.current() === "studio") {
    // a transient evidence overlay (a real WM window) claims keys first
    const hitWin = e.target instanceof Element && e.target.closest(".window");
    if (hitWin) {
      const win = wm.get(hitWin.dataset.winId);
      if (win && win.appApi.onKey) win.appApi.onKey(e);
      return;
    }
    // which fixed region does the event belong to?
    const hitRegion = e.target instanceof Element
      && e.target.closest(".cockpit-confirm, .cockpit-console, .cockpit-center");
    if (hitRegion) {
      for (const [id, rec] of regions) {
        if (rec.el === hitRegion || rec.el.contains(hitRegion) || hitRegion.contains(rec.el)) {
          if (rec.api.onKey) rec.api.onKey(e);
          return;
        }
      }
    }
    // the Confirm queue owns the global j/k/a/r adjudication shortcuts
    if (/^[jkar]$/i.test(e.key)) {
      const rev = regions.get("review");
      if (rev && rev.api.onKey) rev.api.onKey(e);
    }
  }
});

/* ──────────────────────────────────────────────────────── first light */

async function boot() {
  refreshMeta();
  loadOntology().catch(() => { /* surfaces handle the not-ready state */ });

  // is the model built? drives onboarding + the landing surface guidance
  let dataBuilt = false;
  try {
    const ws = await workspaceState();
    const onto = await loadOntology().catch(() => null);
    dataBuilt = !!(ws && ws.built) || !!(onto && onto.classes && onto.classes.length);
  } catch { /* keep false */ }

  // ASK is the default landing for every session
  modes.boot("ask", dataBuilt);
}

boot();
