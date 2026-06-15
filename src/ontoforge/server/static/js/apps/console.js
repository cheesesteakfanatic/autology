/* Data-Engineering Console — the plain-English instruction box. One
   instruction at a time, command-oriented (not chat). Contract:
   POST /api/engineer/interpret {command} → {op,preview} (previewable step)
   | {clarification,options} (ask ONE) | {unsupported,reason,
   supported_examples} (fail to worked examples). ALWAYS preview, never apply
   blind. On Apply: POST /api/engineer/apply {op} → {…,undo_token}; the Data
   Map animates the delta and Undo is offered (POST /api/engineer/undo).
   Destructive previews carry a consequence and need an explicit Apply tap —
   nothing destructive on Enter alone. Unsupported commands never dead-end. */

import { el, clear, api, errorNote, toast } from "../core.js";

const DESTRUCTIVE = new Set(["merge_entities", "split", "remove", "remove_dataset"]);
const EXAMPLES = [
  "Link Orders to Customers on customer_id",
  "Treat these two as the same thing",
  "Rename type Invoices to Bills",
  "Hide field internal_notes",
  "Treat amount as currency",
];

export function createConsoleApp() {
  return {
    id: "console",
    title: "Data-Engineering Console",
    tagline: "tell me what to do with your data, in plain English",
    glyph: "❯",
    w: 620, h: 540, multi: false,

    mount(ctx) {
      ctx.root.classList.add("app-console");
      const scrollback = el("div", { class: "console-scrollback" });
      const preview = el("div", { class: "console-preview" });
      const input = el("input", {
        class: "console-input", type: "text", spellcheck: "false",
        placeholder: "Tell me what to do with your data, in plain English…",
      });
      const examplesLink = el("button", { class: "range-link", type: "button", onclick: toggleExamples }, "Examples");
      const exampleChips = el("div", { class: "console-examples", hidden: "hidden" });
      const form = el("form", { class: "console-form", autocomplete: "off" },
        el("div", { class: "console-field" }, input,
          el("button", { class: "btn btn-forge", type: "submit" }, "Preview")),
        el("div", { class: "console-foot" }, examplesLink, exampleChips));
      ctx.root.append(scrollback, preview, form);

      let lastUndo = null;

      function renderExamples() {
        clear(exampleChips);
        for (const ex of EXAMPLES) {
          exampleChips.append(el("button", {
            class: "chip example-chip", type: "button",
            onclick: () => { input.value = ex; input.focus(); interpret(ex); },
          }, ex));
        }
      }
      function toggleExamples() {
        exampleChips.hidden = !exampleChips.hidden;
        if (!exampleChips.hidden && !exampleChips.childNodes.length) renderExamples();
      }

      function pushScroll(text, outcome) {
        const node = el("div", { class: `scroll-line outcome-${outcome}` },
          el("span", { class: "scroll-cmd" }, text),
          el("span", { class: "scroll-outcome" }, outcome));
        scrollback.append(node);
        scrollback.scrollTop = scrollback.scrollHeight;
        return node;
      }

      function renderUnsupported(res, command) {
        clear(preview);
        preview.append(el("div", { class: "console-card unsupported" },
          el("p", { class: "console-msg" }, "I couldn't turn that into a data step."),
          res.reason ? el("p", { class: "console-echo" }, res.reason) : null,
          el("span", { class: "section-label" }, "Here's what I can do:"),
          el("div", { class: "console-examples" },
            (res.supported_examples && res.supported_examples.length ? res.supported_examples : EXAMPLES)
              .map((ex) => el("button", {
                class: "chip example-chip", type: "button",
                onclick: () => { input.value = ex; interpret(ex); },
              }, ex)))));
      }

      function renderClarification(res, command) {
        clear(preview);
        preview.append(el("div", { class: "console-card clarify" },
          el("span", { class: "section-label" }, "one quick question"),
          el("p", { class: "console-msg" }, res.clarification),
          el("div", { class: "console-options" },
            (res.options || []).map((opt) => el("button", {
              class: "btn", type: "button",
              onclick: () => interpret(`${command} ${opt}`),
            }, opt)))));
      }

      function renderPreview(res) {
        clear(preview);
        const op = res.op;
        const pv = res.preview || {};
        const destructive = DESTRUCTIVE.has(op.kind);
        const card = el("div", { class: `console-card preview-card${destructive ? " destructive" : ""}` },
          el("div", { class: "preview-head" },
            el("span", { class: "section-label" }, "preview — nothing has changed yet"),
            op.confidence !== undefined && op.confidence !== null
              ? el("span", { class: "badge badge-amber" }, `confidence ${Number(op.confidence).toFixed(2)}`) : null),
          el("p", { class: "preview-desc" }, pv.description || op.human_summary || "this step would change the model"),
          pv.affected_count !== undefined
            ? el("p", { class: "preview-count" }, `${Number(pv.affected_count).toLocaleString("en-US")} affected`) : null,
          destructive ? el("p", { class: "preview-consequence" }, "this can't be undone automatically beyond the last step — review it first") : null);

        if (pv.sample && pv.sample.length) {
          const rows = pv.sample.slice(0, 6).map((s) =>
            el("div", { class: "preview-sample mono" }, typeof s === "string" ? s : JSON.stringify(s)));
          card.append(el("div", { class: "preview-samples" }, rows));
        }

        const apply = el("button", { class: "btn btn-forge", type: "button", onclick: () => doApply(op) }, "Apply");
        const cancel = el("button", { class: "btn", type: "button", onclick: () => clear(preview) }, "Cancel");
        card.append(el("div", { class: "preview-actions" }, apply, cancel));
        preview.append(card);
      }

      async function interpret(command) {
        command = String(command || "").trim();
        if (!command) return;
        clear(preview).append(el("div", { class: "skeleton", style: "width:60%" }));
        try {
          const res = await api("/api/engineer/interpret", { command });
          if (res.unsupported || (res.supported_examples && !res.op && !res.clarification)) {
            renderUnsupported(res, command);
          } else if (res.clarification) {
            renderClarification(res, command);
          } else if (res.op) {
            renderPreview(res);
          } else {
            renderUnsupported({ reason: "I didn't understand that.", supported_examples: EXAMPLES }, command);
          }
        } catch (e) {
          if (e.status === 404 || e.status === 405) {
            renderUnsupported({
              reason: "the console isn't exposed by this build — the same operations run from the CLI.",
              supported_examples: EXAMPLES,
            }, command);
          } else {
            clear(preview).append(errorNote(e));
          }
        }
      }

      async function doApply(op) {
        clear(preview).append(el("div", { class: "skeleton", style: "width:50%" }));
        try {
          const out = await api("/api/engineer/apply", { op });
          lastUndo = out.undo_token || null;
          const line = pushScroll(op.human_summary || op.kind, "applied");
          // offer Undo on the scrollback line
          if (lastUndo) {
            line.append(el("button", {
              class: "scroll-undo", type: "button",
              onclick: () => doUndo(lastUndo, line),
            }, "Undo"));
          }
          toast(out.human_summary || "applied", { kind: "ok" });
          // the Data Map redraws from the delta
          ctx.emit("studio:atlas-delta", { delta: out.atlas_delta || null, stats: out.new_stats || null });
          ctx.emit("world:reload", {});
          clear(preview);
          input.value = "";
        } catch (e) {
          clear(preview).append(errorNote(e));
        }
      }

      async function doUndo(token, line) {
        try {
          const out = await api("/api/engineer/undo", { undo_token: token });
          if (line) {
            const u = line.querySelector(".scroll-undo");
            if (u) u.replaceWith(el("span", { class: "scroll-undone" }, "undone"));
          }
          toast("undone", { kind: "ok" });
          ctx.emit("world:reload", {});
        } catch (e) {
          toast("couldn't undo — see the CLI", { kind: "warn" });
        }
      }

      form.addEventListener("submit", (e) => { e.preventDefault(); interpret(input.value); });
      // ghosted example rotation invites the first instruction
      renderExamples();
      requestAnimationFrame(() => input.focus());
      return {};
    },
  };
}
