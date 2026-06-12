/* The entity inspector — HEARTH's bitemporal store made visceral.
   One record; its property card under a temporal stance; a TIME SCRUBBER
   whose handle is the as-of instant (drag it, watch values change); and
   per-property history bars on a shared time axis, each bar one value
   cell with its own provenance ref → the evidence rail. */

const RECENT_KEY = "ontoforge.recent.entities";

export function createEntityPanel(ctx) {
  const { $, el, clear, api, errorNote, openProvenance } = ctx;

  const scrubBox = $("#time-scrubber");
  const body = $("#entity-body");
  const input = $("#entity-input");
  const recentBox = $("#entity-recent");

  let recent = [];
  try { recent = JSON.parse(localStorage.getItem(RECENT_KEY) || "[]"); } catch { /* fresh */ }

  let currentUri = null;
  let history = null;          // prop -> cells (stance-independent)
  let domain = null;           // [t0, t1] ms
  let scrubT = null;           // ms position of the handle (null = current)
  let lastProps = {};          // previous stance card, for change flashes
  let fetchTimer = null;
  let historyCursors = [];

  function remember(uri) {
    recent = [uri, ...recent.filter((u) => u !== uri)].slice(0, 6);
    try { localStorage.setItem(RECENT_KEY, JSON.stringify(recent)); } catch { /* private mode */ }
    renderRecent();
  }

  function renderRecent() {
    clear(recentBox);
    for (const uri of recent) {
      recentBox.append(el("button", {
        class: "chip mono", title: uri, onclick: () => load(uri),
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

  // ───────────────────────────────────────────────────── the scrubber

  function renderScrubber() {
    clear(scrubBox);
    scrubBox.hidden = false;

    const stanceLabel = el("span", { class: "scrub-stance" },
      scrubT === null ? "stance: current" : `stance: as-of ${dayISO(scrubT)}`);

    const track = el("div", { class: "scrub-track", title: "drag — the as-of instant" });
    // boundary ticks: every instant where some value begins or ends
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

    function setFromEvent(e) {
      const r = track.getBoundingClientRect();
      const f = Math.max(0, Math.min(1, (e.clientX - r.left) / r.width));
      scrubT = domain[0] + f * (domain[1] - domain[0]);
      handle.style.left = pct(scrubT);
      stanceLabel.textContent = `stance: as-of ${dayISO(scrubT)}`;
      for (const cur of historyCursors) { cur.style.left = pct(scrubT); cur.hidden = false; }
      scheduleFetch();
    }
    track.addEventListener("pointerdown", (e) => {
      track.setPointerCapture(e.pointerId);
      setFromEvent(e);
      const move = (ev) => setFromEvent(ev);
      const up = () => {
        track.removeEventListener("pointermove", move);
        track.removeEventListener("pointerup", up);
      };
      track.addEventListener("pointermove", move);
      track.addEventListener("pointerup", up);
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

  // ──────────────────────────────────────────────── stance card + bars

  function renderStanceCard(props, stanceLabel) {
    const holder = $("#stance-card-holder");
    if (!holder) return;
    clear(holder);
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
          if (k in lastProps && String(lastProps[k]) !== String(props[k])) td.classList.add("value-changed");
          if (!(k in lastProps)) td.classList.add("value-changed");
          return el("tr", {}, el("td", {}, k), td);
        }))));
    }
    holder.append(card);
    lastProps = { ...props };
  }

  function renderHistory() {
    const target = $("#history-holder");
    clear(target);
    target.append(el("span", { class: "section-label" },
      "bitemporal history — every value ever held, on the valid-time axis"));
    const rows = el("div", { class: "history-rows" });
    historyCursors = [];
    const props = Object.keys(history).sort();
    for (const prop of props) {
      const track = el("div", { class: "history-track" });
      for (const c of history[prop]) {
        const a = ms(c.valid_from) ?? domain[0];
        const b = ms(c.valid_to);
        const left = Math.max(0, Math.min(1, frac(a)));
        const right = b === null ? 1 : Math.max(0, Math.min(1, frac(b)));
        const bar = el("button", {
          class: `history-bar${c.is_current ? " current" : ""}${b === null ? " open-ended" : ""}`,
          style: `left:${(left * 100).toFixed(2)}%;width:${(Math.max(0.008, right - left) * 100).toFixed(2)}%`,
          title: `${prop} = ${c.value}\nvalid ${c.valid_from ? c.valid_from.slice(0, 10) : "−∞"} → ${c.valid_to ? c.valid_to.slice(0, 10) : "open"}\nrecorded ${c.system_from ? c.system_from.slice(0, 19) : "?"} · confidence ${c.confidence.toFixed(2)} · source rank ${c.src_rank}\nclick — derivation`,
          onclick: (ev) => openProvenance(c.prov_ref, `${prop} = ${c.value}`, ev.currentTarget),
        });
        track.append(bar);
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

  // ────────────────────────────────────────────────────────────── load

  async function load(uri) {
    uri = String(uri || "").trim();
    if (!uri) return;
    currentUri = uri;
    input.value = uri;
    scrubT = null;
    lastProps = {};
    const target = clear(body);
    scrubBox.hidden = true;
    target.append(el("div", { class: "skeleton-card" }));
    try {
      const e = await api(`/api/entities/${encodeURI(uri)}`);
      remember(uri);
      history = e.history;
      domain = computeDomain(history);
      clear(target).append(
        el("div", { class: "entity-head" },
          el("div", { class: "entity-uri" }, e.uri),
          el("div", { class: "entity-classes" },
            e.classes.map((c) => el("span", { class: "badge badge-amber", title: c },
              c.split("/").pop())))),
        el("div", { class: "entity-grid" },
          el("div", { id: "stance-card-holder" }),
          el("div", { id: "history-holder" })));
      renderScrubber();
      renderStanceCard(e.properties, e.stance);
      lastProps = { ...e.properties };
      renderHistory();
    } catch (err) {
      scrubBox.hidden = true;
      clear(target).append(errorNote(err));
    }
  }

  $("#entity-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const uri = input.value.trim();
    if (uri) location.hash = `#/entity/${uri}`;
  });

  renderRecent();

  return {
    enter(rest) {
      if (rest) {
        const uri = decodeURIComponent(rest);
        if (uri !== currentUri) load(uri);
      }
    },
  };
}
