/* THE CONVERSATION-FIRST SHELL — one thread where the user talks and the
   autonomous data-engineering agent responds with short narration + rich INLINE
   ARTIFACTS. There are no Ask/Build/Studio modes: a slim left THREAD-HISTORY
   rail, a calm CONVERSATION reading column, and ONE persistent bottom COMPOSER
   ("Ask, build, or wire up your data —"). On load the agent is proactive: it
   calls GET /api/agent/opener and narrates what it mapped + what needs the user.
   On submit it appends the user turn, POSTs /api/agent, and renders the agent
   turn = narration + one inline artifact card per artifact, REUSING the existing
   renderers (artifacts.js). Follow-up chips re-submit. Thread history is
   localStorage-backed.

   SECURITY INVARIANT: every piece of API data enters the DOM through el()/
   svgEl()/createTextNode — never innerHTML. Keyless / offline / deterministic;
   every number is mono; provenance is one disclosure away. */

import {
  $, el, svgEl, clear, fmt, api, errorNote, skeletonCard, store, debounce,
  loadOntology, ontologyNow,
} from "./core.js";
import { renderArtifact, suggestionChips } from "./artifacts.js";

const THREADS_KEY = "ontoforge.threads";       // [{id,title,mtime}]
const THREAD_TURNS = (id) => `ontoforge.thread.${id}`;  // {turns:[...]}
const ACTIVE_KEY = "ontoforge.thread.active";

/* the agent mark (the brand glyph) — XSS-safe svgEl */
function agentMark(size = 15) {
  return svgEl("svg", {
    width: String(size), height: String(size), viewBox: "0 0 24 24", fill: "none",
    stroke: "currentColor", "stroke-width": "2", "stroke-linecap": "round",
    "stroke-linejoin": "round", "aria-hidden": "true",
  },
    svgEl("path", { d: "M12 3l8 4.5v9L12 21l-8-4.5v-9z" }),
    svgEl("path", { d: "M12 12l8-4.5M12 12v9M12 12L4 7.5" }));
}
function plusIcon(size = 15) {
  return svgEl("svg", {
    width: String(size), height: String(size), viewBox: "0 0 24 24", fill: "none",
    stroke: "currentColor", "stroke-width": "1.8", "stroke-linecap": "round",
    "aria-hidden": "true",
  }, svgEl("path", { d: "M12 5v14M5 12h14" }));
}
function sendIcon() {
  return svgEl("svg", {
    width: "17", height: "17", viewBox: "0 0 24 24", fill: "none", stroke: "currentColor",
    "stroke-width": "2", "stroke-linecap": "round", "stroke-linejoin": "round",
    "aria-hidden": "true",
  }, svgEl("path", { d: "M5 12h14M13 6l6 6-6 6" }));
}
function showArrow() {
  return svgEl("svg", {
    width: "14", height: "14", viewBox: "0 0 24 24", fill: "none", stroke: "currentColor",
    "stroke-width": "1.8", "stroke-linecap": "round", "stroke-linejoin": "round",
    "aria-hidden": "true",
  }, svgEl("path", { d: "M5 12h14M13 6l6 6-6 6" }));
}

/* narration with mono numbers: split on number-ish runs and wrap them in the
   <span class="m"> mono treatment so every number reads in the mono face. Built
   purely with text nodes — never innerHTML. */
function narrationNode(text) {
  const node = el("div", { class: "narr" });
  const s = String(text || "");
  const re = /(\d[\d,]*\.?\d*%?|\b[A-Z][a-zA-Z]*(?:[A-Z][a-zA-Z]*)+\b)/g;
  let last = 0, m;
  while ((m = re.exec(s)) !== null) {
    if (m.index > last) node.append(document.createTextNode(s.slice(last, m.index)));
    node.append(el("span", { class: "m" }, m[0]));
    last = m.index + m[0].length;
  }
  if (last < s.length) node.append(document.createTextNode(s.slice(last)));
  return node;
}

export function createAgentShell({ bus, els }) {
  const { rail, scroll, col, composerInput, composerForm, suggestBox } = els;

  let threads = store.get(THREADS_KEY, []);
  let activeId = store.get(ACTIVE_KEY, null);
  let busy = false;

  /* ───────────────────────────────────────── thread persistence */
  function loadTurns(id) { return store.get(THREAD_TURNS(id), { turns: [] }).turns || []; }
  function saveTurns(id, turns) { store.set(THREAD_TURNS(id), { turns }); }
  function touchThread(id, title) {
    const t = threads.find((x) => x.id === id);
    if (t) { t.mtime = Date.now(); if (title && t.title === "New thread") t.title = title; }
    threads.sort((a, b) => b.mtime - a.mtime);
    store.set(THREADS_KEY, threads);
    renderRail();
  }
  function newThread() {
    const id = `t${Date.now().toString(36)}`;
    threads.unshift({ id, title: "New thread", mtime: Date.now() });
    store.set(THREADS_KEY, threads);
    saveTurns(id, []);
    activeId = id;
    store.set(ACTIVE_KEY, id);
    renderRail();
    renderConversation(true);   // proactive opener on a fresh thread
    if (composerInput) composerInput.focus();
  }

  /* ───────────────────────────────────────── the thread-history rail */
  function relTime(ms) {
    const s = Math.floor((Date.now() - ms) / 1000);
    if (s < 60) return "just now";
    if (s < 3600) return `${Math.floor(s / 60)}m ago`;
    if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
    const d = new Date(ms);
    return d.toLocaleDateString("en-US", { month: "short", day: "numeric" });
  }
  function renderRail() {
    clear(rail);
    rail.append(el("button", { class: "newbtn", type: "button", onclick: newThread },
      el("span", { class: "ic" }, plusIcon()), "New thread"));
    if (!threads.length) {
      rail.append(el("div", { class: "railsec" }, "Threads"),
        el("p", { class: "rail-empty" }, "your conversations land here"));
      return;
    }
    const today = [], earlier = [];
    const dayStart = new Date(); dayStart.setHours(0, 0, 0, 0);
    for (const t of threads) (t.mtime >= dayStart.getTime() ? today : earlier).push(t);
    const section = (label, list) => {
      if (!list.length) return;
      rail.append(el("div", { class: "railsec" }, label));
      for (const t of list) {
        rail.append(el("div", {
          class: `thread${t.id === activeId ? " on" : ""}`,
          onclick: () => selectThread(t.id),
        },
          el("div", { class: "t" }, t.title || "New thread"),
          el("div", { class: "m mono" }, relTime(t.mtime))));
      }
    };
    section("Today", today);
    section("Earlier", earlier);
  }

  function selectThread(id) {
    if (id === activeId) return;
    activeId = id;
    store.set(ACTIVE_KEY, id);
    renderRail();
    renderConversation(false);
  }

  /* ───────────────────────────────────────── the conversation column */
  function turnWrap(child) { return el("div", { class: "turn" }, child); }

  function userTurn(text) {
    return turnWrap(el("div", { class: "user" }, el("div", { class: "bubble mono" }, text)));
  }

  function agentTurn(payload, { opener = false } = {}) {
    const abody = el("div", { class: "abody" }, el("div", { class: "aname" }, "OntoForge"));
    abody.append(narrationNode(payload.narration || ""));

    // opener carries stat chips + a "show the model" link
    if (opener && payload.stats) {
      const s = payload.stats;
      const chip = (n, label, cls) => el("span", { class: `chip${cls ? ` ${cls}` : ""}` },
        el("b", { class: "mono" }, fmt(n)), ` ${label}`);
      const chips = el("div", { class: "chips" },
        chip(s.entities, "entities"),
        chip(s.confirmed, "confirmed joins", "accent"),
        s.likely ? chip(s.likely, "likely joins", "warn") : null,
        s.standalone ? chip(s.standalone, "standalone") : null,
        s.datasets ? chip(s.datasets, "datasets") : null);
      abody.append(chips);
      if (payload.built) {
        abody.append(el("a", { class: "showlink", href: "#",
          onclick: (e) => { e.preventDefault(); submit("show me the model"); } },
          "show the model", showArrow()));
      }
    }

    // artifacts → inline cards via the reused renderers
    const opts = { sendPrompt: submit, ontology: ontologyNow() };
    for (const art of (payload.artifacts || [])) {
      const node = renderArtifact(art, opts);
      if (node) abody.append(node);
    }

    // clarification (one question, never a guess) reads as a calm note
    if (payload.clarification) {
      abody.append(el("div", { class: "clarify-inline" }, payload.clarification));
    }

    // follow-up chips re-submit through the composer
    const chips = suggestionChips(payload.followups, submit);
    if (chips) abody.append(chips);

    return turnWrap(el("div", { class: `agent${opener ? " opener" : ""}` },
      el("div", { class: "avatar" }, agentMark()), abody));
  }

  function appendTurn(node) {
    col.append(node);
    requestAnimationFrame(() => { scroll.scrollTop = scroll.scrollHeight; });
  }

  /* render an entire thread from its stored turns (history replay) */
  function renderConversation(freshOpener) {
    clear(col);
    const turns = activeId ? loadTurns(activeId) : [];
    if (!turns.length) {
      // a new/empty thread → the proactive opener
      fetchOpener();
      return;
    }
    for (const t of turns) {
      if (t.role === "user") appendTurn(userTurn(t.text));
      else if (t.role === "opener") appendTurn(agentTurn(t.payload, { opener: true }));
      else appendTurn(agentTurn(t.payload));
    }
  }

  /* ───────────────────────────────────────── the proactive opener */
  async function fetchOpener() {
    const loading = turnWrap(el("div", { class: "agent opener" },
      el("div", { class: "avatar" }, agentMark()),
      el("div", { class: "abody" },
        el("div", { class: "aname" }, "OntoForge"),
        skeletonCard([60, 40, 70]))));
    appendTurn(loading);
    let out;
    try {
      out = await api("/api/agent/opener", undefined, "GET");
    } catch (e) {
      loading.replaceWith(turnWrap(el("div", { class: "agent" },
        el("div", { class: "avatar" }, agentMark()),
        el("div", { class: "abody" },
          el("div", { class: "aname" }, "OntoForge"),
          el("div", { class: "narr" }, "I'm ready when you are — ask me anything about your data.")))));
      return;
    }
    const payload = {
      narration: out.narration, built: out.built, stats: out.stats,
      followups: out.followups || [],
    };
    loading.replaceWith(agentTurn(payload, { opener: true }));
    // persist the opener as the thread's first turn so history replays it
    if (activeId) {
      const turns = loadTurns(activeId);
      if (!turns.length) { turns.push({ role: "opener", payload }); saveTurns(activeId, turns); }
    }
  }

  /* ───────────────────────────────────────── the agent loop */
  async function submit(utterance) {
    utterance = String(utterance || "").trim();
    if (!utterance || busy) return;
    busy = true;
    closeSuggest();
    if (composerInput) { composerInput.value = ""; updatePlaceholder(); }

    if (!activeId) {            // first message creates the thread
      const id = `t${Date.now().toString(36)}`;
      threads.unshift({ id, title: "New thread", mtime: Date.now() });
      store.set(THREADS_KEY, threads);
      activeId = id; store.set(ACTIVE_KEY, id);
    }

    appendTurn(userTurn(utterance));
    const turns = loadTurns(activeId);
    turns.push({ role: "user", text: utterance });
    saveTurns(activeId, turns);
    touchThread(activeId, utterance.slice(0, 42));

    const thinking = turnWrap(el("div", { class: "agent" },
      el("div", { class: "avatar" }, agentMark()),
      el("div", { class: "abody" },
        el("div", { class: "aname" }, "OntoForge"),
        skeletonCard([45, 65, 35]))));
    appendTurn(thinking);

    let out;
    try {
      out = await api("/api/agent", { utterance, thread_id: activeId });
    } catch (e) {
      thinking.replaceWith(turnWrap(el("div", { class: "agent" },
        el("div", { class: "avatar" }, agentMark()),
        el("div", { class: "abody" },
          el("div", { class: "aname" }, "OntoForge"),
          errorNote(e)))));
      busy = false;
      return;
    }
    const payload = {
      narration: out.narration, artifacts: out.artifacts || [],
      followups: out.followups || [], clarification: out.clarification || null,
      intent: out.intent,
    };
    thinking.replaceWith(agentTurn(payload));
    const turns2 = loadTurns(activeId);
    turns2.push({ role: "agent", payload });
    saveTurns(activeId, turns2);
    if (out.intent === "build" || (out.artifacts || []).some((a) => a.kind === "datamap")) {
      // a build/show-model turn may have changed the world — refresh caches
      bus.emit("world:reload", {});
    }
    busy = false;
  }

  /* ───────────────────────────────────────── grounded typeahead (composer)
     reuses /api/suggest, the spotlight debounce + AbortController template. */
  let suggItems = [], suggSel = -1, suggOpen = false, abortCtl = null;
  const queryServer = debounce(async (q) => {
    if (abortCtl) abortCtl.abort();
    abortCtl = new AbortController();
    try {
      const res = await fetch(`/api/suggest?q=${encodeURIComponent(q)}&limit=12`, { signal: abortCtl.signal });
      if (!res.ok) { closeSuggest(); return; }
      const data = await res.json();
      if (composerInput.value.trim() !== q) return;
      renderSuggest(data);
    } catch { /* aborted / offline */ }
  }, 60);

  function closeSuggest() {
    suggOpen = false; suggItems = []; suggSel = -1;
    if (suggestBox) { clear(suggestBox); suggestBox.hidden = true; }
    if (composerInput) composerInput.setAttribute("aria-expanded", "false");
  }
  function renderSuggest(data) {
    if (!suggestBox) return;
    clear(suggestBox);
    suggItems = [];
    const groups = [
      ["Ask", (data.measures || []).map((m) => ({ q: m.question, label: m.label, meta: m.on_class }))],
      ["Entities", (data.entities || []).map((e) => ({ q: e.question, label: e.cls, meta: `${e.fields} fields` }))],
      ["Recalled", (data.questions || []).map((qq) => ({ q: qq.text, label: qq.text, meta: qq.kind }))],
    ];
    for (const [title, list] of groups) {
      if (!list.length) continue;
      suggestBox.append(el("div", { class: "cs-ghead" }, title));
      for (const it of list.slice(0, 6)) {
        const row = el("div", {
          class: "cs-row", role: "option", "aria-selected": "false",
          onpointerdown: (e) => e.preventDefault(),
          onclick: () => { closeSuggest(); submit(it.q); },
        },
          el("span", { class: "cs-name mono" }, it.label),
          it.meta ? el("span", { class: "cs-meta" }, String(it.meta)) : null);
        it.node = row;
        suggItems.push(it);
        suggestBox.append(row);
      }
    }
    if (!suggItems.length) { closeSuggest(); return; }
    suggSel = -1; suggOpen = true; suggestBox.hidden = false;
    composerInput.setAttribute("aria-expanded", "true");
  }
  function moveSuggest(delta) {
    if (!suggOpen || !suggItems.length) return;
    if (suggSel >= 0 && suggItems[suggSel]) suggItems[suggSel].node.setAttribute("aria-selected", "false");
    suggSel = (suggSel + delta + suggItems.length) % suggItems.length;
    const it = suggItems[suggSel];
    it.node.setAttribute("aria-selected", "true");
    if (it.node.scrollIntoView) it.node.scrollIntoView({ block: "nearest" });
  }

  function updatePlaceholder() {
    if (!composerInput) return;
    composerInput.classList.toggle("has-text", !!composerInput.value.trim());
  }

  /* ───────────────────────────────────────── composer wiring */
  function wireComposer() {
    composerForm.addEventListener("submit", (e) => {
      e.preventDefault();
      if (suggOpen && suggSel >= 0 && suggItems[suggSel]) {
        const it = suggItems[suggSel]; closeSuggest(); submit(it.q); return;
      }
      submit(composerInput.value);
    });
    composerInput.addEventListener("input", () => {
      updatePlaceholder();
      const q = composerInput.value.trim();
      if (!q) { closeSuggest(); return; }
      queryServer(q);
    });
    composerInput.addEventListener("keydown", (e) => {
      if (suggOpen && suggItems.length) {
        if (e.key === "ArrowDown") { e.preventDefault(); moveSuggest(1); return; }
        if (e.key === "ArrowUp") { e.preventDefault(); moveSuggest(-1); return; }
        if (e.key === "Escape") { e.preventDefault(); closeSuggest(); return; }
        if (e.key === "Tab") { e.preventDefault(); if (suggSel < 0) moveSuggest(1); const it = suggItems[suggSel]; if (it) composerInput.value = it.q; return; }
      }
    });
    composerInput.addEventListener("blur", () => setTimeout(closeSuggest, 140));
  }

  /* ───────────────────────────────────────── boot */
  function boot() {
    wireComposer();
    renderRail();
    if (activeId && !threads.find((t) => t.id === activeId)) activeId = null;
    if (!activeId && threads.length) { activeId = threads[0].id; store.set(ACTIVE_KEY, activeId); }
    renderConversation(true);
    requestAnimationFrame(() => composerInput && composerInput.focus());
  }

  return {
    boot,
    submit,                 // spotlight / external prompts route here
    newThread,
    focusComposer() { if (composerInput) composerInput.focus(); },
  };
}
