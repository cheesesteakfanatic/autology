/* Dashboards — VISTA's vague-spec synthesis. An utterance grounds into
   the metric layer and returns three ranked proposals, each previewed as
   themed Vega-Lite charts; every chart can be expanded into its own
   window (params.chart turns an instance into a single-chart viewer). */

import { el, clear, api, errorNote } from "../core.js";

const VEGA_CONFIG = {
  background: "transparent",
  view: { stroke: "transparent" },
  axis: {
    labelColor: "#9b978e", titleColor: "#9b978e",
    gridColor: "rgba(232,230,225,0.07)", domainColor: "rgba(232,230,225,0.22)",
    tickColor: "rgba(232,230,225,0.22)",
    labelFont: "monospace", titleFont: "monospace", labelFontSize: 10, titleFontSize: 10,
  },
  legend: { labelColor: "#9b978e", titleColor: "#9b978e" },
  title: { color: "#e8e6e1", font: "Georgia, serif" },
  range: { category: ["#e8a33d", "#b97c26", "#8a5c1d", "#5c3e16", "#f0c47e"] },
  mark: { color: "#e8a33d" },
  bar: { fill: "#e8a33d" },
  line: { stroke: "#e8a33d" },
  point: { fill: "#e8a33d" },
  area: { fill: "#b97c26" },
  arc: { fill: "#e8a33d" },
  text: { fill: "#e8e6e1", font: "monospace" },
};

function renderChart(cell, spec) {
  const mount = el("div", { class: "chart-vega" });
  cell.append(mount);
  if (typeof window.vegaEmbed === "function") {
    window.vegaEmbed(mount, spec, { actions: false, renderer: "svg", config: VEGA_CONFIG })
      .catch((e) => { mount.replaceWith(errorNote(e)); });
  } else {
    cell.append(
      el("div", { class: "offline-note" },
        "vega vendor scripts unavailable — showing the raw Vega-Lite spec"),
      el("pre", { class: "chart-fallback" }, JSON.stringify(spec, null, 2)));
    mount.remove();
  }
}

export function createDashboardsApp() {
  return {
    id: "dashboards",
    title: "Dashboards",
    tagline: "vague-spec synthesis (VISTA)",
    glyph: "▤",
    w: 720, h: 560, multi: true,

    mount(ctx, params) {
      ctx.root.classList.add("app-dashboards");

      // ── single-chart viewer mode: a chart expanded into its own window
      if (params.chart) {
        ctx.setTitle(`Chart — ${params.chart.title || "untitled"}`);
        const cell = el("div", { class: "chart-cell chart-solo" },
          el("div", { class: "chart-title" }, params.chart.title || ""));
        ctx.root.append(cell);
        renderChart(cell, params.chart.vega);
        return { params: () => ({ chart: params.chart }) };
      }

      let lastUtterance = params.utterance || "";

      const input = el("input", {
        class: "ask-input", type: "text", spellcheck: "false",
        placeholder: "maintenance cost overview",
      });
      const form = el("form", { class: "ask-form", autocomplete: "off" },
        el("label", { class: "section-label" }, "vague-spec synthesis — VISTA grounds an utterance in the metric layer"),
        el("div", { class: "ask-field" }, input,
          el("button", { class: "btn btn-forge", type: "submit" }, "Propose")));
      const result = el("div", { class: "dash-result" });
      const savedLabel = el("div", { class: "section-label dash-saved-label" }, "saved proposals (dashboards/)");
      const saved = el("div", { class: "dash-saved" });
      ctx.root.append(form, result, savedLabel, saved);

      function chartCell(chart) {
        const cell = el("div", { class: "chart-cell" },
          el("div", { class: "chart-head" },
            el("span", { class: "chart-title" }, chart.title),
            el("button", {
              class: "chart-expand", type: "button", title: "expand into its own window",
              "aria-label": `expand ${chart.title}`,
              onclick: () => ctx.openNear("dashboards", { chart }),
            }, "⤢")));
        renderChart(cell, chart.vega);
        return cell;
      }

      function dashboardBlock(d, rank) {
        const block = el("div", { class: "dash-proposal" },
          el("div", { class: "dash-head" },
            rank ? el("span", { class: "dash-rank" }, `№${rank}`) : null,
            el("span", { class: "dash-title" }, d.title),
            d.score !== null && d.score !== undefined
              ? el("span", { class: "dash-score" }, `score ${Number(d.score).toFixed(3)}`)
              : null),
          d.rationale ? el("p", { class: "dash-rationale" }, d.rationale) : null);
        const grid = el("div", { class: "chart-grid" });
        block.append(grid);
        for (const chart of d.charts) grid.append(chartCell(chart));
        return block;
      }

      async function propose(utterance) {
        lastUtterance = utterance;
        ctx.setTitle(`Dashboards — ${utterance}`);
        const target = clear(result);
        target.append(el("div", { class: "skeleton-card" }));
        try {
          const out = await api("/api/dashboards", { utterance });
          clear(target);
          if (!out.dashboards.length) {
            target.append(el("div", { class: "empty-note" },
              "no dashboard could be grounded in this ontology — try naming a metric or a class"));
            return;
          }
          out.dashboards.forEach((d, i) => target.append(dashboardBlock(d, i + 1)));
        } catch (e) {
          clear(target).append(errorNote(e));
        }
      }

      async function loadSaved() {
        try {
          const out = await api("/api/dashboards");
          clear(saved);
          if (!out.dashboards.length) {
            saved.append(el("div", { class: "empty-note" },
              "no saved proposals — `ontoforge dashboard` writes them here"));
            return;
          }
          for (const d of out.dashboards) saved.append(dashboardBlock(d, null));
        } catch (e) {
          clear(saved).append(errorNote(e));
        }
      }

      form.addEventListener("submit", (e) => {
        e.preventDefault();
        const utterance = input.value.trim();
        if (utterance) propose(utterance);
      });

      loadSaved();
      if (params.utterance) { input.value = params.utterance; propose(params.utterance); }

      return { params: () => ({ utterance: lastUtterance || undefined }) };
    },
  };
}
