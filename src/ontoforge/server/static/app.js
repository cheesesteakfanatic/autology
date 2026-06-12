/* OntoForge OS — the ontology operating system. Boot module.
   Vanilla ES modules, no build chain, no framework.
   SECURITY INVARIANT: every piece of API data enters the DOM through el()/
   document.createTextNode — nothing interpolated is ever assigned to innerHTML.

   Layers:
     js/core.js       shared kernel helpers (el/api/store/ontology cache)
     js/bus.js        the inter-app bus (namespaced intents)
     js/wm.js         the window manager (drag/resize/snap/z/FLIP/persist)
     js/dock.js       the dock (launch/focus, running dots, minimized shelf)
     js/spotlight.js  the front door (⌘K / just type; /api/search)
     js/apps/*        the micro-apps, registered in js/apps/registry.js   */

import { $, api, fmt, loadOntology } from "./js/core.js";
import { createBus } from "./js/bus.js";
import { createWM } from "./js/wm.js";
import { createDock } from "./js/dock.js";
import { createSpotlight } from "./js/spotlight.js";
import { createRegistry } from "./js/apps/registry.js";

const desktop = $("#desktop");
const epigraph = $("#epigraph");

const registry = createRegistry();
const bus = createBus();

let dock = null; // created after the WM; the WM reaches it through a closure

const wm = createWM({
  desktop, bus, registry,
  dockTarget: (win) => (dock ? dock.targetFor(win) : null),
  onWindows: (wins) => {
    if (dock) dock.update(wins);
    epigraph.classList.toggle("hidden", wins.length > 0);
  },
});

dock = createDock({ root: $("#dock"), registry, wm });

// ─────────────────────────────────────────────── intent routing policy
// Apps emit; the WM (here) decides which window answers. Apps never
// import or reference each other.

bus.on("app:launch", ({ app }) => {
  const spec = registry.get(app);
  if (!spec) return;
  if (spec.multi === false) {
    const existing = wm.find((w) => w.app.id === app);
    if (existing) { wm.focus(existing); return; }
  }
  wm.open(app);
});

bus.on("ask:run", ({ question }) => {
  // reuse the most recent Ask window; spawn one if none is open
  const existing = wm.findAll((w) => w.app.id === "ask").pop();
  if (existing) {
    wm.focus(existing);
    if (existing.appApi.run) existing.appApi.run(question);
  } else {
    wm.open("ask", { question });
  }
});

bus.on("entity:open", ({ uri, sourceWinId }) => {
  if (!uri) return;
  // same entity already inspected → focus it; else another Inspector
  // opens BESIDE the source — the OS moment
  const existing = wm.find((w) => w.app.id === "inspector" && w.appApi.uri && w.appApi.uri() === uri);
  if (existing) { wm.focus(existing); return; }
  const source = sourceWinId ? wm.get(sourceWinId) : null;
  wm.open("inspector", { uri }, source ? { near: source } : {});
});

bus.on("class:focus", ({ uri, prop }) => {
  const existing = wm.find((w) => w.app.id === "constellation");
  if (existing) {
    wm.focus(existing);
    if (uri && existing.appApi.focusClass) existing.appApi.focusClass(uri, prop);
  } else {
    wm.open("constellation", uri ? { uri, prop } : {});
  }
});

function routeEvidence(params, sourceWinId) {
  const source = sourceWinId ? wm.get(sourceWinId) : null;
  if (source) {
    // one evidence child per parent: re-point it rather than stacking copies
    const child = wm.find((w) => w.app.id === "evidence" && w.parentId === source.id);
    if (child) {
      if (child.appApi.show) child.appApi.show(params);
      wm.focus(child);
      return;
    }
    wm.open("evidence", params, { near: source, parentId: source.id });
  } else {
    wm.open("evidence", params);
  }
}
bus.on("evidence:atoms", ({ atomIds, label, sourceWinId }) =>
  routeEvidence({ atomIds, label }, sourceWinId));
bus.on("evidence:prov", ({ provRef, label, sourceWinId }) =>
  routeEvidence({ provRef, label }, sourceWinId));

// ─────────────────────────────────────────────────────────── spotlight

const spotlight = createSpotlight({
  root: $("#spotlight"),
  input: $("#spotlight-input"),
  listEl: $("#spotlight-results"),
  countEl: $("#spotlight-count"),
  registry, wm, bus,
});

$("#spotlight-hint").addEventListener("click", () => spotlight.toggle());

// ───────────────────────────────────────────────────────────── menubar

async function refreshMenubar() {
  try {
    const s = await api("/api/status");
    $("#meta-estate").textContent = s.estate;
    $("#meta-atoms").textContent = s.ledger_exists ? fmt(s.atoms) : "—";
    $("#meta-cost").textContent = s.ledger_exists ? fmt(s.cost_tokens) : "—";
  } catch {
    $("#meta-estate").textContent = "—";
  }
}
bus.on("world:reload", refreshMenubar);
setInterval(refreshMenubar, 60_000);

// ──────────────────────────────────────────────────────────── keyboard
// All shortcuts route through here; the focused window's app receives
// only what the shell didn't claim. No app attaches global listeners.

document.addEventListener("keydown", (e) => {
  // ⌘K toggles Spotlight from anywhere (the same key closes it)
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
    e.preventDefault();
    spotlight.toggle();
    return;
  }
  if (spotlight.isOpen()) return; // the palette owns its own keys

  const a = document.activeElement;
  const typing = a && (a.tagName === "INPUT" || a.tagName === "TEXTAREA" || a.isContentEditable);

  if (e.key === "Escape" && !typing) {
    // Escape dismisses a focused transient (evidence) window
    const top = wm.topWin();
    if (top && top.app.transient) wm.close(top);
    return;
  }
  if (typing || e.metaKey || e.ctrlKey || e.altKey) return;

  // '/' summons Spotlight…
  if (e.key === "/") {
    e.preventDefault();
    spotlight.open();
    return;
  }
  // a key aimed inside a window belongs to that window's app
  const hitWin = e.target instanceof Element && e.target.closest(".window");
  if (hitWin) {
    const win = wm.get(hitWin.dataset.winId);
    if (win && win.appApi.onKey) win.appApi.onKey(e);
    return;
  }
  // …and just typing on the EMPTY workspace summons Spotlight too
  if (!wm.topWin()) {
    if (e.key.length === 1 && /\S/.test(e.key)) {
      e.preventDefault();
      spotlight.open(e.key);
    }
    return;
  }
  // loose keys (focus drifted to the void) still reach the focused window
  const top = wm.topWin();
  if (top && top.appApi.onKey) top.appApi.onKey(e);
});

// ──────────────────────────────────────────────────────── first light

async function boot() {
  refreshMenubar();
  loadOntology().catch(() => { /* surfaces inside the constellation */ });

  const layout = await wm.loadLayout();
  if (layout && layout.windows.length) {
    wm.restoreLayout(layout);
  } else {
    // first run: Ask and the Constellation side by side
    wm.open("ask", {}, { snapped: "left" });
    wm.open("constellation", {}, { snapped: "right" });
  }
}

boot();
