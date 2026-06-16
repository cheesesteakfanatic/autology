/* Observatory — the observability surface nobody else has. Four read-only tabs
   over the EXISTING ledger/HEARTH/CostMeter substrate (nothing recomputed):
   Lineage (a value traced to the RAW source row+column — value-LEVEL lineage,
   not just the table), Audit (the append-only decision/verdict log), Runs (run
   history), Compute (the per-project CostMeter; compute-at-cost, zero margin).
   GET /api/lineage · /api/audit · /api/runs · /api/compute-ledger. API data
   enters the DOM only via el()/createTextNode. Deep-linked over the bus with
   "lineage:open" {cell,prop}|{atom}|{provRef}. Reuses shared chrome so its CSS
   stays small. */

import { el, clear, api, errorNote, fmt, skeletonCard, hueFor } from "../core.js";

const TABS = [
  ["lineage", "Lineage"], ["audit", "Audit"], ["runs", "Runs"], ["compute", "Compute"],
];
const lede = (t) => el("p", { class: "obs-lede" }, t);
const slice19 = (s) => (s || "").slice(0, 19);

/* The app's scoped CSS, injected ONCE — kept here (not in the shared
   stylesheet) so the design system's file stays the design crew's. No new
   palette: it leans entirely on the existing warm tokens. It is static
   stylesheet text, never API data, so this is not an XSS surface. */
const OBS_CSS = `
.app-observatory{display:flex;flex-direction:column}
.obs-tabs{display:flex;gap:.25rem;border-bottom:1px solid var(--hairline);margin-bottom:.75rem}
.obs-tab{background:none;border:none;border-bottom:2px solid transparent;cursor:pointer;padding:.4rem .7rem;font-family:var(--sans);font-size:var(--fs-2);color:var(--walnut)}
.obs-tab.on{color:var(--ink);border-bottom-color:var(--marigold);font-weight:600}
.obs-lede{color:var(--walnut);font-size:var(--fs-2);margin:0 0 .75rem}
.obs-cell-head{display:flex;flex-wrap:wrap;align-items:baseline;gap:.4rem;padding:.5rem .6rem;background:var(--cream);border-radius:var(--radius);margin-bottom:.6rem}
.obs-cell-prop{color:var(--walnut)}
.obs-cell-val{color:var(--ink);font-weight:600}
.obs-cell-uri{color:var(--ink-faint);font-size:var(--fs-1);flex-basis:100%}
.obs-srcs{display:flex;flex-wrap:wrap;align-items:center;gap:.4rem;margin-bottom:.5rem}
.obs-trail{display:flex;flex-direction:column;gap:.3rem}
.obs-atom{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,2fr) auto;gap:.5rem;align-items:center;padding:.4rem .55rem;background:var(--warm-white);border:1px solid var(--hairline);border-radius:var(--radius)}
.obs-atom-val{color:var(--ink);font-weight:600;overflow:hidden;text-overflow:ellipsis}
.obs-loc{color:var(--walnut);font-size:var(--fs-1);overflow:hidden;text-overflow:ellipsis}
.obs-atom-id{color:var(--ink-faint);font-size:var(--fs-0);justify-self:end}
.obs-audit{display:flex;flex-direction:column}
.obs-row{display:grid;grid-template-columns:5.5rem 4rem minmax(0,1fr) auto auto;gap:.5rem;align-items:baseline;padding:.35rem .25rem;border-bottom:1px solid var(--hairline);font-size:var(--fs-1)}
.obs-cat{font-size:var(--fs-0);letter-spacing:.03em;text-transform:uppercase;color:var(--walnut)}
.obs-cat.cat-decision{color:var(--teal)}
.obs-cat.cat-verdict{color:var(--marigold)}
.obs-kind{color:var(--ink)}
.obs-sum{color:var(--walnut);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.obs-meta,.obs-when{color:var(--ink-faint);font-size:var(--fs-0);white-space:nowrap}
.obs-cost{display:flex;flex-direction:column;gap:.3rem;margin-bottom:.6rem}
.obs-cost-row{display:grid;grid-template-columns:minmax(0,1fr) 2fr auto;gap:.5rem;align-items:center}
.obs-cost-label{color:var(--ink);overflow:hidden;text-overflow:ellipsis}
.obs-cost-bar{height:.55rem;background:var(--cream);border-radius:var(--radius-pill);overflow:hidden}
.obs-cost-fill{display:block;height:100%;background:var(--marigold);border-radius:var(--radius-pill)}
.obs-cost-n{color:var(--walnut);font-size:var(--fs-1);justify-self:end}`;

function ensureObsCss() {
  if (document.getElementById("obs-css")) return;
  const s = document.createElement("style");
  s.id = "obs-css";
  s.textContent = OBS_CSS;  // static stylesheet text — never API data
  document.head.append(s);
}

export function createObservatoryApp() {
  return {
    id: "observatory",
    title: "Observatory",
    tagline: "lineage, audit, runs & compute",
    glyph: "❖",
    w: 720, h: 600, multi: false,

    mount(ctx, params) {
      ensureObsCss();
      ctx.root.classList.add("app-observatory");
      let active = TABS.some((t) => t[0] === params.tab) ? params.tab : "lineage";
      const seed = { cell: params.cell, prop: params.prop, atom: params.atom, provRef: params.provRef };
      const tabBar = el("div", { class: "obs-tabs", role: "tablist" });
      const body = el("div", { class: "obs-body" });
      ctx.root.append(tabBar, body);
      const R = { lineage: renderLineage, audit: renderAudit, runs: renderRuns, compute: renderCompute };

      function show() {
        clear(tabBar);
        for (const [id, label] of TABS) {
          tabBar.append(el("button", {
            class: `obs-tab${id === active ? " on" : ""}`, type: "button", role: "tab",
            "aria-selected": id === active ? "true" : "false",
            onclick: () => { active = id; show(); },
          }, label));
        }
        clear(body).append(skeletonCard([40, 70, 55]));
        Promise.resolve().then(R[active]).catch((e) => clear(body).append(errorNote(e)));
        ctx.setTitle(`Observatory — ${TABS.find((t) => t[0] === active)[1]}`);
      }

      // ── Lineage — answer cell → prov term → atoms → RAW rows (the edge)
      async function renderLineage() {
        const out = el("div", {});
        const input = el("input", {
          class: "entity-input mono", type: "text", spellcheck: "false",
          placeholder: "source-record id — or arrive here from a record",
          value: seed.atom || seed.provRef || "",
        });
        const form = el("form", { class: "entity-form", autocomplete: "off" },
          input, el("button", { class: "btn btn-forge", type: "submit" }, "Trace"));
        form.addEventListener("submit", (e) => { e.preventDefault(); trace({ atom: input.value.trim() }); });
        clear(body).append(
          lede("One value, traced to the exact source row and column it came from — not just the table."),
          form, out);

        async function trace(q) {
          const p = new URLSearchParams();
          if (q.cell) { p.set("cell", q.cell); if (q.prop) p.set("prop", q.prop); }
          else if (q.atom) p.set("atom", q.atom);
          else if (q.provRef) p.set("prov_ref", q.provRef);
          else { clear(out).append(el("div", { class: "empty-note" }, "paste a source-record id to trace it")); return; }
          clear(out).append(el("div", { class: "skeleton", style: "width:55%" }));
          try { clear(out).append(lineageView(await api(`/api/lineage?${p}`))); }
          catch (e) {
            clear(out).append(e.status === 404
              ? el("div", { class: "empty-note" }, "no lineage for that reference in the current model")
              : errorNote(e));
          }
        }
        if (seed.cell || seed.atom || seed.provRef) trace(seed);
        else clear(out).append(el("div", { class: "empty-note" },
          "Open a record in Explore and follow “Where this came from”, or paste a source-record id above."));
      }

      function lineageView(lin) {
        const card = el("div", {});
        if (lin.cell) {
          card.append(el("div", { class: "obs-cell-head" },
            el("span", { class: "obs-cell-prop" }, lin.prop || "value"),
            el("span", {}, "="),
            el("span", { class: "obs-cell-val mono" }, lin.value === null ? "∅" : String(lin.value)),
            el("span", { class: "obs-cell-uri mono", title: lin.cell }, lin.cell)));
        }
        card.append(el("div", { class: "obs-srcs" },
          el("span", { class: "section-label", style: "margin:0" }, "source systems"),
          lin.sources.map((s) => el("span", { class: "chip", style: `border-color:${hueFor(s)}` }, s))));
        card.append(el("span", { class: "section-label" },
          `${fmt(lin.n_atoms)} source record${lin.n_atoms === 1 ? "" : "s"} — the raw rows this value rests on`));
        const trail = el("div", { class: "obs-trail" });
        for (const a of lin.atoms) {
          const loc = a.source && a.table
            ? el("span", { class: "obs-loc mono" },
                el("span", { style: `color:${hueFor(a.source)}` }, a.source),
                ` › ${a.table} › row ${a.row} · ${a.column}`)
            : el("span", { class: "obs-loc mono", title: a.uri }, a.uri);
          trail.append(el("div", { class: "obs-atom" },
            el("span", { class: "obs-atom-val mono" }, a.value === null ? "∅" : String(a.value)),
            loc,
            el("span", { class: "obs-atom-id mono", title: a.atom_id }, a.atom_id.slice(0, 12))));
        }
        card.append(lin.atoms.length ? trail : el("div", { class: "empty-note" }, "no backing source records"));
        return card;
      }

      // ── Audit — the append-only decision/verdict log
      async function renderAudit() {
        const a = await api("/api/audit");
        clear(body).append(
          lede("The append-only decision log. Nothing is overwritten — corrections supersede."),
          counters([["entries", fmt(a.total)], ...Object.entries(a.by_category).map(([k, v]) => [k, fmt(v)])]));
        if (!a.entries.length) {
          body.append(el("div", { class: "empty-note" }, "no decisions or verdicts yet — ask a question to populate the log"));
          return;
        }
        const list = el("div", { class: "obs-audit" });
        for (const e of a.entries) {
          const m = [];
          if (e.tier != null) m.push(`tier ${e.tier}`);
          if (e.outcome) m.push(`→ ${e.outcome}`);
          if (e.confidence != null) m.push(`${(e.confidence * 100).toFixed(0)}%`);
          if (e.deferred) m.push("deferred");
          if (e.quarantined) m.push("quarantined");
          list.append(el("div", { class: "obs-row" },
            el("span", { class: `obs-cat cat-${e.category}` }, e.category),
            el("span", { class: "mono obs-kind" }, e.kind),
            el("span", { class: "obs-sum", title: e.summary }, e.summary),
            m.length ? el("span", { class: "mono obs-meta" }, m.join(" · ")) : null,
            el("span", { class: "mono obs-when", title: e.created_at }, slice19(e.created_at))));
        }
        body.append(list);
      }

      // ── Runs — pipeline + answer run history
      async function renderRuns() {
        const r = await api("/api/runs");
        clear(body).append(
          lede("What the engine ran to build this model, and the activity since."),
          counters([["decisions", fmt(r.total_decisions)], ["artifacts", fmt(r.total_artifacts)],
            ["compute (tokens)", fmt(r.total_cost_tokens)]]));
        const stages = el("div", { class: "stage-list" });
        for (const s of r.stages) stages.append(el("span", { class: "stage-item done" }, el("span", { class: "tick" }, "◆"), s));
        body.append(el("span", { class: "section-label" }, "pipeline stages cleared"), stages);
        if (r.runs.length) {
          body.append(el("span", { class: "section-label" }, "run lanes"),
            el("table", { class: "data" },
              el("thead", {}, el("tr", {}, ["run", "decisions", "artifacts", "first seen"].map((h) => el("th", {}, h)))),
              el("tbody", {}, r.runs.map((x) => el("tr", {},
                el("td", {}, x.label || x.kind),
                el("td", { class: "mono" }, fmt(x.decisions)),
                el("td", { class: "mono" }, fmt(x.artifacts)),
                el("td", { class: "mono" }, slice19(x.started_at)))))));
        }
      }

      // ── Compute — the per-project CostMeter (compute at cost)
      async function renderCompute() {
        const c = await api("/api/compute-ledger");
        clear(body).append(
          lede("Compute at cost. Zero margin — exactly what ran on your data. Deterministic tiers cost nothing."),
          counters([["total tokens", fmt(c.total_tokens)], ["metered calls", fmt(c.total_calls)], ["estate", c.estate]]),
          costTable("by task", c.by_task), costTable("by decision tier", c.by_tier));
      }

      function costTable(title, rows) {
        const wrap = el("div", {}, el("span", { class: "section-label" }, title));
        if (!rows.length) { wrap.append(el("div", { class: "empty-note", style: "padding:0.3rem 0" }, "nothing metered here yet")); return wrap; }
        const max = Math.max(1, ...rows.map((r) => r.tokens));
        wrap.append(el("div", { class: "obs-cost" }, rows.map((r) => el("div", { class: "obs-cost-row" },
          el("span", { class: "mono obs-cost-label" }, r.label),
          el("span", { class: "obs-cost-bar" }, el("span", { class: "obs-cost-fill", style: `width:${((r.tokens / max) * 100).toFixed(1)}%` })),
          el("span", { class: "mono obs-cost-n" }, `${fmt(r.calls)}× · ${fmt(r.tokens)}`)))));
        return wrap;
      }

      function counters(pairs) {
        return el("div", { class: "counter-grid" }, pairs.map(([k, v]) => el("div", { class: "counter-cell" },
          el("div", { class: "counter-label" }, k), el("div", { class: "counter-value" }, v))));
      }

      ctx.on("lineage:open", (p) => {
        active = "lineage";
        Object.assign(seed, { cell: p.cell, prop: p.prop, atom: p.atom, provRef: p.provRef });
        show();
      });

      show();
      return { params: () => ({ tab: active }) };
    },
  };
}
