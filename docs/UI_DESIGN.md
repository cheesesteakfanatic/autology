# OntoForge OS — the ontology operating system (warm mid-century redesign)

The web UI (`src/ontoforge/server/static/`) is no longer a page with tabs.
It is an **operating surface**: warm oatmeal paper on which every capability
is a windowed micro-app, the dock launches and collects them, and Spotlight
— summoned by `⌘K`, `/`, or just typing on the empty workspace — is the
front door to everything the estate knows.

The shell wears a **mid-century-modern** skin: the whole OS sits on Canvas
Oatmeal `#ECE1CB` with a 3% paper-grain tooth; windows float as molded
fiberglass shells (cream / warm-white, generous radii) each capped by a
**colored title-bar strip in its app's atlas hue**; ink is Espresso `#2A1F14`
(never black) and every shadow is warm-amber (never black). The references —
Charley Harper, Saul Bass, Eames/Herman Miller, Dieter Rams, mid-century
cartography — are encoded as rules, not decoration (see §6). Warm light is
the default and the first impression; a night theme is opt-in and persisted.

docs/MARKET_EDGE.md found that what nobody else ships are **trust
artifacts**: per-value citations, calibrated abstention, bitemporal
per-value provenance, a testable exit guarantee. The OS shell treats *trust
as the aesthetic* and *investigation as the workflow*: claims open evidence
windows beside them; entities open inspectors beside inspectors; nothing
asserted without a derivation.

Zero frameworks. Vanilla ES modules, one stylesheet, vendored Vega for
charts only. Non-vendor payload ≈ 213 KB (budget 250 KB, enforced by
`tests/server/test_spa.py`). Every piece of API data enters the DOM through
`el()`/`document.createTextNode` — `innerHTML` never carries data (also
test-enforced). The app ships **offline**: no external fonts or CDNs at
runtime; the grain is an inline data-URI; only Vega is vendored.

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
  window; `.focused` lifts the body to elevation 2 (warm-white + the
  `--shadow-2` two-layer warm-amber shadow + a 2px marigold focus ring on
  keyboard focus) and re-saturates the colored title strip, while inactive
  windows desaturate their strip to ~40% and drop to elevation 1. The
  per-app accent is written as `--accent` on each window root by the WM
  (`appHue(spec.id)` from `core.js`), so the title strip, dock tile and any
  cite-dots an app renders all wear the same atlas hue. Keyboard routes
  through the shell to the focused window only; apps attach no global keys.
- **Snap.** The POINTER (not the window edge) is hit-tested against 18px
  edge strips: left/right = halves, top = maximize, corners = quarters. A
  translucent marigold preview island animates to the slot before release; the
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

A single Card-Cream pill floating 12px above the canvas (elevation 1, warm
top-highlight, 18px radius). One **rounded-square tile per app, each in its
own atlas hue** with a flat espresso glyph; the hovered tile lifts 6px and
shows a small-caps label chip above; running apps get a small **marigold
starburst dot** beneath, with a single 140ms pulse on a fresh launch.
Inactive tiles desaturate. No magnification fisheye — just a clean lift.
Click = launch, focus, or restore. Minimized windows collect on a
hairline-separated shelf as Sunken-Bisque tiles — the FLIP target.

**Toasts.** A kernel `toast(msg, {kind})` (in `core.js`) drops calm notices
into one host above the dock — warm-white cards with a hue-keyed left edge
(`ok` teal / `warn` marigold / `error` terracotta) that rise in, hold, and
fade. Used for export results, project reload, and the recalibration moment;
never a frantic stack.

## 5. The micro-apps

| app | glyph | what it is |
|---|---|---|
| **Ask** | ❯ | the console reborn in a window (marigold title strip): question field, history chips, answers as a **warm-white card of record with a teal left-edge** (a cited answer), values in mono with **numbered cite-dots colored by their source island** that *land* with a staggered pop; a dot opens an **Evidence child window beside the answer** (re-pointed, never duplicated); clarifications are `1–9` keystroke chips; abstention renders as the dignified Abstention-Taupe `state-abstained` card with a dash-in-circle mark — *"OntoForge declines to guess."* The confidence **arc gauge** sits inline (see §6). |
| **Evidence** | ⌗ | an index-card tray (mustard strip): source-atom chips fetched live from `/api/atoms` (bisque troughs, source-hue id chips), or the full derivation tree from `/api/provenance` (sums = "any of", products = "all of"). A child of its citing window: closing the parent closes it; `esc` dismisses it. Transient — never persisted. |
| **Constellation / Atlas** | ✶ | the star chart in a resizable window (teal strip): deterministic seeded force layout, stars sized by structure, marigold luminance = confidence, subsumption hairlines, bowed teal link arcs; pan/zoom/reset; class detail drawer beneath; `focusClass(uri, prop)` API the WM routes `class:focus` to. When `GET /api/atlas` is built it becomes **THE ATLAS** (see §5a) and retitles itself *"Atlas — N islands · M silos"*. Singleton. |
| **Inspector** | ◈ | one entity (ocean strip): property card under a temporal stance, the **as-of time scrubber** (drag → debounced refetch, changed values flash) whose domain is **clamped to the data's real activity window** (epoch-floor `1970` cells no longer stretch it useless — see §7), per-property **bitemporal history bars** (click → derivation in Evidence), and the **neighbors list** — clicking a neighbor opens *another Inspector beside this one*: the OS moment. Multiple instances are the point; the same URI refocuses instead of duplicating. |
| **Review** | ⚖ | the adjudication queue (plum strip): kind/tier badges, ER pairs side-by-side under *"same?"* (sides deep-link to Inspectors), conformal chips, confidence arc gauge, `j/k/a/r` routed to this window only, and the recalibration arc (`n/20`) per kind; a verdict toasts, a recalibration toasts loudly. Singleton. |
| **Dashboards** | ▤ | utterance → VISTA's top-3 proposals (avocado strip) with **warm-themed Vega-Lite previews** (the atlas categorical wheel for series, marigold default mark, espresso/walnut axes); every chart has a `⤢` that expands it into its own window (a single-chart viewer instance). Saved proposals below. |
| **Pulse** | ◉ | the instrument cluster, live-ish (terracotta strip): counters in mono tabular-nums, pipeline stages, by-kind/by-tier tables, polled every 10s while open (interval disposed with the window); project reload lives here, toasts on success, and announces `world:reload` on the bus. Singleton. |
| **Exporter** | ⇲ | portability as a visible feature (persimmon strip): one marigold button strikes an AMBER snapshot (`POST /api/export`), the shelf lists bundles (`GET /api/exports`). **Any non-success — a 404/405 (endpoint absent) OR a 500 (server fault) — degrades to one honest Abstention-Taupe guidance card** (*"snapshot could not be struck … run `ontoforge export` from the CLI"*) plus a toast; a raw stack-trace string is never shown. Singleton. |

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

**Islands** are mid-century cartography. Each connected component lays out
as its own island — an intra-component seeded force sim (same deterministic
physics, scaled to the island's size), packed on a loose golden-angle
spiral, largest first. **Each island draws a distinct warm hue from the
locked atlas wheel** (`ISLAND_HUES` in `js/constellation.js`, kept in sync
with `core.js ATLAS_HUES`): a flat kidney/blob hull filled in its hue with a
faint dashed contour halo — a coastline, not a border — and its stars
stroked in the same hue. The island label is small-caps geometric sans with
its `dataset_count`, counterscaled so it reads at every zoom; clicking it
flies the view to fit the island.

**The grammar of joins** — link tiers as map roads:

| tier | rendering | reading |
|---|---|---|
| `confirmed` | solid 2px **teal** road | settled knowledge — quiet, load-bearing |
| `likely` | **dashed marigold**, stroke-opacity ∝ score | a hypothesis carrying its own weight; breathes slightly on hover only |
| `hint` | nearly invisible dotted walnut, **off by default** | static at the edge of hearing |

Hovering a likely arc opens the **evidence card**: tier, score, coverage
%, overlap count, the two column names and up to five sample shared
values in mono. Click pins it; click the void (or ×) releases it. The
legend chips are **filter toggles** (confirmed / likely / hint / silos)
carrying live counts from `stats`.

**The archipelago.** Silos — classes nothing joined — collect in a quieter
band along the bottom under a hairline and the label *"archipelago — N
silos · honest and unjoined"*. They are dimmer (bisque-filled, walnut-
stroked, no halo) but **dignified**: never error-red, because an honest silo
is a finding, not a failure.

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

## 6. Design language — the warm mid-century system

All tokens live as CSS custom properties in `:root` (style.css).

**Palette.** Warm neutral grounds — Canvas Oatmeal `#ECE1CB` (desktop),
Card Cream `#FBF4E6` (elevation 1), Warm White `#FFFDF7` (elevation 2),
Sunken Bisque `#E3D6BB` (recesses). Ink is Espresso `#2A1F14` (primary,
14.7:1 on cream) and Walnut `#6B5A45` (secondary, 6.0:1) — **never #000**.
**Marigold `#E0A126` is the primary-action fill and the "likely" accent;
it is fill/stroke ONLY** — as text on cream it fails at 2.1:1, so the one
marigold-filled button class carries Espresso ink (7.1:1). The locked
**atomic-age 8-hue categorical wheel** — teal `#1F6F6B` (= confirmed),
marigold, terracotta `#C75B39`, avocado `#7C8A3B`, ocean `#2D6E8E`,
persimmon `#B8532A`, plum `#6E4A63`, mustard `#9A6B2F` — carries every
island, dock tile, window strip, chart series and cite-dot. Verdict green
`#4F7A3A` / red `#B23A2E` only on supported/refuted verdicts and hard
errors. Abstention is its own **calm taupe `#D8CDB8`**, never red. All
hairlines are tinted espresso; **all shadows are warm-amber
`rgba(90,55,20,*)`, never black** — the single rule that keeps the whole
OS feeling warm rather than digital.

**Typography.** Geometric-sans chrome (`Futura, Avenir Next, Century
Gothic, …`); the wordmark and window titles are **small-caps, +0.06em**;
the *"Onto✱Forge"* dot is an **atomic starburst glyph**, not a period. Data
values, confidence %, ids and coordinates live in **mono with tabular-nums**
so figures sit in a deliberate instrument-panel grid. The strict
`11/12.5/14/16/20/28` scale. (The system has **no serif** — chrome falls
through the geometric stack; losing it would collapse the MCM identity.)

**Surfaces & shadows.** Elevation 0 = the oatmeal ground under a 3% tiled
fractal-noise grain (tooth, never visible texture). Elevation 1 = Card
Cream, 12px radius, `--shadow-1`. Elevation 2 = Warm White, the focused
window/popovers, `--shadow-2` (tight contact + soft ambient + a thin warm
top-inner highlight = the molded-fiberglass shell). Elevation 3 = the
spotlight modal on a 32% espresso scrim with 8px blur. Recesses (table
headers, evidence troughs, input wells, the scrubber track) are Sunken
Bisque with an inset warm shadow.

**Window anatomy.** Each window = an elevation-2 warm-white body (14px
radius) + a 28px **colored title-bar strip in the owner-app's atlas hue**,
square-bottomed so it reads as a TV-bezel cap. The three controls are flat
solid Charley-Harper disks — min = marigold, close = terracotta — with a
thin espresso glyph only on hover. Inactive windows desaturate their strip
and drop an elevation; the keyboard-focused window earns a 2px marigold
glow ring. Resize handles are invisible but carry directional cursors;
nothing in the chrome shows a text I-beam.

**The confidence gauge** is the signature instrument: a **270° open arc**
(the kidney/TV-screen curve), track in Sunken Bisque, the fill arc
**banded by confidence — ≥0.8 teal (confirmed), 0.5–0.8 marigold (likely),
<0.5 walnut (weak)** — with a tiny starburst tick at the 0.5 threshold, the
value in large mono tabular-nums at the center, and a small-caps band label
beneath. One-time 600ms sweep on render (storytelling), instant after.
Never a plain progress bar. (`confGauge()` in `core.js`.)

**Tables** breathe (Dieter Rams): Sunken-Bisque small-caps sticky headers,
hairline horizontal rules ONLY (no vertical grid), mono tabular-nums for
numerics, zebra OFF by default, one solid badge per row at most.

**The menubar.** A fixed cream strip with the geometric small-caps wordmark
+ atomic dot, "the ontology operating system", live estate/atoms/cost in
mono, the `⌘K` spotlight chip, and a **warm/night theme toggle** (warm is
default and never the dark first impression). No clock — the product's time
axes are the interesting ones.

**First light.** An empty workspace shows a centered epigraph — *"struck
from source atoms — nothing asserted without a derivation"* — and *press
`/` or just type to begin*. First run opens Ask and the Constellation
snapped side by side.

**Motion** lands softly, never wobbles (Saul Bass: form performs meaning).
Standard `cubic-bezier(0.2,0.8,0.2,1)` at 180ms for open/settle, 140ms for
hover/press (buttons depress 1px), 160ms `cubic-bezier(0.4,0,0.6,1)` for
exit; the spotlight rises `0.96→1` over 200ms; cite-dots pop in at 40ms
stagger; the gauge sweeps once at 600ms; nothing else exceeds ~220ms; no
spinners-as-decoration. `prefers-reduced-motion` replaces all travel with
100ms opacity fades and holds the gauge/atlas at their final state. During a
drag there is NO transition — the window is bolted to the cursor.

**Dark mode** is opt-in (`html[data-theme="dark"]`, persisted): espresso-
deep grounds keep the warm cast, the atlas hues and marigold carry through.

## 7. Interaction inventory

| interaction | mechanics |
|---|---|
| Spotlight | `⌘K` toggle, `/`, type-on-empty-workspace; `↑↓⏎`, `⌘1–9`; sub-frame local filtering + 45ms-debounced `/api/search`. Server app ids are remapped to the real JS apps (`entities→inspector`, `status→pulse`, `export→exporter`) so no row dead-ends; non-unique entity labels get a disambiguating ref tail so distinct hits never read as identical duplicates |
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
| Time scrubber | pointer-captured drag → debounced as-of refetch; values flash; domain clamped to the data's real activity window (5th-percentile lower edge, epoch-floor cells excluded) so the slider is never squashed to 1970→now |
| Chart expand | `⤢` → the chart in its own window |
| Export | `POST /api/export` + `GET /api/exports` bundle shelf; any non-success degrades to one honest taupe guidance card + toast (no raw stack trace) |
| Theme | menubar toggle flips warm ↔ night (`data-theme="dark"`), persisted to localStorage; warm is the default |
| Toasts | `toast()` notices above the dock for export / reload / recalibration |
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
