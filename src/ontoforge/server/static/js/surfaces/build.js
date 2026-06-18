/* BUILD — the Tableau-grade view builder. A natural-language VIEW BAR
   (POST /api/view) parses an utterance into a structured ViewSpec + a plain-
   English confirmation (or ONE clarifying question when ambiguous). Editable
   TABLEAU SHELVES (Measure / Break down by / Filter / Chart type) reflect that
   spec; editing a shelf re-runs the view. A FACETED, criticality-RANKED
   FIELD-SEARCH panel (GET /api/fields) scales to thousands — search + facet
   filters + a ranked list; a click drops a field on the right shelf. A real
   CHART renders with the VENDORED Vega from the executed rows. A DASHBOARD GRID
   pins the view as an arrangeable, nameable panel (plain-English definition +
   provenance + Extract-CSV via /api/extract).
   SECURITY: API data enters the DOM via el()/svgEl()/createTextNode — never
   innerHTML. Keyless / offline / deterministic; charts use the vendored Vega. */

import {
  el, svgEl, clear, api, errorNote, fmt, toast, debounce, store,
  loadOntology, ontologyNow, workspaceState,
} from "../core.js";

/* the COOL professional chart theme — the cool desaturated atlas wheel (kept in
   sync with core.js ATLAS_HUES). Single-series mark is the indigo data hue;
   teal anchors positive. */
const ATLAS_RANGE = ["#0E8C84", "#4A56C7", "#5B6B86", "#7D5BA6", "#3E6FA3", "#9A6B86", "#4C5578", "#5E8C7A"];
const INK = "#14161A", WALNUT = "#525866";
const CHART_SANS = "-apple-system, 'SF Pro Text', 'Inter', system-ui, sans-serif";
const CHART_MONO = "ui-monospace, Menlo, monospace";
const VEGA_CONFIG = {
  background: "transparent",
  view: { stroke: "transparent" },
  font: CHART_SANS,
  axis: {
    labelColor: WALNUT, titleColor: WALNUT,
    gridColor: "#E4E7EC", domainColor: "#D0D5DD", tickColor: "#D0D5DD",
    labelFont: CHART_MONO, titleFont: CHART_SANS,
    labelFontSize: 10, titleFontSize: 10, gridDash: [],
  },
  legend: { labelColor: WALNUT, titleColor: WALNUT },
  title: { color: INK, font: CHART_SANS, fontWeight: 600 },
  range: { category: ATLAS_RANGE },
  mark: { color: "#4A56C7" },
  bar: { fill: "#4A56C7" },
  line: { stroke: "#4A56C7", strokeWidth: 2 },
  point: { fill: "#4A56C7" },
  area: { fill: "#4A56C7", fillOpacity: 0.12 },
  arc: { fill: "#4A56C7" },
  text: { fill: INK, font: CHART_MONO },
};

const DASH_KEY = "ontoforge.build.dashboard";

/* ─────────────────────────────────────────────────────── tiny SVG glyphs */
function ic(paths, size = 16, sw = 1.7) {
  const at = {
    width: String(size), height: String(size), viewBox: "0 0 24 24", fill: "none",
    stroke: "currentColor", "stroke-width": String(sw),
    "stroke-linecap": "round", "stroke-linejoin": "round", "aria-hidden": "true",
  };
  return svgEl("svg", at, ...(Array.isArray(paths) ? paths : [paths]).map((d) =>
    svgEl("path", { d })));
}
const G = {
  spark: () => ic("M12 3l1.9 5.6L19.5 10l-5.6 1.4L12 17l-1.9-5.6L4.5 10l5.6-1.4z", 20, 1.6),
  arrow: () => ic("M5 12h14M13 6l6 6-6 6", 16, 2),
  search: () => svgEl("svg", { width: "16", height: "16", viewBox: "0 0 24 24", fill: "none",
    stroke: "currentColor", "stroke-width": "1.8", "stroke-linecap": "round",
    "stroke-linejoin": "round", "aria-hidden": "true" },
    svgEl("circle", { cx: "11", cy: "11", r: "7" }), svgEl("path", { d: "M20 20l-3.5-3.5" })),
  x: () => ic("M6 6l12 12M18 6L6 18", 11, 2.2),
  measure: () => ic("M4 19V5M4 19h16M8 16v-5M12 16V8M16 16v-9M20 16v-3", 15, 1.8),
  dim: () => svgEl("svg", { width: "15", height: "15", viewBox: "0 0 24 24", fill: "none",
    stroke: "currentColor", "stroke-width": "1.8", "stroke-linecap": "round",
    "stroke-linejoin": "round", "aria-hidden": "true" },
    svgEl("rect", { x: "3", y: "4", width: "18", height: "16", rx: "2" }),
    svgEl("path", { d: "M3 10h18M9 10v10" })),
  filter: () => ic("M3 5h18l-7 8v6l-4 2v-8z", 15, 1.8),
  chart: () => ic("M4 18l5-6 4 4 7-9", 15, 1.8),
  download: () => ic("M12 3v12M8 11l4 4 4-4M5 21h14", 14, 1.8),
  pin: () => ic("M5 12V5h7M5 5l7 7M19 12v7h-7M19 19l-7-7", 14, 1.8),
  shield: () => ic(["M12 3l8 4v5c0 5-3.5 8-8 9-4.5-1-8-4-8-9V7z", "M9 12l2 2 4-4"], 14, 1.7),
  grid: () => svgEl("svg", { width: "18", height: "18", viewBox: "0 0 24 24", fill: "none",
    stroke: "currentColor", "stroke-width": "1.7", "stroke-linecap": "round",
    "stroke-linejoin": "round", "aria-hidden": "true" },
    svgEl("rect", { x: "3", y: "3", width: "8", height: "10", rx: "1.5" }),
    svgEl("rect", { x: "13", y: "3", width: "8", height: "6", rx: "1.5" }),
    svgEl("rect", { x: "13", y: "11", width: "8", height: "10", rx: "1.5" }),
    svgEl("rect", { x: "3", y: "15", width: "8", height: "6", rx: "1.5" })),
  plus: () => ic("M12 5v14M5 12h14", 16, 1.8),
};

/* chart-type icons for the segmented control */
const CHART_ICONS = {
  line: () => ic("M4 17l5-6 4 4 7-9", 17, 1.8),
  bar: () => ic("M5 20V9M10 20V5M15 20v-8M20 20v-4", 17, 1.8),
  area: () => ic("M4 18l5-6 4 3 7-8v11z", 17, 1.8),
  table: () => svgEl("svg", { width: "17", height: "17", viewBox: "0 0 24 24", fill: "none",
    stroke: "currentColor", "stroke-width": "1.8", "stroke-linecap": "round",
    "stroke-linejoin": "round", "aria-hidden": "true" },
    svgEl("rect", { x: "4", y: "4", width: "16", height: "16", rx: "1.5" }),
    svgEl("path", { d: "M4 10h16M4 15h16M10 4v16" })),
  kpi: () => ic("M8 8l2-1v9M14 7h3v4h-3v6h3", 17, 1.8),
};
const CHART_KINDS = [
  ["line", "Line"], ["bar", "Bar"], ["area", "Area"], ["table", "Table"], ["kpi", "Big number"],
];

/* ── glyph for a field result row (measure agg vs dimension) */
function fieldGlyph(f) {
  if (f.kind === "measure") {
    const a = (f.agg || "").toLowerCase();
    return a === "avg" ? "x̄" : a === "count" ? "#" : a === "min" ? "↓" : a === "max" ? "↑" : "∑";
  }
  return f.dim_kind === "temporal" ? "◷" : f.dim_kind === "link" ? "⇄" : "⌗";
}

export function createBuildSurface({ bus }) {
  let pane = null;
  let built = false;

  // the current resolved view (the last successful /api/view result)
  let view = null;        // S.ViewOut
  let spec = null;        // S.ViewSpec (drives shelf edits)
  let lastText = "";       // the last NL utterance entered

  // facet/search state for the field panel
  const fieldState = { q: "", type: "", domain: "", dataset: "" };

  // DOM handles (re-created on each renderBuilder)
  let nlInput = null, parsedLine = null;
  let shelvesBox = null, chartCard = null, dashBox = null;
  let fieldSearchInput = null, fieldFacetsBox = null, fieldResultsBox = null, fieldCountEl = null;

  /* ════════════════════════════════════════════════ /api/view (the engine) */

  /** Run a view from free text. */
  async function runText(text) {
    text = String(text || "").trim();
    if (!text) return;
    lastText = text;
    await runView({ text });
  }

  /** Run a view from the current (edited) spec. Text may still add filters. */
  async function runSpec() {
    if (!spec) return;
    await runView({ spec });
  }

  /** POST /api/view and re-render the spec/shelves/chart from the result. */
  async function runView(body) {
    setChartBusy();
    let out;
    try {
      out = await api("/api/view", { ...body, limit: 5000 });
    } catch (e) {
      view = null;
      renderChart(errorNote(e));
      return;
    }
    if (out.abstained) {
      view = null; spec = null;
      renderParsed(null);
      renderShelves();
      renderChart(el("div", { class: "build-empty" },
        el("div", { class: "build-empty-h" }, "Nothing to build yet"),
        el("p", { class: "build-empty-p" },
          out.abstain_reason || "the data isn't modeled yet — open Studio to wire up data")));
      return;
    }
    if (out.clarification) {
      view = null;
      renderClarification(out);
      return;
    }
    view = out;
    spec = out.spec || spec;
    renderParsed(out);
    renderShelves();
    renderChart();
  }

  /* ── the parsed-spec confirmation line under the view bar */
  function renderParsed(out) {
    clear(parsedLine);
    if (!out) {
      parsedLine.append(
        el("span", { class: "parsed-dot dim" }),
        el("span", {}, "Describe a view above, or click a field on the left."));
      return;
    }
    const s = out.spec || {};
    const n = (a) => (Array.isArray(a) ? a.length : 0);
    const bits = [];
    bits.push("1 measure");
    bits.push(`${n(s.breakdowns)} breakdown${n(s.breakdowns) === 1 ? "" : "s"}`);
    bits.push(`${n(s.filters)} filter${n(s.filters) === 1 ? "" : "s"}`);
    parsedLine.append(
      el("span", { class: "parsed-dot" }),
      el("b", {}, "Understood."),
      el("span", {}, ` Mapped to ${bits.join(", ")} and a ${s.viz || "kpi"} chart — tweak the shelves below, or keep typing.`));
  }

  /* ── highlight the parsed tokens inside the view bar (a calm confirmation) */
  function highlightNl(out) {
    if (!nlInput || !out || !out.spec) return;
    const s = out.spec;
    const tokens = [];
    if (s.measure && s.measure.prop) tokens.push(s.measure.prop.replace(/_/g, " "));
    for (const b of (s.breakdowns || [])) tokens.push(b.prop.replace(/_/g, " "));
    for (const f of (s.filters || [])) if (f.value != null) tokens.push(String(f.value));
    nlInput.dataset.tokens = tokens.join("|");
  }

  /* ════════════════════════════════════════════════ the TABLEAU shelves */

  function renderShelves() {
    clear(shelvesBox);
    const s = spec || {};
    shelvesBox.append(
      shelfRow("measure", "Measure", G.measure(), measurePills(s)),
      shelfRow("breakdown", "Break down by", G.dim(), breakdownPills(s)),
      shelfRow("filter", "Filter", G.filter(), filterPills(s)),
      chartTypeRow(s));
  }

  function shelfRow(key, label, glyph, dropChildren) {
    return el("div", { class: `shrow shrow-${key}` },
      el("div", { class: "shlab" }, el("span", { class: "shlab-ic" }, glyph), label),
      el("div", { class: "shdrop", dataset: { shelf: key } }, dropChildren));
  }

  function measurePills(s) {
    const m = s.measure;
    if (!m || (!m.prop && m.agg !== "count")) {
      return el("span", { class: "pill ghost" }, "pick a measure →");
    }
    const agg = (m.agg || "count").toUpperCase();
    return el("span", { class: "pill pill-measure" },
      el("span", { class: "pill-agg" }, agg),
      el("code", {}, m.prop || s.class_name || "rows"),
      m.unit ? el("span", { class: "pill-op" }, `· ${m.unit}`) : null,
      removeBtn(() => { /* measure is required; clearing it clears the view */
        spec = null; view = null; renderParsed(null); renderShelves();
        renderChart(buildHint());
      }));
  }

  function breakdownPills(s) {
    const out = [];
    (s.breakdowns || []).forEach((b, i) => {
      out.push(el("span", { class: "pill pill-dim" },
        el("code", {}, b.prop),
        el("span", { class: "pill-op" }, `· ${b.kind === "temporal" ? "time" : b.kind === "link" ? "link" : "category"}`),
        removeBtn(() => { spec.breakdowns.splice(i, 1); runSpec(); })));
    });
    out.push(el("span", { class: "pill ghost" }, "+ add a breakdown"));
    return out;
  }

  function filterPills(s) {
    const out = [];
    (s.filters || []).forEach((f, i) => {
      out.push(el("span", { class: "pill pill-filter" },
        el("code", {}, f.prop),
        el("span", { class: "pill-op" }, f.op),
        el("span", { class: "pill-v" }, JSON.stringify(f.value)),
        removeBtn(() => { spec.filters.splice(i, 1); runSpec(); })));
    });
    out.push(el("span", { class: "pill ghost" }, "+ add a filter"));
    return out;
  }

  function chartTypeRow(s) {
    const seg = el("div", { class: "chartseg" });
    for (const [kind, label] of CHART_KINDS) {
      const on = (effectiveViz(s)) === kind;
      seg.append(el("button", {
        class: `cseg${on ? " on" : ""}`, type: "button", title: label,
        "aria-pressed": on ? "true" : "false",
        onclick: () => setViz(kind),
      }, CHART_ICONS[kind]()));
    }
    return el("div", { class: "shrow shrow-charttype" },
      el("div", { class: "shlab" }, el("span", { class: "shlab-ic" }, G.chart()), "Chart type"),
      el("div", { class: "shdrop shdrop-solid" }, seg));
  }

  // local viz override so Table/Big-number can recolor an existing view without
  // a server round-trip (the backend resolves kpi/bar/line; table & kpi are
  // client renders over the same executed rows).
  let vizOverride = null;
  function effectiveViz(s) {
    if (vizOverride) return vizOverride;
    return (s && s.viz) || "kpi";
  }
  function setViz(kind) {
    vizOverride = kind;
    renderShelves();
    if (view) renderChart();
  }

  function removeBtn(onRemove) {
    return el("button", { class: "pill-x", type: "button", "aria-label": "remove",
      onclick: (e) => { e.stopPropagation(); onRemove(); } }, G.x());
  }

  /* ────────── adding a field from the search panel onto the right shelf */
  function addField(f) {
    if (f.kind === "measure") {
      // a new measure starts a fresh spec on that field's owning class
      spec = {
        class_uri: f.on_class, class_name: f.dataset,
        measure: { prop: f.prop, agg: f.agg || "sum", unit: f.unit || null },
        breakdowns: (spec && spec.class_uri === f.on_class ? spec.breakdowns : []) || [],
        filters: (spec && spec.class_uri === f.on_class ? spec.filters : []) || [],
        viz: "kpi",
      };
      vizOverride = null;
      runSpec();
      return;
    }
    // a dimension: needs a measure on the SAME class to break down
    if (!spec || !spec.class_uri) {
      toast("pick a measure first — then break it down", { kind: "warn" });
      return;
    }
    if (spec.class_uri !== f.on_class) {
      toast(`"${f.prop}" lives on ${f.dataset}, not the current measure's dataset`, { kind: "warn" });
      return;
    }
    if ((spec.breakdowns || []).some((b) => b.prop === f.prop)) return;
    spec.breakdowns = [...(spec.breakdowns || []), { prop: f.prop, kind: f.dim_kind || "categorical" }];
    vizOverride = null;
    runSpec();
  }

  /* ════════════════════════════════════════════════ the CHART (vendored Vega) */

  function buildHint() {
    return el("div", { class: "build-empty" },
      el("div", { class: "build-empty-h" }, "Describe a view, or click a field"),
      el("p", { class: "build-empty-p" },
        "Type what you want to see in the bar above (e.g. “total cost by month”), or pick a measure from the field panel on the left."));
  }
  function setChartBusy() {
    if (!chartCard) return;
    clear(chartCard).append(el("div", { class: "skeleton-card", "aria-busy": "true" }));
  }

  function renderChart(replacement) {
    if (!chartCard) return;
    clear(chartCard);
    if (replacement) { chartCard.append(replacement); return; }
    if (!view) { chartCard.append(buildHint()); return; }

    const s = view.spec || {};
    const title = chartTitle(s);
    const head = el("div", { class: "cch" },
      el("div", {},
        el("div", { class: "cctitle" }, title),
        el("div", { class: "ccsub" }, view.plain_english || "")),
      el("div", { class: "ccactions" },
        el("button", { class: "iconbtn", type: "button",
          onclick: () => downloadViewCsv() },
          el("span", { class: "iconbtn-ic" }, G.download()), "Extract CSV"),
        el("button", { class: "iconbtn", type: "button",
          onclick: () => pinCurrent() },
          el("span", { class: "iconbtn-ic" }, G.pin()), "Pin to dashboard")));
    chartCard.append(head);

    const body = el("div", { class: "chart-body" });
    chartCard.append(body);
    renderViz(body, view, effectiveViz(s));

    // provenance line — every cell traces to source rows
    const nCites = (view.citations || []).length;
    chartCard.append(el("div", { class: "prov" },
      el("span", { class: "prov-ic" }, G.shield()),
      el("span", {},
        "Every value traces back to "),
      el("b", {}, fmt(nCites)),
      el("span", {}, ` source record${nCites === 1 ? "" : "s"} · `),
      el("a", { class: "prov-link", href: "#", onclick: (e) => {
        e.preventDefault();
        bus.emit("mode:goto", { mode: "studio", panel: "observatory" });
      } }, "where this came from →")));
  }

  function chartTitle(s) {
    const m = s.measure || {};
    const meas = m.prop ? m.prop.replace(/_/g, " ") : "count";
    let t = meas.charAt(0).toUpperCase() + meas.slice(1);
    if ((s.breakdowns || []).length) t += ` by ${s.breakdowns[0].prop.replace(/_/g, " ")}`;
    return t;
  }

  function renderViz(mount, out, viz) {
    const rows = out.rows || [];
    const cols = out.columns || [];
    if (!rows.length) {
      mount.append(el("div", { class: "empty-note" }, "no rows matched this view"));
      return;
    }
    if (viz === "table") { renderTable(mount, cols, rows); return; }
    if (viz === "kpi") { renderKpi(mount, cols, rows); return; }
    // line / bar / area → Vega over the executed rows
    renderVega(mount, out, viz);
  }

  function renderKpi(mount, cols, rows) {
    // the headline value is the (single) value column of the first/only row
    const valIdx = cols.length - 1;
    const v = rows[0] ? rows[0][valIdx] : null;
    mount.append(el("div", { class: "kpi-wrap" },
      el("div", { class: "kpi-num mono" }, v == null ? "∅" : fmt(v)),
      el("div", { class: "kpi-cap" }, cols[valIdx] || "value")));
  }

  function renderTable(mount, cols, rows) {
    const head = el("tr", {}, cols.map((c) => el("th", {}, c)));
    const body = rows.slice(0, 200).map((r) =>
      el("tr", {}, r.map((cell, i) => el("td", {
        class: i === cols.length - 1 ? "num" : "",
      }, cell == null ? "∅" : (typeof cell === "number" ? fmt(cell) : String(cell))))));
    mount.append(el("div", { class: "answer-table-wrap" },
      el("table", { class: "data" }, el("thead", {}, head), el("tbody", {}, body))));
  }

  function renderVega(mount, out, viz) {
    // start from the backend's ready-to-render Vega-Lite spec (data filled),
    // overriding only the mark when the user flipped bar↔line↔area locally.
    let vspec = out.vega || {};
    vspec = { ...vspec };
    if (viz === "area") vspec.mark = { type: "area", line: true, point: true };
    else if (viz === "bar") vspec.mark = { type: "bar" };
    else if (viz === "line") vspec.mark = { type: "line", point: true };
    vspec.width = "container";
    vspec.height = 300;
    const target = el("div", { class: "chart-vega" });
    mount.append(target);
    if (typeof window.vegaEmbed === "function") {
      window.vegaEmbed(target, vspec, { actions: false, renderer: "svg", config: VEGA_CONFIG })
        .catch((e) => { target.replaceWith(errorNote(e)); });
    } else {
      mount.append(el("div", { class: "offline-note" },
        "chart renderer unavailable — showing the table"));
      renderTable(mount, out.columns || [], out.rows || []);
      target.remove();
    }
  }

  /* ── clarification: ONE question + options (never a confident guess) */
  function renderClarification(out) {
    renderParsed(null);
    const box = el("div", { class: "clarify-card" },
      el("div", { class: "clarify-h" }, el("span", { class: "clarify-ic" }, G.spark()), "One quick question"),
      el("p", { class: "clarify-q" }, out.clarification || "Which measure did you mean?"));
    const opts = el("div", { class: "clarify-opts" });
    for (const o of (out.options || [])) {
      opts.append(el("button", { class: "chip clarify-opt", type: "button",
        onclick: () => runText(o) }, o));
    }
    box.append(opts);
    renderChart(box);
  }

  /* ════════════════════════════════════════════════ /api/extract (CSV) */

  function viewExtractCols() {
    if (!spec) return [];
    const cols = [];
    if (spec.measure && spec.measure.prop) cols.push(spec.measure.prop);
    for (const b of (spec.breakdowns || [])) if (b.kind !== "link") cols.push(b.prop);
    return cols;
  }

  async function downloadViewCsv() {
    if (!spec || !spec.class_uri) { toast("build a view first", { kind: "warn" }); return; }
    await downloadCsv(spec.class_uri, viewExtractCols(),
      (spec.filters || []).map((f) => ({ prop: f.prop, op: f.op, value: f.value })),
      `${spec.class_name || "extract"}.csv`);
  }

  async function downloadCsv(typeUri, columns, filters, filename) {
    try {
      const res = await fetch(`/api/extract?format=csv`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ type_uri: typeUri, filters: filters || [], columns, limit: 100000 }),
      });
      if (!res.ok) throw new Error(`${res.status}`);
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = el("a", { href: url, download: filename || "extract.csv" });
      document.body.append(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      toast("CSV downloaded", { kind: "ok" });
    } catch (e) {
      toast("extract not available — use the CLI", { kind: "warn" });
    }
  }

  /* ════════════════════════════════════════════════ the FACETED field panel */

  const refreshFieldsDebounced = debounce(() => refreshFields(), 200);

  async function refreshFields() {
    if (!fieldResultsBox) return;
    const params = new URLSearchParams();
    if (fieldState.q) params.set("q", fieldState.q);
    if (fieldState.type) params.set("type", fieldState.type);
    if (fieldState.domain) params.set("domain", fieldState.domain);
    if (fieldState.dataset) params.set("dataset", fieldState.dataset);
    params.set("limit", "60");
    clear(fieldResultsBox).append(el("div", { class: "skeleton", style: "width:60%;margin:1rem 1rem" }));
    let out;
    try {
      out = await api(`/api/fields?${params.toString()}`, undefined, "GET");
    } catch (e) {
      clear(fieldResultsBox).append(errorNote(e));
      return;
    }
    renderFacets(out.facets || {});
    renderFieldCount(out);
    renderFieldResults(out);
  }

  function renderFieldCount(out) {
    if (!fieldCountEl) return;
    clear(fieldCountEl);
    const facets = out.facets || {};
    const mC = facetTotal(facets.kind, "measure");
    const dC = facetTotal(facets.kind, "dimension");
    const dsC = (facets.dataset || []).length;
    fieldCountEl.append(`${fmt(mC)} measures · ${fmt(dC)} dimensions`);
    if (fieldSearchInput) {
      fieldSearchInput.placeholder = `search ${fmt(mC + dC)} fields across ${fmt(dsC)} datasets…`;
    }
  }
  function facetTotal(arr, value) {
    const f = (arr || []).find((x) => x.value === value);
    return f ? f.count : 0;
  }

  function renderFacets(facets) {
    clear(fieldFacetsBox);
    // Type — a segmented Measures / Dimensions / All
    fieldFacetsBox.append(facetSeg("Type", [
      ["measure", "Measures"], ["dimension", "Dimensions"], ["", "All"],
    ], fieldState.type, (v) => { fieldState.type = v; refreshFields(); }));
    // Domain — facet pills with counts
    if ((facets.domain || []).length) {
      fieldFacetsBox.append(facetTags("Domain", facets.domain, fieldState.domain,
        (v) => { fieldState.domain = (fieldState.domain === v ? "" : v); refreshFields(); }));
    }
    // Dataset — facet pills with counts
    if ((facets.dataset || []).length) {
      fieldFacetsBox.append(facetTags("Dataset", facets.dataset, fieldState.dataset,
        (v) => { fieldState.dataset = (fieldState.dataset === v ? "" : v); refreshFields(); }));
    }
  }

  function facetSeg(title, options, current, onPick) {
    const seg = el("div", { class: "fseg" });
    for (const [val, label] of options) {
      const on = current === val;
      seg.append(el("button", {
        class: `fseg-b${on ? " on" : ""}`, type: "button",
        "aria-pressed": on ? "true" : "false",
        onclick: () => onPick(val),
      }, label));
    }
    return el("div", { class: "frow" }, el("div", { class: "fttl" }, title), seg);
  }

  function facetTags(title, counts, current, onPick) {
    const tags = el("div", { class: "ftags" });
    for (const c of counts.slice(0, 8)) {
      const on = current === c.value;
      tags.append(el("button", {
        class: `ftag${on ? " on" : ""}`, type: "button",
        title: c.label || c.value, "aria-pressed": on ? "true" : "false",
        onclick: () => onPick(c.value),
      }, c.label || c.value, el("span", { class: "ftag-n" }, fmt(c.count))));
    }
    if (counts.length > 8) {
      tags.append(el("span", { class: "ftag ftag-more" }, `+ ${counts.length - 8}`));
    }
    return el("div", { class: "frow" }, el("div", { class: "fttl" }, title), tags);
  }

  function renderFieldResults(out) {
    clear(fieldResultsBox);
    const fields = out.fields || [];
    if (!fields.length) {
      fieldResultsBox.append(el("div", { class: "empty-note" }, "no fields match — clear a filter"));
      return;
    }
    // group by owning dataset (the result list reads as datasets, each ranked)
    const groups = new Map();
    for (const f of fields) {
      const k = f.dataset || f.on_class;
      if (!groups.has(k)) groups.set(k, { dataset: f.dataset, on_class: f.on_class, rows: f.rows, fields: [] });
      groups.get(k).fields.push(f);
    }
    for (const grp of groups.values()) {
      const g = el("div", { class: "rgrp" });
      g.append(el("div", { class: "rgrp-h" },
        el("span", { class: "rgrp-name" }, grp.dataset || "—"),
        el("span", { class: "rgrp-rows mono" }, `${fmt(grp.rows)} rows`)));
      for (const f of grp.fields) g.append(fieldRow(f));
      fieldResultsBox.append(g);
    }
    if (out.total > out.returned) {
      fieldResultsBox.append(el("div", { class: "morrow" },
        ic("M6 9l6 6 6-6", 14, 2),
        `${fmt(out.total - out.returned)} more matches — ranked by criticality`));
    }
  }

  function fieldRow(f) {
    const pct = Math.max(0, Math.min(1, Number(f.score || f.criticality) || 0));
    const sub = f.kind === "measure"
      ? `${f.agg || "sum"}${f.unit ? ` · ${f.unit}` : ""}`
      : `${f.dim_kind || "category"}${f.link_target ? ` → ${f.link_target}` : ""}`;
    return el("button", {
      class: "ritem", type: "button",
      title: `add ${f.label || f.prop} to the ${f.kind === "measure" ? "Measure" : "Break down by"} shelf`,
      onclick: () => addField(f),
    },
      el("span", { class: `ric ${f.kind === "measure" ? "ric-m" : "ric-d"}` }, fieldGlyph(f)),
      el("span", { class: "rmeta" },
        el("span", { class: "rname" }, el("code", {}, f.prop), `  ${sub}`),
        el("span", { class: "rsub" }, f.label && f.label !== f.prop ? f.label : f.dataset)),
      el("span", { class: "crit" },
        el("span", { class: "crit-sc mono" }, pct.toFixed(2)),
        el("span", { class: "cbar" }, el("i", { style: `width:${(pct * 100).toFixed(0)}%` }))));
  }

  /* ════════════════════════════════════════════════ the DASHBOARD grid */

  function loadDash() {
    return store.get(DASH_KEY, { name: "Untitled dashboard", panels: [] });
  }
  function saveDash(d) { store.set(DASH_KEY, d); }

  function pinCurrent() {
    if (!view || !spec) { toast("build a view first", { kind: "warn" }); return; }
    const d = loadDash();
    d.panels.push({
      id: `p${Date.now().toString(36)}`,
      title: chartTitle(view.spec || spec),
      def: view.plain_english || "",
      viz: effectiveViz(view.spec || spec),
      columns: view.columns || [],
      rows: (view.rows || []).slice(0, 200),
      vega: view.vega || {},
      class_uri: spec.class_uri,
      class_name: spec.class_name,
      cols: viewExtractCols(),
      filters: (spec.filters || []).map((f) => ({ prop: f.prop, op: f.op, value: f.value })),
      cites: (view.citations || []).length,
    });
    saveDash(d);
    renderDashboard();
    toast("pinned to dashboard", { kind: "ok" });
  }

  function renderDashboard() {
    if (!dashBox) return;
    clear(dashBox);
    const d = loadDash();

    // header — nameable + saved badge
    const nameInput = el("input", {
      class: "dashname-input", type: "text", value: d.name, spellcheck: "false",
      "aria-label": "dashboard name",
      oninput: () => { const cur = loadDash(); cur.name = nameInput.value; saveDash(cur); },
    });
    const head = el("div", { class: "dashhead" },
      el("div", { class: "dashname" }, el("span", { class: "dashname-ic" }, G.grid()), nameInput,
        el("span", { class: "dash-saved mono" }, `saved · ${d.panels.length} panel${d.panels.length === 1 ? "" : "s"}`)),
      el("div", { class: "dashactions" },
        el("button", { class: "iconbtn", type: "button", onclick: () => exportBundle() },
          el("span", { class: "iconbtn-ic" }, G.download()), "Export bundle")));
    dashBox.append(el("span", { class: "section-label" }, "Dashboard"), head);

    const grid = el("div", { class: "dashgrid" });
    d.panels.forEach((p, i) => grid.append(dashPanel(p, i)));
    dashBox.append(grid);

    // add-panel affordance — pin the view above, or describe one
    dashBox.append(el("button", { class: "addpanel", type: "button",
      onclick: () => pinCurrent() },
      el("span", { class: "addpanel-ad" }, G.plus()),
      el("span", {}, "Add panel — pin the view above")));
  }

  function dashPanel(p, idx) {
    const panel = el("div", { class: "dpanel", draggable: "true", dataset: { idx: String(idx) } });
    // drag to rearrange
    panel.addEventListener("dragstart", (e) => {
      e.dataTransfer.setData("text/plain", String(idx)); panel.classList.add("dragging");
    });
    panel.addEventListener("dragend", () => panel.classList.remove("dragging"));
    panel.addEventListener("dragover", (e) => { e.preventDefault(); panel.classList.add("drop-into"); });
    panel.addEventListener("dragleave", () => panel.classList.remove("drop-into"));
    panel.addEventListener("drop", (e) => {
      e.preventDefault(); panel.classList.remove("drop-into");
      const from = parseInt(e.dataTransfer.getData("text/plain"), 10);
      if (Number.isNaN(from) || from === idx) return;
      const d = loadDash();
      const [moved] = d.panels.splice(from, 1);
      d.panels.splice(idx, 0, moved);
      saveDash(d); renderDashboard();
    });

    panel.append(el("div", { class: "dp-h" },
      el("div", {},
        el("div", { class: "dp-ttl" }, p.title || "Untitled"),
        el("div", { class: "dp-def" }, p.def || "")),
      el("button", { class: "dp-grip", type: "button", title: "remove panel",
        onclick: () => { const d = loadDash(); d.panels.splice(idx, 1); saveDash(d); renderDashboard(); } },
        G.x())));

    const body = el("div", { class: "dp-body" });
    panel.append(body);
    renderViz(body, { columns: p.columns, rows: p.rows, vega: p.vega }, p.viz);

    panel.append(el("div", { class: "dp-foot" },
      el("span", { class: "dp-prov" }, G.shield(), `${fmt(p.cites)} source rows`),
      el("button", { class: "dp-ext", type: "button",
        onclick: () => downloadCsv(p.class_uri, p.cols, p.filters, `${p.class_name || "panel"}.csv`) },
        G.download(), "Extract")));
    return panel;
  }

  async function exportBundle() {
    try {
      await api("/api/export", {});
      toast("dataset exported — portable bundle ready", { kind: "ok" });
    } catch (e) {
      toast(e.status === 404 || e.status === 405
        ? "export not exposed — use `ontoforge export`"
        : "export failed — see the CLI", { kind: "warn" });
    }
  }

  /* ════════════════════════════════════════════════ layout */

  function renderBuilder() {
    clear(pane);
    vizOverride = null;

    /* ── VIEW BAR (natural language) ── */
    nlInput = el("input", {
      class: "nl-input", type: "text", spellcheck: "false",
      placeholder: "describe the view — e.g. “total cost by month”",
      onkeydown: (e) => { if (e.key === "Enter") { e.preventDefault(); runText(nlInput.value); } },
    });
    const buildBtn = el("button", { class: "bld", type: "button",
      onclick: () => runText(nlInput.value) }, "Build view", G.arrow());
    const viewbar = el("div", { class: "viewbar" },
      el("span", { class: "viewbar-ic" }, G.spark()), nlInput, buildBtn);
    parsedLine = el("div", { class: "parsed" });
    renderParsed(null);
    const stage = el("div", { class: "build-stage" },
      el("div", { class: "eyebrow" }, "describe the view"),
      viewbar, parsedLine);

    /* ── LEFT: the faceted field-search panel ── */
    fieldCountEl = el("span", { class: "flabel-count mono" });
    fieldSearchInput = el("input", {
      class: "fsearch-input", type: "text", spellcheck: "false",
      placeholder: "search fields…",
      oninput: () => { fieldState.q = fieldSearchInput.value.trim(); refreshFieldsDebounced(); },
    });
    fieldFacetsBox = el("div", { class: "facets" });
    fieldResultsBox = el("div", { class: "results" });
    const panel = el("div", { class: "panel fpanel" },
      el("div", { class: "fhead" },
        el("div", { class: "flabel" }, el("span", {}, "Fields"), fieldCountEl),
        el("div", { class: "fsearch" }, el("span", { class: "fsearch-ic" }, G.search()), fieldSearchInput)),
      fieldFacetsBox, fieldResultsBox);

    /* ── RIGHT: shelves + chart + dashboard ── */
    shelvesBox = el("div", { class: "shelves" });
    renderShelves();
    chartCard = el("div", { class: "chartcard" });
    renderChart(buildHint());
    dashBox = el("div", { class: "dashboard" });
    const right = el("div", { class: "build-right" }, shelvesBox, chartCard, dashBox);

    pane.append(stage, el("div", { class: "build-grid" }, panel, right));

    refreshFields();
    renderDashboard();
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
    let onto = null;
    try { onto = await loadOntology(); } catch { /* not ready */ }
    onto = onto || ontologyNow();
    built = !!(onto && onto.classes && onto.classes.length);
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
    enter() { /* lazy — the builder reflects the last view */ },
    show(opts = {}) {
      if (opts.utterance && nlInput) { nlInput.value = opts.utterance; runText(opts.utterance); }
      else if (opts.utterance) { lastText = opts.utterance; }
    },
  };
}
