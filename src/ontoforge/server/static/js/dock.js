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

/* fisheye magnification — nearest tile to the cursor swells to PEAK, neighbors
   taper over FALLOFF px, far tiles rest at 1.0. The lift rides the swell so the
   crowd parts upward like the macOS dock. A single rAF loop lerps each tile's
   live scale toward its target with a spring-ish ease (no per-tile transition —
   transforms are GPU-cheap, transform-origin bottom keeps tiles seated). */
const PEAK = 1.6;      // nearest-icon scale
const FALLOFF = 78;    // px over which the swell decays to rest
const LIFT = 13;       // px translateY at full swell
const EASE = 0.26;     // lerp factor per frame (≈ critically damped feel)
const SETTLE = 0.0015; // |scale - target| below which a tile is "settled"

function prefersReducedMotion() {
  return typeof matchMedia === "function"
    && matchMedia("(prefers-reduced-motion: reduce)").matches;
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
    invalidateMagnify(); // tile set changed → resting anchors moved
  }

  /** FLIP target: the window's minimized tile if present, else its app icon. */
  function targetFor(win) {
    const node = tileEls.get(win.id) || iconEls.get(win.app.id);
    return node ? node.getBoundingClientRect() : null;
  }

  /* ── proximity magnification ─────────────────────────────────────────────
     Every live dock button (app icons + minimized tiles) is a magnifier cell.
     `cursorX` is null when the pointer is away → all cells relax to rest. The
     rAF loop runs only while something is still moving, then parks itself. */
  const live = new Map(); // node -> { scale, target }
  let cursorX = null;
  let raf = 0;

  function cells() {
    return [...appsBox.children, ...minBox.children];
  }

  /** swell at a given distance (px) from the cursor — PEAK at 0, 1.0 past FALLOFF. */
  function swellAt(dist) {
    const d = Math.min(Math.abs(dist), FALLOFF);
    // cosine shoulder: flat-topped near the cursor, smooth tail (fisheye, not a spike)
    const k = 0.5 + 0.5 * Math.cos((d / FALLOFF) * Math.PI); // 1 → 0
    return 1 + (PEAK - 1) * k;
  }

  /** Each cell's RESTING center-x in viewport coords. We measure off the
      untransformed layout box (dock rect + offsetLeft/offsetWidth) so a swollen
      tile never shifts its own anchor — otherwise the scale feeds back and the
      dock jitters. Cached per gesture, rebuilt when the cell set changes. */
  let restX = new Map(); // node -> center x
  function invalidateMagnify() {
    restX = new Map();
    // forget per-node tween state for nodes that no longer exist
    const present = new Set(cells());
    for (const node of live.keys()) if (!present.has(node)) live.delete(node);
    if (cursorX != null) kick(); // re-settle the surviving cells against new anchors
  }
  function measureRest() {
    restX = new Map();
    const dockLeft = root.getBoundingClientRect().left - root.scrollLeft;
    for (const node of cells()) {
      restX.set(node, dockLeft + node.offsetLeft + node.offsetWidth / 2);
    }
  }

  function targetScale(node) {
    if (cursorX == null) return 1;
    const cx = restX.get(node);
    return cx == null ? 1 : swellAt(cursorX - cx);
  }

  function frame() {
    raf = 0;
    let moving = false;
    for (const node of cells()) {
      let rec = live.get(node);
      if (!rec) { rec = { scale: 1, target: 1 }; live.set(node, rec); }
      rec.target = targetScale(node);
      rec.scale += (rec.target - rec.scale) * EASE;
      if (Math.abs(rec.target - rec.scale) < SETTLE) rec.scale = rec.target;
      else moving = true;
      applyCell(node, rec.scale);
    }
    if (moving) raf = requestAnimationFrame(frame);
  }

  function applyCell(node, scale) {
    if (scale <= 1.0001) {
      node.style.transform = "";
      node.style.zIndex = "";
      return;
    }
    const lift = LIFT * ((scale - 1) / (PEAK - 1)); // lift scales with the swell
    node.style.transform = `translateY(${-lift.toFixed(2)}px) scale(${scale.toFixed(3)})`;
    node.style.zIndex = "1"; // magnified tiles ride above the panel hairline
  }

  function kick() {
    if (!raf) raf = requestAnimationFrame(frame);
  }

  function onMove(e) {
    if (cursorX == null || !restX.size) measureRest(); // (re)anchor on gesture start
    cursorX = e.clientX;
    kick();
  }
  function onLeave() {
    cursorX = null;
    kick(); // let the loop ease everyone back to rest, then it parks itself
  }

  if (!prefersReducedMotion()) {
    root.classList.add("dock-magnify"); // CSS suppresses its static hover-lift here
    root.addEventListener("mousemove", onMove);
    root.addEventListener("mouseleave", onLeave);
    // the resting anchors move when the dock reflows (window resize / tiles
    // appear or vanish) — drop the cache so the next frame re-measures.
    addEventListener("resize", () => { restX = new Map(); });
  }

  return { update, targetFor, activate };
}
