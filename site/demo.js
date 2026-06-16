/* ════════════════════════════════════════════════════════════════════════
   OntoForge — canned, deterministic, offline "see the magic" walk-through.
   No backend, no randomness, no network. All DOM built via createElement /
   textContent (no innerHTML carrying data), matching the product's security
   posture. < 150 KB total page payload by construction (this file is small).

   The story: 3 fake tables -> the engine SCORES candidate links, CONFIRMS a
   real FK join by executing it, REJECTS a similar-looking but unrelated pair
   (the false-positive killer), types the surviving relationship, then answers
   a question with a value-level citation.
   ════════════════════════════════════════════════════════════════════════ */
(function () {
  "use strict";

  // ── tiny DOM helpers (data only ever via textContent) ──────────────────
  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }
  var SVGNS = "http://www.w3.org/2000/svg";
  function svg(tag, attrs) {
    var n = document.createElementNS(SVGNS, tag);
    if (attrs) for (var k in attrs) n.setAttribute(k, attrs[k]);
    return n;
  }

  // ── the three canned tables (positions are % of the canvas) ────────────
  // teal accent on the table that owns the key; columns can be flagged key.
  var TABLES = {
    flights: {
      name: "flights.csv",
      accent: "var(--ocean)",
      x: 22, y: 30,
      cols: ["flight_id", "tail_num", "route"],
      keys: ["flight_id"],
      rows: [
        ["FL-2201", "N804AX", "JFK→LHR"],
        ["FL-2202", "N771AX", "LHR→DXB"],
        ["FL-2203", "N804AX", "DXB→SIN"]
      ]
    },
    maintenance: {
      name: "maintenance.csv",
      accent: "var(--avocado)",
      x: 78, y: 30,
      cols: ["wo_id", "tail_num", "hours"],
      keys: ["wo_id"],
      hlCol: 1, // tail_num is the join column we'll light up
      rows: [
        ["WO-5510", "N804AX", "4.5"],
        ["WO-5511", "N771AX", "1.0"],
        ["WO-5512", "N804AX", "6.2"]
      ]
    },
    weather: {
      name: "weather.csv",
      accent: "var(--plum)",
      x: 50, y: 76,
      cols: ["station", "wind_kt", "temp_c"],
      keys: ["station"],
      rows: [
        ["JFK", "12", "18"],
        ["LHR", "8", "11"],
        ["DXB", "5", "34"]
      ]
    }
  };

  // ── candidate arcs the engine considers ─────────────────────────────────
  // 'a'/'b' are table keys; verdict drawn per step.
  var ARCS = {
    realFK: { a: "flights", b: "maintenance", label: "tail_num" },
    lookAlike: { a: "weather", b: "maintenance", label: "?" }
  };

  // ── the scripted steps ──────────────────────────────────────────────────
  var STEPS = [
    {
      name: "Three raw sources",
      narr: function () {
        var p = el("p", "demo-narr");
        p.appendChild(document.createTextNode("Three independent CSVs. No shared schema, no foreign keys declared, no query history. OntoForge has to figure out how they relate "));
        var em = el("em", null, "from the values alone");
        p.appendChild(em); p.appendChild(document.createTextNode("."));
        return { narr: p, detail: null, arcs: [], lit: [] };
      }
    },
    {
      name: "Heuristics-first scoring",
      narr: function () {
        var p = el("p", "demo-narr");
        p.appendChild(t("OntoForge profiles every column pair offline — value overlap, "));
        p.appendChild(span("hl-amber", "distribution alignment"));
        p.appendChild(t(", cardinality, key-uniqueness. Two candidates score above the floor. Both "));
        p.appendChild(span("hl-amber", "look"));
        p.appendChild(t(" plausible by raw overlap."));
        return { narr: p, detail: scoreDetail(), arcs: [arc("realFK", "likely", "0.71 likely"), arc("lookAlike", "likely", "0.63 likely")], lit: ["flights", "maintenance", "weather"] };
      }
    },
    {
      name: "Execute-the-join validation",
      narr: function () {
        var p = el("p", "demo-narr");
        p.appendChild(t("Before asserting anything, the engine "));
        p.appendChild(span("hl-teal", "synthesizes each join and runs it"));
        p.appendChild(t(" against the real data. "));
        p.appendChild(strong("flights.tail_num → maintenance.tail_num"));
        p.appendChild(t(" matches cleanly: 100% coverage, no orphans, sane fan-out."));
        return { narr: p, detail: execDetail(), arcs: [arc("realFK", "confirmed", "executed ✓"), arc("lookAlike", "likely", "checking…")], lit: ["flights", "maintenance"] };
      }
    },
    {
      name: "The false-positive killer",
      narr: function () {
        var p = el("p", "demo-narr");
        p.appendChild(t("The look-alike pair "));
        p.appendChild(strong("weather.station ↔ maintenance.tail_num"));
        p.appendChild(t(" shares a vocabulary shape but the "));
        p.appendChild(span("hl-red", "distributions diverge"));
        p.appendChild(t(" and neither side is a key. The executed join orphans every row. "));
        p.appendChild(span("hl-red", "Rejected — unrelated despite similarity."));
        return { narr: p, detail: rejectDetail(), arcs: [arc("realFK", "confirmed", "FK-join ✓"), arc("lookAlike", "rejected", "unrelated ✗")], lit: ["weather", "maintenance"] };
      }
    },
    {
      name: "Typed relationship",
      narr: function () {
        var p = el("p", "demo-narr");
        p.appendChild(t("What survives isn't a binary “join/no-join” — it's a "));
        p.appendChild(strong("typed"));
        p.appendChild(t(" edge with its evidence, calibrated confidence, and a per-value provenance trail you can audit."));
        return { narr: p, detail: typedDetail(), arcs: [arc("realFK", "confirmed", "FK-join ✓")], lit: ["flights", "maintenance"] };
      }
    },
    {
      name: "A cited answer",
      narr: function () {
        var p = el("p", "demo-narr");
        p.appendChild(t("Now ask in plain language. The answer resolves through the validated join — and every value carries a "));
        p.appendChild(span("hl-teal", "citation to its exact source cell"));
        p.appendChild(t(". If it couldn't answer truthfully, it would abstain instead of guessing."));
        return { narr: p, detail: answerDetail(), arcs: [arc("realFK", "confirmed", "FK-join ✓")], lit: ["flights", "maintenance"] };
      },
      last: true
    }
  ];

  // ── detail-panel builders ────────────────────────────────────────────────
  function t(s) { return document.createTextNode(s); }
  function span(cls, s) { return el("span", cls, s); }
  function strong(s) { var b = el("strong"); b.textContent = s; return b; }

  function evCard(kicker, valueNode, cls) {
    var c = el("div", "ev-card" + (cls ? " " + cls : ""));
    c.appendChild(el("div", "ev-k", kicker));
    var v = el("div", "ev-v");
    v.appendChild(valueNode);
    c.appendChild(v);
    return c;
  }
  function valWith(label, mark, markCls) {
    var frag = document.createDocumentFragment();
    frag.appendChild(t(label));
    if (mark) { var s = el("span", markCls, mark); frag.appendChild(s); }
    return frag;
  }

  function scoreDetail() {
    var wrap = el("div", "evidence fade-in");
    wrap.appendChild(evCard("flights ↔ maintenance", valWith("overlap 1.00 · ", "proxy 0.71", "good")));
    wrap.appendChild(evCard("weather ↔ maintenance", valWith("overlap 0.42 · ", "proxy 0.63", null)));
    return wrap;
  }
  function execDetail() {
    var wrap = el("div", "evidence fade-in");
    wrap.appendChild(evCard("match rate", el("span", "good", "100% (3 / 3 rows)"), "boost"));
    wrap.appendChild(evCard("orphans", el("span", "good", "0"), "boost"));
    wrap.appendChild(evCard("fan-out", valWith("1 : N (expected)"), "boost"));
    wrap.appendChild(evCard("null keys", el("span", "good", "0"), "boost"));
    return wrap;
  }
  function rejectDetail() {
    var wrap = el("div", "evidence fade-in");
    wrap.appendChild(evCard("distribution (JS-divergence)", el("span", "bad", "0.88 — diverges"), "veto"));
    wrap.appendChild(evCard("either side a key?", el("span", "bad", "no"), "veto"));
    wrap.appendChild(evCard("executed join", el("span", "bad", "0 / 3 match — all orphan"), "veto"));
    var pill = el("div", "rel-summary");
    pill.appendChild(t("weather.station"));
    pill.appendChild(el("span", "arrow", "×"));
    pill.appendChild(t("maintenance.tail_num"));
    var rp = el("span", "rel-pill rejected", "unrelated-despite-similarity");
    pill.appendChild(rp);
    wrap.appendChild(pill);
    return wrap;
  }
  function typedDetail() {
    var wrap = el("div", "fade-in");
    var line = el("div", "rel-summary");
    line.appendChild(t("flights.tail_num"));
    line.appendChild(el("span", "arrow", "→"));
    line.appendChild(t("maintenance.tail_num"));
    var rp = el("span", "rel-pill confirmed", "FK-join");
    line.appendChild(rp);
    wrap.appendChild(line);
    var ev = el("div", "evidence");
    ev.appendChild(evCard("calibrated confidence", valWith("0.96 · ", "confirmed", "good"), "boost"));
    ev.appendChild(evCard("decided by", valWith("execution veto > vote")));
    ev.appendChild(evCard("provenance", valWith("per-value, bitemporal")));
    wrap.appendChild(ev);
    return wrap;
  }
  function answerDetail() {
    var wrap = el("div", "answer fade-in");
    wrap.appendChild(el("div", "aq", "How many maintenance hours has tail N804AX logged?"));
    var val = el("div", "aval");
    val.appendChild(t("10.7 hrs"));
    var cite = el("span", "cite", "1");
    cite.setAttribute("title", "maintenance.csv · rows WO-5510 (4.5) + WO-5512 (6.2) · joined on tail_num · as-of now");
    val.appendChild(cite);
    wrap.appendChild(val);
    var src = el("div", "asrc");
    src.appendChild(t("from "));
    src.appendChild(el("span", "col", "maintenance.hours"));
    src.appendChild(t(" × 2 cells, summed over the validated FK join — click the dot to trace each value."));
    wrap.appendChild(src);
    return wrap;
  }

  function arc(key, verdict, label) { return { key: key, verdict: verdict, label: label }; }

  // ── render the static tables once ────────────────────────────────────────
  var tablesHost = document.getElementById("demo-tables");
  var svgHost = document.getElementById("demo-svg");
  var nameEl = document.getElementById("step-name");
  var narrEl = document.getElementById("demo-narr");
  var detailEl = document.getElementById("demo-detail");
  var dotsHost = document.getElementById("step-dots");
  var btnNext = document.getElementById("btn-next");
  var btnPrev = document.getElementById("btn-prev");
  var btnReplay = document.getElementById("btn-replay");

  var tableNodes = {}; // key -> element

  function renderTables() {
    tablesHost.textContent = "";
    Object.keys(TABLES).forEach(function (key) {
      var tdef = TABLES[key];
      var card = el("div", "tbl");
      card.style.left = tdef.x + "%";
      card.style.top = tdef.y + "%";
      card.style.setProperty("--accent", tdef.accent);
      card.appendChild(el("div", "tbl-name", tdef.name));
      var table = el("table");
      var thead = el("thead");
      var htr = el("tr");
      tdef.cols.forEach(function (c, i) {
        var th = el("th", tdef.keys.indexOf(c) >= 0 ? "key" : (i === tdef.hlCol ? "col-hl" : ""), c);
        htr.appendChild(th);
      });
      thead.appendChild(htr);
      table.appendChild(thead);
      var tbody = el("tbody");
      tdef.rows.forEach(function (r) {
        var tr = el("tr");
        r.forEach(function (cell, i) {
          var isKey = tdef.keys.indexOf(tdef.cols[i]) >= 0;
          var td = el("td", isKey ? "key" : (i === tdef.hlCol ? "col-hl" : ""), cell);
          tr.appendChild(td);
        });
        tbody.appendChild(tr);
      });
      table.appendChild(tbody);
      card.appendChild(table);
      tablesHost.appendChild(card);
      tableNodes[key] = card;
    });
  }

  // map a table's % position to svg viewBox coords (920 x 420)
  function centerOf(key) {
    var t = TABLES[key];
    return { x: (t.x / 100) * 920, y: (t.y / 100) * 420 };
  }

  function drawArcs(arcs) {
    svgHost.textContent = "";
    arcs.forEach(function (a) {
      var def = ARCS[a.key];
      var p1 = centerOf(def.a), p2 = centerOf(def.b);
      // curved path with a control point bowed toward canvas center-ish
      var mx = (p1.x + p2.x) / 2;
      var my = (p1.y + p2.y) / 2;
      var bow = (p1.y < 300 && p2.y < 300) ? 60 : -50; // bow up for top pair
      var d = "M " + p1.x + " " + p1.y + " Q " + mx + " " + (my + bow) + " " + p2.x + " " + p2.y;
      var path = svg("path", { d: d, class: "arc " + a.verdict });
      svgHost.appendChild(path);
      if (a.label) {
        var lbl = svg("text", { x: mx, y: my + bow * 0.6 - 6, "text-anchor": "middle", class: "arc-label " + a.verdict });
        lbl.textContent = a.label;
        svgHost.appendChild(lbl);
      }
    });
  }

  function renderStep(i) {
    var step = STEPS[i];
    var built = step.narr();
    nameEl.textContent = step.name;
    // replace narration node
    narrEl.replaceWith(built.narr);
    narrEl = built.narr;
    narrEl.id = "demo-narr";
    // detail
    detailEl.textContent = "";
    if (built.detail) detailEl.appendChild(built.detail);
    // arcs + lit tables
    drawArcs(built.arcs || []);
    Object.keys(tableNodes).forEach(function (k) {
      tableNodes[k].classList.toggle("lit", (built.lit || []).indexOf(k) >= 0);
    });
    // dots
    Array.prototype.forEach.call(dotsHost.children, function (d, di) {
      d.className = "sd" + (di < i ? " done" : di === i ? " active" : "");
    });
    // controls
    btnPrev.disabled = i === 0;
    if (step.last) {
      btnNext.style.display = "none";
      btnReplay.style.display = "";
    } else {
      btnNext.style.display = "";
      btnReplay.style.display = "none";
      btnNext.textContent = i === 0 ? "Run discovery →" : "Next →";
    }
  }

  // ── build step dots ──────────────────────────────────────────────────────
  STEPS.forEach(function (_, i) {
    var d = el("span", "sd" + (i === 0 ? " active" : ""));
    dotsHost.appendChild(d);
  });

  var cur = 0;
  renderTables();
  renderStep(0);

  btnNext.addEventListener("click", function () { if (cur < STEPS.length - 1) { cur++; renderStep(cur); } });
  btnPrev.addEventListener("click", function () { if (cur > 0) { cur--; renderStep(cur); } });
  btnReplay.addEventListener("click", function () { cur = 0; renderStep(0); });

  // keyboard: arrows advance/retreat
  document.addEventListener("keydown", function (e) {
    if (e.key === "ArrowRight" && cur < STEPS.length - 1) { cur++; renderStep(cur); }
    else if (e.key === "ArrowLeft" && cur > 0) { cur--; renderStep(cur); }
  });
})();
