/* Pulse — the instrument cluster, live-ish. Ledger counters, pipeline
   stages, decision tiers; refreshed every 10 seconds while the window is
   open (the interval is disposed with the window). Project reload lives
   here and announces itself on the bus. */

import { el, clear, api, errorNote, fmt, skeletonCard, dropCaches } from "../core.js";

const PIPELINE = ["ingest", "profile", "induce", "resolve", "materialize"];
const POLL_MS = 10_000;

export function createPulseApp() {
  return {
    id: "pulse",
    title: "Pulse",
    tagline: "status — the instrument cluster",
    glyph: "◉",
    w: 600, h: 520, multi: false,

    mount(ctx) {
      const body = el("div", { class: "status-body" }, skeletonCard([30, 60, 45]));
      const reload = el("button", {
        class: "btn", type: "button", title: "re-open the project after CLI changes",
        onclick: async () => {
          reload.disabled = true;
          try {
            await api("/api/reload", {});
            dropCaches();
            ctx.emit("world:reload", {});
            await load();
          } catch (e) {
            clear(body).append(errorNote(e));
          } finally {
            reload.disabled = false;
          }
        },
      }, "↻ reload project");
      ctx.root.append(body, reload);
      ctx.root.classList.add("app-pulse");

      function kvTable(title, entries, valueOf) {
        return el("div", {},
          el("span", { class: "section-label" }, title),
          entries.length
            ? el("table", { class: "data" }, el("tbody", {}, entries.map(([k, v]) =>
                el("tr", {}, el("td", {}, k), el("td", {}, valueOf ? valueOf(v) : fmt(v))))))
            : el("div", { class: "empty-note", style: "padding:0.5rem 0;text-align:left" }, "none recorded"));
      }

      let firstLoad = true;

      async function load() {
        try {
          const s = await api("/api/status");
          const target = clear(body);

          target.append(el("div", { class: "status-project" },
            `${s.project} · estate ${s.estate}` + (s.limit ? ` · row limit ${s.limit}` : "")));

          const totalDecisions = Object.values(s.decisions_by_kind).reduce((a, b) => a + b, 0);
          const totalArtifacts = Object.values(s.artifacts).reduce((a, b) => a + b, 0);
          const m = s.materialized || {};
          const counter = (label, value, accent) => el("div", { class: "counter-cell" },
            el("div", { class: "counter-label" }, label),
            el("div", { class: `counter-value${accent ? " accent" : ""}` }, value));
          target.append(el("div", { class: "counter-grid" },
            counter("atoms", s.ledger_exists ? fmt(s.atoms) : "—", true),
            counter("entities", m.entities !== undefined ? fmt(m.entities) : "—"),
            counter("value cells", m.cells !== undefined ? fmt(m.cells) : "—"),
            counter("links", m.links !== undefined ? fmt(m.links) : "—"),
            counter("decisions", fmt(totalDecisions)),
            counter("artifacts", fmt(totalArtifacts)),
            el("div", { class: "counter-cell" },
              el("div", { class: "counter-label" }, "model cost"),
              el("div", { class: "counter-value" }, fmt(s.cost_tokens), el("small", {}, " tok")))));

          const known = new Set(s.stages);
          const stages = el("div", { class: "stage-list" });
          for (const st of PIPELINE) {
            stages.append(el("span", { class: `stage-item${known.has(st) ? " done" : ""}` },
              el("span", { class: "tick" }, known.has(st) ? "◆" : "◇"), st));
          }
          for (const st of s.stages) {
            if (!PIPELINE.includes(st)) {
              stages.append(el("span", { class: "stage-item done" }, el("span", { class: "tick" }, "◆"), st));
            }
          }
          target.append(el("span", { class: "section-label" }, "pipeline stages"), stages);

          target.append(el("div", { class: "status-tables" },
            kvTable("decisions by kind", Object.entries(s.decisions_by_kind)),
            kvTable("decisions by tier", Object.entries(s.decisions_by_tier),
              (t) => `${fmt(t.count)} · ${fmt(t.deferred)} deferred · ${fmt(t.quarantined)} quarantined`),
            kvTable("artifacts", Object.entries(s.artifacts))));

          ctx.setTitle(`Pulse — ${s.estate}`);
        } catch (e) {
          if (firstLoad) clear(body).append(errorNote(e));
          // a failed poll keeps the last good cluster on screen
        } finally {
          firstLoad = false;
        }
      }

      load();
      const timer = setInterval(load, POLL_MS);
      ctx.addDisposer(() => clearInterval(timer));

      return {};
    },
  };
}
