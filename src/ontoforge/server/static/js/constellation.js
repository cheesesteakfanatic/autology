/* The constellation — the induced ontology drawn like a star chart.
   A tiny DETERMINISTIC force simulation (seeded from class URIs, no
   Math.random) laid out once at render; pan/zoom via SVG viewBox.
   Classes are stars sized by structure, amber luminance by confidence;
   subsumption edges are hairlines, link properties faint amber arcs. */

const W = 960, H = 600;

/* seeded PRNG (mulberry32) over a string hash — same input, same sky */
function hash32(s) {
  let h = 2166136261 >>> 0;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return h >>> 0;
}
function mulberry32(seed) {
  let a = seed >>> 0;
  return () => {
    a |= 0; a = (a + 0x6D2B79F5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** ~80 lines of physics: repulsion + edge springs + centering gravity. */
function forceLayout(nodes, edges, iterations = 320) {
  const idx = new Map(nodes.map((n, i) => [n.id, i]));
  const springs = edges
    .filter((e) => idx.has(e.source) && idx.has(e.target))
    .map((e) => ({
      a: idx.get(e.source),
      b: idx.get(e.target),
      len: e.kind === "sub" ? 140 : 220,
      k: e.kind === "sub" ? 0.04 : 0.012,
    }));
  // deterministic start: a ring ordered by name, jittered by URI hash
  nodes.forEach((n, i) => {
    const rand = mulberry32(hash32(n.id));
    const angle = (i / nodes.length) * 2 * Math.PI;
    const ring = 180 + 90 * rand();
    n.x = W / 2 + ring * Math.cos(angle) + (rand() - 0.5) * 40;
    n.y = H / 2 + ring * Math.sin(angle) * 0.72 + (rand() - 0.5) * 40;
    n.vx = 0; n.vy = 0;
  });
  const REPULSE = 36000, GRAVITY = 0.003, DAMP = 0.6;
  for (let it = 0; it < iterations; it++) {
    const step = 0.09 * (1 - (0.7 * it) / iterations); // cool the step, not the forces
    for (let i = 0; i < nodes.length; i++) {
      for (let j = i + 1; j < nodes.length; j++) {
        const a = nodes[i], b = nodes[j];
        let dx = a.x - b.x, dy = a.y - b.y;
        let d2 = dx * dx + dy * dy;
        if (d2 < 1) { dx = 0.5; dy = 0.5; d2 = 0.5; } // deterministic unstick
        const f = REPULSE / d2;
        const d = Math.sqrt(d2);
        const fx = (dx / d) * f, fy = (dy / d) * f;
        a.vx += fx; a.vy += fy;
        b.vx -= fx; b.vy -= fy;
      }
    }
    for (const s of springs) {
      const a = nodes[s.a], b = nodes[s.b];
      const dx = b.x - a.x, dy = b.y - a.y;
      const d = Math.sqrt(dx * dx + dy * dy) || 1;
      const f = s.k * (d - s.len);
      const fx = (dx / d) * f, fy = (dy / d) * f;
      a.vx += fx; a.vy += fy;
      b.vx -= fx; b.vy -= fy;
    }
    for (const n of nodes) {
      n.vx += (W / 2 - n.x) * GRAVITY;
      n.vy += (H / 2 - n.y) * GRAVITY * 1.6; // squash toward the wide axis
      n.x += n.vx * step; n.y += n.vy * step;
      n.vx *= DAMP; n.vy *= DAMP;
      n.x = Math.max(70, Math.min(W - 70, n.x));
      n.y = Math.max(40, Math.min(H - 40, n.y));
    }
  }
}

export function createConstellation({ svg, wrap, card, svgEl, el, clear, onSelect }) {
  let nodes = [], byUri = new Map(), groups = new Map();
  let view = { x: 0, y: 0, w: W, h: H };
  let selectedUri = null;

  function applyView() {
    svg.setAttribute("viewBox", `${view.x} ${view.y} ${view.w} ${view.h}`);
  }

  function nodeRadius(c) {
    return 5 + 2.1 * Math.sqrt(c.properties.length + c.n_shapes + 1);
  }

  function showCard(c, evt) {
    clear(card);
    card.append(
      el("h3", {}, c.name),
      el("div", { class: "nc-meta" },
        `confidence ${c.confidence.toFixed(2)} · ${c.properties.length} propert${c.properties.length === 1 ? "y" : "ies"} · ${c.n_shapes} shape${c.n_shapes === 1 ? "" : "s"}${c.is_event ? " · event" : ""}`),
      c.properties.slice(0, 6).map((p) =>
        el("div", { class: "nc-prop" },
          p.name, " ",
          el("span", { class: "dt" }, p.is_link ? "→ link" : p.datatype),
          p.unit ? [" ", el("span", { class: "dt" }, `[${p.unit}]`)] : null)),
      c.properties.length > 6
        ? el("div", { class: "nc-prop" }, `… ${c.properties.length - 6} more`)
        : null);
    card.hidden = false;
    const r = wrap.getBoundingClientRect();
    let cx = evt.clientX - r.left + 16;
    let cy = evt.clientY - r.top + 12;
    card.style.left = `${Math.min(cx, r.width - card.offsetWidth - 12)}px`;
    card.style.top = `${Math.min(cy, r.height - card.offsetHeight - 12)}px`;
  }

  function hideCard() { card.hidden = true; }

  function select(uri) {
    selectedUri = uri;
    for (const [u, g] of groups) g.classList.toggle("selected", u === uri);
  }

  function render(onto) {
    nodes = onto.classes.map((c) => ({ id: c.uri, c }));
    byUri = new Map(onto.classes.map((c) => [c.uri, c]));
    const edges = [];
    for (const c of onto.classes) {
      for (const p of c.parents) {
        if (byUri.has(p)) edges.push({ source: c.uri, target: p, kind: "sub" });
      }
    }
    for (const e of onto.edges) {
      if (byUri.has(e.source) && byUri.has(e.target)) {
        edges.push({ source: e.source, target: e.target, kind: "link", label: e.link });
      }
    }
    forceLayout(nodes, edges);
    const pos = new Map(nodes.map((n) => [n.id, n]));

    clear(svg);
    const edgeLayer = svgEl("g");
    const nodeLayer = svgEl("g");
    svg.append(edgeLayer, nodeLayer);

    for (const e of edges) {
      const a = pos.get(e.source), b = pos.get(e.target);
      if (e.kind === "sub") {
        edgeLayer.append(svgEl("line", {
          class: "edge-sub", x1: a.x.toFixed(1), y1: a.y.toFixed(1),
          x2: b.x.toFixed(1), y2: b.y.toFixed(1),
        }));
      } else {
        // link properties bow outward: a quadratic arc with a perpendicular lift
        const mx = (a.x + b.x) / 2, my = (a.y + b.y) / 2;
        const dx = b.x - a.x, dy = b.y - a.y;
        const d = Math.sqrt(dx * dx + dy * dy) || 1;
        const lift = Math.min(46, d * 0.22);
        const cx = mx - (dy / d) * lift, cy = my + (dx / d) * lift;
        const path = svgEl("path", {
          class: "edge-link",
          d: `M ${a.x.toFixed(1)} ${a.y.toFixed(1)} Q ${cx.toFixed(1)} ${cy.toFixed(1)} ${b.x.toFixed(1)} ${b.y.toFixed(1)}`,
        });
        path.append(svgEl("title", {},
          `${byUri.get(e.source).name} —${e.label}→ ${byUri.get(e.target).name}`));
        edgeLayer.append(path);
      }
    }

    groups = new Map();
    for (const n of nodes) {
      const c = n.c;
      const r = nodeRadius(c);
      const lum = Math.max(0.15, c.confidence);
      const g = svgEl("g", { class: n.id === selectedUri ? "selected" : "" });
      // luminance halo: amber glow scaled by confidence — the star's heat
      g.append(svgEl("circle", {
        class: "node-halo", cx: n.x.toFixed(1), cy: n.y.toFixed(1),
        r: (r * 2.1).toFixed(1), opacity: (0.10 * lum).toFixed(3),
      }));
      const core = svgEl("circle", {
        class: "node-core", cx: n.x.toFixed(1), cy: n.y.toFixed(1), r: r.toFixed(1),
        "stroke-opacity": lum.toFixed(3),
        onclick: () => { select(n.id); onSelect(c); },
        onpointerenter: (evt) => showCard(c, evt),
        onpointermove: (evt) => showCard(c, evt),
        onpointerleave: hideCard,
      });
      g.append(core);
      if (c.is_event) {
        g.append(svgEl("circle", {
          class: "node-event", cx: n.x.toFixed(1), cy: n.y.toFixed(1), r: (r + 3.5).toFixed(1),
        }));
      }
      g.append(svgEl("text", {
        class: "node-label",
        x: (n.x + r + 6).toFixed(1), y: (n.y + 3.5).toFixed(1),
      }, c.name));
      nodeLayer.append(g);
      groups.set(n.id, g);
    }
    view = { x: 0, y: 0, w: W, h: H };
    applyView();
  }

  // ───────────────────────────────────── pan (drag) + zoom (wheel)

  let panning = null;
  svg.addEventListener("pointerdown", (e) => {
    if (e.target.classList && e.target.classList.contains("node-core")) return;
    panning = { sx: e.clientX, sy: e.clientY, vx: view.x, vy: view.y };
    svg.setPointerCapture(e.pointerId);
  });
  svg.addEventListener("pointermove", (e) => {
    if (!panning) return;
    const scale = view.w / svg.clientWidth;
    view.x = panning.vx - (e.clientX - panning.sx) * scale;
    view.y = panning.vy - (e.clientY - panning.sy) * scale;
    applyView();
  });
  svg.addEventListener("pointerup", () => { panning = null; });
  svg.addEventListener("wheel", (e) => {
    e.preventDefault();
    const factor = e.deltaY > 0 ? 1.1 : 1 / 1.1;
    const rect = svg.getBoundingClientRect();
    const px = view.x + ((e.clientX - rect.left) / rect.width) * view.w;
    const py = view.y + ((e.clientY - rect.top) / rect.height) * view.h;
    const w = Math.max(120, Math.min(W * 3, view.w * factor));
    const h = w * (H / W);
    view = { x: px - ((px - view.x) / view.w) * w, y: py - ((py - view.y) / view.h) * h, w, h };
    applyView();
  }, { passive: false });
  svg.addEventListener("dblclick", () => { view = { x: 0, y: 0, w: W, h: H }; applyView(); });

  return { render, select };
}
