# OntoForge OS — the web shell

The browser surface served at `/` by `ontoforge serve`: an operating system
for an induced ontology, not a website about one. Vanilla ES modules, no
build chain, no framework. Non-vendor payload budget: 250 KB (test-enforced).

## Layers

| module | role |
|---|---|
| `app.js` | boot: registry + WM + dock + spotlight wiring, intent routing policy, keyboard, first-run layout |
| `js/core.js` | kernel helpers — `el()`/`svgEl()` (text-node-safe DOM), `api()`, `store`, ontology cache |
| `js/bus.js` | the inter-app bus: namespaced intents, `on()` returns an unsubscribe |
| `js/wm.js` | window manager — pointer-capture drag, 8-handle resize, transform-only motion (rAF-coalesced), stack-array z-order, edge-zone snap with preview ghost + unsnap memory, FLIP minimize, workspace persistence (`PUT /api/workspace` + localStorage) |
| `js/dock.js` | dock: launch/focus, running embers, minimized shelf (the FLIP target) |
| `js/spotlight.js` | the front door: `⌘K` / `/` / just typing; local registries scored fzy-style, `GET /api/search` debounced + abortable; 'Ask the estate' fallback so no query dead-ends |
| `js/constellation.js` | the deterministic star-chart layout engine (seeded force sim) |
| `js/apps/*.js` | the eight micro-apps, registered in `js/apps/registry.js` |

## Micro-apps

ask · constellation · inspector · evidence · review · dashboards · pulse ·
exporter. Each is a window class `{ id, title, glyph, w, h, multi,
transient?, mount(ctx, params) }`. Apps never import each other — intents
(`entity:open`, `class:focus`, `evidence:atoms`, `ask:run`, `app:launch`)
travel over the bus and `app.js` owns routing policy (singletons, child
evidence windows, spawn-adjacent placement). Window subscriptions and
disposers are torn down by the WM on close.

## Invariants (test-enforced in `tests/server/test_spa.py`)

- **No innerHTML carries data** — everything enters the DOM through
  `el()`/`document.createTextNode`.
- **Abstention is a dignified state** — `state-abstained`, "declines to
  guess", never an error style.
- **The import graph is closed** — every relative import resolves and serves.
- **WM discipline** — `setPointerCapture`, `translate3d`, rAF coalescing,
  a `pointercancel` path, gesture-scoped `will-change`.
- **Payload** — non-vendor static < 250 KB.

`vendor/` holds the Vega trio (charts only) and is excluded from the budget.
