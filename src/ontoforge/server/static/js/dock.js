/* The dock — bottom center, frosted dark. One icon per registered app
   (small-caps label on hover, amber running dot), plus a hairline-separated
   shelf where minimized windows collect. The WM asks targetFor(win) for the
   FLIP minimize target rect. */

import { el, clear, appHue } from "./core.js";

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
      el("span", { class: "di-glyph", "aria-hidden": "true" }, spec.glyph),
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
        el("span", { class: "di-glyph", "aria-hidden": "true" }, w.app.glyph),
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
