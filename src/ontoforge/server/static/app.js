/* OntoForge console — vanilla ES module, no build chain.
   Every piece of interpolated data flows through el()/text nodes —
   nothing from the API is ever assigned to innerHTML. */

"use strict";

// ------------------------------------------------------------------ helpers

const $ = (sel) => document.querySelector(sel);

/** Build a DOM element; children that are strings become TEXT nodes (XSS-safe). */
function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k === "dataset") Object.assign(node.dataset, v);
    else if (k.startsWith("on") && typeof v === "function") {
      node.addEventListener(k.slice(2), v);
    } else if (v !== null && v !== undefined) node.setAttribute(k, v);
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined) continue;
    node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return node;
}

function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
  return node;
}

function fmt(n) {
  return typeof n === "number" ? n.toLocaleString("en-US") : String(n);
}

async function api(path, body) {
  const opts = body === undefined
    ? {}
    : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
  const res = await fetch(path, opts);
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const j = await res.json();
      if (j && j.detail) detail = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail);
    } catch { /* keep the status line */ }
    throw new Error(detail);
  }
  return res.json();
}

function errorNote(err) {
  return el("div", { class: "error-note" }, String(err.message || err));
}

// ------------------------------------------------------------------ masthead

async function refreshMasthead() {
  try {
    const s = await api("/api/status");
    $("#meta-estate").textContent = s.estate;
    $("#meta-atoms").textContent = s.ledger_exists ? fmt(s.atoms) : "—";
    $("#meta-cost").textContent = s.ledger_exists ? fmt(s.cost_tokens) : "—";
    $("#colophon-right").textContent = s.project;
  } catch {
    $("#meta-estate").textContent = "—";
  }
}

// ---------------------------------------------------------------------- ask

const askHistory = [];

function confMeter(confidence) {
  const pct = Math.max(0, Math.min(1, confidence)) * 100;
  const fill = el("div", { class: "conf-fill", style: "width:0%" });
  requestAnimationFrame(() => { fill.style.width = `${pct}%`; });
  return el("div", { class: "conf-meter" },
    el("span", { class: "label" }, "confidence"),
    el("div", { class: "conf-track" }, fill),
    el("span", { class: "conf-value" }, `${(confidence * 100).toFixed(1)}%`));
}

async function openCitationDrawer(atomIds, cellLabel) {
  const drawer = $("#citation-drawer");
  clear(drawer);
  drawer.hidden = false;
  drawer.append(el("div", { class: "drawer-head" },
    el("span", { class: "drawer-title" }, `source atoms — ${cellLabel}`),
    el("button", { class: "drawer-close", onclick: () => { drawer.hidden = true; } }, "close ×")));
  for (const id of atomIds) {
    const row = el("div", { class: "atom-row" }, el("span", { class: "atom-id" }, id), " loading…");
    drawer.append(row);
    try {
      const atom = await api(`/api/atoms/${encodeURIComponent(id)}`);
      clear(row).append(
        el("div", {}, el("span", { class: "atom-id" }, `⌗ ${atom.atom_id}`)),
        el("div", { class: "atom-uri" }, atom.uri),
        el("div", { class: "atom-val" }, "= ", String(atom.value)));
    } catch (e) {
      clear(row).append(el("span", { class: "atom-id" }, id), " ", errorNote(e));
    }
  }
  drawer.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function renderAnswer(out) {
  const target = clear($("#ask-result"));
  $("#citation-drawer").hidden = true;

  if (out.clarification) {
    target.append(el("div", { class: "clarify-card" },
      el("div", { class: "section-label" }, "one clarification, then an answer"),
      el("p", { class: "clarify-q" }, out.clarification),
      el("div", { class: "clarify-options" },
        out.clarification_options.map((opt, i) =>
          el("button", { class: "clarify-option", onclick: () => clarify(out.question, i) },
            el("span", { class: "idx" }, `${i + 1}.`), opt)))));
    return;
  }

  if (out.abstained) {
    target.append(el("div", { class: "abstained-card" },
      el("span", { class: "abstained-mark" }, "— abstained —"),
      el("p", { class: "abstained-reason" }, out.abstain_reason || "the engine declined to answer"),
      el("div", { class: "abstained-note" },
        "nothing asserted without a derivation — abstention is the honest result")));
    return;
  }

  // citations indexed by row|column
  const cites = new Map();
  for (const c of out.citations) cites.set(`${c.row}|${c.column}`, c.atom_ids);

  const thead = el("tr", {}, out.columns.map((c) => el("th", {}, c)));
  const tbody = out.rows.map((row, ri) =>
    el("tr", {}, row.map((v, ci) => {
      const col = out.columns[ci];
      const ids = cites.get(`${ri}|${col}`);
      const td = el("td", {}, v === null ? "∅" : String(v));
      if (ids && ids.length) {
        td.append(el("button", {
          class: "cite-chip",
          title: `${ids.length} source atom${ids.length > 1 ? "s" : ""}`,
          onclick: () => openCitationDrawer(ids, `row ${ri + 1}, ${col}`),
        }, `⌗${ids.length}`));
      }
      return td;
    })));

  target.append(el("div", { class: "answer-card" },
    el("p", { class: "answer-q" }, out.question,
      out.cached ? el("span", { class: "cached-mark" }, "· cached") : null),
    el("table", { class: "data" }, el("thead", {}, thead), el("tbody", {}, tbody)),
    confMeter(out.confidence)));
}

function pushHistory(question) {
  const i = askHistory.indexOf(question);
  if (i !== -1) askHistory.splice(i, 1);
  askHistory.unshift(question);
  if (askHistory.length > 8) askHistory.pop();
  const box = clear($("#ask-history"));
  for (const q of askHistory) {
    box.append(el("button", { class: "history-chip", title: q, onclick: () => ask(q) }, q));
  }
}

async function ask(question) {
  question = question.trim();
  if (!question) return;
  $("#ask-input").value = question;
  const btn = $("#ask-button");
  btn.disabled = true;
  const target = clear($("#ask-result"));
  target.append(el("div", { class: "loading" }, "deriving…"));
  try {
    const out = await api("/api/ask", { question });
    pushHistory(question);
    renderAnswer(out);
  } catch (e) {
    clear(target).append(errorNote(e));
  } finally {
    btn.disabled = false;
  }
}

async function clarify(question, choice) {
  const target = clear($("#ask-result"));
  target.append(el("div", { class: "loading" }, "resolving clarification…"));
  try {
    const out = await api("/api/ask/clarify", { question, choice });
    renderAnswer(out);
  } catch (e) {
    clear(target).append(errorNote(e));
  }
}

// ----------------------------------------------------------------- ontology

let ontoCache = null;
let selectedClassUri = null;

function classBadges(c) {
  const badges = [];
  if (c.is_event) badges.push(el("span", { class: "badge badge-amber" }, "event"));
  return badges;
}

function renderClassDetail(c) {
  selectedClassUri = c.uri;
  for (const b of document.querySelectorAll(".tree-name")) {
    b.classList.toggle("selected", b.dataset.uri === c.uri);
  }
  const byUri = new Map(ontoCache.classes.map((k) => [k.uri, k]));
  const target = clear($("#onto-detail"));
  target.append(
    el("div", { class: "class-uri" }, c.uri),
    el("h2", {}, c.name, classBadges(c)),
    c.definition ? el("p", { class: "class-def" }, c.definition) : null,
    el("div", { class: "review-meta" },
      "confidence ", el("b", {}, c.confidence.toFixed(2)),
      " · shapes ", el("b", {}, String(c.n_shapes)),
      c.parents.length
        ? el("span", {}, " · parents ", el("b", {},
            c.parents.map((p) => (byUri.get(p) || { name: p }).name).join(", ")))
        : null));

  if (!c.properties.length) {
    target.append(el("div", { class: "placeholder" }, "no properties on this class"));
    return;
  }
  const rows = c.properties.map((p) => {
    const range = p.range_class && byUri.has(p.range_class)
      ? el("button", { class: "range-link", onclick: () => renderClassDetail(byUri.get(p.range_class)) },
          byUri.get(p.range_class).name)
      : (p.range_class || "");
    return el("tr", {},
      el("td", {}, p.name,
        p.is_link ? el("span", { class: "badge", style: "margin-left:0.5em" }, "→ link") : null),
      el("td", {}, p.datatype),
      el("td", {}, p.unit ? el("span", { class: "badge badge-amber" }, p.unit) : ""),
      el("td", {}, p.cardinality, p.functional ? " · fn" : ""),
      el("td", {}, range));
  });
  target.append(el("table", { class: "data" },
    el("thead", {}, el("tr", {},
      el("th", {}, "property"), el("th", {}, "datatype"), el("th", {}, "unit"),
      el("th", {}, "card"), el("th", {}, "range"))),
    el("tbody", {}, rows)));
}

function renderTree(onto) {
  const byUri = new Map(onto.classes.map((c) => [c.uri, c]));
  const children = new Map();
  const placed = new Set();
  for (const c of onto.classes) {
    const parent = c.parents.find((p) => byUri.has(p));
    if (parent) {
      if (!children.has(parent)) children.set(parent, []);
      children.get(parent).push(c);
      placed.add(c.uri);
    }
  }
  const roots = onto.classes.filter((c) => !placed.has(c.uri));

  function node(c) {
    const kids = (children.get(c.uri) || []).slice().sort((a, b) => a.name.localeCompare(b.name));
    const row = el("div", { class: "tree-row" });
    const box = el("div", { class: "tree-node" }, row);
    if (kids.length) {
      const sub = el("div", { class: "tree-children" }, kids.map(node));
      const toggle = el("button", { class: "tree-toggle", title: "collapse / expand" }, "▾");
      toggle.addEventListener("click", () => {
        const open = !sub.hidden;
        sub.hidden = open;
        toggle.textContent = open ? "▸" : "▾";
      });
      row.append(toggle);
      box.append(sub);
    } else {
      row.append(el("span", { class: "tree-leaf-dot" }, "·"));
    }
    row.append(
      el("button", { class: "tree-name", dataset: { uri: c.uri }, onclick: () => renderClassDetail(c) },
        c.name),
      ...classBadges(c),
      el("span", { class: "tree-conf" }, c.confidence.toFixed(2)));
    return box;
  }

  const target = clear($("#onto-tree"));
  target.append(el("div", { class: "review-meta" },
    "ontology v", el("b", {}, String(onto.version)),
    " · ", el("b", {}, String(onto.classes.length)), " classes",
    " · ", el("b", {}, String(onto.edges.length)), " links"));
  for (const r of roots.slice().sort((a, b) => a.name.localeCompare(b.name))) target.append(node(r));
}

async function enterOntology() {
  if (ontoCache) return;
  try {
    ontoCache = await api("/api/ontology");
    renderTree(ontoCache);
    const first = ontoCache.classes.find((c) => c.uri === selectedClassUri) || ontoCache.classes[0];
    if (first) renderClassDetail(first);
  } catch (e) {
    clear($("#onto-tree")).append(errorNote(e));
  }
}

// ------------------------------------------------------------------- review

function renderTally(data) {
  const target = clear($("#review-tally"));
  const kinds = new Set([...Object.keys(data.verdicts), ...Object.keys(data.recalibrations)]);
  for (const it of data.items) kinds.add(it.kind);
  if (!kinds.size) return;
  for (const kind of [...kinds].sort()) {
    const n = data.verdicts[kind] || 0;
    const toward = n % data.threshold;
    const recals = data.recalibrations[kind] || 0;
    const pct = (toward / data.threshold) * 100;
    target.append(el("div", { class: "tally-block" },
      el("div", { class: "tally-kind" }, `${kind} — verdicts toward recalibration`),
      el("div", { class: "tally-progress" },
        el("div", { class: "tally-track" },
          el("div", { class: "tally-fill", style: `width:${pct}%` })),
        el("span", { class: "tally-count" }, `${toward} / ${data.threshold}`)),
      el("div", { class: "tally-recal" },
        `${n} total verdict${n === 1 ? "" : "s"} · ${recals} recalibration${recals === 1 ? "" : "s"} recorded`)));
  }
}

function reviewCard(item) {
  const note = el("input", { class: "note-input", placeholder: "reviewer note (optional)", spellcheck: "false" });
  const actions = el("div", { class: "review-actions" });

  async function verdict(v) {
    for (const b of actions.querySelectorAll("button")) b.disabled = true;
    try {
      const out = await api(`/api/review/${encodeURIComponent(item.decision_id)}`,
        { verdict: v, note: note.value });
      clear(actions).append(el("span", { class: "verdict-result" },
        `${v}ed · ${out.verdicts_for_kind} ${out.kind} verdict${out.verdicts_for_kind === 1 ? "" : "s"}`
        + (out.recalibrated ? ` · ⚒ recalibrated ${out.kind}` : "")));
      setTimeout(enterReview, 900);
    } catch (e) {
      actions.append(errorNote(e));
      for (const b of actions.querySelectorAll("button")) b.disabled = false;
    }
  }

  actions.append(
    el("button", { class: "btn btn-accent", onclick: () => verdict("accept") }, "accept"),
    el("button", { class: "btn", onclick: () => verdict("reject") }, "reject"),
    note);

  return el("div", { class: "review-card" },
    el("div", { class: "head" },
      el("span", { class: "badge badge-amber" }, item.kind),
      el("span", { class: "review-id" }, item.decision_id),
      item.deferred_to_human ? el("span", { class: "badge" }, "deferred") : null,
      item.quarantined ? el("span", { class: "badge" }, "quarantined") : null,
      el("span", { class: "badge" }, `tier ${item.tier}`)),
    item.rationale ? el("p", { class: "review-rationale" }, item.rationale) : null,
    el("div", { class: "review-meta" },
      "outcome ", el("b", {}, item.outcome),
      " · confidence ", el("b", {}, item.confidence.toFixed(3)),
      " · ", item.created_at),
    el("div", {},
      item.conformal_set.map((c) =>
        el("span", { class: `conformal-chip${c === item.outcome ? " chosen" : ""}` }, c))),
    confMeter(item.confidence),
    actions);
}

async function enterReview() {
  try {
    const data = await api("/api/review");
    renderTally(data);
    const queue = clear($("#review-queue"));
    if (!data.items.length) {
      queue.append(el("div", { class: "queue-empty" },
        "the queue is clear — no deferred or low-confidence decisions await adjudication"));
      return;
    }
    for (const item of data.items) queue.append(reviewCard(item));
  } catch (e) {
    clear($("#review-queue")).append(errorNote(e));
  }
}

// --------------------------------------------------------------- dashboards

const VEGA_CONFIG = {
  background: "transparent",
  view: { stroke: "transparent" },
  axis: {
    labelColor: "#9a958a", titleColor: "#9a958a",
    gridColor: "rgba(232,228,218,0.07)", domainColor: "rgba(232,228,218,0.25)",
    tickColor: "rgba(232,228,218,0.25)",
    labelFont: "monospace", titleFont: "monospace", labelFontSize: 10, titleFontSize: 10,
  },
  legend: { labelColor: "#9a958a", titleColor: "#9a958a" },
  title: { color: "#e8e4da", font: "Georgia, serif" },
  range: { category: ["#d97706", "#f59e0b", "#b45309", "#92400e", "#fbbf24"] },
  mark: { color: "#d97706" },
  bar: { fill: "#d97706" },
  line: { stroke: "#f59e0b" },
  point: { fill: "#f59e0b" },
  area: { fill: "#d97706" },
  arc: { fill: "#d97706" },
  text: { fill: "#e8e4da", font: "monospace" },
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
        "vega vendor scripts unavailable (offline) — showing the raw Vega-Lite spec"),
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

async function proposeDashboards(utterance) {
  const target = clear($("#dash-result"));
  target.append(el("div", { class: "loading" }, "synthesizing dashboards…"));
  try {
    const out = await api("/api/dashboards", { utterance });
    clear(target);
    if (!out.dashboards.length) {
      target.append(el("div", { class: "queue-empty" }, "no dashboards could be grounded in this ontology"));
      return;
    }
    out.dashboards.forEach((d, i) => target.append(dashboardBlock(d, i + 1)));
  } catch (e) {
    clear(target).append(errorNote(e));
  }
}

let savedLoaded = false;
async function enterDashboards() {
  if (savedLoaded) return;
  savedLoaded = true;
  try {
    const out = await api("/api/dashboards");
    const target = clear($("#dash-saved"));
    if (!out.dashboards.length) {
      target.append(el("div", { class: "queue-empty" }, "no saved proposals — run `ontoforge dashboard`"));
      return;
    }
    for (const d of out.dashboards) target.append(dashboardBlock(d, null));
  } catch (e) {
    clear($("#dash-saved")).append(errorNote(e));
  }
}

// ------------------------------------------------------------------- status

const PIPELINE = ["ingest", "profile", "induce", "resolve", "materialize"];

function kvTable(title, entries, valueOf) {
  const rows = entries.map(([k, v]) =>
    el("tr", {}, el("td", {}, k), el("td", {}, valueOf ? valueOf(v) : fmt(v))));
  return el("div", {},
    el("div", { class: "section-label" }, title),
    entries.length
      ? el("table", { class: "data" }, el("tbody", {}, rows))
      : el("div", { class: "queue-empty" }, "none recorded"));
}

async function enterStatus() {
  const target = clear($("#status-body"));
  target.append(el("div", { class: "loading" }, "reading the ledger…"));
  try {
    const s = await api("/api/status");
    clear(target);

    target.append(el("div", { class: "status-project" }, `${s.project} · estate: ${s.estate}`
      + (s.limit ? ` · limit ${s.limit}` : "")));

    const totalDecisions = Object.values(s.decisions_by_kind).reduce((a, b) => a + b, 0);
    const totalArtifacts = Object.values(s.artifacts).reduce((a, b) => a + b, 0);
    target.append(el("div", { class: "counter-grid" },
      el("div", { class: "counter-cell" },
        el("div", { class: "counter-label" }, "atoms"),
        el("div", { class: "counter-value accent" }, s.ledger_exists ? fmt(s.atoms) : "—")),
      el("div", { class: "counter-cell" },
        el("div", { class: "counter-label" }, "decisions"),
        el("div", { class: "counter-value" }, fmt(totalDecisions))),
      el("div", { class: "counter-cell" },
        el("div", { class: "counter-label" }, "artifacts"),
        el("div", { class: "counter-value" }, fmt(totalArtifacts))),
      el("div", { class: "counter-cell" },
        el("div", { class: "counter-label" }, "model cost"),
        el("div", { class: "counter-value" }, fmt(s.cost_tokens), el("small", {}, " tok")))));

    const stages = el("div", { class: "stage-list" });
    const known = new Set(s.stages);
    for (const st of PIPELINE) {
      stages.append(el("span", { class: `stage-item${known.has(st) ? " done" : ""}` },
        el("span", { class: "tick" }, known.has(st) ? "◆" : "◇"), st));
    }
    for (const st of s.stages) {
      if (!PIPELINE.includes(st)) {
        stages.append(el("span", { class: "stage-item done" },
          el("span", { class: "tick" }, "◆"), st));
      }
    }
    target.append(el("div", { class: "section-label" }, "pipeline stages"), stages);

    target.append(el("div", { class: "status-tables" },
      kvTable("decisions by kind", Object.entries(s.decisions_by_kind)),
      kvTable("decisions by tier", Object.entries(s.decisions_by_tier),
        (t) => `${fmt(t.count)} · ${fmt(t.deferred)} deferred · ${fmt(t.quarantined)} quarantined`),
      kvTable("artifacts", Object.entries(s.artifacts))));
  } catch (e) {
    clear(target).append(errorNote(e));
  }
}

// ------------------------------------------------------------------- router

const TABS = {
  ask: () => {},
  ontology: enterOntology,
  review: enterReview,
  dashboards: enterDashboards,
  status: enterStatus,
};

function route() {
  const tab = (location.hash.replace(/^#\//, "") || "ask").split("/")[0];
  const active = TABS[tab] ? tab : "ask";
  for (const a of document.querySelectorAll(".tabs a")) {
    a.classList.toggle("active", a.dataset.tab === active);
  }
  for (const p of document.querySelectorAll(".tab-panel")) {
    p.hidden = p.id !== `panel-${active}`;
  }
  TABS[active]();
}

// -------------------------------------------------------------------- wires

$("#ask-form").addEventListener("submit", (e) => {
  e.preventDefault();
  ask($("#ask-input").value);
});

$("#dash-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const utterance = $("#dash-input").value.trim();
  if (utterance) proposeDashboards(utterance);
});

$("#reload-button").addEventListener("click", async () => {
  try {
    await api("/api/reload", {});
    ontoCache = null;
    savedLoaded = false;
    await refreshMasthead();
    await enterStatus();
  } catch (e) {
    clear($("#status-body")).append(errorNote(e));
  }
});

window.addEventListener("hashchange", route);

refreshMasthead();
route();
