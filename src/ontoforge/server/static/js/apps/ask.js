/* Ask — the console reborn as a window. Cited answers, one-keystroke
   clarifications, and calibrated abstention as a dignified first-class
   state. Cite-dots open an Evidence window (a child, spawned adjacent)
   instead of a rail. */

import {
  el, clear, api, errorNote, confGauge, skeletonCard, store,
  loadOntology, ontologyNow,
} from "../core.js";

const RECENT_KEY = "ontoforge.recent.questions";

export function createAskApp() {
  return {
    id: "ask",
    title: "Ask",
    tagline: "cited answers from the estate",
    glyph: "❯",
    w: 620, h: 520, multi: true,

    mount(ctx, params) {
      let activeClarify = null;
      let lastQuestion = params.question || "";

      const input = el("input", {
        class: "ask-input", type: "text", spellcheck: "false",
        placeholder: "ask the estate — every value cites its source atoms",
      });
      const button = el("button", { class: "btn btn-forge", type: "submit" }, "Ask");
      const history = el("div", { class: "ask-history" });
      const result = el("div", { class: "ask-result", "aria-live": "polite" });
      const form = el("form", { class: "ask-form", autocomplete: "off" },
        el("div", { class: "ask-field" }, input, button));

      ctx.root.append(form, history, result);
      ctx.root.classList.add("app-ask");

      function recents() { return store.get(RECENT_KEY, []); }

      function renderHistory() {
        clear(history);
        for (const q of recents().slice(0, 6)) {
          history.append(el("button", { class: "chip history-chip", type: "button", title: q, onclick: () => run(q) }, q));
        }
      }

      function pushHistory(question) {
        const next = [question, ...recents().filter((q) => q !== question)].slice(0, 24);
        store.set(RECENT_KEY, next);
        renderHistory();
      }

      function abstainHelp() {
        const onto = ontologyNow();
        const chips = (onto ? onto.classes : [])
          .slice()
          .sort((a, b) => b.properties.length - a.properties.length)
          .slice(0, 6)
          .map((c) => el("button", {
            class: "chip", type: "button", title: c.uri,
            onclick: () => {
              input.value = `${input.value.trim()} ${c.name}`.trim();
              input.focus();
            },
          }, c.name));
        return [
          el("p", { class: "abstain-help" },
            "what would make this answerable — ground the question in the induced ontology ",
            el("button", {
              class: "range-link", type: "button",
              onclick: () => ctx.emit("class:focus", {}),
            }, "(open the constellation)"),
            chips.length ? " or build on one of its classes:" : ""),
          chips.length ? el("div", { class: "clarify-options" }, chips) : null,
        ];
      }

      function citeDot(ids, label, holder) {
        return el("button", {
          class: "cite-dot", type: "button",
          title: `${ids.length} source atom${ids.length === 1 ? "" : "s"} — click for evidence`,
          "aria-label": `evidence for ${label}`,
          onclick: () => {
            ctx.emit("evidence:atoms", { atomIds: ids, label, anchor: holder });
          },
        });
      }

      function renderAnswer(out) {
        const target = clear(result);
        activeClarify = null;

        if (out.clarification) {
          activeClarify = { question: out.question, options: out.clarification_options };
          target.append(el("div", { class: "clarify-card" },
            el("span", { class: "section-label" }, "one clarification, then an answer"),
            el("p", { class: "clarify-q" }, out.clarification),
            el("div", { class: "clarify-options" },
              out.clarification_options.map((opt, i) =>
                el("button", { class: "clarify-option", type: "button", onclick: () => clarifyChoice(i) },
                  el("kbd", {}, String(i + 1)), opt)))));
          return;
        }

        // abstention: never an error style — OntoForge declines to guess
        if (out.abstained) {
          target.append(el("div", { class: "answer-card state-abstained" },
            el("span", { class: "abstain-mark" }, "abstained"),
            el("p", { class: "abstain-line" }, "OntoForge declines to guess."),
            el("p", { class: "abstain-reason" }, out.abstain_reason || "no derivation reached the answer floor"),
            abstainHelp(),
            confGauge(out.confidence, "confidence · below the floor")));
          return;
        }

        const cites = new Map();
        for (const c of out.citations) cites.set(`${c.row}|${c.column}`, c.atom_ids);

        const card = el("div", { class: "answer-card" },
          el("p", { class: "answer-q" }, out.question,
            out.cached ? el("span", { class: "cached-mark" }, "· instant — answer cache") : null));

        if (out.rows.length === 1 && out.columns.length === 1) {
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

      async function run(question) {
        question = String(question || "").trim();
        if (!question) return;
        input.value = question;
        lastQuestion = question;
        ctx.setTitle(`Ask — ${question.length > 40 ? `${question.slice(0, 40)}…` : question}`);
        button.disabled = true;
        const target = clear(result);
        target.append(skeletonCard());
        try {
          const out = await api("/api/ask", { question });
          pushHistory(question);
          renderAnswer(out);
        } catch (e) {
          clear(target).append(errorNote(e));
        } finally {
          button.disabled = false;
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

      form.addEventListener("submit", (e) => {
        e.preventDefault();
        run(input.value);
      });

      renderHistory();
      loadOntology().catch(() => { /* abstain chips simply stay empty */ });
      if (params.question) run(params.question);
      else requestAnimationFrame(() => input.focus());

      return {
        run,
        params: () => ({ question: lastQuestion || undefined }),
        onKey(e) {
          if (activeClarify && /^[1-9]$/.test(e.key)) {
            const i = Number(e.key) - 1;
            if (i < activeClarify.options.length) { e.preventDefault(); clarifyChoice(i); }
            return true;
          }
          return false;
        },
      };
    },
  };
}
