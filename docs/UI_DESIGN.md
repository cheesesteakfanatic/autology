# OntoForge UI — the instrument

The web UI (`src/ontoforge/server/static/`) is the product's market edge made visible.
docs/MARKET_EDGE.md found that what nobody else ships are **trust artifacts**: per-value
citations, calibrated abstention, bi-temporal per-value provenance, and a testable exit
guarantee. The interface therefore treats *trust as the aesthetic*: every signature
interaction drills from a claim to its evidence, and uncertainty is rendered honestly
instead of hidden.

Zero frameworks. Vanilla ES modules (`app.js` + `js/constellation.js`, `js/entity.js`,
`js/dashboards.js`), one stylesheet, vendored Vega for charts only. Non-vendor payload
≈ 93 KB (budget 120 KB, enforced by `tests/server/test_spa.py`). Every piece of API data
enters the DOM through `el()`/`document.createTextNode` — `innerHTML` never carries data
(also test-enforced).

---

## 1. Design system

### Grounds (dark editorial, layered)

| token | value | use |
|---|---|---|
| `--bg0` | `#0b0d10` | page |
| `--bg1` | `#0e1116` | panels/cards |
| `--bg2` | `#11141a` | raised cards, chips |
| `--bg3` | `#161a22` | hover / inset |

### Ink & accent

| token | value | use |
|---|---|---|
| `--ink` | `#e8e6e1` | primary text, data values |
| `--ink-dim` / `--ink-faint` | `#9b978e` / `#66635b` | chrome, labels |
| `--amber` | `#e8a33d` | **the only accent** — provenance/evidence affordances, the live forge identity |
| `--amber-dim` | `#b97c26` | quiet amber (links, superseded-current bars) |
| `--verdict-green` / `--verdict-red` | `#5da57f` / `#c2655c` | **only** on accept/reject buttons |
| `--hairline` | `rgba(232,230,225,.08)` | all borders; `-strong` at `.18` for emphasis |

Discipline: everything else is achromatic. Amber means "this resolves to evidence."
The only drop shadow in the product is the focused glow on the active evidence trail
(`.evidence-active`) and the cite-dot/scrub-handle glows that point at it.

### Type

- `--serif` (`Iowan Old Style, Palatino, …`): the wordmark, questions, clarifications,
  abstention lines, empty states — the editorial voice.
- `--sans` (system): chrome, labels, buttons.
- `--mono` (ui-monospace): **every data value** — answers, URIs, atom paths, timestamps,
  confidences. Values are sacred; chrome is quiet.
- Section labels: 11px sans, `letter-spacing .14em`, uppercase — the small-caps voice.

Strict scale: `11 / 12.5 / 14 / 16 / 20 / 28` px (`--fs-0`…`--fs-5`). 8px spatial grid.
Motion: one curve, 150ms (`--ease`); gauges/arcs settle in 420ms; `prefers-reduced-motion`
collapses everything.

---

## 2. The four profiles, and what each sees first

**The Evaluator (CDO, first 10 minutes).** Lands on **Ask**: a single huge serif
question field. Types a question; a skeleton shimmers; the answer *materializes* —
a one-value answer renders as a 28px mono headline with an amber dot beside it. Clicking
the dot slides in the evidence rail with the actual source cells
(`erp › maintenance_erp › WO-100125 #COMPONENT = LANDING GEAR`), and the thin amber
confidence gauge prints the real number underneath. The magic moment is claim → evidence
in one click. Asking about unicorns produces the second magic moment: *"OntoForge
declines to guess."*

**The Analyst (daily).** Keyboard-first. `⌘K` opens the command palette from anywhere:
ask the typed text, jump to any panel, find a class, re-run a recent question (server
cache answers instantly, marked "instant — answer cache"). `/` focuses the ask field.
Clarifications are one-keystroke chips (`1`–`9`). Recent questions persist as chips
under the field.

**The Steward (data engineer).** **Review** is a queue of decision cards: kind/tier
badges, the engine's rationale in italic serif, the conformal set as chips with the
chosen outcome in amber, the calibrated confidence gauge, and — for ER decisions — the
two records side by side under a serif *"same?"*. `j`/`k` move the amber selection ring,
`a`/`r` adjudicate. Each verdict ticks the **recalibration arc** (`n/20` around an SVG
circle) — the human-in-the-loop flywheel made tangible; at 20 the spine refits and the
card says so. Empty state: *"no review items — the spine is confident today."*

**The Auditor (compliance).** Two rooms. **Entities**: paste/deep-link any `ent://` URI
and get the property card under a stance plus the **as-of time scrubber** — a track with
amber ticks at every validity boundary; dragging the handle refetches the card (debounced
160ms) and changed values flash amber: watch a tail number exist in 1995 and vanish under
"current". Below, **bitemporal history bars** per property on a shared valid-time axis
(amber = current belief, grey = superseded/windowed, faded edge = open-ended); every bar
click opens the **derivation tree** in the evidence rail — sums render as "any of",
products as "all of", leaves as live atom chips. Constraint H, visible.

---

## 3. Panel-by-panel (screenshots in words)

**Masthead.** `Onto●Forge` in serif with a glowing amber spark; small-caps strapline
*induced ontology · bitemporal provenance · calibrated abstention*; live meta (estate,
atom count, token cost) in mono; the `⌘K` hint. One hairline under the tab row; the
active tab is underlined in amber.

**Ask (`#/ask`).** Form → history chips → result. Result states: skeleton (shimmer),
clarification card (amber-edged, serif question, keystroke chips), abstention card
(grey-edged `state-abstained`: small-caps ABSTAINED, serif *declines to guess*, mono
reason, "what would make this answerable" class chips, gauge labelled *below the floor*),
or the answer card (serif question echo, mono table or headline, amber cite dots,
confidence gauge). Cited cells get `td.cited` + dot; the active one glows.

**Evidence rail (right, 380px, slides in 150ms).** Header EVIDENCE in amber small-caps +
context (`row 1 · count_rows = 7`). Two modes: source-atom chips fetched live from
`/api/atoms` (path + value + content hash), or the recursive provenance tree from
`/api/provenance`. `esc` closes. The shell narrows on wide screens instead of being
covered.

**Ontology (`#/ontology[/<class-uri>]`).** **The constellation**: a 960×600 SVG star
chart of the induced model. A ~80-line deterministic force simulation (mulberry32 seeded
from class-URI hashes — same ontology, same sky) lays out classes as stars sized by
structure (`5 + 2.1·√(props+shapes+1)`), with an amber halo whose luminance is the class
confidence; events wear a dashed ring. Subsumption edges are straight hairlines; link
properties bow outward as faint amber arcs with hover titles. Hover a star → floating
property card; click → full class detail below (serif name, badges, definition,
properties table with amber unit badges and clickable ranges). Drag pans, wheel zooms to
cursor, double-click resets (viewBox manipulation, no re-layout).

**Entities (`#/entity[/<ent-uri>]`).** URI field (mono), recent-entity chips
(localStorage), then scrubber + two-column grid: stance card | history bars (above).

**Review (`#/review`).** Recalibration arcs per kind, keyboard legend, then the card
queue (above).

**Dashboards (`#/dashboards`).** Utterance field → VISTA's top-3 proposals (`№1` serif
rank, score in mono, rationale) with Vega-Lite charts themed to the instrument: amber
mark ramp, mono axes, transparent grounds. Saved proposals below; offline fallback
prints the raw spec.

**Status (`#/status`).** The instrument cluster: hairline-divided counter grid (atoms in
amber, entities, value cells, links, decisions, artifacts, model cost), the pipeline
stage checklist with amber ◆ ticks that glow when done, and the by-kind/by-tier/artifact
tables. Reload re-opens the project after CLI changes.

---

## 4. Interaction inventory

| interaction | where | mechanics |
|---|---|---|
| Ask / Enter | ask field, palette | `POST /api/ask`; skeleton → answer states |
| Cite-dot → evidence rail | any cited cell | atoms fetched live; anchor cell glows |
| Provenance tree | entity history bars, rail | `GET /api/provenance/{ref}`; collapsible any-of/all-of |
| Clarification chips | ask | click or keys `1`–`9` → `POST /api/ask/clarify` |
| Abstention guidance | ask | reason verbatim + ontology-class chips into the input |
| Command palette | global | `⌘K`/`ctrl+K`; ask, navigate, classes, recents; `↑↓⏎esc` |
| Focus ask | global | `/` |
| Review keys | review | `j`/`k` select, `a`/`r` verdict → recalibration arc tick |
| ER pair inspect | review er cards | `a‖b` split side-by-side; `ent://` sides deep-link |
| Constellation | ontology | hover card, click detail, drag-pan, wheel-zoom, dbl-reset |
| Time scrubber | entities | drag → debounced as-of refetch; changed values flash |
| History bars | entities | bar = value cell on valid-time axis; click = derivation |
| Recent questions/entities | ask, entities | localStorage chips; cached repeats are instant |
| Deep links | all | `#/ask`, `#/ontology/<uri>`, `#/entity/<uri>`, … |
| Reload project | status | `POST /api/reload`, caches dropped, constellation redrawn |

Prefetch on load: `/api/status` + `/api/ontology` (so the palette knows the classes and
the constellation is warm before the tab is opened).

---

## 5. Why this is twenty years ahead

Every BI surface on the market renders *answers*. This one renders *epistemology*: what
is believed, why, since when, on whose evidence, and — crucially — when the system is
not entitled to an answer. The amber dot is a contract ("this number resolves to source
cells"); the scrubber makes regulator questions ("what did you believe on date X?") a
drag gesture; the recalibration arc shows governance running, not promised. No competitor
surface in MARKET_EDGE.md can draw any of these three, because their platforms don't
store the artifacts. The UI is thin — it merely refuses to hide what the engine knows.
