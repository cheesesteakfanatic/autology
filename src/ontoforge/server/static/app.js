/* OntoForge — the CONVERSATION-FIRST shell. Boot module.
   Vanilla ES modules, no build chain, no framework.

   ONE conversation thread: the user talks in a persistent bottom composer and
   the autonomous data-engineering agent responds with short narration + rich
   INLINE ARTIFACTS (cited answer, Vega chart, confirm-join cards, op preview,
   data map). There are no Ask/Build/Studio modes — a slim thread-history rail,
   a calm conversation reading column, and the composer. The agent is proactive:
   it opens by narrating what it mapped and what needs the user.

   SECURITY INVARIANT: every piece of API data enters the DOM through el()/
   svgEl()/createTextNode — nothing interpolated is ever assigned to innerHTML.
   Keyless / offline / deterministic; system fonts only; vendored Vega only. */

import { $, el, api, fmt, loadOntology, store, svgEl, clear } from "./js/core.js";
import { createBus } from "./js/bus.js";
import { createAgentShell } from "./js/agent.js";
import { createSpotlight } from "./js/spotlight.js";
import { createRegistry } from "./js/apps/registry.js";

const bus = createBus();
const registry = createRegistry();

/* ════════════════════════════ the conversation shell */

const agent = createAgentShell({
  bus,
  els: {
    rail: $("#rail"),
    scroll: $("#scroll"),
    col: $("#thread-col"),
    composerForm: $("#composer-form"),
    composerInput: $("#composer-input"),
    suggestBox: $("#composer-suggest"),
  },
});

/* every external "ask" intent (spotlight free text, a recalled question, an
   entity/class jump) becomes a turn in the one thread — the agent classifies
   it. The shell no longer has modes to switch between. */
bus.on("ask:run", ({ question }) => { if (question) agent.submit(question); });
bus.on("entity:open", ({ uri }) => { if (uri) agent.submit(`what's in ${uri.split(/[/#]/).filter(Boolean).pop() || uri}?`); });
bus.on("class:focus", ({ uri }) => { if (uri) agent.submit(`show me ${uri.split(/[/#]/).filter(Boolean).pop() || uri} in the model`); });
bus.on("app:launch", ({ app }) => {
  // legacy app launches map to a natural request the agent understands
  if (app === "constellation") agent.submit("show me the model");
  else if (app === "review") agent.submit("are there joins I should confirm?");
  else if (app === "console") agent.focusComposer();
  else agent.focusComposer();
});

/* ───────────────────────────────────────────────────────── spotlight
   The spotlight stays ⌘K — a thread / entity / question jump. It still wants a
   registry (for the legacy app-id aliasing the search contract pins) and a wm
   handle; the conversation shell has no window manager, so we hand it a tiny
   no-op shim. Every result routes over the bus into the one thread. */
const wmShim = { list: () => [], get: () => null, focus: () => {} };
const spotlight = createSpotlight({
  root: $("#spotlight"),
  input: $("#spotlight-input"),
  listEl: $("#spotlight-results"),
  countEl: $("#spotlight-count"),
  registry, wm: wmShim, bus,
});
$("#spotlight-hint").addEventListener("click", () => spotlight.toggle());

/* ───────────────────────────────── theme (Slate light is the default) */
const THEME_KEY = "ontoforge.theme";
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
  const a = document.activeElement;
  const typing = a && (a.tagName === "INPUT" || a.tagName === "TEXTAREA" || a.isContentEditable);
  if (typing || e.metaKey || e.ctrlKey || e.altKey) return;
  // '/' summons Spotlight; otherwise typing flows to the composer
  if (e.key === "/") { e.preventDefault(); spotlight.open(); return; }
});

/* ──────────────────────────────────────────────────────── first light */
async function boot() {
  refreshMeta();
  loadOntology().catch(() => { /* the opener/agent handle a not-ready world */ });
  agent.boot();
}

boot();
