/* The constellation engine — two skies over one instrument.

   STAR mode  (render)      : the induced ontology as a single star chart.
   A tiny DETERMINISTIC force simulation (seeded from class URIs, no
   Math.random) laid out once at render; classes are stars sized by
   structure, amber luminance by confidence; subsumption hairlines, bowed
   link arcs; pan/zoom via the SVG viewBox.

   ATLAS mode (renderAtlas) : the same instrument pointed at the wild.
   Every connected component becomes an ISLAND — laid out by its own seeded
   sim, packed on a loose deterministic spiral by size — while singleton
   classes collect in a dimmer, dignified ARCHIPELAGO band along the
   bottom. Joins render by tier of certainty: confirmed solid hairlines,
   likely DASHED AMBER with opacity ∝ score and evidence on hover (click to
   pin), hint nearly invisible dots, off by default.

   ATLAS SCALE GUARD — must stay smooth at 250 nodes / 600 arcs:
   · the simulation runs ONCE per island at render time (iterations shrink
     as islands grow — layoutIterations) and the settled sky is written as
     STATIC SVG; nothing relays out per frame after settle;
   · pan/zoom touch ONLY the viewBox attribute (plus a cheap, threshold-
     gated island-label counterscale);
   · hover/click ride ONE delegated listener set on the <svg> — never
     per-node handlers;
   · class labels hide below the zoom threshold (.labels-hidden); island
     labels counterscale so they read at every altitude. */

const BASE_W = 960, BASE_H = 600;

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

/** el()-style child discipline for raw DOM appends: arrays flatten, null/
    undefined/false vanish, strings become TEXT nodes (never markup). */
function fill(node, ...children) {
  for (const c of children.flat(Infinity)) {
    if (c === null || c === undefined || c === false) continue;
    node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return node;
}

/** iterations shrink as n grows (n=10 → 320, n=250 → ~104) so one huge
    island cannot stall the render — part of the scale guard above. */
function layoutIterations(n) {
  return Math.max(90, Math.min(320, Math.round(26000 / Math.max(1, n))));
}

/** ~80 lines of physics: repulsion + edge springs + centering gravity,
    over an arbitrary box (the whole sky, or one island's patch of it). */
function forceLayout(nodes, edges, opts = {}) {
  const W = opts.w || BASE_W, H = opts.h || BASE_H;
  const padX = opts.padX ?? 70, padY = opts.padY ?? 40;
  const iterations = opts.iterations || 320;
  const REPULSE = opts.repulse ?? 36000;
  const subLen = opts.subLen ?? 140, linkLen = opts.linkLen ?? 220;
  const idx = new Map(nodes.map((n, i) => [n.id, i]));
  const springs = edges
    .filter((e) => idx.has(e.source) && idx.has(e.target))
    .map((e) => ({
      a: idx.get(e.source),
      b: idx.get(e.target),
      len: e.kind === "sub" ? subLen : linkLen,
      k: e.kind === "sub" ? 0.04 : 0.012,
    }));
  // deterministic start: a ring ordered by input order, jittered by URI hash
  nodes.forEach((n, i) => {
    const rand = mulberry32(hash32(n.id));
    const angle = (i / nodes.length) * 2 * Math.PI;
    const ring = 0.28 * Math.min(W, H) + 0.14 * Math.min(W, H) * rand();
    n.x = W / 2 + ring * Math.cos(angle) + (rand() - 0.5) * 40;
    n.y = H / 2 + ring * Math.sin(angle) * 0.72 + (rand() - 0.5) * 40;
    n.vx = 0; n.vy = 0;
  });
  const GRAVITY = 0.003, DAMP = 0.6;
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
      n.x = Math.max(padX, Math.min(W - padX, n.x));
      n.y = Math.max(padY, Math.min(H - padY, n.y));
    }
  }
}

export function createConstellation({ svg, wrap, card, evCard, svgEl, el, clear, onSelect }) {
  let mode = "star";            // "star" | "atlas"
  let nodeInfo = new Map();     // uri -> class (real or dignified stub)
  let groups = new Map();       // uri -> <g>
  let positions = new Map();    // uri -> {x, y}
  let uriIsland = new Map();    // uri -> island id (String)
  let islandGeo = new Map();    // island id (String) -> padded bbox
  let islandLabels = [];        // <text> nodes that counterscale with zoom
  let atlasLinks = [];          // the served links, by index
  let visPaths = [];            // index-aligned visible arc paths
  let pinnedLi = -1;            // the pinned evidence arc, -1 = none
  let selectedUri = null;
  let world = { w: BASE_W, h: BASE_H };
  let view = { x: 0, y: 0, w: BASE_W, h: BASE_H };
  let viewRaf = 0;
  let viewSettle = 0;
  let lastLabelScale = 0;

  // the evidence card may be supplied by the app; otherwise it is grown here
  if (!evCard) {
    evCard = el("div", { class: "evidence-card" });
    evCard.hidden = true;
    wrap.append(evCard);
  }

  /* ─────────────────────────────────────────────────── view machinery */

  function applyView() {
    svg.setAttribute("viewBox", `${view.x} ${view.y} ${view.w} ${view.h}`);
    const cw = svg.clientWidth || 800;
    // class labels hide below the zoom threshold — atlas only; the small
    // star chart keeps its labels at every zoom, as it always has
    svg.classList.toggle("labels-hidden", mode === "atlas" && view.w > Math.max(900, cw * 1.2));
    // island labels counterscale (threshold-gated: a handful of style
    // writes, only when the scale moved >4% — not per-frame relayout).
    // Inline style, because a presentation attribute would lose to the
    // stylesheet's 13px baseline.
    if (islandLabels.length) {
      const s = view.w / cw;
      if (Math.abs(s - lastLabelScale) > 0.04 * (lastLabelScale || 1)) {
        lastLabelScale = s;
        const fs = Math.max(11, Math.min(46, 12.5 * s));
        for (const t of islandLabels) t.style.fontSize = `${fs.toFixed(1)}px`;
      }
    }
  }

  function fitWorld() {
    view = { x: 0, y: 0, w: world.w, h: world.h };
    applyView();
  }

  /** A one-off programmatic flight (spotlight focus, island zoom).
      A hidden or throttled surface may never grant a frame — the settle
      timeout lands the flight regardless (same fallback discipline as the
      WM's transition handling). */
  function tweenView(target) {
    if (viewRaf) cancelAnimationFrame(viewRaf);
    clearTimeout(viewSettle);
    const reduced = typeof matchMedia === "function"
      && matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reduced) { view = target; applyView(); return; }
    const from = { ...view };
    const t0 = performance.now(), D = 340;
    let done = false;
    const step = (t) => {
      if (done) return;
      const k = Math.min(1, (t - t0) / D);
      const e = 1 - Math.pow(1 - k, 3);
      view = {
        x: from.x + (target.x - from.x) * e,
        y: from.y + (target.y - from.y) * e,
        w: from.w + (target.w - from.w) * e,
        h: from.h + (target.h - from.h) * e,
      };
      applyView();
      if (k < 1) { viewRaf = requestAnimationFrame(step); }
      else { viewRaf = 0; done = true; clearTimeout(viewSettle); }
    };
    viewRaf = requestAnimationFrame(step);
    viewSettle = setTimeout(() => {
      if (done) return;
      done = true;
      if (viewRaf) cancelAnimationFrame(viewRaf);
      viewRaf = 0;
      view = target;
      applyView();
    }, D + 140);
  }

  function zoomToIsland(id) {
    const bb = islandGeo.get(String(id));
    if (!bb) return;
    const pad = 70;
    let w = Math.max(bb.w + pad * 2, 420);
    let h = Math.max(bb.h + pad * 2 + 26, w * 0.55);
    tweenView({ x: bb.x + bb.w / 2 - w / 2, y: bb.y + bb.h / 2 - h / 2 + 10, w, h });
  }

  /* ───────────────────────────────────────────── node + card helpers */

  function nodeRadius(c) {
    return 5 + 2.1 * Math.sqrt((c.properties ? c.properties.length : 0) + (c.n_shapes || 0) + 1);
  }

  function shortName(uri) {
    const c = nodeInfo.get(uri);
    if (c) return c.name;
    const tail = String(uri).split(/[/#]/).filter(Boolean).pop();
    return tail || String(uri);
  }

  function placeCard(node, evt) {
    const r = wrap.getBoundingClientRect();
    const cx = evt.clientX - r.left + 16;
    const cy = evt.clientY - r.top + 12;
    node.style.left = `${Math.max(8, Math.min(cx, r.width - node.offsetWidth - 12))}px`;
    node.style.top = `${Math.max(8, Math.min(cy, r.height - node.offsetHeight - 12))}px`;
  }

  function showCard(c, evt) {
    clear(card);
    fill(card,
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
    placeCard(card, evt);
  }

  function hideCard() { card.hidden = true; }

  /* ───────────────────────────── the evidence card (likely-join proof) */

  function showEvidence(li, evt, pin) {
    const l = atlasLinks[li];
    if (!l) return;
    const ev = l.evidence || {};
    clear(evCard);
    evCard.classList.toggle("pinned", !!pin);
    const meta = [];
    if (ev.coverage !== null && ev.coverage !== undefined) {
      meta.push(`coverage ${(ev.coverage * 100).toFixed(0)}%`);
    }
    if (ev.overlap_count !== null && ev.overlap_count !== undefined) {
      meta.push(`overlap ${Number(ev.overlap_count).toLocaleString("en-US")}`);
    }
    if (ev.name_similarity !== null && ev.name_similarity !== undefined) {
      meta.push(`names ${Number(ev.name_similarity).toFixed(2)}`);
    }
    if (ev.semtype_match) meta.push("semtype ✓");
    const samples = (ev.sample_shared_values || []).slice(0, 5);
    fill(evCard,
      pin ? el("button", {
        class: "ev-unpin", type: "button", "aria-label": "unpin evidence",
        onclick: () => unpinEvidence(),
      }, "×") : null,
      el("div", { class: "ev-head" },
        el("span", { class: `ev-tier ev-${l.tier}` }, l.tier),
        el("span", { class: "ev-score" }, `score ${Number(l.score ?? 0).toFixed(2)}`)),
      el("div", { class: "ev-cols" },
        `${shortName(l.src_class)}.${l.src_prop ?? "?"} ⇄ ${shortName(l.dst_class)}.${l.dst_prop ?? "?"}`),
      meta.length ? el("div", { class: "ev-meta" }, meta.join(" · ")) : null,
      samples.length
        ? el("div", { class: "ev-samples" },
            samples.map((v) => el("span", { class: "ev-sample" }, String(v))))
        : null,
      pin ? null : el("div", { class: "ev-pin-hint" }, "click the arc to pin"));
    evCard.hidden = false;
    if (evt) placeCard(evCard, evt);
  }

  function pinEvidence(li, evt) {
    if (pinnedLi >= 0 && pinnedLi !== li && visPaths[pinnedLi]) {
      visPaths[pinnedLi].classList.remove("lit");
    }
    pinnedLi = li;
    if (visPaths[li]) visPaths[li].classList.add("lit");
    showEvidence(li, evt, true);
  }

  function unpinEvidence() {
    if (pinnedLi >= 0 && visPaths[pinnedLi]) visPaths[pinnedLi].classList.remove("lit");
    pinnedLi = -1;
    evCard.hidden = true;
  }

  function hideEvidence() { if (pinnedLi < 0) evCard.hidden = true; }

  /* ─────────────────────────────────────────── selection + spotlight */

  function select(uri) {
    selectedUri = uri;
    for (const [u, g] of groups) g.classList.toggle("selected", u === uri);
  }

  /** Spotlight lands here: in atlas mode, flying to the class's island. */
  function focusClass(uri) {
    select(uri);
    if (mode !== "atlas") return;
    const isl = uriIsland.get(uri);
    if (isl !== undefined) { zoomToIsland(isl); return; }
    const p = positions.get(uri); // a silo in the archipelago
    if (p) tweenView({ x: p.x - 260, y: p.y - 170, w: 520, h: 340 });
  }

  /* ────────────────────────────────────────────── shared node painter */

  function appendNode(layer, uri, x, y, c, { halo = true } = {}) {
    const r = nodeRadius(c);
    const lum = Math.max(0.15, c.confidence);
    const g = svgEl("g", {
      class: uri === selectedUri ? "selected" : "",
      "data-uri": uri,
    });
    if (halo) {
      // luminance halo: amber glow scaled by confidence — the star's heat
      g.append(svgEl("circle", {
        class: "node-halo", cx: x.toFixed(1), cy: y.toFixed(1),
        r: (r * 2.1).toFixed(1), opacity: (0.10 * lum).toFixed(3),
      }));
    }
    g.append(svgEl("circle", {
      class: "node-core", cx: x.toFixed(1), cy: y.toFixed(1), r: r.toFixed(1),
      "stroke-opacity": lum.toFixed(3),
    }));
    if (c.is_event) {
      g.append(svgEl("circle", {
        class: "node-event", cx: x.toFixed(1), cy: y.toFixed(1), r: (r + 3.5).toFixed(1),
      }));
    }
    g.append(svgEl("text", {
      class: "node-label",
      x: (x + r + 6).toFixed(1), y: (y + 3.5).toFixed(1),
    }, c.name));
    layer.append(g);
    groups.set(uri, g);
    return g;
  }

  function arcPath(a, b, liftCap) {
    const mx = (a.x + b.x) / 2, my = (a.y + b.y) / 2;
    const dx = b.x - a.x, dy = b.y - a.y;
    const d = Math.sqrt(dx * dx + dy * dy) || 1;
    const lift = Math.min(liftCap, d * 0.22);
    const cx = mx - (dy / d) * lift, cy = my + (dx / d) * lift;
    return `M ${a.x.toFixed(1)} ${a.y.toFixed(1)} Q ${cx.toFixed(1)} ${cy.toFixed(1)} ${b.x.toFixed(1)} ${b.y.toFixed(1)}`;
  }

  function resetSky() {
    groups = new Map();
    positions = new Map();
    uriIsland = new Map();
    islandGeo = new Map();
    islandLabels = [];
    atlasLinks = [];
    visPaths = [];
    lastLabelScale = 0;
    unpinEvidence();
    hideCard();
    clear(svg);
  }

  /* ════════════════════════════════ STAR mode — the ontology sky ═════ */

  function render(onto) {
    mode = "star";
    resetSky();
    nodeInfo = new Map(onto.classes.map((c) => [c.uri, c]));
    const nodes = onto.classes.map((c) => ({ id: c.uri, c }));
    const edges = [];
    for (const c of onto.classes) {
      for (const p of c.parents) {
        if (nodeInfo.has(p)) edges.push({ source: c.uri, target: p, kind: "sub" });
      }
    }
    for (const e of onto.edges) {
      if (nodeInfo.has(e.source) && nodeInfo.has(e.target)) {
        edges.push({ source: e.source, target: e.target, kind: "link", label: e.link });
      }
    }
    forceLayout(nodes, edges, { w: BASE_W, h: BASE_H, repulse: 36000, iterations: 320 });
    const pos = new Map(nodes.map((n) => [n.id, n]));
    for (const n of nodes) positions.set(n.id, { x: n.x, y: n.y });

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
        const path = svgEl("path", { class: "edge-link", d: arcPath(a, b, 46) });
        path.append(svgEl("title", {},
          `${nodeInfo.get(e.source).name} —${e.label}→ ${nodeInfo.get(e.target).name}`));
        edgeLayer.append(path);
      }
    }
    for (const n of nodes) appendNode(nodeLayer, n.id, n.x, n.y, n.c);

    world = { w: BASE_W, h: BASE_H };
    fitWorld();
  }

  /* ════════════════════════ ATLAS mode — islands over the void ═══════ */

  function renderAtlas(atlas, onto) {
    mode = "atlas";
    resetSky();

    // class info from the ontology when known; dignified stubs otherwise
    const known = new Map(((onto && onto.classes) || []).map((c) => [c.uri, c]));
    const stub = (uri) => ({
      uri, name: String(uri).split(/[/#]/).filter(Boolean).pop() || String(uri),
      parents: [], properties: [], confidence: 0.5,
      is_event: false, n_shapes: 0, definition: "",
    });
    nodeInfo = new Map();
    const comps = atlas.components || [];
    for (const comp of comps) {
      for (const uri of comp.class_uris || []) {
        nodeInfo.set(uri, known.get(uri) || stub(uri));
      }
    }
    atlasLinks = atlas.links || [];

    const isSilo = (c) => !!c.is_silo || (c.class_uris || []).length <= 1;
    const islands = comps.filter((c) => !isSilo(c));
    const silos = comps.filter(isSilo);
    islands.sort((a, b) =>
      (b.class_uris.length - a.class_uris.length)
      || String(a.id).localeCompare(String(b.id)));

    // 1 · each island settles its own seeded sim in local coordinates
    for (const comp of islands) {
      const uris = comp.class_uris;
      const inIsland = new Set(uris);
      const local = uris.map((u) => ({ id: u }));
      const springs = [];
      for (const l of atlasLinks) {
        if (l.tier !== "hint" && inIsland.has(l.src_class) && inIsland.has(l.dst_class)) {
          springs.push({
            source: l.src_class, target: l.dst_class,
            kind: l.tier === "confirmed" ? "sub" : "link",
          });
        }
      }
      const n = local.length;
      const side = Math.min(560, 110 + 62 * Math.sqrt(n));
      forceLayout(local, springs, {
        w: side, h: side * 0.78, padX: 24, padY: 20,
        iterations: layoutIterations(n),
        repulse: Math.max(3600, (1.9 * side * side * 0.78) / Math.max(4, n)),
        subLen: 64, linkLen: 104,
      });
      let cx = 0, cy = 0;
      for (const p of local) { cx += p.x; cy += p.y; }
      cx /= n; cy /= n;
      let r = 0;
      for (const p of local) {
        p.x -= cx; p.y -= cy;
        r = Math.max(r, Math.hypot(p.x, p.y));
      }
      comp._r = r + 34;
      comp._local = local;
    }

    // 2 · pack islands on a loose deterministic spiral, largest first
    const GAP = 64;
    const placed = [];
    islands.forEach((isl, i) => {
      if (i === 0) { isl._cx = 0; isl._cy = 0; placed.push(isl); return; }
      let a = i * 2.39996;                       // golden-angle walk
      let rad = placed[0]._r + isl._r + GAP;
      for (;;) {
        const cx = Math.cos(a) * rad * 1.35;     // wide-axis bias
        const cy = Math.sin(a) * rad * 0.82;
        let ok = true;
        for (const p of placed) {
          if (Math.hypot(p._cx - cx, p._cy - cy) < p._r + isl._r + GAP) { ok = false; break; }
        }
        if (ok) { isl._cx = cx; isl._cy = cy; placed.push(isl); return; }
        a += 0.53; rad += 6;
      }
    });

    // 3 · shift the islands into positive space and size the world
    const M = 90;
    let minX = 0, minY = 0, maxX = BASE_W - 2 * M, maxY = 300;
    if (islands.length) {
      minX = Math.min(...islands.map((c) => c._cx - c._r));
      minY = Math.min(...islands.map((c) => c._cy - c._r));
      maxX = Math.max(...islands.map((c) => c._cx + c._r));
      maxY = Math.max(...islands.map((c) => c._cy + c._r));
    }
    const dx = M - minX, dy = 64 - minY;
    for (const comp of islands) {
      comp._cx += dx; comp._cy += dy;
      for (const p of comp._local) {
        positions.set(p.id, { x: p.x + comp._cx, y: p.y + comp._cy });
        uriIsland.set(p.id, String(comp.id));
      }
    }
    const worldW = Math.max(BASE_W, (maxX - minX) + 2 * M);
    const bandTop = (maxY - minY) + 64 + 72;

    // 4 · silos collect in the archipelago band along the bottom
    const siloUris = [];
    for (const comp of silos) for (const u of comp.class_uris || []) siloUris.push(u);
    const perRow = Math.max(1, Math.floor((worldW - 2 * M) / 52));
    siloUris.forEach((u, i) => {
      positions.set(u, {
        x: M + 26 + (i % perRow) * 52,
        y: bandTop + 48 + Math.floor(i / perRow) * 50,
      });
    });
    const rows = Math.ceil(siloUris.length / perRow);
    world = {
      w: worldW,
      h: Math.max(BASE_H, bandTop + (rows ? 48 + rows * 50 : 0) + 44),
    };

    // 5 · paint, bottom to top: hulls, arcs, archipelago, hit twins, stars
    const hullLayer = svgEl("g", { class: "island-layer" });
    const edgeLayer = svgEl("g", { class: "edge-layer" });
    const arch = svgEl("g", { class: "archipelago" });
    const hitLayer = svgEl("g", { class: "hit-layer" });
    const nodeLayer = svgEl("g", { class: "node-layer" });
    svg.append(hullLayer, edgeLayer, arch, hitLayer, nodeLayer);

    for (const comp of islands) {
      let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
      for (const p of comp._local) {
        const q = positions.get(p.id);
        x0 = Math.min(x0, q.x); y0 = Math.min(y0, q.y);
        x1 = Math.max(x1, q.x); y1 = Math.max(y1, q.y);
      }
      const pad = 30;
      const bb = { x: x0 - pad, y: y0 - pad, w: (x1 - x0) + 2 * pad, h: (y1 - y0) + 2 * pad };
      islandGeo.set(String(comp.id), bb);
      // the hull — a barely-visible rounded boundary, never a border
      hullLayer.append(svgEl("rect", {
        class: "island-hull", x: bb.x.toFixed(1), y: bb.y.toFixed(1),
        width: bb.w.toFixed(1), height: bb.h.toFixed(1), rx: 26,
      }));
      const label = svgEl("text", {
        class: "island-label", "data-island": String(comp.id),
        x: (bb.x + bb.w / 2).toFixed(1), y: (bb.y + bb.h + 18).toFixed(1),
        "text-anchor": "middle",
      }, comp.label || `island ${comp.id}`,
        svgEl("tspan", { class: "il-count", dx: "0.7em" },
          `${comp.dataset_count ?? comp.class_uris.length} sets`));
      hullLayer.append(label);
      islandLabels.push(label);
    }

    // arcs by tier; each gets an invisible wide hit twin for honest hover
    atlasLinks.forEach((l, li) => {
      const a = positions.get(l.src_class), b = positions.get(l.dst_class);
      if (!a || !b) { visPaths.push(null); return; }
      const tier = l.tier === "confirmed" || l.tier === "likely" ? l.tier : "hint";
      const touchesSilo = !uriIsland.has(l.src_class) || !uriIsland.has(l.dst_class);
      const srcIsl = uriIsland.get(l.src_class), dstIsl = uriIsland.get(l.dst_class);
      const bridge = touchesSilo || srcIsl !== dstIsl; // spans two islands
      const cls = `atlas-edge tier-${tier}${touchesSilo ? " touches-silo" : ""}${bridge ? " bridge" : ""}`;
      const d = arcPath(a, b, 110);
      const path = svgEl("path", { class: cls, d, "data-li": String(li) });
      if (tier === "likely") {
        // a hypothesis carries its own weight: opacity ∝ score — but it
        // whispers until hovered (the breathe animation lifts it to 0.9),
        // and a cross-island bridge whispers quieter than a local guess
        const score = Math.max(0, Math.min(1, Number(l.score) || 0));
        path.setAttribute("stroke-opacity",
          ((bridge ? 0.65 : 1) * (0.1 + 0.38 * score)).toFixed(3));
      }
      visPaths.push(path);
      edgeLayer.append(path);
      hitLayer.append(svgEl("path", {
        class: `edge-hit tier-${tier}${touchesSilo ? " touches-silo" : ""}`,
        d, "data-li": String(li),
      }));
    });

    // the archipelago: quieter, dignified — never error-red
    if (siloUris.length) {
      arch.append(svgEl("line", {
        class: "archipelago-line",
        x1: M, y1: bandTop.toFixed(1), x2: (world.w - M).toFixed(1), y2: bandTop.toFixed(1),
      }));
      const archLabel = svgEl("text", {
        class: "island-label archipelago-label", x: M, y: (bandTop + 22).toFixed(1),
      }, `archipelago — ${siloUris.length} silo${siloUris.length === 1 ? "" : "s"} · honest and unjoined`);
      arch.append(archLabel);
      islandLabels.push(archLabel);
      for (const u of siloUris) {
        const p = positions.get(u);
        appendNode(arch, u, p.x, p.y, nodeInfo.get(u), { halo: false });
      }
    }

    for (const comp of islands) {
      for (const p of comp._local) {
        const q = positions.get(p.id);
        appendNode(nodeLayer, p.id, q.x, q.y, nodeInfo.get(p.id));
      }
    }

    fitWorld();
  }

  /* ──────────────── ONE delegated listener set — hover, click, cards */

  const asEl = (t) => (t instanceof Element ? t : null);

  svg.addEventListener("pointerover", (e) => {
    const t = asEl(e.target);
    if (!t) return;
    const hit = t.closest("[data-li]");
    if (hit) {
      const li = Number(hit.getAttribute("data-li"));
      if (visPaths[li]) visPaths[li].classList.add("lit");
      if (pinnedLi < 0) showEvidence(li, e, false);
      return;
    }
    const g = t.closest("g[data-uri]");
    if (g) {
      const c = nodeInfo.get(g.getAttribute("data-uri"));
      if (c) showCard(c, e);
    }
  });

  svg.addEventListener("pointermove", (e) => {
    const t = asEl(e.target);
    if (!t) return;
    if (!card.hidden && t.closest("g[data-uri]")) {
      const c = nodeInfo.get(t.closest("g[data-uri]").getAttribute("data-uri"));
      if (c) showCard(c, e);
    } else if (pinnedLi < 0 && !evCard.hidden && t.closest("[data-li]")) {
      placeCard(evCard, e);
    }
  });

  svg.addEventListener("pointerout", (e) => {
    const t = asEl(e.target);
    if (!t) return;
    const hit = t.closest("[data-li]");
    if (hit) {
      const li = Number(hit.getAttribute("data-li"));
      if (li !== pinnedLi && visPaths[li]) visPaths[li].classList.remove("lit");
      hideEvidence();
    }
    if (t.closest("g[data-uri]")) hideCard();
  });

  svg.addEventListener("click", (e) => {
    const t = asEl(e.target);
    if (!t) return;
    const lbl = t.closest(".island-label");
    if (lbl && lbl.getAttribute("data-island") !== null) {
      zoomToIsland(lbl.getAttribute("data-island"));
      return;
    }
    const g = t.closest("g[data-uri]");
    if (g) {
      const uri = g.getAttribute("data-uri");
      const c = nodeInfo.get(uri);
      select(uri);
      if (c && onSelect) onSelect(c);
      return;
    }
    const hit = t.closest("[data-li]");
    if (hit) { pinEvidence(Number(hit.getAttribute("data-li")), e); return; }
    unpinEvidence(); // a click on the void releases the pinned card
  });

  /* ───────────────────────────────────── pan (drag) + zoom (wheel) —
     viewBox-only transforms; the settled SVG is never relaid out */

  let panning = null;
  svg.addEventListener("pointerdown", (e) => {
    const t = asEl(e.target);
    // capture would re-target the click — interactive targets opt out of pan
    if (t && t.closest("g[data-uri], [data-li], .island-label")) return;
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
  svg.addEventListener("pointercancel", () => { panning = null; });
  svg.addEventListener("wheel", (e) => {
    e.preventDefault();
    const factor = e.deltaY > 0 ? 1.1 : 1 / 1.1;
    const rect = svg.getBoundingClientRect();
    const px = view.x + ((e.clientX - rect.left) / rect.width) * view.w;
    const py = view.y + ((e.clientY - rect.top) / rect.height) * view.h;
    const w = Math.max(140, Math.min(world.w * 1.3, view.w * factor));
    const h = w * (world.h / world.w);
    view = { x: px - ((px - view.x) / view.w) * w, y: py - ((py - view.y) / view.h) * h, w, h };
    applyView();
  }, { passive: false });
  svg.addEventListener("dblclick", () => fitWorld());

  return { render, renderAtlas, select, focusClass, zoomToIsland };
}
