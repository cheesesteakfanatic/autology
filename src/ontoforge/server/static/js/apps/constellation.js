/* Constellation — the induced ontology star chart in a resizable window.
   The deterministic layout engine lives in js/constellation.js; this app
   gives it a window, a class-detail drawer beneath the sky, and a
   focus-on-class API the WM routes 'class:focus' intents to. */

import { el, svgEl, clear, errorNote, loadOntology, dropCaches } from "../core.js";
import { createConstellation } from "../constellation.js";

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
      const wrap = el("div", { class: "constellation-wrap" }, svg, card,
        el("div", { class: "constellation-legend" },
          el("span", {}, el("i", { class: "lg-node" }), " class · sized by structure"),
          el("span", {}, el("i", { class: "lg-lum" }), " amber luminance = confidence"),
          el("span", {}, el("i", { class: "lg-sub" }), " subsumption"),
          el("span", {}, el("i", { class: "lg-link" }), " link property"),
          el("span", { class: "lg-hint" }, "drag to pan · wheel to zoom · double-click to reset")));
      const detail = el("div", { class: "class-detail" },
        el("div", { class: "empty-note" }, "select a star to inspect the class beneath it"));
      ctx.root.append(wrap, detail);
      ctx.root.classList.add("app-constellation");

      let onto = null;
      let pendingFocus = params.uri || null;
      let pendingProp = params.prop || null;
      let lastUri = null;

      const engine = createConstellation({
        svg, wrap, card, svgEl, el, clear,
        onSelect: (c) => renderClassDetail(c),
      });

      function renderClassDetail(c, highlightProp) {
        lastUri = c.uri;
        const byUri = new Map(onto.classes.map((k) => [k.uri, k]));
        const target = clear(detail);

        const jump = (uri) => {
          const k = byUri.get(uri);
          if (k) { engine.select(uri); renderClassDetail(k); }
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

      function focusClass(uri, prop) {
        if (!onto) { pendingFocus = uri; pendingProp = prop || null; return; }
        if (!uri) return;
        const c = onto.classes.find((k) => k.uri === uri || k.name === uri);
        if (c) { engine.select(c.uri); renderClassDetail(c, prop); }
      }

      async function load() {
        try {
          onto = await loadOntology();
        } catch (e) {
          clear(detail).append(errorNote(e));
          return;
        }
        engine.render(onto);
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
