/* The window manager — floating windows over the workspace void.
   Mechanics (not skins) borrowed from real WMs:
   - pointer-capture drags (setPointerCapture; no document mousemove)
   - transform-only motion: translate3d, one rAF-coalesced write per frame,
     geometry read ONCE at gesture start
   - explicit stack array for z-order; bands: desktop(0) < windows(10..990)
     < dock(1000) < spotlight(2000) < ghosts(3000)
   - snap to half/quarter/max from pointer-position edge zones, with a
     translucent preview ghost and unsnap memory
   - FLIP minimize-to-dock (transform+opacity only)
   - interaction shield (body.wm-gesture) during every gesture
   - layout persisted: PUT /api/workspace (debounced) + localStorage always */

import { el, api, debounce, store, appHue } from "./core.js";

const WORKSPACE_KEY = "ontoforge.workspace";
const MIN_W = 300;
const MIN_H = 180;
const EDGE = 18;          // snap-strip width, hit-tested on the POINTER
const HANDLES = ["n", "s", "e", "w", "ne", "nw", "se", "sw"];

function zoneRect(zone, W, H) {
  const w2 = Math.round(W / 2), h2 = Math.round(H / 2);
  switch (zone) {
    case "left":  return { x: 0,  y: 0,  w: w2,     h: H };
    case "right": return { x: w2, y: 0,  w: W - w2, h: H };
    case "max":   return { x: 0,  y: 0,  w: W,      h: H };
    case "tl":    return { x: 0,  y: 0,  w: w2,     h: h2 };
    case "tr":    return { x: w2, y: 0,  w: W - w2, h: h2 };
    case "bl":    return { x: 0,  y: h2, w: w2,     h: H - h2 };
    case "br":    return { x: w2, y: h2, w: W - w2, h: H - h2 };
    default:      return null;
  }
}

function hitZone(px, py, W, H) {
  const L = px <= EDGE, R = px >= W - EDGE, T = py <= EDGE, B = py >= H - EDGE;
  if (L && T) return "tl";
  if (R && T) return "tr";
  if (L && B) return "bl";
  if (R && B) return "br";
  if (T) return "max";
  if (L) return "left";
  if (R) return "right";
  return null;
}

export function createWM({ desktop, bus, registry, onWindows, dockTarget }) {
  const windows = new Map();   // id -> win
  const stack = [];            // ids, bottom → top
  let nextId = 1;
  let opened = 0;              // lifetime count, drives cascade placement
  let restoring = false;

  const preview = el("div", { class: "snap-preview", "aria-hidden": "true" });
  desktop.append(preview);

  const deskSize = () => ({ W: desktop.clientWidth, H: desktop.clientHeight });

  /* The workspace breathes: snapped windows re-tile to the new viewport,
     floating windows are clamped back into reach. Also heals layouts
     measured while the surface had no size yet (hidden tab, iframe). */
  function retile() {
    const { W, H } = deskSize();
    if (W < 50 || H < 50) return;
    for (const win of windows.values()) {
      if (win.snapped) Object.assign(win, zoneRect(win.snapped, W, H));
      else {
        win.x = Math.max(-(win.w - 64), Math.min(W - 64, win.x));
        win.y = Math.max(0, Math.min(H - 24, win.y));
      }
      applyRect(win);
    }
  }
  if (typeof ResizeObserver === "function") {
    new ResizeObserver(retile).observe(desktop);
  } else {
    window.addEventListener("resize", retile);
  }

  function changed() {
    if (onWindows) onWindows(list());
    if (!restoring) persist();
  }

  function list() {
    return stack.map((id) => windows.get(id)).filter(Boolean);
  }

  // ─────────────────────────────────────────────── geometry application

  function applyRect(win) {
    win.el.style.width = `${win.w}px`;
    win.el.style.height = `${win.h}px`;
    win.el.style.transform = `translate3d(${win.x}px, ${win.y}px, 0)`;
  }

  /** A one-off programmatic move (snap/restore): transitions ON for its
      duration, never during pointer-driven gestures. */
  function animateTo(win, rect) {
    win.el.classList.add("win-animate");
    Object.assign(win, rect);
    applyRect(win);
    const done = () => win.el.classList.remove("win-animate");
    win.el.addEventListener("transitionend", done, { once: true });
    setTimeout(done, 260); // reduced-motion / no-transition fallback
  }

  // ───────────────────────────────────────────────── z-order and focus

  function compactZ() {
    stack.forEach((id, i) => {
      const w = windows.get(id);
      if (w) {
        w.el.style.zIndex = String(10 + i);
        w.el.classList.toggle("focused", i === stack.length - 1 && !w.minimized);
      }
    });
  }

  function topWin() {
    for (let i = stack.length - 1; i >= 0; i--) {
      const w = windows.get(stack[i]);
      if (w && !w.minimized) return w;
    }
    return null;
  }

  function focus(win, { keyboard = true } = {}) {
    if (!win) return;
    if (win.minimized) { restore(win); return; }
    const i = stack.indexOf(win.id);
    if (i !== -1) stack.splice(i, 1);
    stack.push(win.id);
    compactZ();
    if (keyboard && !win.el.contains(document.activeElement)) {
      (win.lastFocus && win.el.contains(win.lastFocus) ? win.lastFocus : win.el)
        .focus({ preventScroll: true });
    }
    changed();
  }

  // raise-on-pointerdown is the single source of truth for focus
  desktop.addEventListener("pointerdown", (e) => {
    const root = e.target.closest ? e.target.closest(".window") : null;
    if (!root) return;
    const win = windows.get(root.dataset.winId);
    if (win && stack[stack.length - 1] !== win.id) focus(win, { keyboard: false });
  }, true);

  // ───────────────────────────────────────────────── snap preview ghost

  function showPreview(zone) {
    if (!zone) { preview.classList.remove("visible"); return; }
    const { W, H } = deskSize();
    const r = zoneRect(zone, W, H);
    preview.style.width = `${r.w}px`;
    preview.style.height = `${r.h}px`;
    preview.style.transform = `translate3d(${r.x}px, ${r.y}px, 0)`;
    preview.classList.add("visible");
  }

  function snapTo(win, zone) {
    const { W, H } = deskSize();
    if (W < 50 || H < 50) return; // a zero surface can't be tiled
    if (!win.snapped) win.preSnap = { x: win.x, y: win.y, w: win.w, h: win.h };
    win.snapped = zone;
    animateTo(win, zoneRect(zone, W, H));
    changed();
  }

  function unsnap(win) {
    if (!win.snapped) return;
    const back = win.preSnap || { x: 60, y: 40, w: win.app.w, h: win.app.h };
    win.snapped = null;
    win.preSnap = null;
    animateTo(win, back);
    changed();
  }

  // ────────────────────────────────────────────────────────────── drag

  function wireDrag(win, titlebar) {
    titlebar.addEventListener("pointerdown", (e) => {
      if (e.button !== 0 || e.target.closest(".win-btn")) return;
      const deskRect = desktop.getBoundingClientRect();   // read ONCE
      const W = deskRect.width, H = deskRect.height;

      // unsnap memory: dragging out of a snapped state restores the
      // pre-snap size with the grab point kept proportional under the cursor
      if (win.snapped) {
        const rel = (e.clientX - deskRect.left - win.x) / win.w;
        const r = win.preSnap || { w: win.app.w, h: win.app.h };
        win.w = r.w; win.h = r.h;
        win.x = Math.round(e.clientX - deskRect.left - r.w * rel);
        win.y = Math.round(e.clientY - deskRect.top - 16);
        win.snapped = null;
        win.preSnap = null;
        applyRect(win);
      }

      const start = { px: e.clientX, py: e.clientY, x: win.x, y: win.y };
      try { titlebar.setPointerCapture(e.pointerId); } catch { /* synthetic pointer */ }
      win.el.classList.add("win-gesture");
      win.el.style.willChange = "transform";
      document.body.classList.add("wm-gesture");

      let latest = e, raf = 0, zone = null;

      const step = () => {
        raf = 0;
        const dx = latest.clientX - start.px, dy = latest.clientY - start.py;
        // clamp so at least a strip of titlebar is always recoverable
        win.x = Math.max(-(win.w - 64), Math.min(W - 64, start.x + dx));
        win.y = Math.max(0, Math.min(H - 24, start.y + dy));
        win.el.style.transform = `translate3d(${win.x}px, ${win.y}px, 0)`;
        zone = hitZone(latest.clientX - deskRect.left, latest.clientY - deskRect.top, W, H);
        showPreview(zone);
      };
      const onMove = (ev) => { latest = ev; if (!raf) raf = requestAnimationFrame(step); };

      const finish = (commit) => {
        if (raf) cancelAnimationFrame(raf);
        titlebar.removeEventListener("pointermove", onMove);
        titlebar.removeEventListener("pointerup", onUp);
        titlebar.removeEventListener("pointercancel", onCancel);
        try { titlebar.releasePointerCapture(e.pointerId); } catch { /* gone */ }
        win.el.classList.remove("win-gesture");
        win.el.style.willChange = "";
        document.body.classList.remove("wm-gesture");
        showPreview(null);
        if (commit && zone) snapTo(win, zone);
        else changed();
      };
      const onUp = () => finish(true);
      const onCancel = () => finish(false);

      titlebar.addEventListener("pointermove", onMove);
      titlebar.addEventListener("pointerup", onUp);
      titlebar.addEventListener("pointercancel", onCancel);
    });

    titlebar.addEventListener("dblclick", (e) => {
      if (e.target.closest(".win-btn")) return;
      win.snapped === "max" ? unsnap(win) : snapTo(win, "max");
    });
  }

  // ──────────────────────────────────────────────────────────── resize

  function wireResize(win, handle) {
    const dir = handle.dataset.rs;
    handle.addEventListener("pointerdown", (e) => {
      if (e.button !== 0) return;
      e.stopPropagation();
      focus(win, { keyboard: false });
      const start = { px: e.clientX, py: e.clientY, x: win.x, y: win.y, w: win.w, h: win.h };
      if (win.snapped) { win.snapped = null; win.preSnap = null; } // resizing unsnaps in place
      try { handle.setPointerCapture(e.pointerId); } catch { /* synthetic pointer */ }
      win.el.classList.add("win-gesture");
      document.body.classList.add("wm-gesture");

      let latest = e, raf = 0;
      const step = () => {
        raf = 0;
        const dx = latest.clientX - start.px, dy = latest.clientY - start.py;
        let { x, y, w, h } = start;
        if (dir.includes("e")) w = start.w + dx;
        if (dir.includes("s")) h = start.h + dy;
        if (dir.includes("w")) { w = start.w - dx; x = start.x + dx; }
        if (dir.includes("n")) { h = start.h - dy; y = start.y + dy; }
        if (w < MIN_W) { if (dir.includes("w")) x -= MIN_W - w; w = MIN_W; }
        if (h < MIN_H) { if (dir.includes("n")) y -= MIN_H - h; h = MIN_H; }
        if (y < 0) { h += y; y = 0; }
        Object.assign(win, { x, y, w, h });
        applyRect(win);
      };
      const onMove = (ev) => { latest = ev; if (!raf) raf = requestAnimationFrame(step); };
      const finish = () => {
        if (raf) cancelAnimationFrame(raf);
        handle.removeEventListener("pointermove", onMove);
        handle.removeEventListener("pointerup", finish);
        handle.removeEventListener("pointercancel", finish);
        try { handle.releasePointerCapture(e.pointerId); } catch { /* gone */ }
        win.el.classList.remove("win-gesture");
        document.body.classList.remove("wm-gesture");
        changed();
      };
      handle.addEventListener("pointermove", onMove);
      handle.addEventListener("pointerup", finish);
      handle.addEventListener("pointercancel", finish);
    });
  }

  // ──────────────────────────────────────────── minimize / restore (FLIP)

  function flip(win, toDock) {
    const target = (dockTarget && dockTarget(win)) || null;
    const first = win.el.getBoundingClientRect();
    const deskRect = desktop.getBoundingClientRect();
    const tcx = target ? target.left + target.width / 2 : deskRect.left + deskRect.width / 2;
    const tcy = target ? target.top + target.height / 2 : deskRect.bottom - 30;
    const dx = tcx - (first.left + first.width / 2);
    const dy = tcy - (first.top + first.height / 2);
    const s = Math.max(0.04, (target ? target.width : 44) / Math.max(1, win.w));
    const away = `translate3d(${win.x + dx}px, ${win.y + dy}px, 0) scale(${s.toFixed(4)})`;
    const home = `translate3d(${win.x}px, ${win.y}px, 0)`;

    win.el.classList.add("win-flip");
    const settle = (fn) => {
      let done = false;
      const run = () => { if (!done) { done = true; fn(); } };
      win.el.addEventListener("transitionend", run, { once: true });
      setTimeout(run, 300);
    };

    if (toDock) {
      win.el.style.transform = away;
      win.el.style.opacity = "0";
      settle(() => {
        win.el.classList.remove("win-flip");
        win.el.classList.add("minimized");
        win.el.style.transform = home;
        win.el.style.opacity = "";
      });
    } else {
      win.el.classList.remove("minimized");
      win.el.classList.remove("win-flip");
      win.el.style.transform = away;
      win.el.style.opacity = "0";
      void win.el.offsetWidth; // commit the inverted frame
      win.el.classList.add("win-flip");
      win.el.style.transform = home;
      win.el.style.opacity = "1";
      settle(() => {
        win.el.classList.remove("win-flip");
        win.el.style.opacity = "";
      });
    }
  }

  function minimize(win) {
    if (win.minimized) return;
    win.minimized = true;
    changed();          // dock grows its tile first, so FLIP has a target
    flip(win, true);
    compactZ();
    const next = topWin();
    if (next) focus(next, { keyboard: false });
  }

  function restore(win) {
    if (!win.minimized) { focus(win); return; }
    win.minimized = false;
    flip(win, false);
    focus(win);
  }

  // ──────────────────────────────────────────────────────── open / close

  function place(spec, opts) {
    const { W, H } = deskSize();
    let w = Math.min(spec.w, Math.max(MIN_W, W - 24));
    let h = Math.min(spec.h, Math.max(MIN_H, H - 24));
    if (opts.rect) {
      return {
        x: Math.max(-(opts.rect.w - 64), Math.min(W - 64, opts.rect.x)),
        y: Math.max(0, Math.min(H - 24, opts.rect.y)),
        w: Math.max(MIN_W, opts.rect.w),
        h: Math.max(MIN_H, opts.rect.h),
      };
    }
    if (opts.near) {
      const n = opts.near;
      let x = n.x + n.w + 14;
      if (x + w > W) x = n.x - w - 14;
      if (x < 0) x = Math.max(0, Math.min(W - w, n.x + 48));
      const y = Math.max(0, Math.min(H - h, n.y + 24));
      return { x, y, w, h };
    }
    const k = opened % 9;
    return {
      x: Math.max(0, Math.min(W - w, 56 + k * 36)),
      y: Math.max(0, Math.min(H - h, 32 + k * 30)),
      w, h,
    };
  }

  function open(appId, params = {}, opts = {}) {
    const spec = registry.get(appId);
    if (!spec) return null;
    const id = `w${nextId++}`;
    opened++;

    const titleEl = el("h2", { class: "win-title" }, spec.title);
    const titlebar = el("header", { class: "titlebar" },
      el("span", { class: "win-glyph", "aria-hidden": "true" }, spec.glyph),
      titleEl,
      el("span", { class: "win-spacer" }),
      el("button", {
        class: "win-btn win-min", type: "button", title: "minimize",
        "aria-label": `minimize ${spec.title}`,
        onclick: () => minimize(win),
      }, "–"),
      el("button", {
        class: "win-btn win-close", type: "button", title: "close",
        "aria-label": `close ${spec.title}`,
        onclick: () => close(win),
      }, "×"));
    const body = el("div", { class: "win-body" });
    const root = el("section", {
      class: "window", tabindex: "-1", role: "dialog",
      "aria-label": spec.title,
      // the window's owner-app atlas hue — drives the title strip, dock
      // tile, and any cite-dots the app renders inside it
      style: `--accent:${appHue(spec.id)}`,
      dataset: { app: spec.id, winId: id },
    }, titlebar, body,
      HANDLES.map((d) => el("span", { class: `rs rs-${d}`, dataset: { rs: d }, "aria-hidden": "true" })));

    const win = {
      id, app: spec, params, el: root, body, titleEl,
      ...place(spec, opts),
      minimized: false, snapped: null, preSnap: null,
      parentId: opts.parentId || null,
      lastFocus: null, subs: [], disposers: [], appApi: {},
    };
    applyRect(win);
    root.addEventListener("focusin", (e) => { win.lastFocus = e.target; });

    wireDrag(win, titlebar);
    for (const handle of root.querySelectorAll(".rs")) wireResize(win, handle);

    desktop.append(root);
    windows.set(id, win);
    stack.push(id);

    const ctx = {
      winId: id,
      root: body,
      setTitle(t) { titleEl.textContent = t; root.setAttribute("aria-label", t); win.title = t; if (!restoring) persist(); },
      on(event, fn) { const off = bus.on(event, fn); win.subs.push(off); return off; },
      emit(event, payload = {}) { bus.emit(event, { sourceWinId: id, ...payload }); },
      addDisposer(fn) { win.disposers.push(fn); },
      close() { close(win); },
      focus() { focus(win); },
      openNear(otherApp, otherParams, otherOpts = {}) {
        return open(otherApp, otherParams, { near: win, ...otherOpts });
      },
    };
    win.appApi = spec.mount(ctx, params) || {};

    if (opts.snapped) {
      win.snapped = opts.snapped;
      win.preSnap = opts.preSnap || null;
      const { W, H } = deskSize();
      Object.assign(win, zoneRect(opts.snapped, W, H));
      applyRect(win);
    }
    if (opts.minimized) {
      win.minimized = true;
      win.el.classList.add("minimized");
      compactZ();
      changed();
    } else {
      focus(win, { keyboard: !restoring });
    }
    return win;
  }

  function close(win) {
    if (!windows.has(win.id)) return;
    // children fall with their parent (evidence windows tied to an answer)
    for (const other of [...windows.values()]) {
      if (other.parentId === win.id) close(other);
    }
    for (const off of win.subs) { try { off(); } catch { /* gone */ } }
    for (const fn of win.disposers) { try { fn(); } catch { /* gone */ } }
    windows.delete(win.id);
    const i = stack.indexOf(win.id);
    if (i !== -1) stack.splice(i, 1);
    win.el.remove();
    compactZ();
    const next = topWin();
    if (next) focus(next, { keyboard: false });
    changed();
  }

  // ─────────────────────────────────────────────── workspace persistence

  function serialize() {
    return {
      v: 1,
      windows: stack
        .map((id) => windows.get(id))
        .filter((w) => w && !w.app.transient)
        .map((w) => ({
          app: w.app.id,
          params: w.appApi.params ? w.appApi.params() : w.params,
          x: w.x, y: w.y, w: w.w, h: w.h,
          minimized: w.minimized,
          snapped: w.snapped,
          preSnap: w.preSnap,
        })),
    };
  }

  const persist = debounce(() => {
    if (deskSize().W < 50) return; // never persist a layout measured at zero
    const layout = serialize();
    store.set(WORKSPACE_KEY, layout);
    api("/api/workspace", layout, "PUT").catch(() => { /* endpoint optional */ });
  }, 700);

  async function loadLayout() {
    try {
      const remote = await api("/api/workspace");
      const layout = remote && remote.windows ? remote : remote && remote.layout;
      if (layout && Array.isArray(layout.windows)) return layout;
    } catch { /* endpoint optional — fall through */ }
    const local = store.get(WORKSPACE_KEY, null);
    return local && Array.isArray(local.windows) ? local : null;
  }

  function restoreLayout(layout) {
    restoring = true;
    try {
      for (const w of layout.windows) {
        if (!registry.get(w.app)) continue;
        open(w.app, w.params || {}, {
          rect: { x: w.x, y: w.y, w: w.w, h: w.h },
          minimized: !!w.minimized,
          snapped: w.snapped || null,
          preSnap: w.preSnap || null,
        });
      }
    } finally {
      restoring = false;
    }
    changed();
  }

  return {
    open, close, focus, minimize, restore, list, topWin,
    serialize, restoreLayout, loadLayout,
    find: (pred) => list().find(pred),
    findAll: (pred) => list().filter(pred),
    get: (id) => windows.get(id),
  };
}
