/* INLINE ARTIFACT RENDERERS — the conversation-first shell's rich cards.
   Each agent turn narrates briefly, then mounts one or more of these artifact
   cards into the thread's .abody container. They REUSE the existing engine
   contracts and render logic: the answer card (ask.js), the Vega chart
   (build.js VEGA_CONFIG + window.vegaEmbed), the confirm cards (review.js +
   atlas likely-tier evidence), the op preview (console.js interpret/apply/undo
   discipline incl. the op_token echo), and the data map (constellation.js
   engine). Each returns a DOM node built ONLY via core.js el()/svgEl()/
   createTextNode — never innerHTML (test_spa greps the whole non-vendor
   payload). The mock target is /static (mock_agent.html): an .art card with an
   .arthead, a kind-specific body, and a calm provenance disclosure.

   SECURITY INVARIANT: every piece of API data enters the DOM through el()/
   svgEl()/createTextNode. Keyless / offline / deterministic; charts use the
   vendored Vega only. */

import {
  el, svgEl, clear, fmt, api, errorNote, toast,
} from "./core.js";
import { createConstellation } from "./constellation.js";

const ATOM_URI_RE = /^atom:\/\/([^/]+)\/([^/]+)\/(.+?)(?:#(.*))?$/;

/* the COOL professional chart theme — kept in sync with build.js / core.js
   ATLAS_HUES. Single-series mark is the indigo data hue; teal anchors positive. */
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

/* ─────────────────────────────────────────────── shared tiny SVG glyphs */
function ic(paths, size = 14, sw = 1.6) {
  return svgEl("svg", {
    width: String(size), height: String(size), viewBox: "0 0 24 24", fill: "none",
    stroke: "currentColor", "stroke-width": String(sw),
    "stroke-linecap": "round", "stroke-linejoin": "round", "aria-hidden": "true",
  }, ...(Array.isArray(paths) ? paths : [paths]).map((d) => svgEl("path", { d })));
}
const GLYPH = {
  answer: () => ic("M4 19V5M4 19h16M8 16v-5M12 16V8M16 16v-9M20 16v-3"),
  chart: () => ic("M4 18l5-6 4 4 7-9"),
  join: () => svgEl("svg", { width: "14", height: "14", viewBox: "0 0 24 24", fill: "none",
    stroke: "currentColor", "stroke-width": "1.6", "stroke-linecap": "round",
    "stroke-linejoin": "round", "aria-hidden": "true" },
    svgEl("path", { d: "M7 7h6a4 4 0 014 4M17 17h-6a4 4 0 01-4-4" }),
    svgEl("circle", { cx: "5", cy: "7", r: "2" }), svgEl("circle", { cx: "19", cy: "17", r: "2" })),
  op: () => ic("M4 7V4h16v3M9 20h6M12 4v16"),
  map: () => svgEl("svg", { width: "14", height: "14", viewBox: "0 0 24 24", fill: "none",
    stroke: "currentColor", "stroke-width": "1.6", "stroke-linecap": "round",
    "stroke-linejoin": "round", "aria-hidden": "true" },
    svgEl("circle", { cx: "6", cy: "6", r: "2.5" }), svgEl("circle", { cx: "18", cy: "8", r: "2.5" }),
    svgEl("circle", { cx: "9", cy: "18", r: "2.5" }), svgEl("path", { d: "M8 7l8 .8M8 8l1 8" })),
  shield: () => ic(["M12 3l8 4v5c0 5-3.5 8-8 9-4.5-1-8-4-8-9V7z", "M9 12l2 2 4-4"]),
  check: () => ic("M5 12l5 5 9-11", 14, 2),
  chev: () => svgEl("svg", { width: "13", height: "13", viewBox: "0 0 24 24", fill: "none",
    stroke: "currentColor", "stroke-width": "2", "stroke-linecap": "round",
    "stroke-linejoin": "round", "aria-hidden": "true" }, svgEl("path", { d: "M9 6l6 6-6 6" })),
  cast: () => ic("M5 12h14M13 6l6 6-6 6", 15, 1.8),
  pin: () => ic("M5 12V5h7M5 5l7 7M19 12v7h-7M19 19l-7-7"),
  download: () => ic("M12 3v12M8 11l4 4 4-4M5 21h14"),
  expand: () => ic(["M8 3H5a2 2 0 00-2 2v3", "M16 3h3a2 2 0 012 2v3",
    "M8 21H5a2 2 0 01-2-2v-3", "M16 21h3a2 2 0 002-2v-3"], 13),
  redo: () => ic(["M3 12a9 9 0 109-9 9 9 0 00-7 3.3", "M3 4v4h4"], 14),
};

/* the shared artifact-card frame: header (kind icon + label + tag) + body */
function artCard(kindGlyph, label, tag, body) {
  const head = el("div", { class: "arthead" },
    el("span", { class: "ic" }, kindGlyph),
    el("span", { class: "lab" }, label),
    el("span", { class: "spacer" }),
    tag ? el("span", { class: "tag mono" }, tag) : null);
  return el("div", { class: "art" }, head, body);
}

/* a suggestion-chip row that re-submits a prompt through the composer */
export function suggestionChips(followups, sendPrompt) {
  const list = (followups || []).filter(Boolean);
  if (!list.length || typeof sendPrompt !== "function") return null;
  const wrap = el("div", { class: "sugg" });
  for (const f of list) {
    wrap.append(el("button", {
      class: "sg", type: "button", title: f, onclick: () => sendPrompt(f),
    }, el("span", {}, f),
      el("span", { class: "ar", "aria-hidden": "true" }, GLYPH.chev())));
  }
  return wrap;
}

/* ════════════════════════════════════════════════ 1 · ANSWER ARTIFACT
   Reuses the ask.js answer contract (AskOut: value/columns/rows/confidence/
   citations/plain_english/question). Builds the mock's .art > .ansbody: a
   bignum (single value) or a data table, a confidence pill, a plain-English
   sub, and the lazy provenance disclosure resolving atoms via /api/atoms. */

function confPill(confidence) {
  const c = Math.max(0, Math.min(1, Number(confidence) || 0));
  const cls = c >= 0.8 ? "confirmed" : c >= 0.5 ? "likely" : "weak";
  const word = c >= 0.8 ? "confirmed" : c >= 0.5 ? "likely" : "below the floor";
  return el("span", { class: `pill ${cls}` },
    el("i", { "aria-hidden": "true" }), `${word} · `, el("b", {}, c.toFixed(2)));
}

function fmtVal(v) {
  if (v === null || v === undefined) return "∅";
  return typeof v === "number" ? fmt(v) : String(v);
}

/* the inline-expandable provenance table — the real cited cells (dataset · row
   · field → value), resolving each atom's RAW uri lazily through /api/atoms. */
function provDisclosure(citations) {
  const flat = [];
  for (const c of citations || []) {
    for (const id of c.atom_ids || []) flat.push({ row: c.row, column: c.column, value: c.value, id });
  }
  if (!flat.length) return null;
  const shown = flat.slice(0, 3);
  const det = el("details", { class: "disc" });
  det.append(el("summary", { class: "disc-h" },
    el("span", { class: "chev", "aria-hidden": "true" }, GLYPH.chev()),
    el("span", { class: "ic" }, GLYPH.shield()),
    el("b", { class: "mono" }, fmt(flat.length)),
    ` source record${flat.length === 1 ? "" : "s"} · where this came from`));
  const table = el("table", { class: "provtable" });
  table.append(el("tr", {},
    el("th", {}, "dataset"), el("th", {}, "row id"), el("th", {}, "field"), el("th", {}, "value")));
  for (const f of shown) {
    const dsCell = el("td", {}, el("span", { class: "ds" }, "resolving…"));
    const rowCell = el("td", {}, `⌗ ${f.id}`);
    table.append(el("tr", {},
      dsCell, rowCell,
      el("td", {}, el("span", { class: "field" }, f.column || "")),
      el("td", {}, fmtVal(f.value))));
    api(`/api/atoms/${encodeURIComponent(f.id)}`)
      .then((atom) => {
        const m = ATOM_URI_RE.exec(atom.uri || "");
        if (m) {
          clear(dsCell).append(el("span", { class: "ds" }, m[2]));
          clear(rowCell).append(m[3]);
        }
      })
      .catch(() => { /* keep the atom id fallback */ });
  }
  det.append(table);
  if (flat.length > shown.length) {
    det.append(el("div", { class: "provmore" }, `+ ${fmt(flat.length - shown.length)} more rows`));
  }
  return det;
}

export function renderAnswerArtifact(art, { sendPrompt } = {}) {
  // honest clarification / abstention come through as text artifacts upstream,
  // but guard here too: a missing value with no rows is a plain note.
  const cols = art.columns || [];
  const rows = art.rows || [];
  const tag = art.plain_english ? truncate(art.plain_english, 48) : (cols[0] || "answer");

  const body = el("div", { class: "ansbody" });
  const cites = new Map();
  for (const c of art.citations || []) cites.set(`${c.row}|${c.column}`, c.atom_ids);

  const single = rows.length === 1 && cols.length === 1;
  if (single) {
    const v = rows[0][0];
    body.append(el("div", { class: "ansrow" },
      el("div", { class: "bignum mono" }, fmtVal(v)),
      confPill(art.confidence)));
  } else if (rows.length) {
    body.append(el("div", { class: "ansrow" },
      el("div", { class: "anstable-head" }, `${fmt(rows.length)} rows`),
      confPill(art.confidence)));
    const thead = el("tr", {}, cols.map((c) => el("th", {}, c)));
    const tbody = rows.slice(0, 50).map((row, ri) =>
      el("tr", {}, row.map((v, ci) => {
        const td = el("td", { class: typeof v === "number" ? "mono" : null }, fmtVal(v));
        return td;
      })));
    body.append(el("div", { class: "anstable" },
      el("table", { class: "data" }, el("thead", {}, thead), el("tbody", {}, tbody))));
  } else {
    body.append(el("div", { class: "ansrow" },
      el("div", { class: "anssub" }, "No rows matched."), confPill(art.confidence)));
  }

  if (art.plain_english) {
    body.append(el("div", { class: "anssub" }, art.plain_english));
  }
  const prov = provDisclosure(art.citations);
  if (prov) body.append(prov);

  return artCard(GLYPH.answer(), "Answer", tag, body);
}

/* ════════════════════════════════════════════════ 2 · CHART ARTIFACT
   Reuses the build.js Vega render path: out.vega is a ready-to-render Vega-Lite
   spec; render it via the vendored window.vegaEmbed with the cool VEGA_CONFIG
   (renderer svg). KPI/table fall back inline. The .artfoot carries pin / extract
   csv / where-this-came-from (extract → /api/extract?format=csv). */

function chartRenderInto(mount, art) {
  const rows = art.rows || [];
  const cols = art.columns || [];
  const viz = (art.spec && art.spec.viz) || "line";
  if (!rows.length) {
    mount.append(el("div", { class: "empty-note" }, "no rows matched this view"));
    return;
  }
  if (viz === "table") { chartTable(mount, cols, rows); return; }
  if (viz === "kpi") {
    const valIdx = cols.length - 1;
    const v = rows[0] ? rows[0][valIdx] : null;
    mount.append(el("div", { class: "kpi-wrap" },
      el("div", { class: "kpi-num mono" }, fmtVal(v)),
      el("div", { class: "kpi-cap" }, cols[valIdx] || "value")));
    return;
  }
  // line / bar / area → the vendored Vega over the executed rows
  let vspec = { ...(art.vega || {}) };
  if (viz === "area") vspec.mark = { type: "area", line: true, point: true };
  else if (viz === "bar") vspec.mark = { type: "bar" };
  else vspec.mark = { type: "line", point: true };
  vspec.width = "container";
  vspec.height = 280;
  const target = el("div", { class: "chart-vega" });
  mount.append(target);
  if (typeof window.vegaEmbed === "function" && art.vega && Object.keys(art.vega).length) {
    window.vegaEmbed(target, vspec, { actions: false, renderer: "svg", config: VEGA_CONFIG })
      .catch(() => { target.replaceWith(chartTableNode(cols, rows)); });
  } else {
    target.replaceWith(chartTableNode(cols, rows));
  }
}

function chartTableNode(cols, rows) {
  const wrap = el("div", { class: "anstable" });
  chartTable(wrap, cols, rows);
  return wrap;
}
function chartTable(mount, cols, rows) {
  const head = el("tr", {}, cols.map((c) => el("th", {}, c)));
  const body = rows.slice(0, 100).map((r) =>
    el("tr", {}, r.map((cell, i) => el("td", {
      class: i === cols.length - 1 ? "mono" : null,
    }, fmtVal(cell)))));
  mount.append(el("table", { class: "data" }, el("thead", {}, head), el("tbody", {}, body)));
}

async function extractCsv(art) {
  const s = art.spec || {};
  const typeUri = s.class_uri;
  if (!typeUri) { toast("nothing to extract", { kind: "warn" }); return; }
  const cols = [];
  if (s.measure && s.measure.prop) cols.push(s.measure.prop);
  for (const b of (s.breakdowns || [])) if (b.kind !== "link") cols.push(b.prop);
  const filters = (s.filters || []).map((f) => ({ prop: f.prop, op: f.op, value: f.value }));
  try {
    const res = await fetch(`/api/extract?format=csv`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ type_uri: typeUri, filters, columns: cols, limit: 100000 }),
    });
    if (!res.ok) throw new Error(`${res.status}`);
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = el("a", { href: url, download: `${s.class_name || "extract"}.csv` });
    document.body.append(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    toast("CSV downloaded", { kind: "ok" });
  } catch {
    toast("extract not available — use the CLI", { kind: "warn" });
  }
}

function chartTitleFor(s) {
  const m = (s && s.measure) || {};
  const meas = m.prop ? m.prop.replace(/_/g, " ") : "count";
  let t = meas.charAt(0).toUpperCase() + meas.slice(1);
  if (s && (s.breakdowns || []).length) t += ` by ${s.breakdowns[0].prop.replace(/_/g, " ")}`;
  return t;
}

export function renderChartArtifact(art, { sendPrompt } = {}) {
  const s = art.spec || {};
  const viz = s.viz || "line";
  const label = viz === "bar" ? "Bar chart" : viz === "area" ? "Area chart"
    : viz === "table" ? "Table" : viz === "kpi" ? "Big number" : "Line chart";
  const tag = chartTitleFor(s);

  const wrap = el("div", { class: "chartwrap" });
  chartRenderInto(wrap, art);

  const nCites = (art.citations || []).length;
  const foot = el("div", { class: "artfoot" },
    el("div", { class: "cap" }, art.plain_english || "",
      nCites ? el("span", {}, " · ", el("b", { class: "mono" }, fmt(nCites)),
        ` source record${nCites === 1 ? "" : "s"}`) : null),
    el("div", { class: "qact" },
      qbtn(GLYPH.pin(), "pin", () => toast("pinned", { kind: "ok" })),
      el("span", { class: "qsep" }),
      qbtn(GLYPH.download(), "extract csv", () => extractCsv(art)),
      el("span", { class: "qsep" }),
      qbtn(GLYPH.shield(), "where this came from",
        () => { if (sendPrompt) sendPrompt("where did this come from?"); })));

  const card = artCard(GLYPH.chart(), label, tag, wrap);
  card.append(foot);
  return card;
}

function qbtn(glyph, label, onClick) {
  return el("button", { class: "qbtn", type: "button", onclick: onClick },
    el("span", { class: "ic" }, glyph), label);
}

/* ════════════════════════════════════════════════ 3 · CONFIRM-JOINS ARTIFACT
   Reuses the review.js verdict POST (/api/review/{decision_id}) for flagged
   items AND composes the atlas likely-tier arcs (each AtlasLink carries
   evidence: coverage/overlap_count/name_similarity) into the richer confirm
   card the mock shows — title "Link A → B on field", coverage bar, a "94% of
   rows match" line, cardinality, Confirm/Reject. The verdict + the link
   confirm both keep their server contracts. */

function shortName(uri) {
  const tail = String(uri || "").split(/[/#]/).filter(Boolean).pop();
  return tail || String(uri || "");
}

function likelyJoinCard(link, onAct) {
  const cov = Math.max(0, Math.min(1, Number(link.evidence && link.evidence.coverage) || 0));
  const score = Math.max(0, Math.min(1, Number(link.score) || 0));
  const card = el("div", { class: "confcard" });
  const meta = el("div", { class: "confmeta" });
  meta.append(el("div", { class: "conftitle" },
    "Link ", el("code", {}, shortName(link.src_class)),
    el("span", { class: "arrow" }, "→"),
    el("code", {}, shortName(link.dst_class)),
    link.src_prop ? [" on ", el("span", { class: "field mono" }, link.src_prop)] : null));
  const sub = el("div", { class: "confsub" },
    el("span", {}, el("b", { class: "mono" }, `${(cov * 100).toFixed(0)}%`), " of rows match"));
  if (link.evidence && link.evidence.overlap_count != null) {
    sub.append(el("span", {}, el("b", { class: "mono" }, fmt(link.evidence.overlap_count)), " shared values"));
  }
  if (link.rel_type) sub.append(el("span", {}, link.rel_type.replace(/_/g, " ")));
  meta.append(sub);
  meta.append(el("div", { class: "confbarwrap" },
    el("div", { class: `confbar${score < 0.9 ? " indigo" : ""}` },
      el("i", { style: `width:${(cov * 100).toFixed(0)}%` })),
    el("span", { class: "confpct mono" }, score.toFixed(2))));

  const btns = el("div", { class: "confbtns" });
  const confirm = el("button", { class: "btn primary", type: "button" },
    el("span", { class: "ic" }, GLYPH.check()), "Confirm");
  const reject = el("button", { class: "btn ghost", type: "button" }, "Reject");
  confirm.addEventListener("click", () => resolveLink(card, btns, link, true, onAct));
  reject.addEventListener("click", () => resolveLink(card, btns, link, false, onAct));
  btns.append(confirm, reject);

  card.append(el("div", { class: "conftop" }, meta, btns));
  return card;
}

/* Confirm a likely join → the engineer link op (interpret → apply, echoing the
   op_token). Reject → a quiet local dismissal (no destructive server call). */
async function resolveLink(card, btns, link, accept, onAct) {
  for (const b of btns.querySelectorAll("button")) b.disabled = true;
  if (!accept) {
    clear(btns).append(el("span", { class: "verdict-result" }, "dismissed"));
    if (onAct) onAct("reject", link);
    return;
  }
  const command = `Link ${shortName(link.src_class)} to ${shortName(link.dst_class)} on ${link.src_prop || ""}`.trim();
  try {
    const res = await api("/api/engineer/interpret", { command });
    if (res && res.op && res.preview) {
      const out = await api("/api/engineer/apply", { op: res.preview.op_token || res.op });
      clear(btns).append(el("span", { class: "verdict-result recalibrated" },
        out && out.blocked ? "below the join floor — not applied" : "confirmed"));
    } else {
      clear(btns).append(el("span", { class: "verdict-result" }, "noted"));
    }
    if (onAct) onAct("accept", link);
  } catch (e) {
    clear(btns).append(errorNote(e));
  }
}

/* a flagged review item (an ER same-thing pair) → the verdict POST, unchanged */
function reviewItemCard(item, onAct) {
  const card = el("div", { class: "confcard" });
  const meta = el("div", { class: "confmeta" });
  meta.append(el("div", { class: "conftitle" },
    "Same thing? ", el("code", {}, item.kind || "match")));
  if (item.rationale) meta.append(el("div", { class: "confsub" }, el("span", {}, item.rationale)));
  const score = Math.max(0, Math.min(1, Number(item.confidence) || 0));
  meta.append(el("div", { class: "confbarwrap" },
    el("div", { class: "confbar indigo" }, el("i", { style: `width:${(score * 100).toFixed(0)}%` })),
    el("span", { class: "confpct mono" }, score.toFixed(2))));

  const btns = el("div", { class: "confbtns" });
  const confirm = el("button", { class: "btn primary", type: "button" },
    el("span", { class: "ic" }, GLYPH.check()), "Confirm");
  const reject = el("button", { class: "btn ghost", type: "button" }, "Not the same");
  async function verdict(v) {
    for (const b of btns.querySelectorAll("button")) b.disabled = true;
    try {
      const out = await api(`/api/review/${encodeURIComponent(item.decision_id)}`, { verdict: v, note: "" });
      clear(btns).append(el("span", { class: `verdict-result${out && out.recalibrated ? " recalibrated" : ""}` },
        v === "accept" ? "confirmed" : "marked not the same"));
      if (onAct) onAct(v, item);
    } catch (e) {
      clear(btns).append(errorNote(e));
    }
  }
  confirm.addEventListener("click", () => verdict("accept"));
  reject.addEventListener("click", () => verdict("reject"));
  btns.append(confirm, reject);

  card.append(el("div", { class: "conftop" }, meta, btns));
  return card;
}

export function renderConfirmArtifact(art, { sendPrompt, onAct } = {}) {
  const items = art.items || [];
  const likely = art.likely_joins || [];
  const total = items.length + likely.length;
  const grid = el("div", { class: "confgrid" });
  if (!total) {
    grid.append(el("div", { class: "confcard" },
      el("div", { class: "empty-note" }, "Nothing to confirm — the engine is confident right now.")));
  } else {
    for (const link of likely.slice(0, 6)) grid.append(likelyJoinCard(link, onAct));
    for (const item of items.slice(0, 6)) grid.append(reviewItemCard(item, onAct));
  }
  const tag = total ? `${Math.min(total, 12)} of ${total}` : "0 pending";
  return artCard(GLYPH.join(), "Confirm joins", tag, grid);
}

/* ════════════════════════════════════════════════ 4 · OP-PREVIEW ARTIFACT
   Reuses the console.js interpret→preview→apply discipline. Given an
   InterpretOut {op, preview}, render the mock's .art "Operation preview"
   with description, affected count, before/after sample, the reversible note,
   and Apply/Cancel. Apply posts {op: preview.op_token} (the echo discipline is
   PRESERVED), offering Undo (/api/engineer/undo). The "nothing has changed yet"
   string and the DESTRUCTIVE set are kept verbatim (test-pinned in console.js;
   mirrored here for the inline card). */

const DESTRUCTIVE = new Set(["merge_entities", "split", "remove", "remove_dataset"]);

export function renderOpPreviewArtifact(art, { sendPrompt } = {}) {
  const op = art.op || {};
  const pv = art.preview || {};
  const destructive = DESTRUCTIVE.has(op.kind);
  const reversible = !destructive;
  const tag = `${(op.kind || "op").replace(/_/g, " ")} · ${reversible ? "reversible" : "review first"}`;

  const body = el("div", { class: "opbody" });
  body.append(el("div", { class: "optitle" },
    pv.description || op.human_summary || "this step would change the model"));

  const meta = el("div", { class: "opmeta" });
  if (pv.affected_count !== undefined && pv.affected_count !== null) {
    meta.append(el("span", { class: "chip" }, el("b", { class: "mono" }, fmt(pv.affected_count)), " records affected"));
  }
  if (op.confidence !== undefined && op.confidence !== null) {
    meta.append(el("span", { class: "chip accent" }, el("b", { class: "mono" }, Number(op.confidence).toFixed(2)), " confidence"));
  }
  if (meta.childNodes.length) body.append(meta);

  // a before/after sample table, when the preview carries sample rows
  const sample = pv.sample || [];
  if (sample.length) {
    const box = el("div", { class: "opsample" });
    box.append(el("div", { class: "sh" },
      el("div", {}, "before"), el("div", {}), el("div", {}, "after")));
    for (const s of sample.slice(0, 4)) {
      let before = s, after = "";
      if (s && typeof s === "object") { before = s.before ?? s.from ?? JSON.stringify(s); after = s.after ?? s.to ?? ""; }
      box.append(el("div", { class: "sr" },
        el("div", { class: "c before mono" }, String(before)),
        el("div", { class: "ar" }, GLYPH.cast()),
        el("div", { class: "c after mono" }, String(after))));
    }
    body.append(box);
  }

  body.append(el("div", { class: "opnote" },
    el("span", { class: "ic" }, GLYPH.redo()),
    el("span", {}, "preview — nothing has changed yet · ",
      destructive
        ? "this can't be undone automatically beyond the last step — review it first"
        : "fully reversible — you can undo this from history at any time")));

  const actions = el("div", { class: "opactions" });
  const apply = el("button", { class: "btn primary", type: "button" },
    el("span", { class: "ic" }, GLYPH.check()), `Apply ${(op.kind || "op").replace(/_/g, " ")}`);
  const cancel = el("button", { class: "btn ghost", type: "button" }, "Cancel");

  async function doApply() {
    apply.disabled = true; cancel.disabled = true;
    clear(actions).append(el("div", { class: "skeleton", style: "width:50%" }));
    try {
      // op_token echo discipline: apply with the token the preview carried
      const out = await api("/api/engineer/apply", { op: pv.op_token || op });
      if (out && out.blocked) {
        clear(actions).append(el("span", { class: "verdict-result" }, "below the join floor — not applied"));
        return;
      }
      const undoToken = out && out.undo_token;
      const done = el("div", { class: "opactions" },
        el("span", { class: "verdict-result recalibrated" }, out && out.human_summary ? out.human_summary : "applied"));
      if (undoToken) {
        const undo = el("button", { class: "btn ghost", type: "button" }, "Undo");
        undo.addEventListener("click", async () => {
          undo.disabled = true;
          try { await api("/api/engineer/undo", { undo_token: undoToken }); undo.replaceWith(el("span", { class: "verdict-result" }, "undone")); toast("undone", { kind: "ok" }); }
          catch { toast("couldn't undo — see the CLI", { kind: "warn" }); }
        });
        done.append(undo);
      }
      clear(actions).append(...done.childNodes);
      toast(out && out.human_summary ? out.human_summary : "applied", { kind: "ok" });
    } catch (e) {
      clear(actions).append(errorNote(e));
    }
  }
  apply.addEventListener("click", doApply);
  cancel.addEventListener("click", () => { apply.disabled = true; cancel.replaceWith(el("span", { class: "verdict-result" }, "cancelled")); });
  actions.append(apply, cancel);
  body.append(actions);

  return artCard(GLYPH.op(), "Operation preview", tag, body);
}

/* ════════════════════════════════════════════════ 5 · DATA-MAP ARTIFACT
   Reuses the constellation.js engine. We build the DOM scaffold the engine
   expects ({svg, wrap, card, evCard, svgEl, el, clear, onSelect}) inside a
   thread .art ("Data map · N entities · M edges" + .mapwrap + .maplegend +
   expand) and call engine.renderAtlas(atlas, onto). The agent passes the
   atlas {components, links, stats} straight from /api/agent; ontology labels
   are looked up lazily so node names read. The expand affordance re-submits
   "show me the full model" so the agent can open a bigger view. */

export function renderDataMapArtifact(art, { sendPrompt, ontology } = {}) {
  const stats = art.stats || {};
  const atlas = { components: art.components || [], links: art.links || [], stats };
  const nEntities = stats.classes ?? atlas.components.reduce((a, c) => a + (c.class_uris || []).length, 0);
  const nEdges = stats.confirmed != null || stats.likely != null
    ? (Number(stats.confirmed || 0) + Number(stats.likely || 0))
    : (atlas.links || []).filter((l) => l.tier !== "hint").length;
  const tag = `${nEntities} entities · ${nEdges} edges`;

  const svg = svgEl("svg", {
    class: "constellation", viewBox: "0 0 960 460",
    preserveAspectRatio: "xMidYMid meet", role: "img", "aria-label": "the data map",
  });
  const card = el("div", { class: "node-card", hidden: "hidden" });
  const evCard = el("div", { class: "evidence-card", hidden: "hidden" });
  const wrap = el("div", { class: "constellation-wrap mapwrap-inner" }, svg, card, evCard);
  const mapwrap = el("div", { class: "mapwrap" }, wrap);

  const onto = ontology || { classes: [], edges: [] };
  const engine = createConstellation({
    svg, wrap, card, evCard, svgEl, el, clear, onSelect: () => {},
  });
  // render once the node is in the DOM (the engine reads layout/box sizing)
  requestAnimationFrame(() => {
    try {
      if (atlas.components.length) engine.renderAtlas(atlas, onto);
      else if (onto.classes && onto.classes.length) engine.render(onto);
    } catch { /* a malformed atlas just leaves an empty sky */ }
  });

  const legend = el("div", { class: "maplegend" },
    el("span", {}, el("i", { class: "solid" }), "confirmed join"),
    el("span", {}, el("i", { class: "dash" }), "likely join"),
    el("span", {}, el("i", { class: "node" }), "entity"),
    el("span", {}, el("i", { class: "silo" }), "standalone"),
    el("span", { class: "spacer" }),
    el("button", { class: "expandbtn", type: "button",
      onclick: () => { if (sendPrompt) sendPrompt("show me the full model"); } },
      el("span", { class: "ic" }, GLYPH.expand()), "expand"));

  const artNode = artCard(GLYPH.map(), "Data map", tag, mapwrap);
  artNode.append(legend);
  return artNode;
}

/* ════════════════════════════════════════════════ 6 · TEXT ARTIFACT
   Plain narration / an honest abstention — no rich card, just a calm note. */
export function renderTextArtifact(art) {
  const t = (art && art.text) || "";
  if (!t) return null;
  return el("div", { class: "art-text" }, t);
}

/* ─────────────────────────────────────────────── the dispatch table */
export function renderArtifact(art, opts = {}) {
  switch (art && art.kind) {
    case "answer": return renderAnswerArtifact(art, opts);
    case "chart": return renderChartArtifact(art, opts);
    case "confirm_joins": return renderConfirmArtifact(art, opts);
    case "op_preview": return renderOpPreviewArtifact(art, opts);
    case "datamap": return renderDataMapArtifact(art, opts);
    case "text": return renderTextArtifact(art);
    default: return null;
  }
}

function truncate(s, n) {
  s = String(s || "");
  return s.length > n ? `${s.slice(0, n - 1)}…` : s;
}
