/* The three-mode shell — ASK | BUILD | STUDIO. One always-visible segmented
   switcher in the top bar; the user always knows which mode they're in.
   Switching is instant (no reload) and changes the whole workspace; each
   mode shows ONLY its own surfaces. ASK = the questioner; BUILD = the
   dashboard/data builder; STUDIO = the data-engineer's playground (Catalog,
   Data Map, Console, Confirm suggestions, Activity), powered by the WM/dock.
   The WM/dock are STUDIO's substrate only; ASK and BUILD are calm single
   surfaces that never spawn windows. De-jargon is presentation-only. */

import { $, store } from "./core.js";

const MODE_KEY = "ontoforge.mode";
const COACH_KEY = "ontoforge.coach.seen";
const FIRSTVISIT_KEY = "ontoforge.mode.firstvisit"; // {ask,build,studio}
export const MODES = ["ask", "build", "studio"];

export function createModeShell({ bus, surfaces }) {
  // surfaces: { ask: {mount, show?}, build: {mount, show?}, studio: {mount, show?, badge?} }
  const segs = new Map(MODES.map((m) => [m, $(`#mode-${m}`)]));
  const panes = new Map(MODES.map((m) => [m, $(`#pane-${m}`)]));
  const dock = $("#dock");
  const badge = $("#studio-badge");

  let current = null;
  const mounted = new Set();

  function firstVisits() { return store.get(FIRSTVISIT_KEY, {}); }
  function markVisited(mode) {
    const fv = firstVisits();
    if (!fv[mode]) { fv[mode] = true; store.set(FIRSTVISIT_KEY, fv); }
  }
  function isFirstVisit(mode) { return !firstVisits()[mode]; }

  function switchTo(mode, opts = {}) {
    if (!MODES.includes(mode)) mode = "ask";
    if (mode === current) {
      // already here — still honor an explicit show() (e.g. a suggested Q)
      const s = surfaces[mode];
      if (s && s.show) s.show(opts);
      return;
    }
    const previous = current;
    current = mode;
    store.set(MODE_KEY, mode);

    for (const [m, seg] of segs) {
      const on = m === mode;
      seg.classList.toggle("active", on);
      seg.setAttribute("aria-selected", on ? "true" : "false");
    }
    for (const [m, pane] of panes) {
      pane.hidden = m !== mode;
    }
    // the dock belongs to STUDIO alone — never floats over Ask/Build
    if (dock) dock.hidden = mode !== "studio";

    // mount lazily, on first entry to a mode
    const s = surfaces[mode];
    if (s) {
      if (!mounted.has(mode)) {
        mounted.add(mode);
        s.mount({ pane: panes.get(mode), firstVisit: isFirstVisit(mode) });
      }
      if (s.enter) s.enter({ firstVisit: isFirstVisit(mode), from: previous, ...opts });
      if (s.show && opts && Object.keys(opts).length) s.show(opts);
    }
    markVisited(mode);
    bus.emit("mode:changed", { mode, from: previous });
  }

  for (const [m, seg] of segs) {
    if (seg) seg.addEventListener("click", () => switchTo(m));
  }

  // keyboard: ⌘1 / ⌘2 / ⌘3 jump straight to a mode (the shell claims these
  // before any window). Handled in app.js, exposed here.
  function modeForDigit(d) { return MODES[d - 1] || null; }

  function setBadge(count) {
    if (!badge) return;
    const n = Number(count) || 0;
    if (n > 0) { badge.hidden = false; badge.textContent = String(n); }
    else { badge.hidden = true; badge.textContent = ""; }
  }

  /* ─────────────────────────────── first-run orientation coach */
  function maybeCoach(dataBuilt) {
    if (store.get(COACH_KEY, false)) return;
    const coach = $("#coach");
    if (!coach) return;
    const go = $("#coach-go");
    const x = $("#coach-x");
    // adapt the primary action to the data state
    if (go) {
      if (dataBuilt) {
        go.textContent = "Try a question →";
        go.onclick = () => { dismissCoach(); switchTo("ask"); bus.emit("ask:suggest", {}); };
      } else {
        go.textContent = "Add your first dataset →";
        go.onclick = () => { dismissCoach(); switchTo("studio", { panel: "catalog" }); };
      }
    }
    if (x) x.onclick = dismissCoach;
    coach.hidden = false;
    // light the switcher so the eye lands on it
    const sw = $("#mode-switcher");
    if (sw) sw.classList.add("coach-lit");
    function dismissCoach() {
      store.set(COACH_KEY, true);
      coach.hidden = true;
      if (sw) sw.classList.remove("coach-lit");
    }
  }

  function reopenCoach() {
    store.set(COACH_KEY, false);
    maybeCoach(lastDataBuilt);
  }
  let lastDataBuilt = false;

  const helpBtn = $("#help-toggle");
  if (helpBtn) helpBtn.addEventListener("click", reopenCoach);

  return {
    switchTo,
    current: () => current,
    modeForDigit,
    setBadge,
    boot(initialMode, dataBuilt) {
      lastDataBuilt = !!dataBuilt;
      switchTo(initialMode || store.get(MODE_KEY, "ask") || "ask");
      maybeCoach(dataBuilt);
    },
  };
}
