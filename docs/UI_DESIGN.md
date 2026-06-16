# OntoForge — three plain-language modes (warm mid-century redesign)

The web UI (`src/ontoforge/server/static/`) is organized around **three
modes**, switched by one always-visible segmented control in the top bar —
**Ask · Build · Studio** — and the user always knows which one they're in.
Each mode shows ONLY its own surfaces; switching is instant (a JS pane flip,
never a navigation) and obviously changes the whole workspace.

- **ASK** — *the questioner.* The default landing for every session: a single
  large, centered question box, suggested questions generated from the model,
  recent questions, and — after you ask — a cited answer card with inline
  **"Where this came from"** dots. No window chrome, no dock, no graph.
- **BUILD** — *measure something / pull data out.* A two-pane builder: pick a
  **measure** and **break-it-down-by** dimensions in plain terms (or describe
  it free-text), get ranked **dashboard proposals** as warm Vega charts, then
  two clearly-separated outputs — **Extract** (the filtered table → Download
  CSV, a slice) and **Export** (Download the whole dataset, portable — the
  bundle).
- **STUDIO** — *the data-engineering playground.* A workspace of labeled,
  persistent sections named by a left rail — **Data Catalog**, **Data Map**,
  **Console**, **Confirm suggestions**, **Activity** — powered underneath by
  the window manager and dock. The signature moment: type a plain-English
  instruction in the Console and watch the Data Map react, or add data and
  **watch the model build live** as nodes pop and join-arcs draw from real
  engine events.

The WM + dock (§2, §4) are **Studio's substrate only** — ASK and BUILD are
calm single surfaces that never spawn windows.

**De-jargon is presentation-only** (§0.1). Internal engine codenames
(STRATA/HEARTH/LODESTONE/ANVIL/TEMPER/WARDEN/VISTA/AMBER/OQIR) never appear in
any user-facing label; only the *labels* change — code, URIs, API routes and
bus intents keep their internal names so routing/persistence still work.

The shell wears a **mid-century-modern** skin: the whole UI sits on Canvas
Oatmeal `#ECE1CB` with a 3% paper-grain tooth; Studio windows float as molded
fiberglass shells (cream / warm-white, generous radii) each capped by a
**colored title-bar strip in its app's atlas hue**; ink is Espresso `#2A1F14`
(never black) and every shadow is warm-amber (never black). The references —
Charley Harper, Saul Bass, Eames/Herman Miller, Dieter Rams, mid-century
cartography — are encoded as rules, not decoration (see §6). Warm light is
the default and the first impression; a night theme is opt-in and persisted.

docs/MARKET_EDGE.md found that what nobody else ships are **trust
artifacts**: per-value citations, calibrated abstention, bitemporal
per-value provenance, a testable exit guarantee. The UI treats *trust as the
aesthetic* and *investigation as the workflow*: every Ask answer shows where
it came from; in Studio, claims open evidence beside them, entities open
records beside records, nothing asserted without a derivation.

Zero frameworks. Vanilla ES modules, one stylesheet, vendored Vega for
charts only. Non-vendor payload ≈ 287 KB (budget **290 KB** = 296 960 bytes,
enforced by `tests/server/test_spa.py`; headroom is tight — copy/label
additions must watch the budget). Every piece of API data enters the DOM
through `el()`/`svgEl()`/`document.createTextNode` — `innerHTML` never
carries data (also test-enforced). The app ships **offline**: no external
fonts or CDNs at runtime; the grain is an inline data-URI; only Vega is
vendored.

---

## 0. The three-mode shell (js/modes.js)

The shell (`createModeShell`) owns three segments (`#mode-ask`,
`#mode-build`, `#mode-studio`) and three panes (`#pane-ask`, `#pane-build`,
`#pane-studio`). `switchTo(mode, opts)` flips `aria-selected` + the `.active`
class on the lit segment, toggles `hidden` on the panes, hides the dock
outside Studio, lazily mounts each mode's surface on first entry, and emits
`mode:changed`. `⌘1/⌘2/⌘3` jump straight to a mode (the shell claims these
before any window). ASK is the default landing (`modes.boot("ask", …)`).

- **First-run orientation.** A dismissible coach card ("three ways to work")
  names all three modes in one glance; its primary action adapts to the data
  state — *"Add your first dataset →"* (→ Studio › Catalog) when empty,
  *"Try a question →"* (→ Ask, pre-filled) when a model exists. Dismissal and
  per-mode first-visit nudges persist in `localStorage` (`COACH_KEY`,
  `FIRSTVISIT_KEY`); a top-bar `?` re-opens it on demand.
- **The Studio segment carries a count badge** for pending *Confirm
  suggestions* (`review:count` on the bus → `setBadge`), mirrored on the rail.

### 0.1 The de-jargon naming map (labels only)

| internal (code / URIs / intents — unchanged) | user-facing label |
|---|---|
| atoms / `atom://…` | **source records** |
| Atlas / Constellation (`id:"constellation"`, `/api/atlas`) | **Data Map** |
| Pulse | **Activity** |
| Evidence / cite-dots | **Where this came from** / Sources |
| Inspector | **Explore record** (action) / **Record** (title) |
| Exporter / AMBER snapshot | **Export** / Download the whole dataset, portable |
| Review / adjudication (`/api/review`, `verdict("accept")`) | **Confirm suggestions** / Confirm · Not the same |
| classes / ontology | **types** ("things") / **the model it built** |
| VISTA dashboards | **Dashboard proposals** / Build a view |
| atlas tiers confirmed / likely / hint / silo | **confirmed join** / **likely join** / possible / **standalone** |
| ingest→profile→induce→resolve→materialize | Reading the data → Finding the shape → Building the model → Matching records → Filling in values |
| abstained | **No grounded answer — won't guess** |

---

## 1. Architecture

```
app.js                 boot + the three-mode shell wiring + Studio routing
js/core.js             kernel: el/svgEl/clear/api/store + ontology/atlas cache
js/bus.js              inter-app bus: namespaced intents, disposable subs
js/modes.js            the three-mode shell controller (ASK | BUILD | STUDIO)
js/surfaces/ask.js     ASK — the centered questioner (single surface)
js/surfaces/build.js   BUILD — the measure/breakdown builder + Extract/Export
js/wm.js               the window manager (Studio substrate)
js/dock.js             the dock (Studio substrate)
js/spotlight.js        the search (⌘K front door, all modes)
js/constellation.js    deterministic Data-Map layout engine
js/apps/registry.js    the Studio app registry
js/apps/{catalog,datamap,console,review,pulse,inspector,evidence}.js
```

**ASK and BUILD are single surfaces** mounted directly into their pane —
no windows. **STUDIO** is the windowed power-tool workspace: a left rail
(`STUDIO_PANELS`) names the five sections and clicking one focuses-or-opens
the matching window; on entry the signature pairing is tiled —
`tileStudioSignature()` puts the **Data Map** across the top with the
**Console** docked along the bottom. The Catalog leads instead when the
project is empty.

**Apps never import each other.** An app emits intents over the bus —
`entity:open`, `class:focus`, `evidence:atoms`, `evidence:prov`, `ask:run`,
`app:launch`, plus the playground intents `studio:build-started`,
`studio:atlas-delta`, `studio:show-map`, `workspace:built`, `world:reload`,
`mode:goto` — with `sourceWinId` stamped where relevant, and `app.js` decides
which window answers: focus the existing singleton, re-point an existing
child, or spawn adjacent to the source. The WM collects every bus
subscription and disposer a window makes and tears them down on close.

## 2. The window manager (js/wm.js) — Studio's substrate

The WM lives inside the **Studio** pane only; Ask and Build never spawn
windows. Mechanics borrowed from real WMs; chrome native to the web (no
traffic lights, no aero glass — the uncanny valley is avoided by not
entering it):

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

## 4. The dock — Studio only

The dock is hidden outside Studio (`dock.hidden = mode !== "studio"`). In
Studio the named **left rail** is the plain-language way into the five
sections; the dock is the substrate that powers and collects the windows
underneath. A single Card-Cream pill floating 12px above the canvas (elevation 1, warm
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

## 5. The Studio micro-apps

The Studio apps keep their internal registry ids (`constellation`,
`pulse`, …) but wear de-jargoned **labels**. The five rail sections plus two
shared utilities (Record, Where this came from) opened on demand:

| app (id) | label / glyph | what it is |
|---|---|---|
| **Data Catalog** (`catalog`) | ▦ | browse downloaded datasets grouped by **domain** (table-of-contents first, collapsed), search across name/columns, per-row **build-status pill** (Modeled / Building… / Not yet modeled / Needs attention), an **Add data** sheet (folder/file path or drop, optional domain), guarded **Remove** ("your source files are not deleted"). Select up to **25** datasets → **Build map** → `POST /api/workspace/build` → emits `studio:build-started`. |
| **Data Map** (`constellation`) | ✶ | the live join graph (teal strip): types as nodes, joins tiered **confirmed join** (solid teal) / **likely join** (dashed marigold) / **standalone** (no link found). **The signature live build:** on `studio:build-started` it polls `GET /api/workspace/build/{job_id}` and animates from REAL events — a node pops the moment a type is induced (`type_found`), an arc draws the moment a join is classified (`join_found`), batched **≤4 per frame on rAF** so a burst never strobes; an honest progress strip names the stage in plain words with a determinate bar + live tally. When the build finishes it renders the final interactive **Atlas** (§5a). Tap a node → Explore record; tap an arc → Where this came from. Singleton. |
| **Console** (`console`) | ❯ | the plain-English Data-Engineering console: one instruction at a time → `POST /api/engineer/interpret` → **always a Preview card first** ("nothing has changed yet"), then **Apply** (`/api/engineer/apply`, animates the map delta) with **Undo** (`/api/engineer/undo`). Destructive ops (merge/split/remove) carry a consequence and need an explicit Apply tap — **nothing destructive on Enter alone**. Clarification asks **ONE** question; unsupported never dead-ends — it falls to worked-example chips. |
| **Confirm suggestions** (`review`) | ⚖ | the adjudication queue (plum strip): candidate cards side-by-side under *"same?"*, **Confirm / Not the same** (internally `verdict("accept"/"reject")` on `/api/review`), `j/k` keys, the recalibration arc per kind; a count badge feeds the Studio segment. Singleton. |
| **Activity** (`pulse`) | ◉ | the timeline (terracotta strip): plain pipeline-stage labels — **Reading the data / Finding the shape / Building the model / Matching records / Filling in values** — counters of source records / things / joins, polled every 10s while open; "re-open project" lives here and announces `world:reload`. Singleton. |
| **Explore record** (`inspector`) | ◈ | one record (ocean strip), a shared utility opened from the map/catalog: values under a temporal stance, the **"rewind to a date" time slider** (drag → debounced refetch; domain clamped off the 1970 epoch — see §7), per-field history bars, and the **related-records** list — clicking one opens *another Record beside this one*. Same URI refocuses instead of duplicating. |
| **Where this came from** (`evidence`) | ⌗ | the source-record tray (mustard strip), a shared utility: **source-record** chips fetched from `/api/atoms`, or the derivation tree from `/api/provenance` ("any of" / "all of"). A transient child of its citing window — never persisted. |

ASK and BUILD are **not** in this table — they are single surfaces
(`js/surfaces/ask.js`, `js/surfaces/build.js`), not windowed apps. ASK renders
the cited answer card with inline "Where this came from" dots and the
dignified `state-abstained` card; BUILD renders the measure/breakdown
pickers, the warm-Vega **Dashboard proposals** (`/api/dashboards`), and the
two separated outputs **Extract** (`/api/extract` → Download CSV) and
**Export** (`/api/export` → "Download the whole dataset, portable").

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

**Attention hierarchy — "don't compete for attention you haven't earned."**
The orientation/navigation chrome RECEDES so the *active work surface*
visually dominates. A dedicated **chrome ink tier** sits between `--ink` and
`--ink-faint` in the same espresso hue family (≈34°; only lightness lifts):

| token | hex | on cream | used by |
|---|---|---|---|
| `--chrome-ink` | `#4A3B29` | 9.84:1 | the wordmark, the menubar meta **values** — recessed but primary |
| `--chrome-dim` | `#806D56` | 4.53:1 | the menubar meta **captions** — recessed secondary |

The active mode segment and active rail item keep **full `--ink`** and earn
their primacy through *elevation* (warm-white lift + inset marigold line +
`--shadow-1`) — never by letting the inactive labels fall below AA, so the
bisque-grounded switcher/rail labels stay at `--walnut` (4.60:1 on bisque).
Window **title-bar caps recede**: the per-app glyph drops to `--fs-0` at 0.7α
(identity, not a competing label) and the title sits at 0.94α near-white;
unfocused windows desaturate their strip to ~0.32 saturation + 0.92 opacity
so a single focused window's work surface is unambiguously the foreground.
The night theme inverts the tier (`--chrome-ink #D8CBAF`, `--chrome-dim
#9E8F75`) so chrome dims *down* from the light ink on espresso grounds.

**Palette governance (so contrast can't drift).** The ink ramp is
HCL-disciplined and its measured AA figures are recorded *inline in
`style.css`* beside the `:root` tokens (a `PALETTE GOVERNANCE` /
`ATTENTION-HIERARCHY` comment block): every ink shares one warm espresso hue
and steps only in lightness — `--ink` 14.7:1, `--walnut` 6.0:1, `--ink-faint`
4.6:1 on Card Cream, all ≥ AA. The accent hues (marigold, teal, the eight
atlas hues) are reserved for **meaning** — primary action, the certainty
tiers, per-island/per-app identity — never decoration. `tests/server/
test_spa.py` asserts the chrome tier exists, the governance note is present,
the recorded ratios stay AA, and the active segment keeps full contrast.

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

Every BI surface on the market renders *answers in a page* and asks the
analyst to learn its jargon. This one offers **three plain-language modes**
that meet a person where they are: **Ask** a question and see where every
answer came from; **Build** a view by naming what to measure and pull the
data out; open the **Studio** to add data and *watch the model build itself*
— nodes popping, join-arcs drawing from real engine events — then steer it in
plain English with a console that previews before it touches anything and
always offers Undo. The de-jargon layer is presentation-only, so the trust
artifacts the engine actually stores — per-value citations, calibrated
abstention, bitemporal provenance, a portable exit bundle — are exactly what
the surfaces show, under names a newcomer can read. Ask shows where; Build
measures and extracts; Studio is the autonomous-data-engineering playground
no competitor in MARKET_EDGE.md can draw, because their platforms don't
induce the model or store the artifacts in the first place. The shell is
thin — it merely refuses to hide what the engine knows, and now it hands that
knowledge to three different kinds of user without making any of them learn
the engine's vocabulary.

## 9. Maturation pass — from "warm but childish" to natural / premium

The founder's read of the first warm system: *"I like the colors but it feels
childish."* This pass keeps the exact same warm-midcentury **direction** and
grows it up — calmer, more editorial, fewer bytes. Five disciplines, all
test-enforced in `tests/server/test_spa.py`:

**9.1 Chroma discipline — color is information, not decoration.** The 8-hue
wheel + marigold were pulled out of the ~50-70% "crayon" saturation band into
a muted **25-40% S** band (desaturated ~30-40%, *not* to gray — the warmth the
founder likes is kept). Each hue holds its angle and its **locked `ATLAS_HUES`
order** (mirrored in `js/core.js` and the import-free `js/constellation.js`),
and its lightness drops so any hue used as *text* clears AA on cream:

| token | was (crayon) | now (muted) | S% | on cream |
|---|---|---|---|---|
| `--marigold` | `#E0A126` | `#D09735` | 62 | fill only — ink-on-marigold 6.26:1 |
| `--teal` (= confirmed) | `#1F6F6B` | `#2C5956` | 34 | 7.2:1 |
| `--terracotta` | `#C75B39` | `#945442` | 38 | 5.3:1 |
| `--avocado` | `#7C8A3B` | `#6C733A` | 33 | 4.6:1 |
| `--ocean` | `#2D6E8E` | `#375E72` | 35 | 6.4:1 |
| `--persimmon` | `#B8532A` | `#945942` | 38 | 5.1:1 |
| `--plum` | `#6E4A63` | `#713D68` | 30 | 7.5:1 |
| `--mustard` | `#9A6B2F` | `#86663C` | 38 | 4.8:1 |
| `--dusty-rose` | `#C98B7A` | `#A97060` | 30 | decorative only |

Marigold stays a touch warmer than the wheel deliberately: it is the **single
primary FILL** and ink-on-marigold must read. WCAG was re-measured after every
L/S change. The wheel is now used **only as information** — atlas islands,
category chips, cite-dots — never as decoration.

**9.2 Neutral : accent ratio — one accent per view.** The colored window
**title-bar strip is neutralized**: it is now a cream cap with a hairline
border and a thin **2px accent underline** (it still carries `var(--accent)`,
but as a marker, not a hue fill). The five per-card 3px colored left-edges
(`.build-export` persimmon, `.build-extract` avocado, `.console-card.clarify`
ocean, `.dash-rank` avocado-text, the scrub-stance ocean-text) collapse to a
**hairline default** plus exactly **two semantic accents**: **teal = confirmed
/ cited** (the answer card's left-edge, the gauge "confirmed" band) and
**marigold = the one primary action** (the previewed apply, the clarify edge,
the selected review card). Color now touches a small fraction of pixels.

**9.3 Type maturity.** A real **system serif** (`--serif: 'Iowan Old Style',
Palatino, Georgia, ui-serif` — a system stack, no webfont, honors the offline
invariant) carries **hero headlines only**: the Ask tagline, the clarify
question, the not-ready title, the type-detail `h2`, the answer question, the
coach title. The chrome sans drops Futura for a **humanist** stack
(`-apple-system, Inter, system-ui`). `font-variant:small-caps` is **removed
from buttons / dock / window titles / mode segments / table headers / domain
names** (the retro-poster "costume" tell) and **reserved for tiny eyebrow
kickers only** (`.badge`, the gauge band, counter/stage/pair labels). Tracking
is capped at ~0.01em on body/labels (eyebrows keep ~0.08em); exactly two
weights (400 / 600 — the stray 700s dropped to 600); the editorial scale gains
`--fs-6: 2.75rem` for the serif hero.

**9.4 Form restraint + calmer motion.** Radii tighten — `--radius 12→8`,
`--radius-win 14→10`, the dock `18→12`, and the 999px pills become a `6px`
rounded-rect (`--radius-pill`). The **toy motifs are deleted**: the dock
`dot-pulse` scale(2) launch-bounce and its conic-gradient starburst running
indicator (now a flat 5px dot), the `switcher-halo` coach ring, the `node-pop`
overshoot, the `likely-breathe` pulse (a lit arc now simply holds full
opacity), and the 45° striped `chart-placeholder` (now a flat dashed box). The
`value-flash` is a one-shot bg fade. Shadows shrink and the plasticky
`rgba(255,253,247,0.6) inset` highlight is gone (the warm-amber cast stays —
the differentiator vs gray SaaS). Grain is quieter (`baseFrequency 0.9→0.65`,
`opacity 0.03→0.025`). Data surfaces tighten ~15-20% (table cells, cards).

**9.5 Performance — a canvas render path for the Data Map (constellation).**
`js/constellation.js` (which had 23 `svgEl` sites per island, SVG ceiling
~1-2k elements) gains a **canvas acceleration layer**. Past
`CANVAS_THRESHOLD = 300` (nodes + arcs), the geometry — island hulls, node
cores/halos, and arc strokes — paints to **one `<canvas>`** on the *same
seeded-force settled positions* and the *same viewBox transform*, in a single
rAF-scheduled pass (`drawCanvas`), holding the frame budget to several-thousand
nodes. The `<svg>` stays the **interaction layer**: it keeps the arc hit-twins,
the island labels, and the selection ring, so pan/zoom (viewBox-only) and arc
hover/pin are unchanged; node hover/click uses a cheap **JS nearest-node
hit-test** (`canvasNodeAt`) over the same node list the canvas paints. The
canvas sits *behind* the svg with `pointer-events: none`, so the svg still owns
every gesture (the security/hover discipline is intact). **Below the threshold
NOTHING changes — the pure-SVG path renders for crisp text + accessibility (the
fallback).** The Meridian demo (202 elements) uses the SVG fallback; the
250-node / 620-arc synthetic atlas exercises the canvas path. The Atlas tier
filters (`hide-confirmed/likely/hint/silos`), the evidence card, the island
labels, and the `ATLAS SCALE GUARD` are all honored in both paths.

**Net result.** The decorative bloat (keyframes, gradients, verbose comments,
a duplicate rule) was removed so `style.css` *shrank* below its pre-maturation
size, and the whole non-vendor shell — even with the additive canvas layer —
stays under the 290 KB payload budget. The system reads calmer, more editorial,
and unmistakably grown-up while staying the same warm paper it always was.
