/* ASK — the questioner. A centered hero + question box + suggested/recent
   questions; cited answers with inline "Where this came from" dots opening a
   read-only Sources panel. De-jargon is presentation-only (atoms → source
   records); API routes keep their internal names. SECURITY: data → el()/svgEl()
   only, never innerHTML. */

import {
  el, svgEl, clear, api, errorNote, confGauge, skeletonCard, store,
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
/* the arrow glyph on the Ask button */
function arrowIcon() {
  return svgEl("svg", {
    class: "go-arrow", width: "16", height: "16",
    viewBox: "0 0 24 24", fill: "none", stroke: "currentColor",
    "stroke-width": "2", "stroke-linecap": "round", "stroke-linejoin": "round",
    "aria-hidden": "true",
  }, svgEl("path", { d: "M5 12h14M13 6l6 6-6 6" }));
}

export function createAskSurface({ bus }) {
  let pane = null;
  let input = null;
  let result = null;
  let suggestBox = null;
  let historyBox = null;
  let sourcesPanel = null;
  let activeClarify = null;
  let built = false;

  function recents() { return store.get(RECENT_KEY, []); }
  function pushHistory(question) {
    const next = [question, ...recents().filter((q) => q !== question)].slice(0, 24);
    store.set(RECENT_KEY, next);
    renderHistory();
  }

  /* suggested questions, generated from the model it built: the most-
     connected types and their measures, in plain language. */
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

  function renderSuggested() {
    if (!suggestBox) return;
    clear(suggestBox);
    const qs = suggestedQuestions();
    if (!qs.length) return;
    suggestBox.append(el("span", { class: "ask-suggest-head" }, "Try one of these"));
    const chips = el("div", { class: "ask-suggest-chips" });
    for (const q of qs) {
      const chevron = svgEl("svg", {
        class: "chip-ic", width: "13", height: "13", viewBox: "0 0 24 24",
        fill: "none", stroke: "currentColor", "stroke-width": "2",
        "stroke-linecap": "round", "aria-hidden": "true",
      }, svgEl("path", { d: "M9 6l6 6-6 6" }));
      chips.append(el("button", {
        class: "chip suggest-chip", type: "button", title: q,
        onclick: () => run(q),
      }, chevron, el("span", { class: "chip-txt" }, q)));
    }
    suggestBox.append(chips);
  }

  function renderHistory() {
    if (!historyBox) return;
    clear(historyBox);
    const rs = recents().slice(0, 8);
    if (!rs.length) return;
    historyBox.append(el("span", { class: "ask-recent-head" }, "Recent questions"));
    const chips = el("div", { class: "ask-recent-chips" });
    for (const q of rs) {
      chips.append(el("button", {
        class: "chip history-chip", type: "button", title: q, onclick: () => run(q),
      }, q));
    }
    historyBox.append(chips);
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
        class: "chip", type: "button", title: c.uri,
        onclick: () => { input.value = `${input.value.trim()} ${c.name}`.trim(); input.focus(); },
      }, c.name));
    return [
      el("p", { class: "abstain-help" },
        "what would make this answerable — ask about one of the things in the model it built",
        chips.length ? ":" : ""),
      chips.length ? el("div", { class: "clarify-options" }, chips) : null,
    ];
  }

  /* ───────────────── "Where this came from" — the de-jargoned Sources
     panel. Read-only here: it opens beside the answer when a citation dot
     is tapped, and shows the source records behind a value. */
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
          el("div", { class: "atom-value" }, atom.value === null ? "∅" : String(atom.value)),
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

  function renderAnswer(out) {
    const target = clear(result);
    activeClarify = null;
    citeSeq = 0;
    pane.classList.add("answered");

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
        el("p", { class: "abstain-reason" }, out.abstain_reason || "nothing in the model could ground this"),
        abstainHelp(),
        confGauge(out.confidence, "below the floor")));
      return;
    }

    const cites = new Map();
    for (const c of out.citations) cites.set(`${c.row}|${c.column}`, c.atom_ids);

    const card = el("div", { class: "answer-card" },
      el("p", { class: "answer-q" }, out.question,
        out.cached ? el("span", { class: "cached-mark" }, "· instant — answered before") : null));

    if (out.rows.length === 1 && out.columns.length === 1) {
      const v = out.rows[0][0];
      const ids = cites.get(`0|${out.columns[0]}`);
      const headline = el("div", { class: "answer-headline" },
        el("span", {}, v === null ? "∅" : String(v)),
        el("span", { class: "headline-col" }, out.columns[0]));
      if (ids && ids.length) headline.append(citeDot(ids, `${out.columns[0]} = ${v}`, out.columns[0]));
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
            td.append(citeDot(ids, `row ${ri + 1} · ${col} = ${v}`, col));
          }
          return td;
        })));
      card.append(el("div", { class: "answer-table-wrap" },
        el("table", { class: "data" }, el("thead", {}, thead), el("tbody", {}, tbody))));
    }
    card.append(el("div", { class: "answer-foot" },
      el("span", { class: "sources-hint" }, "tap a numbered dot to see where it came from"),
      confGauge(out.confidence)));
    target.append(card);
  }

  async function run(question) {
    question = String(question || "").trim();
    if (!question) return;
    if (!built) { return; } // guarded — empty state already explains
    input.value = question;
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
    pane.classList.remove("answered", "sources-open");
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
    pane.classList.remove("answered", "sources-open");

    input = el("input", {
      class: "ask-input", type: "text", spellcheck: "false",
      "aria-label": "ask a question about your data",
      placeholder: "Ask anything about your data",
    });
    const button = el("button", { class: "btn btn-forge ask-go", type: "submit" },
      "Ask", arrowIcon());
    const form = el("form", { class: "ask-form-big", autocomplete: "off" },
      el("div", { class: "ask-field-big" },
        el("span", { class: "ask-field-ic", "aria-hidden": "true" }, sparkIcon(20)),
        input, button));

    suggestBox = el("div", { class: "ask-suggest" });
    historyBox = el("div", { class: "ask-recent" });
    result = el("div", { class: "ask-result", "aria-live": "polite" });
    sourcesPanel = el("aside", { class: "sources-panel", "aria-label": "where this came from" });

    const stage = el("div", { class: "ask-stage" },
      el("span", { class: "ask-eyebrow" }, "ask anything"),
      el("h1", { class: "ask-headline" }, "What do you want to know?"),
      el("p", { class: "ask-tagline" },
        "Ask a plain-language question — every answer shows where it came from."),
      form, suggestBox, historyBox);

    pane.append(el("div", { class: "ask-layout" }, stage, result, sourcesPanel));

    form.addEventListener("submit", (e) => { e.preventDefault(); run(input.value); });
    renderSuggested();
    renderHistory();
    requestAnimationFrame(() => input && input.focus());
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
