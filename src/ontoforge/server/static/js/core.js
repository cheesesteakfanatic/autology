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

/** The honest confidence gauge: thin amber fill + the number, always. */
export function confGauge(confidence, label = "confidence") {
  const pct = Math.max(0, Math.min(1, confidence)) * 100;
  const fill = el("div", { class: "gauge-fill", style: "width:0%" });
  requestAnimationFrame(() => requestAnimationFrame(() => { fill.style.width = `${pct}%`; }));
  return el("div", { class: "conf-gauge" },
    el("span", { class: "gauge-label" }, label),
    el("div", { class: "gauge-track" }, fill),
    el("span", { class: "gauge-value" }, `${(confidence * 100).toFixed(1)}%`));
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

const cache = { ontology: null, ontologyPromise: null };

export function loadOntology() {
  if (!cache.ontologyPromise) {
    cache.ontologyPromise = api("/api/ontology")
      .then((o) => { cache.ontology = o; return o; })
      .catch((e) => { cache.ontologyPromise = null; throw e; });
  }
  return cache.ontologyPromise;
}

export function ontologyNow() { return cache.ontology; }

export function dropCaches() {
  cache.ontology = null;
  cache.ontologyPromise = null;
}
