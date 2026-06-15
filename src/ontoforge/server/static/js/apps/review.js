/* Review — human adjudication in a window. Queue cards with j/k/a/r
   keys (routed by the WM to the focused window only), the recalibration
   arc per decision kind, ER pairs side-by-side, and verdicts that feed
   the spine's recalibration flywheel. */

import { el, svgEl, clear, api, errorNote, confGauge, toast } from "../core.js";

const ER_PAIR_RE = /^er:([^:]+):(.+)\|\|(.+)$/;

export function createReviewApp() {
  return {
    id: "review",
    title: "Confirm suggestions",
    tagline: "are these the same thing?",
    glyph: "⚖",
    w: 620, h: 560, multi: false,

    mount(ctx) {
      const tally = el("div", { class: "review-tally" });
      const keysHint = el("div", { class: "review-keys" },
        el("span", {}, el("kbd", {}, "j"), "/", el("kbd", {}, "k"), " move"),
        el("span", {}, el("kbd", {}, "a"), " confirm"),
        el("span", {}, el("kbd", {}, "r"), " not the same"));
      const queue = el("div", { class: "review-queue" }, el("div", { class: "skeleton-card" }));
      ctx.root.append(tally, keysHint, queue);
      ctx.root.classList.add("app-review");

      let reviewSel = -1;
      let reloadTimer = null;
      ctx.addDisposer(() => clearTimeout(reloadTimer));

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
        clear(tally);
        const kinds = new Set([...Object.keys(data.verdicts), ...Object.keys(data.recalibrations)]);
        for (const it of data.items) kinds.add(it.kind);
        for (const kind of [...kinds].sort()) {
          const n = data.verdicts[kind] || 0;
          const toward = n % data.threshold;
          const recals = data.recalibrations[kind] || 0;
          tally.append(el("div", { class: "tally-block" },
            tallyArc(toward, data.threshold, recals),
            el("div", {},
              el("div", { class: "tally-kind" }, `${kind} — learning from your confirmations`),
              el("div", { class: "tally-sub" },
                `${n} confirmation${n === 1 ? "" : "s"} · ${recals} time${recals === 1 ? "" : "s"} the engine learned`))));
        }
      }

      function evidencePair(item) {
        const m = ER_PAIR_RE.exec(item.decision_id);
        if (!m) return null;
        const [, , a, b] = m;
        const side = (label, uri) => el("div", { class: "pair-side" },
          el("span", { class: "pair-label" }, label),
          uri,
          uri.startsWith("ent://")
            ? el("div", {}, el("button", {
                class: "range-link", type: "button", style: "font-size:var(--fs-0)",
                onclick: () => ctx.emit("entity:open", { uri }),
              }, "explore record →"))
            : null);
        return el("div", { class: "pair-grid" },
          side("one record", a),
          el("span", { class: "pair-vs" }, "same thing?"),
          side("the other record", b));
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
            const msg = `${v === "accept" ? "confirmed" : "marked not the same"} · ${out.verdicts_for_kind} confirmation${out.verdicts_for_kind === 1 ? "" : "s"}`;
            clear(actions).append(el("span", {
              class: `verdict-result${out.recalibrated ? " recalibrated" : ""}`,
            }, msg, out.recalibrated ? " · the engine learned from this" : ""));
            toast(
              out.recalibrated ? "the engine learned from your confirmations" : msg,
              { kind: out.recalibrated ? "ok" : "info" });
            clearTimeout(reloadTimer);
            reloadTimer = setTimeout(load, 750);
          } catch (e) {
            actions.append(errorNote(e));
            for (const b of actions.querySelectorAll("button")) b.disabled = false;
          }
        }

        actions.append(
          el("button", { class: "btn btn-accept", type: "button", onclick: () => verdict("accept") }, "Confirm (a)"),
          el("button", { class: "btn btn-reject", type: "button", onclick: () => verdict("reject") }, "Not the same (r)"),
          note);

        const card = el("div", {
          class: "review-card", dataset: { idx: String(idx) },
          onclick: () => selectReview(idx, false),
        },
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
                  class: "range-link", type: "button", style: "margin-left:0.75em",
                  onclick: () => ctx.emit("evidence:atoms", {
                    atomIds: item.prov_atoms, label: item.decision_id,
                  }),
                }, `where this came from ⌗${item.prov_atoms.length}`)
              : null),
          el("div", {}, item.conformal_set.map((c) =>
            el("span", { class: `conformal-chip${c === item.outcome ? " chosen" : ""}` }, c))),
          confGauge(item.confidence),
          actions);
        card._verdict = verdict;
        return card;
      }

      function selectReview(idx, scroll = true) {
        const cards = queue.querySelectorAll(".review-card");
        if (!cards.length) { reviewSel = -1; return; }
        reviewSel = Math.max(0, Math.min(cards.length - 1, idx));
        cards.forEach((c, i) => c.classList.toggle("selected", i === reviewSel));
        if (scroll) cards[reviewSel].scrollIntoView({ block: "nearest", behavior: "smooth" });
      }

      async function load() {
        try {
          const data = await api("/api/review");
          renderTally(data);
          clear(queue);
          ctx.setTitle(data.items.length ? `Confirm suggestions — ${data.items.length} pending` : "Confirm suggestions");
          // tell the shell how many are pending, for the STUDIO badge
          ctx.emit("review:count", { count: data.items.length });
          if (!data.items.length) {
            queue.append(el("div", { class: "empty-note" },
              "Nothing to confirm — the engine is confident right now."));
            reviewSel = -1;
            return;
          }
          data.items.forEach((item, i) => queue.append(reviewCard(item, i)));
          selectReview(reviewSel === -1 ? 0 : reviewSel, false);
        } catch (e) {
          clear(queue).append(errorNote(e));
        }
      }

      load();

      return {
        onKey(e) {
          const cards = queue.querySelectorAll(".review-card");
          if (!cards.length) return false;
          if (e.key === "j") { selectReview(reviewSel + 1); e.preventDefault(); return true; }
          if (e.key === "k") { selectReview(reviewSel - 1); e.preventDefault(); return true; }
          if ((e.key === "a" || e.key === "r") && reviewSel >= 0) {
            cards[reviewSel]._verdict(e.key === "a" ? "accept" : "reject");
            e.preventDefault();
            return true;
          }
          return false;
        },
      };
    },
  };
}
