/* OntoForge — the instrument. Vanilla ES modules, no build chain, no framework.
   SECURITY INVARIANT: every piece of API data enters the DOM through el()/
   document.createTextNode — nothing interpolated is ever assigned to innerHTML. */

import { createConstellation } from "./js/constellation.js";
import { createEntityPanel } from "./js/entity.js";
import { createDashboardsPanel } from "./js/dashboards.js";

// ─────────────────────────────────────────────────────────────── helpers

const $ = (sel, root = document) => root.querySelector(sel);

/** Build an element; string/number children become TEXT nodes (XSS-safe). */
function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined) continue;
    if (k === "class") node.className = v;
    else if (k === "dataset") Object.assign(node.dataset, v);
    else if (k.startsWith("on") && typeof v === "function") node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v);
  }
  for (const c of children.flat(Infinity)) {
    if (c === null || c === undefined || c === false) continue;
    node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return node;
}

const SVG_NS = "http://www.w3.org/2000/svg";
function svgEl(tag, attrs = {}, ...children) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined) continue;
    if (k.startsWith("on") && typeof v === "function") node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v);
  }
  for (const c of children.flat(Infinity)) {
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
  return el("div", { class: "error-note" }, String((err && err.message) || err));
}

/** The honest confidence gauge: thin amber fill + the number, always. */
function confGauge(confidence, label = "confidence") {
  const pct = Math.max(0, Math.min(1, confidence)) * 100;
  const fill = el("div", { class: "gauge-fill", style: "width:0%" });
  requestAnimationFrame(() => requestAnimationFrame(() => { fill.style.width = `${pct}%`; }));
  return el("div", { class: "conf-gauge" },
    el("span", { class: "gauge-label" }, label),
    el("div", { class: "gauge-track" }, fill),
    el("span", { class: "gauge-value" }, `${(confidence * 100).toFixed(1)}%`));
}

function skeletonCard(widths = [42, 68, 55]) {
  return el("div", { class: "answer-card", "aria-busy": "true" },
    widths.map((w) => el("div", { class: "skeleton", style: `width:${w}%` })));
}

// ───────────────────────────────────────────────────────── evidence rail
// The provenance drill: any cited value opens its source atoms; any
// prov_ref opens the full derivation tree (sum = any-of, product = all-of).

const rail = $("#evidence-rail");
const railBody = $("#rail-body");
const railContext = $("#rail-context");
let evidenceAnchor = null;

function anchorEvidence(node) {
  if (evidenceAnchor) evidenceAnchor.classList.remove("evidence-active");
  evidenceAnchor = node || null;
  if (evidenceAnchor) evidenceAnchor.classList.add("evidence-active");
}

function openRail(contextLabel, anchor) {
  rail.hidden = false;
  requestAnimationFrame(() => rail.classList.add("open"));
  document.body.classList.add("rail-open");
  railContext.textContent = contextLabel || "";
  anchorEvidence(anchor);
  clear(railBody);
}

function closeRail() {
  rail.classList.remove("open");
  document.body.classList.remove("rail-open");
  anchorEvidence(null);
  setTimeout(() => { if (!rail.classList.contains("open")) rail.hidden = true; }, 180);
}

const ATOM_URI_RE = /^atom:\/\/([^/]+)\/([^/]+)\/(.+?)(?:#(.*))?$/;

/** source › table › row # column — the atom URI rendered as a path. */
function atomPath(uri) {
  const m = ATOM_URI_RE.exec(uri || "");
  if (!m) return el("div", { class: "atom-path" }, uri || "");
  const [, source, table, row, column] = m;
  return el("div", { class: "atom-path" },
    source, el("span", { class: "sep" }, "›"),
    table, el("span", { class: "sep" }, "›"),
    row,
    column ? el("span", { class: "col" }, ` #${column}`) : null);
}

function atomChip(atom) {
  return el("div", { class: "atom-chip" },
    atomPath(atom.uri),
    el("div", { class: "atom-value" }, atom.value === null ? "∅" : String(atom.value)),
    el("div", { class: "atom-id" }, `⌗ ${atom.atom_id}`));
}

/** Atom chips fetched live from /api/atoms — the rail's ground truth. */
async function railShowAtoms(atomIds, contextLabel, anchor) {
  openRail(contextLabel, anchor);
  railBody.append(el("div", { class: "section-label" },
    `${atomIds.length} source atom${atomIds.length === 1 ? "" : "s"}`));
  for (const id of atomIds) {
    const slot = el("div", { class: "atom-chip" },
      el("div", { class: "atom-id" }, `⌗ ${id}`),
      el("div", { class: "skeleton", style: "width:70%" }));
    railBody.append(slot);
    api(`/api/atoms/${encodeURIComponent(id)}`)
      .then((atom) => slot.replaceWith(atomChip(atom)))
      .catch((e) => slot.replaceWith(el("div", { class: "atom-chip" },
        el("div", { class: "atom-id" }, `⌗ ${id}`), errorNote(e))));
  }
}

/** The derivation tree behind a prov_ref: collapsible sums/products. */
function provNodeView(node) {
  if (node.kind === "atom") {
    return atomChip({ uri: node.uri, value: node.value, atom_id: node.atom_id });
  }
  if (node.kind === "one" || node.kind === "zero") {
    return el("div", { class: "atom-id" }, node.kind === "one" ? "⊤ (trivially derived)" : "⊥ (no support)");
  }
  const label = node.kind === "sum" ? "any of" : "all of";
  const kids = el("div", { class: "prov-children" }, node.terms.map(provNodeView));
  const twist = el("span", { class: "twist" }, "▾");
  const head = el("button", {
    class: "prov-op", type: "button",
    onclick: () => {
      const open = !kids.hidden;
      kids.hidden = open;
      twist.textContent = open ? "▸" : "▾";
    },
  }, twist, label, el("span", { class: "arity" }, `(${node.terms.length})`));
  return el("div", { class: "prov-node" }, head, kids);
}

async function railShowProvenance(provRef, contextLabel, anchor) {
  openRail(contextLabel, anchor);
  railBody.append(el("div", { class: "skeleton", style: "width:55%" }));
  try {
    const out = await api(`/api/provenance/${encodeURIComponent(provRef)}`);
    clear(railBody).append(
      el("div", { class: "section-label" },
        `derivation — ${out.n_atoms} atom${out.n_atoms === 1 ? "" : "s"} · ref ${out.prov_ref}`),
      provNodeView(out.tree));
  } catch (e) {
    clear(railBody).append(errorNote(e));
  }
}

$("#rail-close").addEventListener("click", closeRail);

// ─────────────────────────────────────────────────────────────── masthead

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

// ──────────────────────────────────────────────────────────────────── ask

const RECENT_KEY = "ontoforge.recent.questions";
let recentQuestions = [];
try { recentQuestions = JSON.parse(localStorage.getItem(RECENT_KEY) || "[]"); } catch { /* fresh */ }

let activeClarify = null;   // {question, options} while a clarification is on screen

function renderHistory() {
  const box = clear($("#ask-history"));
  for (const q of recentQuestions.slice(0, 8)) {
    box.append(el("button", { class: "chip history-chip", title: q, onclick: () => ask(q) }, q));
  }
}

function pushHistory(question) {
  recentQuestions = [question, ...recentQuestions.filter((q) => q !== question)].slice(0, 24);
  try { localStorage.setItem(RECENT_KEY, JSON.stringify(recentQuestions)); } catch { /* private mode */ }
  renderHistory();
}

function abstainHelp() {
  const chips = (state.ontology ? state.ontology.classes : [])
    .slice()
    .sort((a, b) => b.properties.length - a.properties.length)
    .slice(0, 6)
    .map((c) => el("button", {
      class: "chip", title: c.uri,
      onclick: () => {
        const input = $("#ask-input");
        input.value = `${input.value.trim()} ${c.name}`.trim();
        input.focus();
      },
    }, c.name));
  return [
    el("p", { class: "abstain-help" },
      "what would make this answerable — ground the question in the induced ontology ",
      el("a", { href: "#/ontology" }, "(open the constellation)"),
      chips.length ? " or build on one of its classes:" : ""),
    chips.length ? el("div", { class: "clarify-options" }, chips) : null,
  ];
}

function renderAnswer(out) {
  const target = clear($("#ask-result"));
  activeClarify = null;

  // — clarification: one question, one keystroke ————————————————
  if (out.clarification) {
    activeClarify = { question: out.question, options: out.clarification_options };
    target.append(el("div", { class: "clarify-card" },
      el("span", { class: "section-label" }, "one clarification, then an answer"),
      el("p", { class: "clarify-q" }, out.clarification),
      el("div", { class: "clarify-options" },
        out.clarification_options.map((opt, i) =>
          el("button", { class: "clarify-option", onclick: () => clarifyChoice(i) },
            el("kbd", {}, String(i + 1)), opt)))));
    return;
  }

  // — abstention: a dignified first-class state, never an error ————
  if (out.abstained) {
    target.append(el("div", { class: "answer-card state-abstained" },
      el("span", { class: "abstain-mark" }, "abstained"),
      el("p", { class: "abstain-line" }, "OntoForge declines to guess."),
      el("p", { class: "abstain-reason" }, out.abstain_reason || "no derivation reached the answer floor"),
      abstainHelp(),
      confGauge(out.confidence, "confidence · below the floor")));
    return;
  }

  // — the answer: every cited value carries its amber dot ——————————
  const cites = new Map();
  for (const c of out.citations) cites.set(`${c.row}|${c.column}`, c.atom_ids);

  function citeDot(ids, label, holder) {
    return el("button", {
      class: "cite-dot",
      title: `${ids.length} source atom${ids.length === 1 ? "" : "s"} — click for evidence`,
      "aria-label": `evidence for ${label}`,
      onclick: () => railShowAtoms(ids, label, holder),
    });
  }

  const card = el("div", { class: "answer-card" },
    el("p", { class: "answer-q" }, out.question,
      out.cached ? el("span", { class: "cached-mark" }, "· instant — answer cache") : null));

  if (out.rows.length === 1 && out.columns.length === 1) {
    // a single value is a headline, not a table
    const v = out.rows[0][0];
    const ids = cites.get(`0|${out.columns[0]}`);
    const headline = el("div", { class: "answer-headline" },
      el("span", {}, v === null ? "∅" : String(v)),
      el("span", { class: "headline-col" }, out.columns[0]));
    if (ids && ids.length) headline.append(citeDot(ids, `${out.columns[0]} = ${v}`, headline));
    card.append(headline);
  } else {
    const thead = el("tr", {}, out.columns.map((c) => el("th", {}, c)));
    const tbody = out.rows.map((row, ri) =>
      el("tr", {}, row.map((v, ci) => {
        const col = out.columns[ci];
        const ids = cites.get(`${ri}|${col}`);
        const td = el("td", {}, v === null ? "∅" : String(v));
        if (ids && ids.length) {
          td.classList.add("cited");
          td.append(citeDot(ids, `row ${ri + 1} · ${col} = ${v}`, td));
        }
        return td;
      })));
    card.append(el("div", { class: "answer-table-wrap" },
      el("table", { class: "data" }, el("thead", {}, thead), el("tbody", {}, tbody))));
  }

  card.append(confGauge(out.confidence));
  target.append(card);
}

async function ask(question) {
  question = String(question || "").trim();
  if (!question) return;
  if (location.hash.indexOf("#/ask") !== 0) location.hash = "#/ask";
  $("#ask-input").value = question;
  const btn = $("#ask-button");
  btn.disabled = true;
  closeRail();
  const target = clear($("#ask-result"));
  target.append(skeletonCard());
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

async function clarifyChoice(choice) {
  if (!activeClarify) return;
  const { question } = activeClarify;
  activeClarify = null;
  const target = clear($("#ask-result"));
  target.append(skeletonCard([60, 35, 50]));
  try {
    const out = await api("/api/ask/clarify", { question, choice });
    renderAnswer(out);
  } catch (e) {
    clear(target).append(errorNote(e));
  }
}

// ─────────────────────────────────────────────────────────────── ontology

const state = { ontology: null };
let ontologyPromise = null;

function loadOntology() {
  if (!ontologyPromise) {
    ontologyPromise = api("/api/ontology").then((o) => { state.ontology = o; return o; });
  }
  return ontologyPromise;
}

let constellation = null;

function renderClassDetail(c) {
  const onto = state.ontology;
  const byUri = new Map(onto.classes.map((k) => [k.uri, k]));
  const target = clear($("#class-detail"));

  const jump = (uri) => {
    const k = byUri.get(uri);
    if (k) { constellation && constellation.select(uri); renderClassDetail(k); }
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
          ? el("button", { class: "range-link", onclick: () => jump(p) }, byUri.get(p).name)
          : p,
      ])] : null));

  if (!c.properties.length) {
    target.append(el("div", { class: "empty-note" }, "no properties induced on this class"));
    return;
  }
  const rows = c.properties.map((p) =>
    el("tr", {},
      el("td", {}, p.name, p.is_link ? el("span", { class: "badge", style: "margin-left:0.625em" }, "→ link") : null),
      el("td", {}, p.datatype),
      el("td", {}, p.unit ? el("span", { class: "badge badge-amber" }, p.unit) : ""),
      el("td", {}, p.cardinality, p.functional ? " · fn" : ""),
      el("td", {}, p.range_class && byUri.has(p.range_class)
        ? el("button", { class: "range-link", onclick: () => jump(p.range_class) }, byUri.get(p.range_class).name)
        : (p.range_class || ""))));
  target.append(el("table", { class: "data" },
    el("thead", {}, el("tr", {},
      el("th", {}, "property"), el("th", {}, "datatype"), el("th", {}, "unit"),
      el("th", {}, "cardinality"), el("th", {}, "range"))),
    el("tbody", {}, rows)));
}

async function enterOntology(rest) {
  let onto;
  try {
    onto = await loadOntology();
  } catch (e) {
    clear($("#class-detail")).append(errorNote(e));
    return;
  }
  if (!constellation) {
    constellation = createConstellation({
      svg: $("#constellation"),
      wrap: $("#constellation-wrap"),
      card: $("#node-card"),
      svgEl, el, clear,
      onSelect: renderClassDetail,
    });
    constellation.render(onto);
  }
  if (rest) {
    const uri = decodeURIComponent(rest);
    const c = onto.classes.find((k) => k.uri === uri || k.name === uri);
    if (c) { constellation.select(c.uri); renderClassDetail(c); }
  }
}

// ───────────────────────────────────────────────────────────────── review

let reviewItems = [];
let reviewSel = -1;

function tallyArc(toward, threshold, recals) {
  const r = 20, C = 2 * Math.PI * r;
  const frac = Math.max(0, Math.min(1, toward / threshold));
  const fill = svgEl("circle", {
    class: "arc-fill", cx: 26, cy: 26, r,
    "stroke-width": 3, "stroke-dasharray": C.toFixed(2), "stroke-dashoffset": C.toFixed(2),
    transform: "rotate(-90 26 26)",
  });
  requestAnimationFrame(() => requestAnimationFrame(() => {
    fill.setAttribute("stroke-dashoffset", (C * (1 - frac)).toFixed(2));
  }));
  return svgEl("svg", { class: "tally-arc", width: 52, height: 52, viewBox: "0 0 52 52" },
    svgEl("circle", { class: "arc-track", cx: 26, cy: 26, r, "stroke-width": 1 }),
    fill,
    svgEl("text", { x: 26, y: 27 }, `${toward}/${threshold}`),
    svgEl("text", { x: 26, y: 39, style: "font-size:8px;fill:var(--ink-faint)" },
      recals ? `↻${recals}` : ""));
}

function renderTally(data) {
  const target = clear($("#review-tally"));
  const kinds = new Set([...Object.keys(data.verdicts), ...Object.keys(data.recalibrations)]);
  for (const it of data.items) kinds.add(it.kind);
  for (const kind of [...kinds].sort()) {
    const n = data.verdicts[kind] || 0;
    const toward = n % data.threshold;
    const recals = data.recalibrations[kind] || 0;
    target.append(el("div", { class: "tally-block" },
      tallyArc(toward, data.threshold, recals),
      el("div", {},
        el("div", { class: "tally-kind" }, `${kind} — toward recalibration`),
        el("div", { class: "tally-sub" },
          `${n} verdict${n === 1 ? "" : "s"} · ${recals} recalibration${recals === 1 ? "" : "s"} · refit at every ${data.threshold}`))));
  }
}

const ER_PAIR_RE = /^er:([^:]+):(.+)\|\|(.+)$/;

function evidencePair(item) {
  const m = ER_PAIR_RE.exec(item.decision_id);
  if (!m) return null;
  const [, , a, b] = m;
  const side = (label, uri) => el("div", { class: "pair-side" },
    el("span", { class: "pair-label" }, label),
    uri,
    uri.startsWith("ent://")
      ? el("div", {}, el("button", {
          class: "range-link", style: "font-size:var(--fs-0)",
          onclick: () => { location.hash = `#/entity/${uri}`; },
        }, "inspect entity →"))
      : null);
  return el("div", { class: "pair-grid" },
    side("left record", a),
    el("span", { class: "pair-vs" }, "same?"),
    side("right record", b));
}

function reviewCard(item, idx) {
  const note = el("input", {
    class: "note-input", type: "text",
    placeholder: "reviewer note (optional)", spellcheck: "false",
  });
  const actions = el("div", { class: "review-actions" });

  async function verdict(v) {
    for (const b of actions.querySelectorAll("button")) b.disabled = true;
    try {
      const out = await api(`/api/review/${encodeURIComponent(item.decision_id)}`,
        { verdict: v, note: note.value });
      const msg = `${v === "accept" ? "accepted" : "rejected"} · ${out.verdicts_for_kind} ${out.kind} verdict${out.verdicts_for_kind === 1 ? "" : "s"}`;
      clear(actions).append(el("span", {
        class: `verdict-result${out.recalibrated ? " recalibrated" : ""}`,
      }, msg, out.recalibrated ? ` · ⚒ ${out.kind} recalibrated` : ""));
      setTimeout(enterReview, 750);
    } catch (e) {
      actions.append(errorNote(e));
      for (const b of actions.querySelectorAll("button")) b.disabled = false;
    }
  }

  actions.append(
    el("button", { class: "btn btn-accept", onclick: () => verdict("accept") }, "accept (a)"),
    el("button", { class: "btn btn-reject", onclick: () => verdict("reject") }, "reject (r)"),
    note);

  const card = el("div", { class: "review-card", dataset: { idx: String(idx) }, onclick: () => selectReview(idx, false) },
    el("div", { class: "review-head" },
      el("span", { class: "badge badge-amber" }, item.kind),
      item.deferred_to_human ? el("span", { class: "badge" }, "deferred") : null,
      item.quarantined ? el("span", { class: "badge" }, "quarantined") : null,
      el("span", { class: "badge" }, `tier ${item.tier}`),
      el("span", { class: "review-id" }, item.decision_id)),
    evidencePair(item),
    item.rationale ? el("p", { class: "review-rationale" }, item.rationale) : null,
    el("div", { class: "review-meta" },
      "outcome ", el("b", {}, item.outcome),
      " · ", item.created_at,
      item.prov_atoms.length
        ? el("button", {
            class: "range-link", style: "margin-left:0.75em",
            onclick: (ev) => {
              railShowAtoms(item.prov_atoms, item.decision_id, ev.currentTarget.closest(".review-card"));
            },
          }, `evidence ⌗${item.prov_atoms.length}`)
        : null),
    el("div", {}, item.conformal_set.map((c) =>
      el("span", { class: `conformal-chip${c === item.outcome ? " chosen" : ""}` }, c))),
    confGauge(item.confidence),
    actions);
  card._verdict = verdict;
  return card;
}

function selectReview(idx, scroll = true) {
  const cards = document.querySelectorAll("#review-queue .review-card");
  if (!cards.length) { reviewSel = -1; return; }
  reviewSel = Math.max(0, Math.min(cards.length - 1, idx));
  cards.forEach((c, i) => c.classList.toggle("selected", i === reviewSel));
  if (scroll) cards[reviewSel].scrollIntoView({ block: "nearest", behavior: "smooth" });
}

async function enterReview() {
  try {
    const data = await api("/api/review");
    renderTally(data);
    reviewItems = data.items;
    const queue = clear($("#review-queue"));
    if (!data.items.length) {
      queue.append(el("div", { class: "empty-note" },
        "no review items — the spine is confident today"));
      reviewSel = -1;
      return;
    }
    data.items.forEach((item, i) => queue.append(reviewCard(item, i)));
    selectReview(reviewSel === -1 ? 0 : reviewSel, false);
  } catch (e) {
    clear($("#review-queue")).append(errorNote(e));
  }
}

function reviewKey(key) {
  const cards = document.querySelectorAll("#review-queue .review-card");
  if (!cards.length) return false;
  if (key === "j") { selectReview(reviewSel + 1); return true; }
  if (key === "k") { selectReview(reviewSel - 1); return true; }
  if ((key === "a" || key === "r") && reviewSel >= 0) {
    cards[reviewSel]._verdict(key === "a" ? "accept" : "reject");
    return true;
  }
  return false;
}

// ───────────────────────────────────────────────────────────────── status

const PIPELINE = ["ingest", "profile", "induce", "resolve", "materialize"];

function kvTable(title, entries, valueOf) {
  return el("div", {},
    el("span", { class: "section-label" }, title),
    entries.length
      ? el("table", { class: "data" }, el("tbody", {}, entries.map(([k, v]) =>
          el("tr", {}, el("td", {}, k), el("td", {}, valueOf ? valueOf(v) : fmt(v))))))
      : el("div", { class: "empty-note", style: "padding:0.5rem 0;text-align:left" }, "none recorded"));
}

async function enterStatus() {
  const target = clear($("#status-body"));
  target.append(skeletonCard([30, 60, 45]));
  try {
    const s = await api("/api/status");
    clear(target);

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
  } catch (e) {
    clear(target).append(errorNote(e));
  }
}

// ──────────────────────────────────────────────────────── feature modules

const ctx = {
  $, el, svgEl, clear, fmt, api, errorNote, confGauge,
  openAtoms: railShowAtoms, openProvenance: railShowProvenance, closeRail,
};

const entityPanel = createEntityPanel(ctx);
const dashboardsPanel = createDashboardsPanel(ctx);

// ──────────────────────────────────────────────────────── command palette

const cmdk = $("#cmdk");
const cmdkInput = $("#cmdk-input");
const cmdkList = $("#cmdk-list");
let cmdkItems = [];
let cmdkSel = 0;

const NAV_COMMANDS = [
  { kind: "go", label: "Ask — cited answers", hash: "#/ask" },
  { kind: "go", label: "Ontology — the constellation", hash: "#/ontology" },
  { kind: "go", label: "Entities — time-travel inspector", hash: "#/entity" },
  { kind: "go", label: "Review — adjudication queue", hash: "#/review" },
  { kind: "go", label: "Dashboards — vague-spec synthesis", hash: "#/dashboards" },
  { kind: "go", label: "Status — instrument cluster", hash: "#/status" },
];

function buildCmdkItems(q) {
  const needle = q.trim().toLowerCase();
  const items = [];
  if (needle) {
    items.push({ kind: "ask", label: `“${q.trim()}”`, run: () => ask(q.trim()) });
    if (q.trim().startsWith("ent://")) {
      items.push({ kind: "entity", label: q.trim(), mono: true,
        run: () => { location.hash = `#/entity/${q.trim()}`; } });
    }
  }
  for (const c of NAV_COMMANDS) {
    if (!needle || c.label.toLowerCase().includes(needle)) {
      items.push({ kind: "go to", label: c.label, run: () => { location.hash = c.hash; } });
    }
  }
  for (const question of recentQuestions) {
    if (items.length >= 14) break;
    if (!needle || question.toLowerCase().includes(needle)) {
      items.push({ kind: "recent", label: question, serif: true, run: () => ask(question) });
    }
  }
  if (state.ontology) {
    for (const c of state.ontology.classes) {
      if (items.length >= 18) break;
      if (needle && c.name.toLowerCase().includes(needle)) {
        items.push({ kind: "class", label: c.name, run: () => { location.hash = `#/ontology/${encodeURIComponent(c.uri)}`; } });
      }
    }
  }
  return items;
}

function renderCmdk() {
  clear(cmdkList);
  if (!cmdkItems.length) {
    cmdkList.append(el("div", { class: "cmdk-empty" }, "nothing matches — press enter to ask it as a question"));
    return;
  }
  cmdkItems.forEach((item, i) => {
    cmdkList.append(el("button", {
      class: `cmdk-item${i === cmdkSel ? " active" : ""}`,
      onclick: () => { closePalette(); item.run(); },
    },
      el("span", { class: "ci-kind" }, item.kind),
      el("span", { class: `ci-label${item.serif ? " serif" : ""}${item.mono ? " mono" : ""}` }, item.label)));
  });
  const active = cmdkList.children[cmdkSel];
  if (active) active.scrollIntoView({ block: "nearest" });
}

function openPalette() {
  cmdk.hidden = false;
  cmdkInput.value = "";
  cmdkSel = 0;
  cmdkItems = buildCmdkItems("");
  renderCmdk();
  cmdkInput.focus();
}

function closePalette() {
  cmdk.hidden = true;
  cmdkInput.blur();
}

cmdkInput.addEventListener("input", () => {
  cmdkSel = 0;
  cmdkItems = buildCmdkItems(cmdkInput.value);
  renderCmdk();
});

cmdkInput.addEventListener("keydown", (e) => {
  if (e.key === "ArrowDown") { e.preventDefault(); cmdkSel = Math.min(cmdkItems.length - 1, cmdkSel + 1); renderCmdk(); }
  else if (e.key === "ArrowUp") { e.preventDefault(); cmdkSel = Math.max(0, cmdkSel - 1); renderCmdk(); }
  else if (e.key === "Enter") {
    e.preventDefault();
    const item = cmdkItems[cmdkSel] || (cmdkInput.value.trim() && { run: () => ask(cmdkInput.value.trim()) });
    if (item) { closePalette(); item.run(); }
  }
});

cmdk.addEventListener("mousedown", (e) => { if (e.target === cmdk) closePalette(); });
$("#palette-open").addEventListener("click", openPalette);

// ───────────────────────────────────────────────────────────────── router

const PANELS = {
  ask: () => {},
  ontology: enterOntology,
  entity: (rest) => entityPanel.enter(rest),
  review: enterReview,
  dashboards: () => dashboardsPanel.enter(),
  status: enterStatus,
};

let currentTab = "ask";

function route() {
  const raw = location.hash.replace(/^#\//, "");
  const slash = raw.indexOf("/");
  const tab = slash === -1 ? raw : raw.slice(0, slash);
  const rest = slash === -1 ? "" : raw.slice(slash + 1);
  currentTab = PANELS[tab] ? tab : "ask";
  for (const a of document.querySelectorAll(".tabs a")) {
    a.classList.toggle("active", a.dataset.tab === currentTab);
  }
  for (const p of document.querySelectorAll(".tab-panel")) {
    p.hidden = p.id !== `panel-${currentTab}`;
  }
  PANELS[currentTab](rest);
}

// ─────────────────────────────────────────────────────────────── keyboard

document.addEventListener("keydown", (e) => {
  const a = document.activeElement;
  const typing = a && (a.tagName === "INPUT" || a.tagName === "TEXTAREA" || a.isContentEditable);

  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
    e.preventDefault();
    cmdk.hidden ? openPalette() : closePalette();
    return;
  }
  if (e.key === "Escape") {
    if (!cmdk.hidden) closePalette();
    else closeRail();
    return;
  }
  if (typing || !cmdk.hidden || e.metaKey || e.ctrlKey || e.altKey) return;

  if (e.key === "/") {
    e.preventDefault();
    location.hash = "#/ask";
    $("#ask-input").focus();
    return;
  }
  if (currentTab === "ask" && activeClarify && /^[1-9]$/.test(e.key)) {
    const i = Number(e.key) - 1;
    if (i < activeClarify.options.length) { e.preventDefault(); clarifyChoice(i); }
    return;
  }
  if (currentTab === "review" && reviewKey(e.key)) e.preventDefault();
});

// ──────────────────────────────────────────────────────────────────── wire

$("#ask-form").addEventListener("submit", (e) => {
  e.preventDefault();
  ask($("#ask-input").value);
});

$("#reload-button").addEventListener("click", async () => {
  try {
    await api("/api/reload", {});
    ontologyPromise = null;
    state.ontology = null;
    constellation = null;
    clear($("#constellation"));
    dashboardsPanel.reset();
    await refreshMasthead();
    await loadOntology();
    await enterStatus();
  } catch (e) {
    clear($("#status-body")).append(errorNote(e));
  }
});

window.addEventListener("hashchange", route);

// prefetch the world on load: status + ontology warm before the first click
refreshMasthead();
loadOntology().catch(() => { /* surfaces on the ontology panel */ });
renderHistory();
route();
