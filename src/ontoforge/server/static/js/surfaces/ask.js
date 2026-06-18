/* ASK — the questioner. A command field with a grounded typeahead
   (/api/suggest: Measures / Entities / Questions, keyboard-navigable); an
   answer-as-product surface (mono value, plain echo, confidence read, inline
   "Where this came from" source cells, follow-up chips); a capability empty
   state; a quiet recent rail; a first-class abstention/clarification card.
   De-jargon is presentation-only (atoms → source records). SECURITY: data
   enters the DOM only via el()/svgEl()/createTextNode — never innerHTML. */

import {
  el, svgEl, clear, fmt, api, errorNote, confGauge, skeletonCard, store, debounce,
  loadOntology, ontologyNow, hueFor, workspaceState,
} from "../core.js";

const RECENT_KEY = "ontoforge.recent.questions";
const ATOM_URI_RE = /^atom:\/\/([^/]+)\/([^/]+)\/(.+?)(?:#(.*))?$/;

/* the leading sparkle that sits inside the ask field (XSS-safe svgEl) */
function sparkIcon(size) {
  return svgEl("svg", {
    class: "ask-spark", width: String(size), height: String(size),
    viewBox: "0 0 24 24", fill: "none", stroke: "currentColor",
    "stroke-width": "1.6", "stroke-linecap": "round", "stroke-linejoin": "round",
    "aria-hidden": "true",
  }, svgEl("path", { d: "M12 3l1.9 5.6L19.5 10l-5.6 1.4L12 17l-1.9-5.6L4.5 10l5.6-1.4z" }));
}
/* the arrow glyph on the Ask button / follow-up chips */
function arrowIcon() {
  return svgEl("svg", {
    class: "go-arrow", width: "16", height: "16",
    viewBox: "0 0 24 24", fill: "none", stroke: "currentColor",
    "stroke-width": "2", "stroke-linecap": "round", "stroke-linejoin": "round",
    "aria-hidden": "true",
  }, svgEl("path", { d: "M5 12h14M13 6l6 6-6 6" }));
}
/* per-kind typeahead row glyphs (XSS-safe svgEl) */
function kindIcon(kind) {
  const at = {
    width: "15", height: "15", viewBox: "0 0 24 24", fill: "none",
    stroke: "currentColor", "stroke-width": "1.7", "stroke-linecap": "round",
    "stroke-linejoin": "round", "aria-hidden": "true",
  };
  if (kind === "measure")
    return svgEl("svg", at, svgEl("path", { d: "M4 19V5M4 19h16M8 16v-5M13 16V8M18 16v-9" }));
  if (kind === "entity")
    return svgEl("svg", at,
      svgEl("rect", { x: "3", y: "6", width: "13", height: "10", rx: "1.5" }),
      svgEl("path", { d: "M16 9h3.5L22 11.5V16h-6M7 19a2 2 0 100-4 2 2 0 000 4zM18 19a2 2 0 100-4 2 2 0 000 4z" }));
  // question
  return svgEl("svg", at,
    svgEl("circle", { cx: "12", cy: "12", r: "9" }),
    svgEl("path", { d: "M9.2 9a2.8 2.8 0 015.4 1c0 1.9-2.8 2.5-2.8 2.5M12 17h.01" }));
}
function chevron(size = 16) {
  return svgEl("svg", {
    class: "chev-ic", width: String(size), height: String(size), viewBox: "0 0 24 24",
    fill: "none", stroke: "currentColor", "stroke-width": "2", "stroke-linecap": "round",
    "stroke-linejoin": "round", "aria-hidden": "true",
  }, svgEl("path", { d: "M9 6l6 6-6 6" }));
}

/* a small mono criticality meter — hairline track + accent fill (no gradient) */
function critMeter(score) {
  const pct = Math.max(0, Math.min(1, Number(score) || 0));
  return el("span", { class: "s-crit", title: `criticality ${(pct * 100).toFixed(0)}%` },
    el("span", { class: "s-crit-k" }, "crit"),
    el("span", { class: "s-crit-bar" },
      el("i", { style: `width:${(pct * 100).toFixed(0)}%` })));
}

export function createAskSurface({ bus }) {
  let pane = null;
  let input = null;
  let result = null;
  let suggestBox = null;     // the grounded typeahead dropdown
  let historyBox = null;     // the recent-questions rail
  let sourcesPanel = null;   // legacy side panel (kept for tests/back-compat)
  let activeClarify = null;
  let built = false;

  /* typeahead state (the spotlight.js debounce + abort template) */
  let suggItems = [];        // flattened [{kind, question, node, ...}]
  let suggSel = -1;
  let suggOpen = false;
  let abortCtl = null;

  function recents() { return store.get(RECENT_KEY, []); }
  function pushHistory(question) {
    const next = [question, ...recents().filter((q) => q !== question)].slice(0, 24);
    store.set(RECENT_KEY, next);
    renderHistory();
  }

  /* suggested questions, generated from the model it built: the most-
     connected types and their measures, in plain language. (Kept for the
     coach CTA + the empty-state capability cards.) */
  function suggestedQuestions() {
    const onto = ontologyNow();
    if (!onto || !onto.classes || !onto.classes.length) return [];
    const ranked = onto.classes
      .slice()
      .sort((a, b) => b.properties.length - a.properties.length)
      .slice(0, 6);
    const qs = [];
    for (const c of ranked) {
      const measure = c.properties.find((p) => p.unit || /cost|amount|price|delay|count|total|qty|quantity/i.test(p.name));
      if (measure) qs.push(`What was the total ${measure.name.replace(/_/g, " ")} for ${c.name}?`);
      else qs.push(`How many ${c.name} are there?`);
      if (qs.length >= 6) break;
    }
    return qs.slice(0, 6);
  }

  /* the capability EMPTY STATE: the top entities, each expanding to a couple
     of example questions drawn from its measures. Built from /api/ontology. */
  function renderSuggested() {
    if (!suggestBox) return; // (the dropdown owns suggestBox now; capability cards live in the stage)
  }

  function exampleQuestionsFor(c) {
    const qs = [];
    const measures = c.properties.filter((p) => !p.is_link &&
      (p.unit || p.dimension != null || /cost|amount|price|delay|count|total|qty|quantity|spend|revenue|sales|weight|volume|duration/i.test(p.name)));
    for (const m of measures.slice(0, 2)) {
      qs.push(`What was the total ${m.name.replace(/_/g, " ")} for ${c.name}?`);
    }
    qs.push(`How many ${c.name} are there?`);
    return qs.slice(0, 3);
  }

  function capabilityCards() {
    const onto = ontologyNow();
    if (!onto || !onto.classes || !onto.classes.length) return null;
    const ranked = onto.classes.slice()
      .sort((a, b) => b.properties.length - a.properties.length)
      .slice(0, 4);
    const wrap = el("div", { class: "cap-grid" });
    for (const c of ranked) {
      const open = el("details", { class: "cap-card" });
      const sum = el("summary", { class: "cap-head" },
        el("span", { class: "cap-ic", "aria-hidden": "true" }, kindIcon("entity")),
        el("span", { class: "cap-name mono" }, c.name),
        el("span", { class: "cap-meta" }, `${c.properties.length} fields`),
        chevron(14));
      open.append(sum);
      const body = el("div", { class: "cap-body" });
      for (const q of exampleQuestionsFor(c)) {
        body.append(el("button", {
          class: "cap-q", type: "button", title: q, onclick: () => run(q),
        }, arrowIcon(), el("span", {}, q)));
      }
      open.append(body);
      wrap.append(open);
    }
    return el("div", { class: "ask-capability" },
      el("span", { class: "ask-suggest-head" }, "Try one of these"),
      wrap);
  }

  function renderHistory() {
    if (!historyBox) return;
    clear(historyBox);
    const rs = recents().slice(0, 8);
    historyBox.append(el("div", { class: "rail-label" },
      el("span", {}, "Recent questions"),
      rs.length ? el("span", { class: "rail-ct mono" }, String(recents().length)) : null));
    if (!rs.length) {
      historyBox.append(el("p", { class: "rail-empty" }, "your asked questions land here"));
      return;
    }
    const list = el("div", { class: "ask-recent-chips recent-list" });
    for (const q of rs) {
      list.append(el("button", {
        class: "chip history-chip ritem", type: "button", title: q, onclick: () => run(q),
      },
        el("span", { class: "ritem-q" }, q),
        arrowIcon()));
    }
    historyBox.append(list);
  }

  /* "What would make this answerable" — drawn from real types in the model,
     never an error style. */
  function abstainHelp() {
    const onto = ontologyNow();
    const chips = (onto ? onto.classes : [])
      .slice()
      .sort((a, b) => b.properties.length - a.properties.length)
      .slice(0, 6)
      .map((c) => el("button", {
        class: "chip fixq", type: "button", title: c.uri,
        onclick: () => { input.value = `${input.value.trim()} ${c.name}`.trim(); input.focus(); },
      }, arrowIcon(), el("span", {}, c.name)));
    return [
      el("p", { class: "abstain-help" },
        "what would make this answerable — ask about one of the things in the model it built",
        chips.length ? ":" : ""),
      chips.length ? el("div", { class: "clarify-options fix-options" }, chips) : null,
    ];
  }

  /* ───────────────── GROUNDED TYPEAHEAD — debounced fetch to /api/suggest,
     grouped Measures / Entities / Questions, keyboard-navigable. Reuses the
     spotlight.js debounce + AbortController template. */
  const queryServer = debounce(async (q) => {
    if (abortCtl) abortCtl.abort();
    abortCtl = new AbortController();
    try {
      const res = await fetch(`/api/suggest?q=${encodeURIComponent(q)}&limit=18`, { signal: abortCtl.signal });
      if (!res.ok) { closeSuggest(); return; }
      const out = await res.json();
      if (input.value.trim() !== q) return; // stale — the field moved on
      renderSuggest(out, q);
    } catch { /* aborted or offline — leave the field clean */ }
  }, 45);

  function onInput() {
    const q = input.value.trim();
    if (!q) { closeSuggest(); return; }
    queryServer(q);
  }

  function closeSuggest() {
    suggOpen = false;
    suggItems = [];
    suggSel = -1;
    if (suggestBox) { clear(suggestBox); suggestBox.hidden = true; }
    if (pane) pane.classList.remove("typeahead-open");
    input && input.setAttribute("aria-expanded", "false");
  }

  function suggGroup(title, count) {
    return el("div", { class: "s-ghead" },
      el("span", {}, title),
      el("span", { class: "s-cnt mono" }, `${count} match${count === 1 ? "" : "es"}`));
  }

  function suggRow(item) {
    const row = el("div", {
      class: "s-row", role: "option", "aria-selected": "false",
      onpointerdown: (e) => e.preventDefault(),     // keep focus in the input
      onclick: () => { closeSuggest(); run(item.question); },
    },
      el("span", { class: `s-kind k-${item.kind}`, "aria-hidden": "true" }, kindIcon(item.kind)),
      el("span", { class: "s-mid" }, item.mid, item.meta),
      item.tail);
    item.node = row;
    return row;
  }

  function renderSuggest(out, q) {
    if (!suggestBox) return;
    clear(suggestBox);
    suggItems = [];
    const measures = out.measures || [];
    const entities = out.entities || [];
    const questions = out.questions || [];

    if (measures.length) {
      suggestBox.append(suggGroup("Measures", measures.length));
      for (const m of measures) {
        const aggTxt = m.agg === "group" ? "dimension" : (m.agg === "avg" ? "avg" : "sum");
        const unitTxt = m.unit ? ` · ${m.unit}` : "";
        const rowsTxt = m.rows != null ? ` · ${fmt(m.rows)} rows` : "";
        const item = {
          kind: "measure", question: m.question,
          mid: el("span", { class: "s-name" },
            el("b", { class: "mono" }, m.label)),
          meta: el("span", { class: "s-meta" }, `${aggTxt}${unitTxt} · on `,
            el("code", {}, m.on_class), rowsTxt),
          tail: critMeter(m.criticality),
        };
        suggItems.push(item);
        suggestBox.append(suggRow(item));
      }
    }
    if (entities.length) {
      suggestBox.append(suggGroup("Entities", entities.length));
      for (const e of entities) {
        const recTxt = e.records != null ? `${fmt(e.records)} records · ` : "";
        const item = {
          kind: "entity", question: e.question,
          mid: el("span", { class: "s-name" }, el("b", { class: "mono" }, e.cls)),
          meta: el("span", { class: "s-meta" }, `${recTxt}${e.fields} fields`),
          tail: critMeter(e.criticality),
        };
        suggItems.push(item);
        suggestBox.append(suggRow(item));
      }
    }
    if (questions.length) {
      suggestBox.append(suggGroup("Questions", questions.length));
      for (const qq of questions) {
        const item = {
          kind: "question", question: qq.text,
          mid: el("span", { class: "s-name" }, qq.text),
          meta: el("span", { class: "s-meta" }, qq.kind),
          tail: el("span", { class: "s-asked mono" }, "recalled"),
        };
        suggItems.push(item);
        suggestBox.append(suggRow(item));
      }
    }

    if (!suggItems.length) {
      suggestBox.append(el("div", { class: "s-empty" },
        "no grounded match — press ", el("kbd", {}, "↵"), " to ask it anyway"));
    } else {
      suggestBox.append(el("div", { class: "s-foot" },
        el("span", {}, "grounded in the model it built"),
        el("span", { class: "s-keys" },
          el("kbd", {}, "↑↓"), el("kbd", {}, "tab"), el("kbd", {}, "↵ ask"))));
    }
    suggSel = -1;
    suggOpen = true;
    suggestBox.hidden = false;
    pane.classList.add("typeahead-open");
    input.setAttribute("aria-expanded", "true");
  }

  function moveSuggest(delta) {
    if (!suggOpen || !suggItems.length) return;
    if (suggSel >= 0 && suggItems[suggSel]) suggItems[suggSel].node.setAttribute("aria-selected", "false");
    suggSel = (suggSel + delta + suggItems.length) % suggItems.length;
    const it = suggItems[suggSel];
    it.node.setAttribute("aria-selected", "true");
    if (it.node.scrollIntoView) it.node.scrollIntoView({ block: "nearest" });
  }

  /* ───────────────── "Where this came from" — the de-jargoned source panel,
     here inline-expandable inside the answer card AND in the side aside. */
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

  function openSources(atomIds, label) {
    if (!sourcesPanel) return;
    pane.classList.add("sources-open");
    clear(sourcesPanel);
    sourcesPanel.append(
      el("div", { class: "sources-head" },
        el("span", { class: "section-label" }, "Where this came from"),
        el("button", {
          class: "sources-close", type: "button", "aria-label": "close sources",
          onclick: () => { pane.classList.remove("sources-open"); },
        }, "×")),
      label ? el("div", { class: "sources-context mono" }, label) : null,
      el("div", { class: "section-label" },
        `${atomIds.length} source record${atomIds.length === 1 ? "" : "s"}`));
    for (const id of atomIds) {
      const slot = el("div", { class: "atom-chip" },
        el("div", { class: "atom-id" }, `⌗ ${id}`),
        el("div", { class: "skeleton", style: "width:70%" }));
      sourcesPanel.append(slot);
      api(`/api/atoms/${encodeURIComponent(id)}`)
        .then((atom) => slot.replaceWith(el("div", { class: "atom-chip" },
          atomPath(atom.uri),
          el("div", { class: "atom-value mono" }, atom.value === null ? "∅" : String(atom.value)),
          el("div", { class: "atom-id" }, `⌗ ${atom.atom_id}`))))
        .catch((e) => slot.replaceWith(el("div", { class: "atom-chip" },
          el("div", { class: "atom-id" }, `⌗ ${id}`), errorNote(e))));
    }
  }

  let citeSeq = 0;
  function citeDot(ids, label, hueKey) {
    const n = ++citeSeq;
    const dot = el("button", {
      class: "cite-dot", type: "button",
      style: hueKey ? `background:${hueFor(hueKey)}` : null,
      title: `${ids.length} source record${ids.length === 1 ? "" : "s"} — where this came from`,
      "aria-label": `where ${label} came from`,
      onclick: () => openSources(ids, label),
    }, ids.length ? String(n) : null);
    requestAnimationFrame(() => requestAnimationFrame(() =>
      setTimeout(() => dot.classList.add("landed"), n * 40)));
    return dot;
  }

  /* the inline-expandable provenance block: the real cited source cells
     (record · column → value), drawn straight from the answer's citations.
     Resolves each atom's RAW uri lazily through /api/atoms. */
  function provenanceBlock(citations, out) {
    const flat = [];
    for (const c of citations) {
      for (const id of c.atom_ids || []) flat.push({ row: c.row, column: c.column, value: c.value, id });
    }
    if (!flat.length) return null;
    const shown = flat.slice(0, 6);
    const det = el("details", { class: "prov", open: citations.length <= 3 ? "" : null });
    const head = el("summary", { class: "prov-toggle" },
      chevron(15),
      el("span", { class: "prov-ic", "aria-hidden": "true" }, svgEl("svg", {
        width: "16", height: "16", viewBox: "0 0 24 24", fill: "none", stroke: "currentColor",
        "stroke-width": "1.6", "stroke-linecap": "round", "stroke-linejoin": "round",
      }, svgEl("path", { d: "M4 7V5a1 1 0 011-1h14a1 1 0 011 1v2M4 7h16M4 7v12a1 1 0 001 1h14a1 1 0 001-1V7M9 11h6M9 15h4" }))),
      el("span", {}, "Where this came from"),
      el("span", { class: "prov-hint" },
        `${fmt(flat.length)} source record${flat.length === 1 ? "" : "s"}${flat.length > shown.length ? ` · showing ${shown.length}` : ""}`));
    det.append(head);

    const body = el("div", { class: "prov-body" });
    body.append(el("p", { class: "prov-intro" },
      "Every value traces to one cell — open any row to see the exact source record."));
    const table = el("div", { class: "src-table" });
    table.append(el("div", { class: "src-row src-h" },
      el("span", {}, "record"), el("span", {}, "column → value"), el("span", {}, "")));
    for (const f of shown) {
      const row = el("div", { class: "src-row", title: `source record ⌗ ${f.id}` });
      const coord = el("span", { class: "src-coord mono" }, "resolving…");
      const cell = el("span", { class: "src-cell" },
        el("span", { class: "src-cellk mono" }, `${f.column} → `),
        el("span", { class: "src-val mono" }, f.value === null ? "∅" : String(f.value)));
      const go = el("span", { class: "src-go", "aria-hidden": "true" }, svgEl("svg", {
        width: "14", height: "14", viewBox: "0 0 24 24", fill: "none", stroke: "currentColor",
        "stroke-width": "1.8", "stroke-linecap": "round", "stroke-linejoin": "round",
      }, svgEl("path", { d: "M7 17L17 7M9 7h8v8" })));
      row.append(coord, cell, go);
      row.addEventListener("click", () => openSources([f.id], `${f.column} = ${f.value}`));
      api(`/api/atoms/${encodeURIComponent(f.id)}`)
        .then((atom) => {
          clear(coord);
          const m = ATOM_URI_RE.exec(atom.uri || "");
          if (m) {
            coord.append(el("span", { class: "src-tbl" }, m[2]),
              el("span", { class: "src-rk" }, m[3]));
          } else { coord.append(atom.uri || `⌗ ${f.id}`); }
        })
        .catch(() => { coord.textContent = `⌗ ${f.id}`; });
      table.append(row);
    }
    body.append(table);
    body.append(el("p", { class: "prov-more" },
      el("span", { class: "prov-dot", "aria-hidden": "true" }),
      el("span", {}, "tap a numbered dot in the answer to open that exact source record")));
    det.append(body);
    return det;
  }

  /* follow-up chips — derived from {columns, citations, ontology}: a couple of
     plain next steps that keep the conversation going. */
  function followUps(out) {
    const chips = [];
    const onto = ontologyNow();
    const cols = out.columns || [];
    const measureCol = cols.find((c) => /cost|amount|price|spend|total|count|qty|quantity|revenue|sales|weight/i.test(c)) || cols[0];
    // a dimension to break down by, from the touched class's scalar props
    if (measureCol && onto && onto.classes && onto.classes.length) {
      const dims = [];
      for (const c of onto.classes) {
        for (const p of c.properties) {
          if (!p.is_link && !p.unit && p.dimension == null &&
            /date|month|year|status|type|mode|region|country|category|currency|carrier|class|state/i.test(p.name)) {
            dims.push(p.name);
          }
        }
      }
      const uniq = [...new Set(dims)].slice(0, 2);
      for (const d of uniq) {
        const q = `${measureCol.replace(/_/g, " ")} by ${d.replace(/_/g, " ")}`;
        chips.push({ label: `Break down by ${d.replace(/_/g, " ")}`, q });
      }
    }
    // a top-ranked follow-up from the model's most-connected types
    const sq = suggestedQuestions().filter((q) => q !== out.question).slice(0, 2);
    for (const q of sq) chips.push({ label: q, q });
    const seen = new Set();
    const out2 = [];
    for (const c of chips) {
      if (seen.has(c.q.toLowerCase())) continue;
      seen.add(c.q.toLowerCase());
      out2.push(c);
      if (out2.length >= 3) break;
    }
    if (!out2.length) return null;
    const wrap = el("div", { class: "follow" },
      el("div", { class: "follow-label" }, "Keep going"));
    const row = el("div", { class: "f-chips" });
    for (const c of out2) {
      row.append(el("button", {
        class: "f-chip", type: "button", title: c.q, onclick: () => run(c.q),
      }, arrowIcon(), el("span", {}, c.label)));
    }
    wrap.append(row);
    return wrap;
  }

  /* a plain-English echo of what the answer summed — derived from
     {columns, rows, citations} only (the OQIR/echo prose is not in the API). */
  function echoLine(out) {
    const cols = out.columns || [];
    const rows = out.rows || [];
    if (rows.length === 1 && cols.length === 1) {
      const n = (out.citations || []).reduce((a, c) => a + (c.atom_ids ? c.atom_ids.length : 0), 0);
      if (n > 1) {
        return el("p", { class: "echo" },
          "Rolled up ", el("b", { class: "mono" }, fmt(n)),
          ` source record${n === 1 ? "" : "s"} into `,
          el("code", {}, cols[0].replace(/_/g, " ")), ".");
      }
      return el("p", { class: "echo" }, "A single grounded value — ",
        el("code", {}, cols[0].replace(/_/g, " ")), ".");
    }
    return el("p", { class: "echo" },
      el("b", { class: "mono" }, fmt(rows.length)), ` row${rows.length === 1 ? "" : "s"} across `,
      el("b", { class: "mono" }, fmt(cols.length)), ` column${cols.length === 1 ? "" : "s"}.`);
  }

  function renderAnswer(out) {
    const target = clear(result);
    activeClarify = null;
    citeSeq = 0;
    pane.classList.add("answered");
    closeSuggest();

    if (out.clarification) {
      activeClarify = { question: out.question, options: out.clarification_options };
      target.append(el("div", { class: "clarify-card" },
        el("span", { class: "section-label" }, "one quick question, then an answer"),
        el("p", { class: "clarify-q" }, out.clarification),
        el("div", { class: "clarify-options" },
          out.clarification_options.map((opt, i) =>
            el("button", { class: "clarify-option", type: "button", onclick: () => clarifyChoice(i) },
              el("kbd", {}, String(i + 1)), opt)))));
      return;
    }

    if (out.abstained) {
      target.append(el("div", { class: "answer-card state-abstained" },
        el("span", { class: "abstain-mark" }, "no grounded answer"),
        el("p", { class: "abstain-line" }, "OntoForge declines to guess."),
        el("p", { class: "abstain-reason mono" }, out.abstain_reason || "nothing in the model could ground this"),
        abstainHelp(),
        confGauge(out.confidence, "below the floor")));
      return;
    }

    const cites = new Map();
    for (const c of out.citations) cites.set(`${c.row}|${c.column}`, c.atom_ids);

    const card = el("div", { class: "answer-card answered-card" });
    // header: the question echo + the confidence read side by side
    card.append(el("div", { class: "answer-head" },
      el("p", { class: "answer-q" }, "You asked ",
        el("span", { class: "qm" }, "“"), out.question, el("span", { class: "qm" }, "”"),
        out.cached ? el("span", { class: "cached-mark" }, "· instant — answered before") : null),
      confGauge(out.confidence)));

    if (out.rows.length === 1 && out.columns.length === 1) {
      const v = out.rows[0][0];
      const ids = cites.get(`0|${out.columns[0]}`);
      const headline = el("div", { class: "answer-headline bignum" },
        el("span", { class: "hl-val" }, v === null ? "∅" : (typeof v === "number" ? fmt(v) : String(v))),
        el("span", { class: "headline-col" }, out.columns[0].replace(/_/g, " ")));
      if (ids && ids.length) headline.append(citeDot(ids, `${out.columns[0]} = ${v}`, out.columns[0]));
      card.append(el("div", { class: "answer-body" }, headline, echoLine(out)));
    } else {
      const thead = el("tr", {}, out.columns.map((c) => el("th", {}, c)));
      const tbody = out.rows.map((row, ri) =>
        el("tr", {}, row.map((v, ci) => {
          const col = out.columns[ci];
          const ids = cites.get(`${ri}|${col}`);
          const td = el("td", { class: typeof v === "number" ? "mono" : null },
            v === null ? "∅" : (typeof v === "number" ? fmt(v) : String(v)));
          if (ids && ids.length) {
            td.classList.add("cited");
            td.append(citeDot(ids, `row ${ri + 1} · ${col} = ${v}`, col));
          }
          return td;
        })));
      card.append(el("div", { class: "answer-body" },
        echoLine(out),
        el("div", { class: "answer-table-wrap" },
          el("table", { class: "data" }, el("thead", {}, thead), el("tbody", {}, tbody)))));
    }

    const prov = provenanceBlock(out.citations || [], out);
    if (prov) card.append(prov);
    const follow = followUps(out);
    if (follow) card.append(follow);

    target.append(card);
  }

  async function run(question) {
    question = String(question || "").trim();
    if (!question) return;
    if (!built) { return; } // guarded — empty state already explains
    input.value = question;
    closeSuggest();
    pane.classList.remove("sources-open");
    const target = clear(result);
    pane.classList.add("answered");
    target.append(skeletonCard());
    try {
      const out = await api("/api/ask", { question });
      pushHistory(question);
      renderAnswer(out);
    } catch (e) {
      clear(target).append(errorNote(e));
    }
  }

  async function clarifyChoice(choice) {
    if (!activeClarify) return;
    const { question } = activeClarify;
    activeClarify = null;
    const target = clear(result);
    target.append(skeletonCard([60, 35, 50]));
    try {
      const out = await api("/api/ask/clarify", { question, choice });
      renderAnswer(out);
    } catch (e) {
      clear(target).append(errorNote(e));
    }
  }

  /* ─────────────────────────────────── empty / not-ready states */
  function renderEmptyNotReady() {
    clear(pane);
    pane.classList.remove("answered", "sources-open", "typeahead-open");
    pane.append(el("div", { class: "ask-stage ask-notready" },
      el("div", { class: "notready-card" },
        el("h2", { class: "notready-title" }, "your data isn't ready to answer questions yet"),
        el("p", { class: "notready-line" },
          "Go to Studio to add data and let it build the model — then come back here."),
        el("button", {
          class: "btn btn-forge", type: "button",
          onclick: () => bus.emit("mode:goto", { mode: "studio", panel: "catalog" }),
        }, "Open Studio →"))));
  }

  function renderReady() {
    clear(pane);
    pane.classList.remove("answered", "sources-open", "typeahead-open");

    input = el("input", {
      class: "ask-input", type: "text", spellcheck: "false", autocomplete: "off",
      role: "combobox", "aria-autocomplete": "list", "aria-expanded": "false",
      "aria-label": "ask a question about your data",
      placeholder: "Ask anything about your data",
    });
    const button = el("button", { class: "btn btn-forge ask-go", type: "submit" },
      "Ask", arrowIcon());
    suggestBox = el("div", { class: "ask-suggest-drop", role: "listbox", hidden: "" });
    const field = el("div", { class: "ask-field-big" },
      el("span", { class: "ask-field-ic", "aria-hidden": "true" }, sparkIcon(20)),
      input, button);
    const form = el("form", { class: "ask-form-big", autocomplete: "off" },
      el("div", { class: "ask-cmd" }, field, suggestBox));

    result = el("div", { class: "ask-result", "aria-live": "polite" });
    sourcesPanel = el("aside", { class: "sources-panel", "aria-label": "where this came from" });
    historyBox = el("aside", { class: "ask-recent rail-sec" });

    const caps = capabilityCards();
    const stage = el("div", { class: "ask-stage" },
      el("span", { class: "ask-eyebrow" }, "ask anything"),
      el("h1", { class: "ask-headline" }, "What do you want to know?"),
      el("p", { class: "ask-tagline" },
        "Ask a plain-language question — every answer shows where it came from."),
      form,
      el("div", { class: "ask-empty-slot" }, caps));

    pane.append(el("div", { class: "ask-layout" },
      el("div", { class: "ask-main" }, stage, result, sourcesPanel),
      el("div", { class: "ask-rail" }, historyBox)));

    form.addEventListener("submit", (e) => { e.preventDefault(); run(input.value); });
    input.addEventListener("input", onInput);
    input.addEventListener("focus", () => { if (input.value.trim()) onInput(); });
    input.addEventListener("keydown", onFieldKey);
    input.addEventListener("blur", () => setTimeout(closeSuggest, 120));
    renderHistory();
    requestAnimationFrame(() => input && input.focus());
  }

  /* keyboard nav inside the command field — ↑/↓ move, tab completes, enter
     runs the selection (or the raw text), escape closes the dropdown. */
  function onFieldKey(e) {
    if (suggOpen && suggItems.length) {
      if (e.key === "ArrowDown") { e.preventDefault(); moveSuggest(1); return; }
      if (e.key === "ArrowUp") { e.preventDefault(); moveSuggest(-1); return; }
      if (e.key === "Tab") {
        e.preventDefault();
        if (suggSel < 0) moveSuggest(1);
        const it = suggItems[suggSel];
        if (it) input.value = it.question;
        return;
      }
      if (e.key === "Enter") {
        if (suggSel >= 0 && suggItems[suggSel]) {
          e.preventDefault();
          const it = suggItems[suggSel];
          closeSuggest();
          run(it.question);
          return;
        }
        // no row selected → fall through to the form submit (ask raw text)
      }
      if (e.key === "Escape") { e.preventDefault(); closeSuggest(); return; }
    }
  }

  async function refresh() {
    let ws = null;
    try { ws = await workspaceState(); } catch { ws = null; }
    // ontology presence is the real readiness signal; workspace.built backs it
    let onto = null;
    try { onto = await loadOntology(); } catch { onto = null; }
    built = !!(onto && onto.classes && onto.classes.length) || !!(ws && ws.built);
    if (built) { renderReady(); }
    else { renderEmptyNotReady(); }
  }

  return {
    mount({ pane: p }) {
      pane = p;
      pane.classList.add("surface-ask");
      refresh();
      bus.on("world:reload", refresh);
      bus.on("workspace:built", refresh);
    },
    enter() {
      if (built && input) requestAnimationFrame(() => input.focus());
    },
    // a suggested-question tap from the coach, or a routed ask:run
    show(opts = {}) {
      if (opts.question) run(opts.question);
      else if (opts.suggest) {
        const qs = suggestedQuestions();
        if (qs.length) run(qs[0]);
      }
    },
    onKey(e) {
      if (activeClarify && /^[1-9]$/.test(e.key)) {
        const i = Number(e.key) - 1;
        if (i < activeClarify.options.length) { e.preventDefault(); clarifyChoice(i); return true; }
      }
      return false;
    },
  };
}
