/* Spotlight — the front door. A pre-mounted glass field summoned by ⌘K,
   "/", or just typing on the empty workspace. Local registries (apps, open
   windows, recent questions, induced classes) filter synchronously on every
   keystroke — never debounced; GET /api/search rides behind a short debounce
   with AbortController cancellation and merges by score without yanking the
   list out from under the selection. Ranking: exact-prefix > word-prefix >
   substring > fuzzy (fzy-style boundary/run bonuses). Free text ending in
   '?' — or matching nothing — falls through to 'Ask the estate', so no query
   ever dead-ends. WAI-ARIA combobox: focus stays in the input,
   aria-activedescendant carries the highlight. */

import { $, el, clear, store, ontologyNow, debounce } from "./core.js";

const KIND_GLYPHS = {
  app: "▣", window: "▢", class: "✶", entity: "◈",
  property: "∷", question: "❝", ask: "❯",
};
const RECENT_QUESTIONS_KEY = "ontoforge.recent.questions";

/* The server's static app registry uses legacy ids that diverge from the
   JS micro-app ids (server: entities/status/export; JS: inspector/pulse/
   exporter). Map them so a server app result routes correctly; ids that
   match the JS registry pass through unchanged. */
const SERVER_APP_ALIAS = {
  entities: "inspector",
  status: "pulse",
  export: "exporter",
};

/* ─────────────────────────── ranking: exact-prefix > word-prefix >
   substring > fuzzy subsequence with boundary/consecutive-run bonuses */

const isBoundary = (text, i) =>
  i === 0 || /[\s_\-./:#]/.test(text[i - 1]) ||
  (text[i] >= "A" && text[i] <= "Z" && text[i - 1] >= "a" && text[i - 1] <= "z");

export function matchScore(query, text) {
  const q = query.toLowerCase(), t = String(text).toLowerCase();
  if (!q) return 0;
  if (t.startsWith(q)) return 1000 - Math.min(200, t.length - q.length);
  const sub = t.indexOf(q);
  if (sub !== -1) {
    return isBoundary(String(text), sub)
      ? 800 - Math.min(200, sub)
      : 600 - Math.min(200, sub);
  }
  // fuzzy: query chars in order, scored by runs and word starts
  let score = 0, ti = 0, prev = -2;
  for (let qi = 0; qi < q.length; qi++) {
    const found = t.indexOf(q[qi], ti);
    if (found === -1) return null;
    score += 8;
    if (found === prev + 1) score += 10;                  // consecutive run
    if (isBoundary(String(text), found)) score += 14;     // word/camel start
    score -= Math.min(6, found - ti);                     // affine-ish gap cost
    prev = found;
    ti = found + 1;
  }
  return Math.max(1, Math.min(560, 300 + score - Math.min(120, t.length)));
}

export function createSpotlight({ root, input, listEl, countEl, registry, wm, bus }) {
  let items = [];
  let sel = 0;
  let openState = false;
  let lastFocused = null;
  let abortCtl = null;
  let serverResults = [];
  let serverFor = "";

  /* ───────────────────────────────────────────── local result sources */

  function localItems(q) {
    const out = [];
    for (const spec of registry.all()) {
      const s = q ? matchScore(q, spec.title) ?? matchScore(q, spec.id) : 250;
      if (s !== null) {
        out.push({ kind: "app", title: spec.title, subtitle: spec.tagline || "", ref: spec.id, score: s + 40 });
      }
    }
    for (const w of wm.list()) {
      const label = w.title || w.app.title;
      const s = q ? matchScore(q, label) : null;
      if (s !== null && q) {
        out.push({ kind: "window", title: label, subtitle: w.minimized ? "minimized window" : "open window", ref: w.id, score: s + 60 });
      }
    }
    const recents = store.get(RECENT_QUESTIONS_KEY, []);
    recents.forEach((question, i) => {
      const s = q ? matchScore(q, question) : 200 - i;
      if (s !== null) {
        out.push({ kind: "question", title: question, subtitle: "recent ask", ref: question, score: s });
      }
    });
    const onto = ontologyNow();
    if (onto && q) {
      for (const c of onto.classes) {
        const s = matchScore(q, c.name);
        if (s !== null) {
          out.push({ kind: "class", title: c.name, subtitle: "a type in the model", ref: c.uri, score: s + 20 });
        }
        for (const p of c.properties) {
          const ps = matchScore(q, p.name);
          if (ps !== null && ps >= 600) {
            out.push({ kind: "property", title: p.name, subtitle: `property of ${c.name}`, ref: `${c.uri}#${p.name}`, score: ps - 30 });
          }
        }
      }
    }
    return out;
  }

  /* ─────────────────────────── server provider: debounced + abortable */

  const queryServer = debounce(async (q) => {
    if (abortCtl) abortCtl.abort();
    abortCtl = new AbortController();
    try {
      const res = await fetch(`/api/search?q=${encodeURIComponent(q)}&limit=20`, { signal: abortCtl.signal });
      if (!res.ok) return; // endpoint optional — local results stand alone
      const out = await res.json();
      serverResults = [];
      for (const r of out.results || []) {
        let ref = r.ref;
        if (r.kind === "app") {
          // route legacy server app ids to the real JS micro-apps; drop any
          // that still don't resolve so no Spotlight row dead-ends on click
          ref = SERVER_APP_ALIAS[ref] || ref;
          if (!registry.get(ref)) continue;
        }
        serverResults.push({
          kind: r.kind, title: r.title, subtitle: r.subtitle || "", ref,
          score: Math.round((r.score || 0) * 900),
        });
      }
      serverFor = q;
      if (openState && input.value.trim() === q) rebuild(false);
    } catch { /* aborted or offline — keep local results */ }
  }, 45);

  /* ─────────────────────────────────────────────────── list assembly */

  function rebuild(resetSel = true) {
    const q = input.value.trim();
    const merged = new Map(); // kind|ref -> item
    for (const it of localItems(q)) {
      const key = `${it.kind}|${it.ref}`;
      if (!merged.has(key) || merged.get(key).score < it.score) merged.set(key, it);
    }
    if (q && serverFor === q) {
      for (const it of serverResults) {
        const key = `${it.kind}|${it.ref}`;
        if (!merged.has(key) || merged.get(key).score < it.score) merged.set(key, it);
      }
    }
    items = [...merged.values()].sort((a, b) => b.score - a.score).slice(0, 12);

    // the guaranteed fallback: free text is always askable
    if (q) {
      const ask = { kind: "ask", title: `Ask — “${q}”`, subtitle: "a cited answer, or an honest 'won't guess'", ref: q, score: -1, fallback: true };
      if (q.endsWith("?") || !items.length) items.unshift(ask);
      else items.push(ask);
    }
    if (resetSel) sel = 0;
    sel = Math.max(0, Math.min(items.length - 1, sel));
    render();
    if (q) queryServer(q);
  }

  /** A short, stable tail of an entity ref/uri — the last id segment —
      used to disambiguate rows that share a non-unique display label. */
  function refTail(ref) {
    const seg = String(ref).split(/[/#]/).filter(Boolean).pop() || String(ref);
    return seg.length > 14 ? `…${seg.slice(-12)}` : seg;
  }

  function render() {
    clear(listEl);
    // labels are often non-unique (a port-of-loading name shared across many
    // contracts). Find which titles repeat so we can append a disambiguating
    // ref tail — otherwise identical rows crowd out distinct hits.
    const titleCounts = new Map();
    for (const it of items) {
      if (it.kind === "entity") titleCounts.set(it.title, (titleCounts.get(it.title) || 0) + 1);
    }
    items.forEach((item, i) => {
      const ambiguous = item.kind === "entity" && titleCounts.get(item.title) > 1;
      const title = el("span", {
        class: `si-title${item.kind === "question" || item.kind === "ask" ? " serif" : ""}${item.kind === "entity" ? " mono" : ""}`,
      }, item.title);
      if (ambiguous) title.append(el("span", { class: "si-disamb" }, ` · ${refTail(item.ref)}`));
      listEl.append(el("div", {
        class: `spot-item${i === sel ? " active" : ""}${item.fallback ? " fallback" : ""}`,
        id: `spot-opt-${i}`, role: "option",
        "aria-selected": i === sel ? "true" : "false",
        onpointerdown: (e) => e.preventDefault(), // keep focus in the input
        onclick: () => run(item),
      },
        el("span", { class: "si-glyph", "aria-hidden": "true" }, KIND_GLYPHS[item.kind] || "·"),
        el("span", { class: "si-main" },
          title,
          item.subtitle ? el("span", { class: "si-sub" }, item.subtitle) : null),
        el("span", { class: "si-kind" }, item.kind)));
    });
    if (!items.length) {
      listEl.append(el("div", { class: "spot-empty" }, "find anything — types, records, questions, tools — or just ask"));
    }
    input.setAttribute("aria-activedescendant", items.length ? `spot-opt-${sel}` : "");
    if (countEl) countEl.textContent = items.length ? `${items.length} result${items.length === 1 ? "" : "s"}` : "no results";
    const active = listEl.children[sel];
    if (active && active.scrollIntoView) active.scrollIntoView({ block: "nearest" });
  }

  /* ────────────────────────────────────────────────── intent routing */

  function run(item) {
    close();
    switch (item.kind) {
      case "app":      bus.emit("app:launch", { app: item.ref }); break;
      case "window": { const w = wm.get(item.ref); if (w) wm.focus(w); break; }
      case "entity":   bus.emit("entity:open", { uri: item.ref }); break;
      case "class":    bus.emit("class:focus", { uri: item.ref }); break;
      case "property": {
        const [uri, prop] = item.ref.split("#");
        bus.emit("class:focus", { uri, prop });
        break;
      }
      case "question": bus.emit("ask:run", { question: item.ref }); break;
      case "ask":      bus.emit("ask:run", { question: item.ref }); break;
      default: break;
    }
  }

  /* ─────────────────────────────────────────────────── open / close */

  function open(prefill = "") {
    if (openState) { input.focus(); return; }
    openState = true;
    lastFocused = document.activeElement;
    root.hidden = false;
    requestAnimationFrame(() => root.classList.add("open"));
    input.value = prefill;
    input.setAttribute("aria-expanded", "true");
    serverResults = [];
    serverFor = "";
    rebuild();
    input.focus();
    input.setSelectionRange(input.value.length, input.value.length);
  }

  function close() {
    if (!openState) return;
    openState = false;
    root.classList.remove("open");
    root.hidden = true;
    input.setAttribute("aria-expanded", "false");
    queryServer.cancel();
    if (abortCtl) abortCtl.abort();
    if (lastFocused && lastFocused.focus && document.contains(lastFocused)) {
      lastFocused.focus({ preventScroll: true });
    }
  }

  input.addEventListener("input", () => rebuild(true));

  input.addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown") { e.preventDefault(); sel = Math.min(items.length - 1, sel + 1); render(); }
    else if (e.key === "ArrowUp") { e.preventDefault(); sel = Math.max(0, sel - 1); render(); }
    else if (e.key === "Enter") {
      e.preventDefault();
      const item = items[sel] || (input.value.trim() && { kind: "ask", ref: input.value.trim() });
      if (item) run(item);
    } else if (e.key === "Escape") {
      e.preventDefault();
      close();
    } else if ((e.metaKey || e.ctrlKey) && /^[1-9]$/.test(e.key)) {
      const i = Number(e.key) - 1;
      if (i < items.length) { e.preventDefault(); run(items[i]); }
    }
  });

  root.addEventListener("pointerdown", (e) => { if (e.target === root) close(); });

  return { open, close, toggle: () => (openState ? close() : open()), isOpen: () => openState };
}
