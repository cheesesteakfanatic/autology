/* The dock — bottom center, frosted dark. One icon per registered app
   (small-caps label on hover, amber running dot), plus a hairline-separated
   shelf where minimized windows collect. The WM asks targetFor(win) for the
   FLIP minimize target rect. */

import { el, svgEl, clear, appHue } from "./core.js";

/* clean inline-SVG line icons (1.6 stroke, rounded, currentColor) keyed by
   app id — XSS-safe via svgEl. Each entry is a list of [tag, attrs] paths so a
   fresh node tree is built per tile. The studio app ids differ from the
   mockup's order; this map pins each to the right glyph. */
const DOCK_PATHS = {
  catalog: [["rect", { x: "3", y: "3", width: "7", height: "7", rx: "1.5" }],
            ["rect", { x: "14", y: "3", width: "7", height: "7", rx: "1.5" }],
            ["rect", { x: "3", y: "14", width: "7", height: "7", rx: "1.5" }],
            ["rect", { x: "14", y: "14", width: "7", height: "7", rx: "1.5" }]],
  constellation: [["circle", { cx: "6", cy: "6", r: "2.5" }],
                  ["circle", { cx: "18", cy: "9", r: "2.5" }],
                  ["circle", { cx: "9", cy: "18", r: "2.5" }],
                  ["path", { d: "M8 7.5l8 .8M7.5 16l1.2-7M11 17l5.5-6" }]],
  console: [["rect", { x: "3", y: "4", width: "18", height: "16", rx: "2.5" }],
            ["path", { d: "M7 9l3 3-3 3M13 15h4" }]],
  review: [["circle", { cx: "12", cy: "12", r: "9" }],
           ["path", { d: "M8.5 12.5l2.3 2.3 4.7-5" }]],
  pulse: [["path", { d: "M3 12h4l2.5 7 5-14 2.5 7h4" }]],
  inspector: [["circle", { cx: "12", cy: "12", r: "8" }],
              ["circle", { cx: "12", cy: "12", r: "3" }]],
  evidence: [["path", { d: "M9 3v18M15 3v18M3 9h18M3 15h18" }]],
  observatory: [["circle", { cx: "11", cy: "11", r: "6" }],
                ["path", { d: "M15.5 15.5L21 21" }]],
};
function dockIcon(appId) {
  const svg = svgEl("svg", {
    class: "di-svg", width: "21", height: "21", viewBox: "0 0 24 24",
    fill: "none", stroke: "currentColor", "stroke-width": "1.6",
    "stroke-linecap": "round", "stroke-linejoin": "round", "aria-hidden": "true",
  });
  for (const [tag, attrs] of (DOCK_PATHS[appId] || [])) svg.append(svgEl(tag, attrs));
  return svg;
}

export function createDock({ root, registry, wm }) {
  const appsBox = el("div", { class: "dock-apps", role: "group", "aria-label": "applications" });
  const sep = el("span", { class: "dock-sep", hidden: "hidden", "aria-hidden": "true" });
  const minBox = el("div", { class: "dock-min", role: "group", "aria-label": "minimized windows" });
  root.append(appsBox, sep, minBox);

  const iconEls = new Map(); // appId -> button
  const tileEls = new Map(); // winId -> button

  for (const spec of registry.all()) {
    const icon = el("button", {
      class: "dock-icon", type: "button", dataset: { app: spec.id },
      style: `--accent:${appHue(spec.id)}`,   // the tile wears its app hue
      "aria-label": `${spec.title} — launch or focus`,
      onclick: () => activate(spec.id),
    },
      el("span", { class: "di-glyph", "aria-hidden": "true" }, dockIcon(spec.id)),
      el("span", { class: "di-label" }, spec.title),
      el("span", { class: "di-dot", "aria-hidden": "true" }));
    iconEls.set(spec.id, icon);
    appsBox.append(icon);
  }

  function activate(appId) {
    const wins = wm.findAll((w) => w.app.id === appId);
    if (!wins.length) { wm.open(appId); return; }
    const visible = wins.filter((w) => !w.minimized);
    if (visible.length) wm.focus(visible[visible.length - 1]);
    else wm.restore(wins[wins.length - 1]);
  }

  function update(wins) {
    const running = new Set(wins.map((w) => w.app.id));
    for (const [appId, icon] of iconEls) {
      const now = running.has(appId);
      // a quiet running dot fades in — no launch bounce
      icon.classList.toggle("running", now);
    }

    const minimized = wins.filter((w) => w.minimized);
    clear(minBox);
    tileEls.clear();
    sep.hidden = !minimized.length;
    for (const w of minimized) {
      const tile = el("button", {
        class: "dock-tile", type: "button",
        title: w.title || w.app.title,
        "aria-label": `restore ${w.title || w.app.title}`,
        onclick: () => wm.restore(w),
      },
        el("span", { class: "di-glyph", "aria-hidden": "true" }, dockIcon(w.app.id)),
        el("span", { class: "di-label" }, w.title || w.app.title));
      tileEls.set(w.id, tile);
      minBox.append(tile);
    }
  }

  /** FLIP target: the window's minimized tile if present, else its app icon. */
  function targetFor(win) {
    const node = tileEls.get(win.id) || iconEls.get(win.app.id);
    return node ? node.getBoundingClientRect() : null;
  }

  return { update, targetFor, activate };
}
