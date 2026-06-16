/* Shared kernel helpers — the only module every layer may import.
   SECURITY INVARIANT: every piece of API data enters the DOM through el()/
   document.createTextNode — nothing interpolated is ever assigned to innerHTML. */

export const $ = (sel, root = document) => root.querySelector(sel);

/** Build an element; string/number children become TEXT nodes (XSS-safe). */
export function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined) continue;
    if (k === "class") node.className = v;
    else if (k === "dataset") Object.assign(node.dataset, v);
    else if (k.startsWith("on") && typeof v === "function") node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v);
  }
  for (const c of children.flat(Infinity)) {
    if (c === null || c === undefined || c === false) continue;
    node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return node;
}

const SVG_NS = "http://www.w3.org/2000/svg";
export function svgEl(tag, attrs = {}, ...children) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined) continue;
    if (k.startsWith("on") && typeof v === "function") node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v);
  }
  for (const c of children.flat(Infinity)) {
    if (c === null || c === undefined) continue;
    node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return node;
}

export function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
  return node;
}

export function fmt(n) {
  return typeof n === "number" ? n.toLocaleString("en-US") : String(n);
}

export async function api(path, body, method) {
  const opts = body === undefined && !method
    ? {}
    : {
        method: method || "POST",
        headers: { "Content-Type": "application/json" },
        body: body === undefined ? undefined : JSON.stringify(body),
      };
  const res = await fetch(path, opts);
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const j = await res.json();
      if (j && j.detail) detail = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail);
    } catch { /* keep the status line */ }
    const err = new Error(detail);
    err.status = res.status;
    throw err;
  }
  return res.json();
}

export function errorNote(err) {
  return el("div", { class: "error-note" }, String((err && err.message) || err));
}

/* ─────────────────────────────────────────── the categorical atlas wheel
   The locked atomic-age 8-hue family. Every island, tag, chart series and
   cite-dot picks from here, deterministically, so a concept keeps its hue
   across the whole OS. Index 0 (teal) doubles as 'confirmed'. */
export const ATLAS_HUES = [
  "#2C5956", // teal      — anchor / confirmed   (muted from #1F6F6B)
  "#D09735", // marigold                          (muted from #E0A126)
  "#945442", // terracotta                        (muted from #C75B39)
  "#6C733A", // avocado                           (muted from #7C8A3B)
  "#375E72", // ocean                             (muted from #2D6E8E)
  "#945942", // persimmon rust                    (muted from #B8532A)
  "#713D68", // plum raisin                       (muted from #6E4A63)
  "#86663C", // mustard brown                     (muted from #9A6B2F)
];

/** Stable hue for any string key (uri / id / name) — same key, same hue. */
export function hueFor(key) {
  let h = 2166136261 >>> 0;
  const s = String(key);
  for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 16777619); }
  return ATLAS_HUES[(h >>> 0) % ATLAS_HUES.length];
}

/** Per-app accent (the title-strip / dock-tile hue) — the dock order is the
    wheel order, so the eight apps each own a distinct atlas hue. */
export const APP_HUE = {
  ask: "#D09735",          // marigold — the primary console
  constellation: "#2C5956",// teal — the map
  inspector: "#375E72",    // ocean
  evidence: "#86663C",     // mustard brown — the index-card tray
  review: "#713D68",       // plum raisin — adjudication
  dashboards: "#6C733A",   // avocado
  pulse: "#945442",        // terracotta — the instrument cluster
  exporter: "#945942",     // persimmon rust
};
export function appHue(appId) { return APP_HUE[appId] || "#6B5A45"; }

/* ───────────────────────────────────────────── the confidence gauge
   A 270° open arc (the kidney/TV-screen curve). Track in sunken bisque;
   the fill arc is banded by confidence: ≥0.8 teal (confirmed), 0.5–0.8
   marigold (likely), <0.5 walnut (weak). The value rides in mono at the
   center with a small-caps band label. One-time sweep on first paint
   (storytelling); reduced-motion holds it at the final state. */
function gaugeBand(c) {
  if (c >= 0.8) return { color: "var(--teal)", label: "confirmed" };
  if (c >= 0.5) return { color: "var(--marigold)", label: "likely" };
  return { color: "var(--walnut)", label: "weak" };
}

export function confGauge(confidence, label = "confidence") {
  const c = Math.max(0, Math.min(1, Number(confidence) || 0));
  const band = gaugeBand(c);
  // 270° arc: from 135° (lower-left) sweeping clockwise to 45° (lower-right)
  const R = 34, CX = 44, CY = 42, SPAN = 270, START = 135;
  const polar = (deg) => {
    const a = (deg * Math.PI) / 180;
    return [CX + R * Math.cos(a), CY + R * Math.sin(a)];
  };
  const arc = (fromDeg, toDeg) => {
    const [x0, y0] = polar(fromDeg);
    const [x1, y1] = polar(toDeg);
    const large = Math.abs(toDeg - fromDeg) > 180 ? 1 : 0;
    return `M ${x0.toFixed(2)} ${y0.toFixed(2)} A ${R} ${R} 0 ${large} 1 ${x1.toFixed(2)} ${y1.toFixed(2)}`;
  };
  const endDeg = START + SPAN * c;
  // the threshold tick (decision floor at 0.5) as a tiny starburst mark
  const [tx, ty] = polar(START + SPAN * 0.5);

  const track = svgEl("path", { class: "gauge-track-arc", d: arc(START, START + SPAN) });
  const fill = svgEl("path", {
    class: "gauge-fill-arc", d: arc(START, endDeg),
    style: `stroke:${band.color}`,
  });
  // sweep on first paint: animate via stroke-dash, then leave it static
  const len = (SPAN * c * Math.PI * R) / 180;
  fill.setAttribute("stroke-dasharray", `${len.toFixed(1)} 9999`);
  fill.setAttribute("stroke-dashoffset", `${len.toFixed(1)}`);
  requestAnimationFrame(() => requestAnimationFrame(() => {
    fill.classList.add("sweep");
    fill.setAttribute("stroke-dashoffset", "0");
  }));

  const svg = svgEl("svg", {
    class: "gauge-arc", viewBox: "0 0 88 80", width: 88, height: 80,
    role: "img", "aria-label": `${label}: ${(c * 100).toFixed(1)}%`,
  },
    track, fill,
    svgEl("circle", { class: "gauge-tick-dot", cx: tx.toFixed(1), cy: ty.toFixed(1), r: 2.3 }),
    svgEl("text", { class: "gauge-num", x: 44, y: 44, "text-anchor": "middle" }, c.toFixed(2)),
    svgEl("text", { class: "gauge-band", x: 44, y: 60, "text-anchor": "middle" }, band.label));

  return el("div", { class: `conf-gauge band-${band.label}` },
    svg,
    el("span", { class: "gauge-label" }, label));
}

/* ─────────────────────────────────────────────── toasts / notifications
   A single calm host (created lazily) collects brief notices. MCM motion:
   they rise in, hold, fade out — never a frantic stack. */
let toastHost = null;
function ensureToastHost() {
  if (toastHost && document.body.contains(toastHost)) return toastHost;
  toastHost = el("div", { class: "toast-host", id: "toast-host", role: "status", "aria-live": "polite" });
  document.body.append(toastHost);
  return toastHost;
}

export function toast(message, { kind = "info", timeout = 3600 } = {}) {
  const host = ensureToastHost();
  const node = el("div", { class: `toast toast-${kind}`, role: "status" },
    el("span", { class: "toast-dot", "aria-hidden": "true" }),
    el("span", { class: "toast-msg" }, String(message)));
  host.append(node);
  requestAnimationFrame(() => requestAnimationFrame(() => node.classList.add("in")));
  const dismiss = () => {
    node.classList.remove("in");
    node.classList.add("out");
    setTimeout(() => node.remove(), 220);
  };
  node.addEventListener("click", dismiss);
  if (timeout) setTimeout(dismiss, timeout);
  return dismiss;
}

export function skeletonCard(widths = [42, 68, 55]) {
  return el("div", { class: "answer-card", "aria-busy": "true" },
    widths.map((w) => el("div", { class: "skeleton", style: `width:${w}%` })));
}

export function debounce(fn, ms) {
  let t = null;
  const wrapped = (...args) => {
    clearTimeout(t);
    t = setTimeout(() => fn(...args), ms);
  };
  wrapped.cancel = () => clearTimeout(t);
  return wrapped;
}

/** localStorage that never throws (private mode, quota). */
export const store = {
  get(key, fallback) {
    try {
      const raw = localStorage.getItem(key);
      return raw === null ? fallback : JSON.parse(raw);
    } catch { return fallback; }
  },
  set(key, value) {
    try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* private mode */ }
  },
};

/* ───────────────────────────── shared world cache (status + ontology) */

const cache = { ontology: null, ontologyPromise: null, atlas: null, atlasPromise: null };

export function loadOntology() {
  if (!cache.ontologyPromise) {
    cache.ontologyPromise = api("/api/ontology")
      .then((o) => { cache.ontology = o; return o; })
      .catch((e) => { cache.ontologyPromise = null; throw e; });
  }
  return cache.ontologyPromise;
}

export function ontologyNow() { return cache.ontology; }

/** The Atlas — GET /api/atlas. The endpoint may not exist yet (a parallel
    crew owns it): resolve null instead of throwing, so the constellation
    can fall back to the plain ontology sky with a quiet note. */
export function loadAtlas() {
  if (!cache.atlasPromise) {
    cache.atlasPromise = api("/api/atlas")
      .then((a) => { cache.atlas = a; return a; })
      .catch(() => { cache.atlas = null; return null; });
  }
  return cache.atlasPromise;
}

export function dropCaches() {
  cache.ontology = null;
  cache.ontologyPromise = null;
  cache.atlas = null;
  cache.atlasPromise = null;
}

/* ───────────────────── playground workspace (the Studio data state)
   GET /api/workspace/state tells us whether a model has been built and
   how big it is, so every mode can guide the user honestly when the data
   isn't ready. The endpoint may 404 in older builds — resolve a safe
   "not built" shape rather than throwing into the DOM. */
export async function workspaceState() {
  try {
    return await api("/api/workspace/state");
  } catch {
    return { datasets: [], built: false, active_world: null, stats: {} };
  }
}
