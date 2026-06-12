/* Dashboards — VISTA's vague-spec synthesis rendered through vendored
   Vega-Lite, themed to the instrument (achromatic chrome, amber data). */

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

export function createDashboardsPanel(ctx) {
  const { $, el, clear, api, errorNote } = ctx;

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
    for (const chart of d.charts) {
      const cell = el("div", { class: "chart-cell" }, el("div", { class: "chart-title" }, chart.title));
      grid.append(cell);
      renderChart(cell, chart.vega);
    }
    return block;
  }

  async function propose(utterance) {
    const target = clear($("#dash-result"));
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

  let savedLoaded = false;
  async function loadSaved() {
    if (savedLoaded) return;
    savedLoaded = true;
    try {
      const out = await api("/api/dashboards");
      const target = clear($("#dash-saved"));
      if (!out.dashboards.length) {
        target.append(el("div", { class: "empty-note" },
          "no saved proposals — `ontoforge dashboard` writes them here"));
        return;
      }
      for (const d of out.dashboards) target.append(dashboardBlock(d, null));
    } catch (e) {
      clear($("#dash-saved")).append(errorNote(e));
    }
  }

  $("#dash-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const utterance = $("#dash-input").value.trim();
    if (utterance) propose(utterance);
  });

  return {
    enter() { loadSaved(); },
    reset() { savedLoaded = false; clear($("#dash-saved")); },
  };
}
