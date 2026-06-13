# OntoForge OS — the ontology operating system

The web UI (`src/ontoforge/server/static/`) is no longer a page with tabs.
It is an **operating surface**: a near-black workspace void where every
capability is a windowed micro-app, the dock launches and collects them,
and Spotlight — summoned by `⌘K`, `/`, or just typing on the empty
workspace — is the front door to everything the estate knows.

docs/MARKET_EDGE.md found that what nobody else ships are **trust
artifacts**: per-value citations, calibrated abstention, bitemporal
per-value provenance, a testable exit guarantee. The OS shell treats *trust
as the aesthetic* and *investigation as the workflow*: claims open evidence
windows beside them; entities open inspectors beside inspectors; nothing
asserted without a derivation.

Zero frameworks. Vanilla ES modules, one stylesheet, vendored Vega for
charts only. Non-vendor payload ≈ 150 KB (budget 250 KB, enforced by
`tests/server/test_spa.py`). Every piece of API data enters the DOM through
`el()`/`document.createTextNode` — `innerHTML` never carries data (also
test-enforced).

---

## 1. Architecture

```
app.js                 boot + intent routing policy (the WM owns routing)
js/core.js             kernel: el/svgEl/clear/api/store + ontology cache
js/bus.js              inter-app bus: namespaced intents, disposable subs
js/wm.js               the window manager
js/dock.js             the dock
js/spotlight.js        the search
js/constellation.js    deterministic star-chart layout engine
js/apps/registry.js    the app registry (dock order)
js/apps/{ask,constellation,inspector,evidence,
         review,dashboards,pulse,exporter}.js
```

**Apps never import each other.** An app emits intents over the bus —
`entity:open`, `class:focus`, `evidence:atoms`, `evidence:prov`, `ask:run`,
`app:launch` — with `sourceWinId` stamped on every payload, and `app.js`
decides which window answers: focus the existing singleton, re-point an
existing child, or spawn adjacent to the source. The WM collects every bus
subscription and disposer a window makes and tears them down on close.

## 2. The window manager (js/wm.js)

Mechanics borrowed from real WMs; chrome native to the web (no traffic
lights, no aero glass — the uncanny valley is avoided by not entering it):

- **Pointer-capture gestures.** `setPointerCapture` on the titlebar/handle;
  no document-level mousemove; `pointercancel` runs the same cleanup path.
  `touch-action: none` on titlebars and handles.
- **Transform-only motion.** Windows position exclusively via
  `translate3d`; pointermove coalesces into one rAF-scheduled write per
  frame; geometry (desktop rect) is read once at gesture start.
  `will-change: transform` lives only for the gesture's lifetime.
- **Transitions are for programmatic moves only** (`.win-animate` on snap,
  `.win-flip` on minimize/restore) and are hard-disabled during
  pointer-driven gestures (`.win-gesture`) — a window must never rubber-band
  behind the cursor.
- **Stack-array z-order.** Focus splices the id to the top and reassigns
  compact z-indexes (10…) in one pass. Bands: desktop(0) < windows(10–990) <
  dock(1000) < menubar(1500) < spotlight(2000). Every window root carries
  `isolation: isolate`; window bodies carry `contain: layout paint`.
- **Focus-follows-raise.** A capture-phase pointerdown raises the hit
  window; `.focused` drives the titlebar tint (amber glyph) and the larger
  shadow (`0 14px 24px @ 40%` — only the focused window). Keyboard routes
  through the shell to the focused window only; apps attach no global keys.
- **Snap.** The POINTER (not the window edge) is hit-tested against 18px
  edge strips: left/right = halves, top = maximize, corners = quarters. A
  translucent amber preview ghost animates to the slot before release; the
  pre-snap rect is remembered, so dragging a snapped titlebar restores the
  original size under the cursor. Double-click the titlebar toggles
  maximize. Resizing a snapped window unsnaps in place.
- **Resize.** Eight handles with 12px hit areas extending outside the
  border, each with the right directional cursor; min sizes clamped in the
  math; a strip of titlebar always stays reachable.
- **FLIP minimize.** Window rect → dock-tile rect, inverted
  translate+scale, 220ms transform/opacity only, `display:none` on settle;
  restore plays the inverse. `prefers-reduced-motion` collapses everything.
- **Interaction shield.** `body.wm-gesture` during any gesture:
  `user-select:none` everywhere, `pointer-events:none` on iframes/canvases
  (removed on pointerup AND pointercancel).
- **The workspace breathes.** A ResizeObserver re-tiles snapped windows to
  the new viewport and clamps floating ones back into reach — which also
  heals layouts measured while the surface had no size (hidden tab).
- **Persistence.** Layout (apps, params, rects, snap states, stack order)
  serializes to `PUT /api/workspace` (debounced 700ms) and localStorage on
  every change; boot restores server-first, then local, then the first-run
  default. Zero-size measurements are never persisted. Evidence windows are
  transient and skipped.

## 3. Spotlight (js/spotlight.js) — the front door

Summoned by `⌘K` (same key closes), `/`, the menubar hint, or **just
typing on the empty workspace** (the keystroke lands in the field). The
palette is pre-mounted: open is instant.

- **Local registries filter synchronously on every keystroke** — apps, open
  windows, recent questions, induced classes and properties — never
  debounced. Ranking: exact-prefix > word-prefix > substring > fuzzy
  subsequence with fzy-style bonuses (consecutive runs, word/camelCase
  starts, gap costs).
- **`GET /api/search?q=&limit=20`** (the frozen contract: kinds
  `class|entity|property|question|app`, scored) rides behind a 45ms
  debounce with AbortController cancellation; results merge by score,
  deduped by `kind|ref` against local hits.
- **No query dead-ends.** "Ask the estate — “q”" is pinned as fallback;
  free text ending in `?` (or matching nothing) makes it the primary row.
  Empty query shows recents + apps, never a blank panel.
- **Routing on Enter**: entity → Inspector window; class/property →
  Constellation focused on it; question → Ask pre-run; app → launch;
  window → focus.
- **WAI-ARIA combobox.** DOM focus never leaves the input;
  `aria-activedescendant` carries the virtual highlight; result counts are
  `aria-live: polite`; `↑↓⏎esc`, `⌘1–9` jumps; focus returns to the
  invoker on close.

## 4. The dock

Bottom center, frosted dark (`rgba(14,17,22,.72)` + 14px blur, hairline
border). One icon per app: glyph, small-caps label floating above on hover,
an amber ember under running apps. Click = launch, focus, or restore.
Minimized windows collect on a hairline-separated shelf as titled tiles —
the FLIP target.

## 5. The micro-apps

| app | glyph | what it is |
|---|---|---|
| **Ask** | ❯ | the console reborn in a window: serif question field, history chips, answers as mono headlines/tables with **amber cite-dots**; a dot opens an **Evidence child window beside the answer** (re-pointed, never duplicated); clarifications are `1–9` keystroke chips; abstention renders as the dignified `state-abstained` card — *"OntoForge declines to guess."* |
| **Evidence** | ⌗ | source-atom chips fetched live from `/api/atoms`, or the full derivation tree from `/api/provenance` (sums = "any of", products = "all of"). A child of its citing window: closing the parent closes it; `esc` dismisses it. Transient — never persisted. |
| **Constellation / Atlas** | ✶ | the star chart in a resizable window: deterministic seeded force layout, stars sized by structure, amber luminance = confidence, subsumption hairlines, bowed link arcs; pan/zoom/reset; class detail drawer beneath; `focusClass(uri, prop)` API the WM routes `class:focus` to. When `GET /api/atlas` is built it becomes **THE ATLAS** (see §5a) and retitles itself *"Atlas — N islands · M silos"*. Singleton. |
| **Inspector** | ◈ | one entity: property card under a temporal stance, the **as-of time scrubber** (drag → debounced refetch, changed values flash), per-property **bitemporal history bars** (click → derivation in Evidence), and the **neighbors list** — clicking a neighbor opens *another Inspector beside this one*: the OS moment. Multiple instances are the point; the same URI refocuses instead of duplicating. |
| **Review** | ⚖ | the adjudication queue: kind/tier badges, ER pairs side-by-side under a serif *"same?"* (sides deep-link to Inspectors), conformal chips, confidence gauge, `j/k/a/r` routed to this window only, and the recalibration arc (`n/20`) per kind. Singleton. |
| **Dashboards** | ▤ | utterance → VISTA's top-3 proposals with themed Vega-Lite previews; every chart has a `⤢` that expands it into its own window (a single-chart viewer instance). Saved proposals below. |
| **Pulse** | ◉ | the instrument cluster, live-ish: counters, pipeline stages, by-kind/by-tier tables, polled every 10s while open (interval disposed with the window); project reload lives here and announces `world:reload` on the bus. Singleton. |
| **Exporter** | ⇲ | portability as a visible feature: one button strikes an AMBER snapshot (`POST /api/export`), the shelf lists bundles (`GET /api/exports`). Degrades to honest CLI guidance if the endpoints are absent. Singleton. |

## 5a. The Atlas — the visual grammar of certainty

The Constellation's second sky. Pointed at hundreds of wild internet
datasets, the question the screen must answer at a glance is *how much of
this did the engine autonomously join, and how honest is it about the
rest?* The Atlas encodes **certainty as visual weight** — nothing is
colored "right" or "wrong"; things are *warmer* the more the engine has
earned belief in them.

**The contract.** `GET /api/atlas` →
`{components: [{id, label, class_uris, dataset_count, is_silo}], links:
[{src_class, dst_class, src_prop, dst_prop, tier, score, evidence}],
stats}`. The endpoint may 404 while its crew lands: `loadAtlas()` resolves
`null` and the app falls back to the plain ontology sky with a quiet
*"atlas not built — induced ontology shown"* note. Never an error state.

**Islands.** Each connected component lays out as its own island — an
intra-component seeded force sim (same deterministic physics, scaled to
the island's size), packed on a loose golden-angle spiral, largest first.
The hull is a barely-visible rounded boundary (amber at 2% fill, hairline
stroke) — a coastline, not a border. The island label is small-caps serif
with its `dataset_count`, counterscaled so it reads at every zoom;
clicking it flies the view to fit the island.

**The grammar of joins.**

| tier | rendering | reading |
|---|---|---|
| `confirmed` | solid amber-dim hairline arc | settled knowledge — quiet, load-bearing |
| `likely` | **dashed amber**, stroke-opacity ∝ score | a hypothesis carrying its own weight; breathes slightly on hover only |
| `hint` | nearly invisible dotted, **off by default** | static at the edge of hearing |

Hovering a likely arc opens the **evidence card**: tier, score, coverage
%, overlap count, the two column names and up to five sample shared
values in mono. Click pins it; click the void (or ×) releases it. The
legend chips are **filter toggles** (confirmed / likely / hint / silos)
carrying live counts from `stats`.

**The archipelago.** Silos — classes nothing joined — collect in a dimmer
band along the bottom under a hairline and the label *"archipelago — N
silos · honest and unjoined"*. They are quieter (ink-stroked, no halo)
but **dignified**: never error-red, because an honest silo is a finding,
not a failure.

**Scale discipline** (the ATLAS SCALE GUARD comment in
`js/constellation.js`, enforced by `tests/server/test_spa.py` against a
committed 250-node / 620-arc fixture,
`tests/server/fixtures/atlas_synthetic_250.json`): the sim settles once
per island at render (iterations shrink as islands grow); the settled sky
is static SVG; pan/zoom touch only the `viewBox`; hover and click ride one
delegated listener set on the `<svg>` — hairline arcs get invisible
9px-wide hit twins; class labels hide below the zoom threshold while
island labels counterscale; arcs hold 1px at every altitude via
`vector-effect: non-scaling-stroke`.

## 6. Design language

The instrument's tokens, unchanged: grounds `#0b0d10 → #161a22`, one warm
ink at three strengths, **forge amber as the only accent** (provenance,
evidence, the live identity), verdict green/red only on human verdicts,
hairline discipline, serif display (`Iowan Old Style…`), system sans
chrome, mono data, the strict `11/12.5/14/16/20/28` scale.

What the OS adds:

- **The void.** The workspace is near-black with one barely-there radial
  amber vignette. No gradients elsewhere, no glassmorphism kitsch — the
  dock and spotlight panel are the only frosted surfaces, and they are
  dark, not milky.
- **Window chrome.** `#11141a` panels, hairline borders, 8px radius; the
  focused window earns the 24px/40% shadow and the amber-lit glyph;
  unfocused windows recede. Titlebars are `cursor: default`,
  `user-select: none`; resize handles carry directional cursors; nothing in
  the chrome ever shows a text I-beam.
- **The menubar.** A fixed strip: serif wordmark with the amber spark,
  "the ontology operating system", live estate/atoms/cost in mono, the
  `⌘K` hint. No clock — the product's time axes are the interesting ones.
- **First light.** An empty workspace shows a centered serif epigraph —
  *"struck from source atoms — nothing asserted without a derivation"* —
  and *press `/` or just type*. First run opens Ask and the Constellation
  snapped side by side.
- **Motion.** One 150ms curve for chrome; 160ms programmatic window moves;
  220ms FLIP; gauges settle in 420ms; everything collapses under
  `prefers-reduced-motion`. During a drag there is NO transition — the
  window is bolted to the cursor.

## 7. Interaction inventory

| interaction | mechanics |
|---|---|
| Spotlight | `⌘K` toggle, `/`, type-on-empty-workspace; `↑↓⏎`, `⌘1–9`; sub-frame local filtering + 45ms-debounced `/api/search` |
| Ask / Enter | `POST /api/ask`; skeleton → answer/clarify/abstain states |
| Cite-dot | spawns/re-points the Evidence child beside the answer window |
| Derivation tree | history bars → `evidence:prov` → `GET /api/provenance/{ref}` |
| Clarification | chips or keys `1–9` → `POST /api/ask/clarify` |
| Window drag | pointer capture, rAF transform, edge-zone snap preview, unsnap memory |
| Window resize | 8 handles, outside hit areas, math-clamped minima |
| Minimize / restore | FLIP to/from the dock tile; dock shelf collects them |
| Double-click titlebar | maximize toggle with pre-snap memory |
| `esc` | closes Spotlight; else dismisses a focused transient Evidence window |
| Review keys | `j/k/a/r` routed to the focused Review window only |
| Neighbors | `GET /api/entities/{uri}/neighbors` → click → another Inspector, adjacent |
| Time scrubber | pointer-captured drag → debounced as-of refetch; values flash |
| Chart expand | `⤢` → the chart in its own window |
| Export | `POST /api/export` + `GET /api/exports` bundle shelf |
| Workspace | every change → debounced `PUT /api/workspace` + localStorage; restored on boot; re-tiled on viewport resize |

## 8. Why this is twenty years ahead

Every BI surface on the market renders *answers in a page*. This one gives
the analyst an *operating system for belief*: questions, evidence,
entities, time, and governance are spatial objects you arrange, not routes
you visit. The investigation workflow that defines the product — claim →
evidence → entity → neighbor → its evidence — is a chain of windows opening
beside each other, each one a provenance contract. The dock shows
governance running (Review, Pulse); the Exporter makes leaving a visible
feature, which is exactly what makes staying credible. No competitor in
MARKET_EDGE.md can draw any of these windows, because their platforms don't
store the artifacts. The shell is thin — it merely refuses to hide what the
engine knows, and now it gives you a desk to spread it out on.
