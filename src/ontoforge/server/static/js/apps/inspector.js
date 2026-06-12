/* Inspector — one entity, made visceral. The property card under a
   temporal stance, the as-of TIME SCRUBBER (drag the handle, watch values
   change), per-property bitemporal history bars whose clicks open the
   derivation in an Evidence window, and the neighbors list — where
   clicking a neighbor opens ANOTHER Inspector beside this one: the OS
   moment. Multiple instances are the point. */

import { el, clear, api, errorNote, store } from "../core.js";

const RECENT_KEY = "ontoforge.recent.entities";

export function createInspectorApp() {
  return {
    id: "inspector",
    title: "Inspector",
    tagline: "one entity, every value it ever held",
    glyph: "◈",
    w: 680, h: 560, multi: true,

    mount(ctx, params) {
      const input = el("input", {
        class: "entity-input mono", type: "text", spellcheck: "false",
        placeholder: "ent://…  (paste a URI, or arrive here from Spotlight)",
      });
      const form = el("form", { class: "entity-form", autocomplete: "off" },
        input, el("button", { class: "btn btn-forge", type: "submit" }, "Inspect"));
      const recentBox = el("div", { class: "entity-recent" });
      const scrubBox = el("div", { class: "time-scrubber", hidden: "hidden" });
      const body = el("div", { class: "entity-body" },
        el("div", { class: "empty-note" }, "no entity loaded — the as-of scrubber appears when one is"));
      ctx.root.append(form, recentBox, scrubBox, body);
      ctx.root.classList.add("app-inspector");

      let currentUri = params.uri || null;
      let history = null;
      let domain = null;
      let scrubT = null;
      let lastProps = {};
      let fetchTimer = null;
      let historyCursors = [];
      let stanceHolder = null;

      function remember(uri) {
        const recent = [uri, ...store.get(RECENT_KEY, []).filter((u) => u !== uri)].slice(0, 6);
        store.set(RECENT_KEY, recent);
        renderRecent();
      }

      function renderRecent() {
        clear(recentBox);
        for (const uri of store.get(RECENT_KEY, [])) {
          recentBox.append(el("button", {
            class: "chip mono", type: "button", title: uri, onclick: () => load(uri),
          }, uri));
        }
      }

      const ms = (iso) => (iso ? Date.parse(iso) : null);
      const dayISO = (t) => new Date(t).toISOString().slice(0, 10);

      function computeDomain(hist) {
        let lo = Infinity, hi = -Infinity;
        for (const cells of Object.values(hist)) {
          for (const c of cells) {
            const a = ms(c.valid_from); if (a !== null) { lo = Math.min(lo, a); hi = Math.max(hi, a); }
            const b = ms(c.valid_to);   if (b !== null) { lo = Math.min(lo, b); hi = Math.max(hi, b); }
          }
        }
        const now = Date.now();
        if (!isFinite(lo)) { lo = now - 365 * 864e5; }
        hi = Math.max(isFinite(hi) ? hi : now, now);
        if (hi - lo < 864e5) { lo -= 30 * 864e5; hi += 30 * 864e5; }
        const pad = (hi - lo) * 0.04;
        return [lo - pad, hi + pad];
      }

      const frac = (t) => (t - domain[0]) / (domain[1] - domain[0]);
      const pct = (t) => `${(Math.max(0, Math.min(1, frac(t))) * 100).toFixed(2)}%`;

      // ─────────────────────────────────────────────────── the scrubber

      function renderScrubber() {
        clear(scrubBox);
        scrubBox.hidden = false;

        const stanceLabel = el("span", { class: "scrub-stance" },
          scrubT === null ? "stance: current" : `stance: as-of ${dayISO(scrubT)}`);

        const track = el("div", { class: "scrub-track", title: "drag — the as-of instant" });
        const ticks = new Set();
        for (const cells of Object.values(history)) {
          for (const c of cells) {
            const a = ms(c.valid_from); if (a !== null) ticks.add(a);
            const b = ms(c.valid_to);   if (b !== null) ticks.add(b);
          }
        }
        for (const t of ticks) {
          if (t >= domain[0] && t <= domain[1]) {
            track.append(el("span", { class: "scrub-tick", style: `left:${pct(t)}`, title: dayISO(t) }));
          }
        }
        const handle = el("span", { class: "scrub-handle", style: `left:${pct(scrubT === null ? Date.now() : scrubT)}` });
        track.append(handle);

        function setFromEvent(e, rect) {
          const f = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
          scrubT = domain[0] + f * (domain[1] - domain[0]);
          handle.style.left = pct(scrubT);
          stanceLabel.textContent = `stance: as-of ${dayISO(scrubT)}`;
          for (const cur of historyCursors) { cur.style.left = pct(scrubT); cur.hidden = false; }
          scheduleFetch();
        }

        track.addEventListener("pointerdown", (e) => {
          if (e.button !== 0) return;
          const rect = track.getBoundingClientRect(); // read once per gesture
          try { track.setPointerCapture(e.pointerId); } catch { /* synthetic pointer */ }
          setFromEvent(e, rect);
          const move = (ev) => setFromEvent(ev, rect);
          const up = () => {
            track.removeEventListener("pointermove", move);
            track.removeEventListener("pointerup", up);
            track.removeEventListener("pointercancel", up);
          };
          track.addEventListener("pointermove", move);
          track.addEventListener("pointerup", up);
          track.addEventListener("pointercancel", up);
        });

        scrubBox.append(
          el("div", { class: "scrub-head" },
            el("span", { class: "section-label", style: "margin:0" }, "as-of time scrubber"),
            stanceLabel,
            el("button", {
              class: "btn scrub-now-btn", type: "button",
              onclick: () => { scrubT = null; load(currentUri); },
            }, "↩ current")),
          track,
          el("div", { class: "scrub-axis" },
            el("span", {}, dayISO(domain[0])),
            el("span", {}, "valid time →"),
            el("span", {}, dayISO(domain[1]))));
      }

      function scheduleFetch() {
        clearTimeout(fetchTimer);
        fetchTimer = setTimeout(async () => {
          if (currentUri === null || scrubT === null) return;
          const stance = `as_of:${new Date(scrubT).toISOString().replace(/\.\d{3}Z$/, "+00:00")}`;
          try {
            const e = await api(`/api/entities/${encodeURI(currentUri)}?stance=${encodeURIComponent(stance)}`);
            renderStanceCard(e.properties, e.stance);
          } catch { /* keep the last good card while dragging */ }
        }, 160);
      }
      ctx.addDisposer(() => clearTimeout(fetchTimer));

      // ───────────────────────────────────────────── stance card + bars

      function renderStanceCard(props, stanceLabel) {
        if (!stanceHolder) return;
        clear(stanceHolder);
        const keys = Object.keys(props).sort();
        const card = el("div", { class: "stance-card" },
          el("span", { class: "section-label" },
            scrubT === null ? "property card — current" : `property card — ${stanceLabel || "as-of"}`));
        if (!keys.length) {
          card.append(el("div", { class: "stance-empty" },
            "nothing was valid at this instant — the record is silent here"));
        } else {
          card.append(el("table", { class: "data" }, el("tbody", {},
            keys.map((k) => {
              const td = el("td", {}, props[k] === null ? "∅" : String(props[k]));
              if (!(k in lastProps) || String(lastProps[k]) !== String(props[k])) td.classList.add("value-changed");
              return el("tr", {}, el("td", {}, k), td);
            }))));
        }
        stanceHolder.append(card);
        lastProps = { ...props };
      }

      function renderHistory(target) {
        clear(target);
        target.append(el("span", { class: "section-label" },
          "bitemporal history — every value ever held, on the valid-time axis"));
        const rows = el("div", { class: "history-rows" });
        historyCursors = [];
        for (const prop of Object.keys(history).sort()) {
          const track = el("div", { class: "history-track" });
          for (const c of history[prop]) {
            const a = ms(c.valid_from) ?? domain[0];
            const b = ms(c.valid_to);
            const left = Math.max(0, Math.min(1, frac(a)));
            const right = b === null ? 1 : Math.max(0, Math.min(1, frac(b)));
            track.append(el("button", {
              class: `history-bar${c.is_current ? " current" : ""}${b === null ? " open-ended" : ""}`,
              type: "button",
              style: `left:${(left * 100).toFixed(2)}%;width:${(Math.max(0.008, right - left) * 100).toFixed(2)}%`,
              title: `${prop} = ${c.value}\nvalid ${c.valid_from ? c.valid_from.slice(0, 10) : "−∞"} → ${c.valid_to ? c.valid_to.slice(0, 10) : "open"}\nrecorded ${c.system_from ? c.system_from.slice(0, 19) : "?"} · confidence ${c.confidence.toFixed(2)} · source rank ${c.src_rank}\nclick — derivation`,
              onclick: () => ctx.emit("evidence:prov", { provRef: c.prov_ref, label: `${prop} = ${c.value}` }),
            }));
          }
          const cursor = el("span", { class: "history-cursor", hidden: scrubT === null ? "hidden" : null });
          if (scrubT !== null) cursor.style.left = pct(scrubT);
          track.append(cursor);
          historyCursors.push(cursor);
          rows.append(el("div", { class: "history-row" },
            el("span", { class: "history-prop", title: prop }, prop), track));
        }
        target.append(rows, el("div", { class: "history-legend" },
          "amber bar — current belief · grey bar — superseded or windowed · fade — open-ended · every bar resolves to its derivation"));
      }

      // ───────────────────────────────── neighbors — the OS moment

      async function renderNeighbors(target, uri) {
        target.append(el("span", { class: "section-label" }, "neighbors"));
        const list = el("div", { class: "neighbor-list" },
          el("div", { class: "skeleton", style: "width:60%" }));
        target.append(list);
        try {
          const out = await api(`/api/entities/${encodeURI(uri)}/neighbors`);
          const links = out.links || [];
          clear(list);
          if (!links.length) {
            list.append(el("div", { class: "neighbor-none" }, "no linked entities"));
            return;
          }
          for (const n of links) {
            if (!n.target_uri) continue;
            const via = n.direction === "in" ? `← ${n.predicate}` : `${n.predicate} →`;
            list.append(el("button", {
              class: "neighbor-row", type: "button", title: `${n.target_uri}\n${via}`,
              onclick: () => ctx.emit("entity:open", { uri: n.target_uri }),
            },
              el("span", { class: "nb-glyph", "aria-hidden": "true" }, "◈"),
              el("span", { class: "nb-main" },
                el("span", { class: "nb-uri mono" }, n.target_label || n.target_uri),
                el("span", { class: "nb-via" }, via)),
              el("span", { class: "nb-open", "aria-hidden": "true" }, "→")));
          }
        } catch (e) {
          clear(list).append(el("div", { class: "neighbor-none" },
            e.status === 404 || e.status === 405
              ? "neighbor graph not exposed by this server build"
              : String(e.message || e)));
        }
      }

      // ───────────────────────────────────────────────────────── load

      async function load(uri) {
        uri = String(uri || "").trim();
        if (!uri) return;
        currentUri = uri;
        input.value = uri;
        scrubT = null;
        lastProps = {};
        ctx.setTitle(`Inspector — ${uri.split("/").pop()}`);
        const target = clear(body);
        scrubBox.hidden = true;
        target.append(el("div", { class: "skeleton-card" }));
        try {
          const e = await api(`/api/entities/${encodeURI(uri)}`);
          remember(uri);
          history = e.history;
          domain = computeDomain(history);
          stanceHolder = el("div", { class: "stance-holder" });
          const historyHolder = el("div", { class: "history-holder" });
          const neighborsHolder = el("div", { class: "neighbors-holder" });
          clear(target).append(
            el("div", { class: "entity-head" },
              el("div", { class: "entity-uri" }, e.uri),
              el("div", { class: "entity-classes" },
                e.classes.map((c) => el("button", {
                  class: "badge badge-amber badge-btn", type: "button", title: `${c} — open in the constellation`,
                  onclick: () => ctx.emit("class:focus", { uri: c }),
                }, c.split("/").pop())))),
            el("div", { class: "entity-grid" },
              el("div", {}, stanceHolder, neighborsHolder),
              historyHolder));
          renderScrubber();
          renderStanceCard(e.properties, e.stance);
          lastProps = { ...e.properties };
          renderHistory(historyHolder);
          renderNeighbors(neighborsHolder, uri);
        } catch (err) {
          scrubBox.hidden = true;
          clear(target).append(errorNote(err));
        }
      }

      form.addEventListener("submit", (e) => {
        e.preventDefault();
        const uri = input.value.trim();
        if (uri) load(uri);
      });

      renderRecent();
      if (currentUri) load(currentUri);
      else requestAnimationFrame(() => input.focus());

      return {
        load,
        uri: () => currentUri,
        params: () => ({ uri: currentUri || undefined }),
      };
    },
  };
}
