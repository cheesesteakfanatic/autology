/* BUILD — the dashboard / data builder. "Measure something, pull data out."
   LEFT: a plain-language picker — "Measure something" + "Break it down by"
   (searchable chips drawn from the model in human terms) and a free-text
   "or just describe what you want to see" box feeding the same synthesis.
   RIGHT: live Dashboard proposals (warm Vega), then two separated outputs —
   Extract (the filtered table → Download CSV, a slice) and Export (Download
   the whole dataset, portable — the bundle).
   De-jargon: VISTA → Dashboard proposals; AMBER → Export. API routes
   (/api/dashboards, /api/extract, /api/export, /api/exports) are unchanged. */

import {
  el, clear, api, errorNote, fmt, toast, loadOntology, ontologyNow,
} from "../core.js";

/* the warm editorial chart theme — the MUTED atlas wheel (kept in sync with
   core.js ATLAS_HUES); humanist sans chrome, mono labels */
const ATLAS_RANGE = ["#2C5956", "#D09735", "#945442", "#6C733A", "#375E72", "#945942", "#713D68", "#86663C"];
const INK = "#2A1F14", WALNUT = "#6B5A45";
const CHART_SANS = "-apple-system, 'Inter', 'Segoe UI', system-ui, sans-serif";
const VEGA_CONFIG = {
  background: "transparent",
  view: { stroke: "transparent" },
  font: CHART_SANS,
  axis: {
    labelColor: WALNUT, titleColor: WALNUT,
    gridColor: "rgba(42,31,20,0.08)", domainColor: "rgba(42,31,20,0.26)",
    tickColor: "rgba(42,31,20,0.26)",
    labelFont: "ui-monospace, Menlo, monospace", titleFont: CHART_SANS,
    labelFontSize: 10, titleFontSize: 10, gridDash: [1, 3],
  },
  legend: { labelColor: WALNUT, titleColor: WALNUT },
  title: { color: INK, font: CHART_SANS, fontWeight: 600 },
  range: { category: ATLAS_RANGE },
  mark: { color: "#D09735" },
  bar: { fill: "#D09735" },
  line: { stroke: "#2C5956", strokeWidth: 2 },
  point: { fill: "#2C5956" },
  area: { fill: "#D09735", fillOpacity: 0.65 },
  arc: { fill: "#D09735" },
  text: { fill: INK, font: "ui-monospace, Menlo, monospace" },
};

function renderChart(cell, spec) {
  const mount = el("div", { class: "chart-vega" });
  cell.append(mount);
  if (typeof window.vegaEmbed === "function") {
    window.vegaEmbed(mount, spec, { actions: false, renderer: "svg", config: VEGA_CONFIG })
      .catch((e) => { mount.replaceWith(errorNote(e)); });
  } else {
    cell.append(
      el("div", { class: "offline-note" }, "chart renderer unavailable — showing the raw spec"),
      el("pre", { class: "chart-fallback" }, JSON.stringify(spec, null, 2)));
    mount.remove();
  }
}

const MEASURE_RE = /cost|amount|price|delay|count|total|qty|quantity|hours?|rate|fee|spend|revenue|duration|distance|weight|score/i;
const DIM_RE = /date|month|year|day|time|type|category|region|status|code|name|class|group/i;

export function createBuildSurface({ bus }) {
  let pane = null;
  let built = false;
  let onto = null;
  let measures = [];        // {label, prop, class}
  let dimensions = [];      // {label, prop, class}
  let pickedMeasure = null;
  let pickedDims = new Set();
  let proposalsBox = null;
  let extractBox = null;
  let exportBox = null;
  let measureList = null;
  let dimList = null;

  function deriveTerms() {
    onto = ontologyNow();
    measures = [];
    dimensions = [];
    if (!onto || !onto.classes) return;
    const seenM = new Set(), seenD = new Set();
    for (const c of onto.classes) {
      for (const p of c.properties) {
        const human = `${p.name.replace(/_/g, " ")}`;
        if ((p.unit || MEASURE_RE.test(p.name)) && !seenM.has(human)) {
          seenM.add(human);
          measures.push({ label: human, prop: p.name, cls: c.uri, unit: p.unit || null, clsName: c.name });
        }
        if (DIM_RE.test(p.name) && !p.is_link && !seenD.has(human)) {
          seenD.add(human);
          dimensions.push({ label: `by ${human}`, prop: p.name, cls: c.uri, clsName: c.name });
        }
      }
    }
  }

  /* a synthesis utterance assembled from the plain picks, fed to VISTA */
  function utteranceFromPicks() {
    if (!pickedMeasure) return "";
    let u = pickedMeasure.label;
    const dims = [...pickedDims];
    if (dims.length) u += " " + dims.map((d) => d.label).join(" ");
    return u.trim();
  }

  function chartCell(chart) {
    const cell = el("div", { class: "chart-cell" },
      el("div", { class: "chart-head" }, el("span", { class: "chart-title" }, chart.title)));
    renderChart(cell, chart.vega);
    return cell;
  }

  function dashboardBlock(d, rank) {
    const block = el("div", { class: "dash-proposal" },
      el("div", { class: "dash-head" },
        rank ? el("span", { class: "dash-rank" }, `№${rank}`) : null,
        el("span", { class: "dash-title" }, d.title),
        d.score !== null && d.score !== undefined
          ? el("span", { class: "dash-score" }, `match ${Number(d.score).toFixed(2)}`) : null),
      d.rationale ? el("p", { class: "dash-rationale" }, d.rationale) : null);
    const grid = el("div", { class: "chart-grid" });
    block.append(grid);
    for (const chart of d.charts) grid.append(chartCell(chart));
    return block;
  }

  async function propose(utterance) {
    utterance = String(utterance || "").trim();
    if (!utterance) return;
    const target = clear(proposalsBox);
    target.append(el("div", { class: "skeleton-card" }));
    try {
      const out = await api("/api/dashboards", { utterance });
      clear(target);
      if (!out.dashboards.length) {
        target.append(el("div", { class: "empty-note" },
          "no view could be built from that — try a different measure or breakdown"));
        return;
      }
      out.dashboards.forEach((d, i) => target.append(dashboardBlock(d, i + 1)));
    } catch (e) {
      clear(target).append(errorNote(e));
    }
    refreshExtract();
  }

  /* ─────────────────────────── Extract — the filtered table → CSV (slice) */
  function refreshExtract() {
    if (!extractBox) return;
    clear(extractBox);
    extractBox.append(el("span", { class: "section-label" }, "Extract — download the filtered table (CSV)"));
    if (!pickedMeasure) {
      extractBox.append(el("div", { class: "empty-note" },
        "Your filtered table will appear here once you build a view"));
      return;
    }
    const cls = pickedMeasure.cls;
    const cols = [pickedMeasure.prop, ...[...pickedDims].map((d) => d.prop)];
    const preview = el("div", { class: "extract-preview" }, el("div", { class: "skeleton", style: "width:60%" }));
    const dl = el("a", {
      class: "btn btn-forge", href: `/api/extract?format=csv`,
      onclick: (e) => { e.preventDefault(); downloadCsv(cls, cols); },
    }, "Download CSV");
    extractBox.append(el("div", { class: "extract-actions" },
      el("span", { class: "extract-target mono" }, `${pickedMeasure.clsName} · ${cols.join(", ")}`), dl), preview);

    api("/api/extract", { type_uri: cls, filters: [], columns: cols, limit: 12 })
      .then((out) => {
        clear(preview);
        if (!out.rows || !out.rows.length) {
          preview.append(el("div", { class: "empty-note" }, "no rows matched"));
          return;
        }
        const head = el("tr", {}, (out.columns || cols).map((c) => el("th", {}, c)));
        const body = out.rows.slice(0, 12).map((r) =>
          el("tr", {}, r.map((v) => el("td", {}, v === null ? "∅" : String(v)))));
        preview.append(el("div", { class: "answer-table-wrap" },
          el("table", { class: "data" }, el("thead", {}, head), el("tbody", {}, body))));
      })
      .catch((e) => {
        clear(preview).append(el("div", { class: "empty-note" },
          e.status === 404 || e.status === 405
            ? "extract not exposed by this build — `ontoforge` CLI extracts the same slice"
            : String(e.message || e)));
      });
  }

  async function downloadCsv(typeUri, columns) {
    try {
      const res = await fetch(`/api/extract?format=csv`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ type_uri: typeUri, filters: [], columns, limit: 100000 }),
      });
      if (!res.ok) throw new Error(`${res.status}`);
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = el("a", { href: url, download: "extract.csv" });
      document.body.append(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      toast("CSV downloaded", { kind: "ok" });
    } catch (e) {
      toast("extract not available — use the CLI", { kind: "warn" });
    }
  }

  /* ───────────── Export — the WHOLE portable dataset (the AMBER bundle) */
  function refreshExport() {
    if (!exportBox) return;
    clear(exportBox);
    exportBox.append(
      el("span", { class: "section-label" }, "Export — download the whole dataset, portable"),
      el("p", { class: "export-blurb" },
        "the entire model — every type, record and source — sealed into one portable file. nothing is locked in."));
    const btn = el("button", { class: "btn btn-forge", type: "button" }, "Download the whole dataset");
    const history = el("div", { class: "export-list" });
    exportBox.append(btn, el("span", { class: "section-label", style: "margin-top:1rem" }, "previous downloads"), history);

    btn.addEventListener("click", async () => {
      btn.disabled = true;
      try {
        const out = await api("/api/export", {});
        toast("dataset exported — portable bundle ready", { kind: "ok" });
        loadExports(history);
      } catch (e) {
        toast(e.status === 404 || e.status === 405
          ? "export not exposed — use `ontoforge export`"
          : "export failed — see the CLI for details", { kind: "warn" });
      } finally { btn.disabled = false; }
    });
    loadExports(history);
  }

  async function loadExports(history) {
    clear(history).append(el("div", { class: "skeleton", style: "width:50%" }));
    try {
      const out = await api("/api/exports");
      const bundles = out.exports || [];
      clear(history);
      if (!bundles.length) {
        history.append(el("div", { class: "empty-note" },
          "No exports yet — build a view, then download the whole dataset"));
        return;
      }
      for (const b of bundles) {
        const label = typeof b === "string" ? b : (b.path || b.name || JSON.stringify(b));
        history.append(el("div", { class: "export-bundle mono" }, String(label)));
      }
    } catch (e) {
      clear(history).append(el("div", { class: "empty-note" },
        "No exports yet — build a view, then download the whole dataset"));
    }
  }

  /* ─────────────────────────────────────────── pickers (left pane) */
  function renderPicker(filter = "") {
    const lower = filter.toLowerCase();
    clear(measureList);
    const ms = measures.filter((m) => !lower || m.label.toLowerCase().includes(lower));
    if (!ms.length) measureList.append(el("div", { class: "empty-note" }, "no measures match"));
    for (const m of ms) {
      const on = pickedMeasure && pickedMeasure.prop === m.prop && pickedMeasure.cls === m.cls;
      measureList.append(el("button", {
        class: `chip measure-chip${on ? " picked" : ""}`, type: "button",
        title: `${m.clsName}.${m.prop}${m.unit ? ` (${m.unit})` : ""}`,
        "aria-pressed": on ? "true" : "false",
        onclick: () => pickMeasure(m),
      }, m.label, m.unit ? el("span", { class: "chip-unit" }, m.unit) : null));
    }
    clear(dimList);
    const ds = dimensions.filter((d) => !lower || d.label.toLowerCase().includes(lower));
    if (!ds.length) dimList.append(el("div", { class: "empty-note" }, "no breakdowns match"));
    for (const d of ds) {
      const key = `${d.cls}#${d.prop}`;
      const on = pickedDims.has(d) || [...pickedDims].some((x) => `${x.cls}#${x.prop}` === key);
      dimList.append(el("button", {
        class: `chip dim-chip${on ? " picked" : ""}`, type: "button",
        title: `${d.clsName}.${d.prop}`, "aria-pressed": on ? "true" : "false",
        onclick: () => toggleDim(d),
      }, d.label));
    }
  }

  function pickMeasure(m) {
    pickedMeasure = (pickedMeasure && pickedMeasure.prop === m.prop && pickedMeasure.cls === m.cls) ? null : m;
    renderPicker(searchVal());
    if (pickedMeasure) propose(utteranceFromPicks());
    refreshExtract();
  }
  function toggleDim(d) {
    const key = `${d.cls}#${d.prop}`;
    const existing = [...pickedDims].find((x) => `${x.cls}#${x.prop}` === key);
    if (existing) pickedDims.delete(existing); else pickedDims.add(d);
    renderPicker(searchVal());
    if (pickedMeasure) propose(utteranceFromPicks());
    refreshExtract();
  }

  let searchInput = null;
  function searchVal() { return searchInput ? searchInput.value.trim() : ""; }

  function renderBuilder() {
    clear(pane);

    // LEFT: pickers + free-text
    searchInput = el("input", {
      class: "picker-search", type: "text", spellcheck: "false",
      placeholder: "search measures and breakdowns",
      oninput: () => renderPicker(searchVal()),
    });
    measureList = el("div", { class: "picker-chips measure-chips" });
    dimList = el("div", { class: "picker-chips dim-chips" });
    const freeText = el("input", {
      class: "ask-input", type: "text", spellcheck: "false",
      placeholder: "or just describe what you want to see",
    });
    const freeForm = el("form", { class: "free-form", autocomplete: "off" },
      freeText, el("button", { class: "btn", type: "submit" }, "Show"));
    freeForm.addEventListener("submit", (e) => { e.preventDefault(); if (freeText.value.trim()) propose(freeText.value); });

    const left = el("div", { class: "build-left" },
      el("div", { class: "picker-block" },
        el("span", { class: "section-label" }, "Measure something"),
        el("p", { class: "picker-help" }, "Start by picking what you want to measure."),
        searchInput, measureList),
      el("div", { class: "picker-block" },
        el("span", { class: "section-label" }, "Break it down by"),
        dimList),
      el("div", { class: "picker-block free-block" },
        el("span", { class: "section-label" }, "or describe it"),
        freeForm));

    // RIGHT: proposals + extract + export
    proposalsBox = el("div", { class: "dash-result" },
      el("div", { class: "chart-placeholder" }, "Pick something to measure and proposals appear here."));
    extractBox = el("div", { class: "build-extract" });
    exportBox = el("div", { class: "build-export" });
    const right = el("div", { class: "build-right" },
      el("span", { class: "section-label proposals-label" }, "Dashboard proposals"),
      proposalsBox,
      el("div", { class: "build-outputs" }, extractBox, exportBox));

    pane.append(el("div", { class: "build-layout" }, left, right));
    renderPicker();
    refreshExtract();
    refreshExport();
    // first-visit nudge: glow the measure picker
    pane.querySelector(".measure-chips")?.classList.add("glow-hint");
    setTimeout(() => pane.querySelector(".measure-chips")?.classList.remove("glow-hint"), 2400);
  }

  function renderNotReady() {
    clear(pane);
    pane.append(el("div", { class: "build-notready" },
      el("div", { class: "notready-card" },
        el("h2", { class: "notready-title" }, "no measures found yet"),
        el("p", { class: "notready-line" },
          "This usually means the data is still being modeled. Open Studio to add data or confirm suggestions."),
        el("button", {
          class: "btn btn-forge", type: "button",
          onclick: () => bus.emit("mode:goto", { mode: "studio", panel: "catalog" }),
        }, "Open Studio →"))));
  }

  async function refresh() {
    try { await loadOntology(); } catch { /* fall through to not-ready */ }
    deriveTerms();
    built = measures.length > 0;
    if (built) renderBuilder();
    else renderNotReady();
  }

  return {
    mount({ pane: p }) {
      pane = p;
      pane.classList.add("surface-build");
      refresh();
      bus.on("world:reload", refresh);
      bus.on("workspace:built", refresh);
    },
    enter() { /* lazy — proposals already reflect the picks */ },
    show(opts = {}) { if (opts.utterance) propose(opts.utterance); },
  };
}
