/* Constellation — the induced ontology star chart in a resizable window,
   and, when GET /api/atlas is built, THE ATLAS: islands of connected
   classes, joins tiered by certainty (confirmed / likely / hint), silos in
   a dignified archipelago. The engine lives in js/constellation.js; this
   app gives it a window, the tier filter toggles, the evidence card, a
   class-detail drawer, and the focus-on-class API the WM routes
   'class:focus' intents to. */

import { el, svgEl, clear, errorNote, loadOntology, loadAtlas, dropCaches } from "../core.js";
import { createConstellation } from "../constellation.js";

const TIERS = ["confirmed", "likely", "hint", "silos"];

export function createConstellationApp() {
  return {
    id: "constellation",
    title: "Constellation",
    tagline: "the induced world model",
    glyph: "✶",
    w: 760, h: 560, multi: false,

    mount(ctx, params) {
      const svg = svgEl("svg", {
        class: "constellation", viewBox: "0 0 960 600",
        preserveAspectRatio: "xMidYMid meet",
        role: "img", "aria-label": "ontology constellation",
      });
      const card = el("div", { class: "node-card", hidden: "hidden" });
      const evCard = el("div", { class: "evidence-card", hidden: "hidden" });
      const legend = el("div", { class: "constellation-legend" });
      const wrap = el("div", { class: "constellation-wrap" }, svg, card, evCard, legend);
      const detail = el("div", { class: "class-detail" },
        el("div", { class: "empty-note" }, "select a star to inspect the class beneath it"));
      ctx.root.append(wrap, detail);
      ctx.root.classList.add("app-constellation");

      let onto = null;
      let pendingFocus = params.uri || null;
      let pendingProp = params.prop || null;
      let lastUri = null;

      const engine = createConstellation({
        svg, wrap, card, evCard, svgEl, el, clear,
        onSelect: (c) => renderClassDetail(c),
      });

      /* ────────────────────────────────────── legends: star vs atlas */

      function starLegend(note) {
        clear(legend).append(
          el("span", {}, el("i", { class: "lg-node" }), " class · sized by structure"),
          el("span", {}, el("i", { class: "lg-lum" }), " amber luminance = confidence"),
          el("span", {}, el("i", { class: "lg-sub" }), " subsumption"),
          el("span", {}, el("i", { class: "lg-link" }), " link property"),
          note ? el("span", { class: "atlas-absent" }, note) : null,
          el("span", { class: "lg-hint" }, "drag to pan · wheel to zoom · double-click to reset"));
      }

      /** A legend chip that is also a filter: toggles its tier on the sky. */
      function tierToggle(tier, count, { off = false, swatch = tier } = {}) {
        const btn = el("button", {
          class: `tier-toggle${off ? "" : " on"}`, type: "button",
          "data-tier": tier, "aria-pressed": off ? "false" : "true",
          title: `show or hide ${tier}`,
          onclick: () => {
            const on = btn.classList.toggle("on");
            btn.setAttribute("aria-pressed", on ? "true" : "false");
            svg.classList.toggle(`hide-${tier}`, !on);
          },
        },
          el("i", { class: `lg-${swatch}`, "aria-hidden": "true" }),
          ` ${tier} `,
          el("b", {}, Number(count || 0).toLocaleString("en-US")));
        if (off) svg.classList.add(`hide-${tier}`);
        return btn;
      }

      function atlasLegend(stats) {
        clear(legend).append(
          tierToggle("confirmed", stats.confirmed),
          tierToggle("likely", stats.likely),
          tierToggle("hint", stats.hint, { off: true, swatch: "hintline" }),
          tierToggle("silos", stats.silos, { swatch: "silo" }),
          el("span", { class: "lg-hint" },
            "hover a dashed arc for evidence · click to pin · click an island name to fly there"));
      }

      /* ───────────────────────────────────────── class detail drawer */

      function renderClassDetail(c, highlightProp) {
        lastUri = c.uri;
        const byUri = new Map(onto.classes.map((k) => [k.uri, k]));
        const target = clear(detail);

        const jump = (uri) => {
          const k = byUri.get(uri);
          if (k) { engine.focusClass(uri); renderClassDetail(k); }
        };

        target.append(
          el("div", { class: "class-uri" }, c.uri),
          el("h2", {}, c.name,
            c.is_event ? el("span", { class: "badge" }, "event") : null,
            el("span", { class: "badge badge-amber" }, `confidence ${c.confidence.toFixed(2)}`)),
          c.definition ? el("p", { class: "class-def" }, c.definition) : null,
          el("div", { class: "detail-meta" },
            el("b", {}, String(c.n_shapes)), " validation shape", c.n_shapes === 1 ? "" : "s",
            c.parents.length ? [" · subclass of ", c.parents.map((p, i) => [
              i ? ", " : null,
              byUri.has(p)
                ? el("button", { class: "range-link", type: "button", onclick: () => jump(p) }, byUri.get(p).name)
                : p,
            ])] : null));

        if (!c.properties.length) {
          target.append(el("div", { class: "empty-note" }, "no properties induced on this class"));
          return;
        }
        const rows = c.properties.map((p) => {
          const tr = el("tr", {},
            el("td", {}, p.name, p.is_link ? el("span", { class: "badge", style: "margin-left:0.625em" }, "→ link") : null),
            el("td", {}, p.datatype),
            el("td", {}, p.unit ? el("span", { class: "badge badge-amber" }, p.unit) : ""),
            el("td", {}, p.cardinality, p.functional ? " · fn" : ""),
            el("td", {}, p.range_class && byUri.has(p.range_class)
              ? el("button", { class: "range-link", type: "button", onclick: () => jump(p.range_class) }, byUri.get(p.range_class).name)
              : (p.range_class || "")));
          if (highlightProp && p.name === highlightProp) tr.classList.add("prop-highlight");
          return tr;
        });
        target.append(el("table", { class: "data" },
          el("thead", {}, el("tr", {},
            el("th", {}, "property"), el("th", {}, "datatype"), el("th", {}, "unit"),
            el("th", {}, "cardinality"), el("th", {}, "range"))),
          el("tbody", {}, rows)));
      }

      /* ─────────────────────────── spotlight + bus land here (focus) */

      function focusClass(uri, prop) {
        if (!onto) { pendingFocus = uri; pendingProp = prop || null; return; }
        if (!uri) return;
        const c = onto.classes.find((k) => k.uri === uri || k.name === uri);
        engine.focusClass(c ? c.uri : uri); // atlas mode flies to the island
        if (c) renderClassDetail(c, prop);
      }

      /* ───────────────────────────────────────────────── world load */

      const countTier = (atlas, tier) =>
        (atlas.links || []).filter((l) => l.tier === tier).length;

      async function load() {
        try {
          onto = await loadOntology();
        } catch (e) {
          clear(detail).append(errorNote(e));
          return;
        }
        for (const t of TIERS) svg.classList.remove(`hide-${t}`);
        // the atlas endpoint may 404 while its crew lands — null falls back
        const atlas = await loadAtlas();
        if (atlas && Array.isArray(atlas.components) && atlas.components.length) {
          const stats = atlas.stats || {};
          engine.renderAtlas(atlas, onto);
          atlasLegend({
            confirmed: stats.confirmed ?? countTier(atlas, "confirmed"),
            likely: stats.likely ?? countTier(atlas, "likely"),
            hint: stats.hint ?? countTier(atlas, "hint"),
            silos: stats.silos ?? atlas.components.filter((c) => c.is_silo).length,
          });
          const silos = stats.silos ?? atlas.components.filter((c) => c.is_silo).length;
          const islands = Math.max(0, (stats.components ?? atlas.components.length) - silos);
          ctx.setTitle(`Atlas — ${islands} island${islands === 1 ? "" : "s"} · ${silos} silo${silos === 1 ? "" : "s"}`);
        } else {
          engine.render(onto);
          starLegend("atlas not built — induced ontology shown");
          ctx.setTitle("Constellation");
        }
        if (pendingFocus) {
          focusClass(pendingFocus, pendingProp);
          pendingFocus = null;
          pendingProp = null;
        }
      }

      // the project was reloaded elsewhere — redraw the sky
      ctx.on("world:reload", () => {
        onto = null;
        dropCaches();
        clear(detail).append(el("div", { class: "empty-note" }, "redrawing the sky…"));
        load();
      });

      load();

      return {
        focusClass,
        params: () => ({ uri: lastUri || undefined }),
      };
    },
  };
}
