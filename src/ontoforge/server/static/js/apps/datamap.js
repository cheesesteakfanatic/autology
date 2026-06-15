/* Data Map (de-jargoned Constellation/Atlas) — joins tier as confirmed join /
   likely join / separate (no link found). Every entry renders the PERSISTED
   atlas (GET /api/atlas) at once; the map never waits on a build. The live
   watch-it-build is ADDITIVE: on studio:build-started it polls the build job
   and, only while a real job streams, animates from REAL events before
   resolving to the final map via engine.renderAtlas. */

import {
  el, svgEl, clear, loadOntology, loadAtlas, dropCaches,
} from "../core.js";
import { createConstellation } from "../constellation.js";

const TIERS = ["confirmed", "likely", "hint", "silos"];
const POLL_MS = 600;
const REVEAL_PER_FRAME = 4;     // calm pacing: ≤4 events applied per frame

export function createDataMapApp() {
  return {
    id: "constellation",          // KEEP the internal id — bus/registry/spotlight route to it
    title: "Data Map",
    tagline: "types as dots, joins as lines",
    glyph: "✶",
    w: 820, h: 600, multi: false,

    mount(ctx, params) {
      ctx.root.classList.add("app-datamap");

      // ── live-build layer (its own light SVG, only used while building) ──
      const buildStrip = el("div", { class: "build-strip", hidden: "hidden" },
        el("span", { class: "build-stage" }, "Reading the data…"),
        el("div", { class: "build-bar" }, el("i", { class: "build-bar-fill" })),
        el("span", { class: "build-tally mono" }, "Types: 0 · Confirmed joins: 0 · Likely joins: 0"));
      const liveSvg = svgEl("svg", {
        class: "constellation live-map", viewBox: "0 0 960 600",
        preserveAspectRatio: "xMidYMid meet", role: "img",
        "aria-label": "the model building live", hidden: "hidden",
      });
      const narrative = el("div", { class: "build-narrative", hidden: "hidden" });

      // ── final interactive map ──
      const svg = svgEl("svg", {
        class: "constellation", viewBox: "0 0 960 600",
        preserveAspectRatio: "xMidYMid meet",
        role: "img", "aria-label": "the data map",
      });
      const card = el("div", { class: "node-card", hidden: "hidden" });
      const evCard = el("div", { class: "evidence-card", hidden: "hidden" });
      const legend = el("div", { class: "constellation-legend" });
      const wrap = el("div", { class: "constellation-wrap" }, buildStrip, liveSvg, svg, card, evCard, legend);
      const detail = el("div", { class: "class-detail" });  // load() fills it
      ctx.root.append(wrap, narrative, detail);

      let onto = null;
      let pendingFocus = params.uri || null;
      let pendingProp = params.prop || null;
      let lastUri = null;
      let pollTimer = null;
      ctx.addDisposer(() => clearTimeout(pollTimer));

      const engine = createConstellation({
        svg, wrap, card, evCard, svgEl, el, clear,
        onSelect: (c) => renderClassDetail(c),
      });

      /* legend / tier toggles (de-jargoned) */
      function starLegend(note) {
        clear(legend).append(
          el("span", {}, el("i", { class: "lg-node" }), " type · sized by structure"),
          el("span", {}, el("i", { class: "lg-link" }), " connection"),
          note ? el("span", { class: "atlas-absent" }, note) : null,
          el("span", { class: "lg-hint" }, "drag to pan · wheel to zoom · double-click to reset"));
      }

      function tierToggle(tier, count, { off = false, swatch = tier, label = tier } = {}) {
        const btn = el("button", {
          class: `tier-toggle${off ? "" : " on"}`, type: "button",
          "data-tier": tier, "aria-pressed": off ? "false" : "true",
          title: `show or hide ${label}`,
          onclick: () => {
            const on = btn.classList.toggle("on");
            btn.setAttribute("aria-pressed", on ? "true" : "false");
            svg.classList.toggle(`hide-${tier}`, !on);
          },
        },
          el("i", { class: `lg-${swatch}`, "aria-hidden": "true" }),
          ` ${label} `, el("b", {}, Number(count || 0).toLocaleString("en-US")));
        if (off) svg.classList.add(`hide-${tier}`);
        return btn;
      }

      function atlasLegend(stats) {
        clear(legend).append(
          tierToggle("confirmed", stats.confirmed, { label: "confirmed join" }),
          tierToggle("likely", stats.likely, { label: "likely join" }),
          tierToggle("hint", stats.hint, { off: true, swatch: "hintline", label: "possible" }),
          tierToggle("silos", stats.silos, { swatch: "silo", label: "standalone" }),
          el("span", { class: "lg-hint" },
            "hover a dashed line for where it came from · click to pin · click a group name to fly there"));
      }

      /* type-detail drawer (de-jargoned) */
      function renderClassDetail(c, highlightProp) {
        lastUri = c.uri;
        const byUri = new Map((onto ? onto.classes : []).map((k) => [k.uri, k]));
        const target = clear(detail);
        const jump = (uri) => { const k = byUri.get(uri); if (k) { engine.focusClass(uri); renderClassDetail(k); } };
        target.append(
          el("div", { class: "class-uri" }, c.uri),
          el("h2", {}, c.name,
            c.is_event ? el("span", { class: "badge" }, "event") : null,
            el("span", { class: "badge badge-amber" }, `confidence ${c.confidence.toFixed(2)}`),
            el("button", {
              class: "range-link", type: "button", style: "margin-left:0.75em",
              onclick: () => ctx.emit("entity:open", { uri: c.uri }),
            }, "Explore record →")),
          c.definition ? el("p", { class: "class-def" }, c.definition) : null,
          el("div", { class: "detail-meta" },
            el("b", {}, String(c.n_shapes)), " validation shape", c.n_shapes === 1 ? "" : "s",
            c.parents.length ? [" · a kind of ", c.parents.map((p, i) => [
              i ? ", " : null,
              byUri.has(p) ? el("button", { class: "range-link", type: "button", onclick: () => jump(p) }, byUri.get(p).name) : p,
            ])] : null));
        if (!c.properties.length) {
          target.append(el("div", { class: "empty-note" }, "no fields found on this type")); return;
        }
        const rows = c.properties.map((p) => {
          const tr = el("tr", {},
            el("td", {}, p.name, p.is_link ? el("span", { class: "badge", style: "margin-left:0.625em" }, "→ connection") : null),
            el("td", {}, p.datatype),
            el("td", {}, p.unit ? el("span", { class: "badge badge-amber" }, p.unit) : ""),
            el("td", {}, p.cardinality, p.functional ? " · one" : ""),
            el("td", {}, p.range_class && byUri.has(p.range_class)
              ? el("button", { class: "range-link", type: "button", onclick: () => jump(p.range_class) }, byUri.get(p.range_class).name)
              : (p.range_class || "")));
          if (highlightProp && p.name === highlightProp) tr.classList.add("prop-highlight");
          return tr;
        });
        target.append(el("table", { class: "data" },
          el("thead", {}, el("tr", {}, el("th", {}, "field"), el("th", {}, "datatype"),
            el("th", {}, "unit"), el("th", {}, "how many"), el("th", {}, "connects to"))),
          el("tbody", {}, rows)));
      }

      function focusClass(uri, prop) {
        if (!onto) { pendingFocus = uri; pendingProp = prop || null; return; }
        if (!uri) return;
        const c = onto.classes.find((k) => k.uri === uri || k.name === uri);
        engine.focusClass(c ? c.uri : uri);
        if (c) renderClassDetail(c, prop);
      }

      /* LIVE BUILD — additive watch-it-build animation */
      // deterministic seeded layout so arriving nodes don't reflow
      function seedPos(key) {
        let h = 2166136261 >>> 0;
        const s = String(key);
        for (let k = 0; k < s.length; k++) { h ^= s.charCodeAt(k); h = Math.imul(h, 16777619); }
        const ang = ((h >>> 0) % 360) * Math.PI / 180;
        const rad = 140 + ((h >>> 9) % 160);
        return { x: 480 + Math.cos(ang) * rad, y: 300 + Math.sin(ang) * rad };
      }

      const livePos = new Map();    // type label -> {x,y}
      let liveTypes = 0, liveConfirmed = 0, liveLikely = 0;
      let pendingEvents = [];
      let revealRaf = 0;

      function liveTally() {
        buildStrip.querySelector(".build-tally").textContent =
          `Types: ${liveTypes} · Confirmed joins: ${liveConfirmed} · Likely joins: ${liveLikely}`;
      }

      function popNode(label) {
        if (livePos.has(label)) return;
        const p = seedPos(label);
        livePos.set(label, p);
        liveTypes++;
        const g = svgEl("g", { class: "live-node", transform: `translate(${p.x.toFixed(1)} ${p.y.toFixed(1)})` });
        g.append(
          svgEl("circle", { class: "live-dot", r: 7 }),
          svgEl("text", { class: "live-label", y: -12, "text-anchor": "middle" }, label));
        liveSvg.append(g);
        requestAnimationFrame(() => g.classList.add("in"));
      }

      function drawArc(srcLabel, dstLabel, tier, msg) {
        popNode(srcLabel); popNode(dstLabel);
        const a = livePos.get(srcLabel), b = livePos.get(dstLabel);
        if (!a || !b) return;
        if (tier === "confirmed") liveConfirmed++; else if (tier === "likely") liveLikely++;
        const path = svgEl("path", {
          class: `live-arc tier-${tier}`,
          d: `M ${a.x.toFixed(1)} ${a.y.toFixed(1)} L ${b.x.toFixed(1)} ${b.y.toFixed(1)}`,
        });
        if (msg) path.append(svgEl("title", {}, msg));
        liveSvg.insertBefore(path, liveSvg.firstChild);
        const len = Math.hypot(b.x - a.x, b.y - a.y);
        path.setAttribute("stroke-dasharray", `${len.toFixed(1)}`);
        path.setAttribute("stroke-dashoffset", `${len.toFixed(1)}`);
        requestAnimationFrame(() => requestAnimationFrame(() => {
          path.classList.add("drawn");
          path.setAttribute("stroke-dashoffset", "0");
        }));
      }

      function logNarrative(text) {
        const line = el("div", { class: "narr-line" }, text);
        narrative.append(line);
        narrative.scrollTop = narrative.scrollHeight;
      }

      // batch event application on rAF — small staggered groups, never strobe
      function pumpEvents() {
        revealRaf = 0;
        const batch = pendingEvents.splice(0, REVEAL_PER_FRAME);
        for (const ev of batch) applyEvent(ev);
        if (batch.length) liveTally();
        if (pendingEvents.length) revealRaf = requestAnimationFrame(pumpEvents);
      }
      function queueEvents(events) {
        for (const ev of events) pendingEvents.push(ev);
        if (!revealRaf && pendingEvents.length) revealRaf = requestAnimationFrame(pumpEvents);
      }

      function applyEvent(ev) {
        if (ev.kind === "type_found") {
          popNode(ev.label || ev.msg || `type ${liveTypes + 1}`);
          if (ev.msg) logNarrative(ev.msg);
        } else if (ev.kind === "join_found") {
          // msg shape: "found a join: airports <-> routes on iata_code"
          const tier = ev.tier || "likely";
          let s = ev.src_label, d = ev.dst_label;
          if ((!s || !d) && ev.msg) {
            const m = /:\s*(.+?)\s*<->\s*(.+?)(?:\s+on\b|$)/.exec(ev.msg);
            if (m) { s = s || m[1].trim(); d = d || m[2].trim(); }
          }
          if (s && d) drawArc(s, d, tier, ev.msg);
          if (ev.msg) logNarrative(ev.msg);
        } else if (ev.kind === "silo") {
          popNode(ev.label || ev.msg || `standalone`);
          if (ev.msg) logNarrative(ev.msg);
        } else if (ev.kind === "stage") {
          buildStrip.querySelector(".build-stage").textContent = stageLabel(ev.msg || ev.stage);
          if (ev.msg) logNarrative(ev.msg);
        }
      }

      // pipeline stage → plain Activity-style words
      function stageLabel(raw) {
        const k = String(raw || "").toLowerCase();
        if (k.includes("ingest") || k.includes("read")) return "Reading the data…";
        if (k.includes("profile") || k.includes("shape")) return "Finding the shape…";
        if (k.includes("induce") || k.includes("model")) return "Building the model…";
        if (k.includes("resolve") || k.includes("match")) return "Matching records…";
        if (k.includes("materialize") || k.includes("fill")) return "Filling in values…";
        return raw || "Working…";
      }

      // ADDITIVE live layer: covers the map only while a job streams
      let liveActive = false;
      function showLive() {
        liveActive = true;
        buildStrip.hidden = false;
        liveSvg.hidden = false;
        narrative.hidden = false;
        svg.style.display = "none";
        legend.style.display = "none";
      }
      function hideLive() {
        liveActive = false;
        buildStrip.hidden = true;
        liveSvg.hidden = true;
        narrative.hidden = true;
        svg.style.display = "";
        legend.style.display = "";
      }

      let lastSeq = -1;
      let buildWatchdog = null;
      ctx.addDisposer(() => clearTimeout(buildWatchdog));

      // a stalled/gone job must not strand the map — fall back to the atlas
      function armWatchdog() {
        clearTimeout(buildWatchdog);
        buildWatchdog = setTimeout(() => { if (liveActive) finishBuild(); }, 8000);
      }

      async function pollBuild(jobId) {
        try {
          const res = await fetch(`/api/workspace/build/${encodeURIComponent(jobId)}`);
          // non-OK (404 / expired) → no live stream; render the persisted atlas
          const out = res.ok ? await res.json() : null;
          if (!out) { finishBuild(); return; }
          if (!liveActive) showLive();   // first proof of a real job → reveal
          armWatchdog();
          const fresh = (out.events || []).filter((e) => e.seq === undefined || e.seq > lastSeq);
          for (const e of fresh) lastSeq = Math.max(lastSeq, e.seq ?? lastSeq);
          if (fresh.length) queueEvents(fresh);
          const pct = Math.max(0, Math.min(1, out.progress || 0));
          buildStrip.querySelector(".build-bar-fill").style.width = `${(pct * 100).toFixed(0)}%`;
          if (out.stage) buildStrip.querySelector(".build-stage").textContent = stageLabel(out.stage);
          if (out.status === "done" || out.status === "error") {
            // drain the queue, then resolve to the final interactive map
            setTimeout(finishBuild, 400);
            return;
          }
          pollTimer = setTimeout(() => pollBuild(jobId), POLL_MS);
        } catch {
          finishBuild();
        }
      }

      async function finishBuild() {
        clearTimeout(pollTimer);
        clearTimeout(buildWatchdog);
        if (revealRaf) cancelAnimationFrame(revealRaf);
        pendingEvents = [];
        if (!liveActive) { dropCaches(); load(); return; }  // never showed → just render
        const stage = buildStrip.querySelector(".build-stage");
        if (stage) {
          stage.textContent = `Model built — ${liveTypes} type${liveTypes === 1 ? "" : "s"}, ${liveConfirmed} confirmed, ${liveLikely} likely`;
        }
        dropCaches();
        setTimeout(() => { hideLive(); load(); ctx.emit("workspace:built", {}); }, 700);
      }

      function startBuild(jobId) {
        // reset, but reveal the live layer only once a poll proves a real job
        // — an already-built world keeps its map, never a stuck "Reading the…"
        clearTimeout(pollTimer);
        clear(liveSvg);
        livePos.clear();
        liveTypes = 0; liveConfirmed = 0; liveLikely = 0; lastSeq = -1;
        clear(narrative);
        liveTally();
        buildStrip.querySelector(".build-stage").textContent = "Reading the data…";
        buildStrip.querySelector(".build-bar-fill").style.width = "0%";
        armWatchdog();
        pollBuild(jobId);
      }

      /* ─────── world load: render the PERSISTED atlas (GET /api/atlas) on
         every entry — never gated on a live build (showLive only ADDS atop) */
      const countTier = (atlas, tier) => (atlas.links || []).filter((l) => l.tier === tier).length;
      const TAP_HINT = "tap a type to explore its record · tap a line for where it came from";
      const drained = () => {   // shared tail: detail hint + any pending focus
        clear(detail).append(el("div", { class: "empty-note" }, TAP_HINT));
        if (pendingFocus) { focusClass(pendingFocus, pendingProp); pendingFocus = null; pendingProp = null; }
      };

      async function load() {
        if (liveActive) hideLive();   // an entry never leaves the map covered
        for (const t of TIERS) svg.classList.remove(`hide-${t}`);

        const atlas = await loadAtlas();   // 404 → null, never throws
        if (atlas && Array.isArray(atlas.components) && atlas.components.length) {
          try { onto = await loadOntology(); }
          catch { onto = onto || { classes: [], edges: [] }; }  // render from atlas stubs
          const stats = atlas.stats || {};
          engine.renderAtlas(atlas, onto);
          const silos = stats.silos ?? atlas.components.filter((c) => c.is_silo).length;
          atlasLegend({
            confirmed: stats.confirmed ?? countTier(atlas, "confirmed"),
            likely: stats.likely ?? countTier(atlas, "likely"),
            hint: stats.hint ?? countTier(atlas, "hint"),
            silos,
          });
          const islands = Math.max(0, (stats.components ?? atlas.components.length) - silos);
          ctx.setTitle(`Data Map — ${islands} group${islands === 1 ? "" : "s"} · ${silos} standalone`);
          drained();
          return;
        }

        // no atlas: induced-types sky if a model exists, else a calm invite —
        // never a permanent "Reading the data…".
        let ontoErr = null;
        try { onto = await loadOntology(); }
        catch (e) { ontoErr = e; }
        ctx.setTitle("Data Map");
        if (onto && Array.isArray(onto.classes) && onto.classes.length) {
          engine.render(onto);
          starLegend("model not built yet — induced types shown");
          drained();
        } else {
          clear(svg); clear(legend);
          clear(detail).append(el("div", { class: "empty-note" },
            el("p", {}, ontoErr
              ? "Couldn't read the model. Pick datasets in Catalog and build to draw the map."
              : "No data map yet — pick datasets in Catalog and build; the map draws itself as joins are found."),
            el("button", {
              class: "btn", type: "button", style: "margin-top:0.75em",
              onclick: () => ctx.emit("studio:show-catalog", {}),
            }, "Open Catalog →")));
        }
      }

      ctx.on("world:reload", () => { onto = null; dropCaches(); clear(detail).append(el("div", { class: "empty-note" }, "redrawing the map…")); load(); });
      ctx.on("studio:build-started", ({ job_id }) => { if (job_id) startBuild(job_id); });
      ctx.on("studio:atlas-delta", () => { dropCaches(); load(); });

      load();
      return { focusClass, params: () => ({ uri: lastUri || undefined }) };
    },
  };
}
