/* OntoForge — the three-mode shell. Boot module.
   Vanilla ES modules, no build chain, no framework.
   SECURITY INVARIANT: every piece of API data enters the DOM through el()/
   document.createTextNode — nothing interpolated is ever assigned to innerHTML.

   THREE MODES (one always-visible switcher): ASK the questioner, BUILD the
   dashboard/data builder, STUDIO the windowed data-engineering desktop. */

import { $, api, fmt, loadOntology, store, workspaceState, svgEl, clear } from "./js/core.js";
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

/* ════════════════════════════ STUDIO — the windowed power-tool substrate.
   The WM + dock are born inside the STUDIO pane only. ASK and BUILD never
   spawn windows. */

const studioDesktop = $("#desktop");
let dock = null;
let studioMounted = false;
let studioApi = null;

const wm = createWM({
  desktop: studioDesktop, bus, registry,
  dockTarget: (win) => (dock ? dock.targetFor(win) : null),
  onWindows: (wins) => { if (dock) dock.update(wins); },
});
dock = createDock({ root: $("#dock"), registry, wm });

/* ─────────────────────────────────── intent routing policy (Studio).
   Apps emit; the shell decides which window answers. Apps never import or
   reference each other. Several intents must also surface STUDIO first so a
   window has somewhere to live. */

function ensureStudio() {
  if (modes && modes.current() !== "studio") modes.switchTo("studio");
}

bus.on("app:launch", ({ app }) => {
  const spec = registry.get(app);
  if (!spec) return;
  ensureStudio();
  if (spec.multi === false) {
    const existing = wm.find((w) => w.app.id === app);
    if (existing) { wm.focus(existing); return; }
  }
  wm.open(app);
});

bus.on("entity:open", ({ uri, sourceWinId }) => {
  if (!uri) return;
  ensureStudio();
  const existing = wm.find((w) => w.app.id === "inspector" && w.appApi.uri && w.appApi.uri() === uri);
  if (existing) { wm.focus(existing); return; }
  const source = sourceWinId ? wm.get(sourceWinId) : null;
  wm.open("inspector", { uri }, source ? { near: source } : {});
});

bus.on("class:focus", ({ uri, prop }) => {
  ensureStudio();
  const existing = wm.find((w) => w.app.id === "constellation");
  if (existing) {
    wm.focus(existing);
    if (uri && existing.appApi.focusClass) existing.appApi.focusClass(uri, prop);
  } else {
    wm.open("constellation", uri ? { uri, prop } : {});
  }
});

function routeEvidence(params, sourceWinId) {
  ensureStudio();
  const source = sourceWinId ? wm.get(sourceWinId) : null;
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

/* ════════════════════════════ STUDIO layout — the labeled panels.
   The signature pairing on entry: the Data Map canvas with the Console
   docked along the bottom. A left rail names the sections; clicking one
   focuses (or opens) the matching window. The dock still powers the
   windows; the rail is the plain-language way in. */

const STUDIO_PANELS = [
  { id: "catalog", label: "Data Catalog" },
  { id: "constellation", label: "Data Map" },
  { id: "console", label: "Console" },
  { id: "review", label: "Confirm suggestions" },
  { id: "pulse", label: "Activity" },
];

function focusOrOpen(appId, params) {
  const existing = wm.find((w) => w.app.id === appId);
  if (existing) { wm.focus(existing); return existing; }
  return wm.open(appId, params || {});
}

async function mountStudio(firstVisit) {
  if (studioMounted) return;
  studioMounted = true;

  // a left rail of named sections (not a flat icon dock)
  const rail = document.createElement("nav");
  rail.className = "studio-rail";
  rail.setAttribute("aria-label", "studio sections");
  for (const p of STUDIO_PANELS) {
    const btn = document.createElement("button");
    btn.className = "rail-item";
    btn.type = "button";
    btn.dataset.panel = p.id;
    btn.textContent = p.label;
    if (p.id === "review") {
      const b = document.createElement("span");
      b.className = "rail-badge";
      b.id = "rail-review-badge";
      b.hidden = true;
      btn.append(b);
    }
    btn.addEventListener("click", () => showPanel(p.id));
    rail.append(btn);
  }
  studioDesktop.parentElement.insertBefore(rail, studioDesktop);

  // decide the entry panel from the data state
  let ws = null;
  try { ws = await workspaceState(); } catch { ws = null; }
  let onto = null;
  try { onto = await loadOntology(); } catch { onto = null; }
  const hasModel = !!(onto && onto.classes && onto.classes.length) || !!(ws && ws.built);
  const hasData = !!(ws && ws.datasets && ws.datasets.length);

  if (!hasData && !hasModel) {
    // empty project: Data Catalog front and center
    showPanel("catalog");
  } else {
    // the signature moment: Data Map on top, Console docked along the bottom
    focusOrOpen("constellation");
    focusOrOpen("console");
    tileStudioSignature();
    showPanel("constellation");
  }

  studioApi = { showPanel };
}

function tileStudioSignature() {
  // Data Map fills the top; Console docked along the bottom.
  const map = wm.find((w) => w.app.id === "constellation");
  const con = wm.find((w) => w.app.id === "console");
  const W = studioDesktop.clientWidth, H = studioDesktop.clientHeight;
  if (W < 50 || H < 50) return;
  const split = Math.round(H * 0.62);
  if (map) { Object.assign(map, { x: 0, y: 0, w: W, h: split, snapped: null }); wm.applyRect(map); }
  if (con) { Object.assign(con, { x: 0, y: split + 8, w: W, h: Math.max(180, H - split - 8), snapped: null }); wm.applyRect(con); }
}

function showPanel(panelId) {
  const win = focusOrOpen(panelId);
  // light the rail item
  for (const b of document.querySelectorAll(".rail-item")) {
    b.classList.toggle("active", b.dataset.panel === panelId);
  }
  return win;
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

/* ───────────────────────────────────────────────── theme (warm default) */
const THEME_KEY = "ontoforge.theme";
/* sun (shown in Observatory/dark → tap to go warm) vs crescent moon
   (shown in Atelier/warm → tap to go dark), built XSS-safe via svgEl. */
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
applyTheme(store.get(THEME_KEY, "warm"));
$("#theme-toggle").addEventListener("click", () => {
  const next = document.documentElement.getAttribute("data-theme") === "dark" ? "warm" : "dark";
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
  // keys aimed inside a Studio window belong to that window's app
  if (modes.current() === "studio") {
    const hitWin = e.target instanceof Element && e.target.closest(".window");
    if (hitWin) {
      const win = wm.get(hitWin.dataset.winId);
      if (win && win.appApi.onKey) win.appApi.onKey(e);
      return;
    }
    const top = wm.topWin();
    if (top && top.appApi.onKey) top.appApi.onKey(e);
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
