# CLAUDE.md

OntoForge — an autonomous semantic data platform. Point it at messy CSV/Parquet
sources; it induces a validated ontology, resolves entities, materializes them in
a bitemporal store with per-value provenance, answers natural-language questions
with atom-level citations, and exports the estate as a portable bundle. Built to
`ontoforge-whitepaper-v2-complete.md`; deviations are recorded as typed amendments
in **[docs/DEVIATIONS.md](docs/DEVIATIONS.md)** (read it before changing engine behavior).

## Commands

Always run from the repo root with `uv` on PATH:

```bash
export PATH="$HOME/.local/bin:$PATH"   # uv lives here; system python is 3.9 (too old)

uv sync --all-extras                    # set up the 3.12 venv
uv run pytest tests/ -q                 # full suite — 1186 tests, must stay green
uv run pytest tests/m12 -q              # LODESTONE / free-text robustness gate (70)
uv run pytest tests/m12/test_competency.py tests/meridian -q   # competency + gold gate
uv run ruff check src/                  # lint (pre-existing debt isolated to temper/)

# One-line demo: full pipeline over the Meridian enterprise estate, then serve it
uv run ontoforge demo meridian /tmp/mcm_demo
uv run ontoforge serve -p /tmp/mcm_demo --port 8765   # → OntoForge OS web app

# Point it at YOUR data (any dir of *.csv / *.parquet):
uv run ontoforge init myproj --source /path/to/data
uv run ontoforge ingest -p myproj && uv run ontoforge profile -p myproj
uv run ontoforge induce -p myproj && uv run ontoforge resolve -p myproj
uv run ontoforge materialize -p myproj && uv run ontoforge serve -p myproj

uv run ontoforge ask -p myproj "free-text question"   # cited answer or honest abstention
```

## Architecture

Pipeline (the CLI subcommands, in order): **init → ingest → profile → induce →
resolve → materialize**, then **ask / dashboard / snapshot / serve** over the
materialized world. Each stage appends to the provenance ledger.

Modules under `src/ontoforge/` (whitepaper module → package):

| M | Module | Package | Role |
|---|--------|---------|------|
| M0 | Ledger | `ledger` | content-addressed atoms, provenance semiring |
| M1 | CDC | `cdc` | source pull + RAW Parquet mirror |
| M2 | Spine | `spine` | calibration, conformal sets, tier/budget decisions |
| M3 | Profiler | `profiling` | keys, FDs, INDs, units |
| M4 | STRATA | `strata` | FCA type-lattice ontology induction |
| M5 | ER | `er` | blocking, Fellegi–Sunter, clustering |
| M6 | HEARTH | `hearth` | bitemporal entity store, per-cell provenance |
| M7 | Transforms | `transforms` | transform graph + lineage |
| M8 | ANVIL | `anvil` | by-ontology transform synthesis |
| M9 | WARDEN | `warden` | expectations + drift sentinels |
| M10 | TEMPER | `temper` | ontology-evolution calculus |
| M11 | Export | `export` | RDF/OWL/SHACL round-trip |
| M12 | LODESTONE | `lodestone` | NL → OQIR query planning (grounding, candidates, typecheck) |
| M13 | VISTA | `vista` | dashboard synthesis |
| M14 | AMBER | `amber` | freeze-frame snapshot bundle |

`estates/` holds the swappable estate engines (aviation fixtures + the generic
any-data builder + Meridian generator + the 450-dataset Wild corpus fetcher in
`estates/wild.py`). `pipeline/` orchestrates stages end-to-end; `pipeline/playground.py`
+ `pipeline/playground_events.py` run a **threaded live build** from a catalog
selection into `<project>/playground` and stream a discovery narrative.
`engineer/` is the common-language data-engineering layer: `commands.py`
(deterministic keyless cue-word + slot parser against the live ontology/estate —
clarify-don't-guess) and `operators.py` (`EngineerService` wrapping the real
TEMPER/ANVIL/ER; previews link coverage and **refuses sub-floor joins**, applies
invertible ops with exact undo).

**Server** (`server/`): FastAPI REST API + the web UI. `serve` mounts `server/static/`.
`server/catalog.py` enumerates every downloadable dataset; `server/world.py` routes
all reads through an **active world** (the demo world by default, the playground
after a build). The SPA is vanilla ES modules (no build chain) in a **three-mode
shell** — `js/modes.js` flips between **Ask** / **Build** / **Studio** with no
reload. `js/core.js` is the shared kernel (`el`/`svgEl`/`createTextNode` DOM helpers,
`confGauge`, `toast`, the locked `ATLAS_HUES`/`hueFor`/`APP_HUE`); `js/surfaces/ask.js`
+ `js/surfaces/build.js` are the single-surface modes; Studio is the window-managed
desktop (`wm.js`, `dock.js`, `spotlight.js`, `constellation.js`) hosting `js/apps/*`
(catalog, datamap, console, review, pulse, inspector, evidence). User-facing strings
are **de-jargoned** (atoms→source records, Atlas→Data Map, Pulse→Activity,
Review→Confirm, Inspector→Record, Evidence→Where this came from); internal
ids/URIs/verdicts keep their names. Static-UI tests live in `tests/server/test_spa.py`.

**API contract** (all existing read endpoints — `/api/ask`, `/api/ontology`, `/api/atlas`,
`/api/entities`, `/api/dashboards`, `/api/review`, `/api/status`, `/api/search`,
`/api/export` — operate on the *active world*). The playground/engineer additions:

- `GET /api/catalog` → `{datasets:[{id,name,source,domain,rows,cols,columns,description}], domains}`
  — every downloadable dataset (wild + meridian + aviation), id = `<corpus>:<slug>`.
- `GET /api/workspace/state` → `{datasets,built,active_world,stats}`.
- `POST /api/workspace/build {dataset_ids, mode:"replace"|"add"}` → `{job_id}` (cap 25);
  builds a playground world and flips the active world to it on done.
- `GET /api/workspace/build/{job_id}?since=<seq>` → pollable `{status,progress,stage,events,result?}`;
  `events[].kind ∈ {stage,type_found,join_found,silo}` — `join_found` arcs fire EARLY (raw INDs,
  before profiling) so the UI animates joins forming.
- `POST /api/engineer/interpret {command}` → discriminated union: `{op,preview}` |
  `{clarification,options}` | `{unsupported,reason,supported_examples}`. PREVIEW ONLY; the
  preview carries `op_token` that `apply` echoes back verbatim.
- `POST /api/engineer/apply {op}` → `{ok,deferred,blocked,human_summary,new_stats,atlas_delta,undo_token}`.
- `POST /api/engineer/undo {undo_token}` → `{ok,new_stats}` (exact TEMPER inverse).
- `POST /api/extract {type_uri,filters,columns,limit}` → `{columns,rows,citations}` (+ `?format=csv`).

## Key gotchas

- **Keyless and deterministic.** No API key is ever needed; the spine runs
  deterministic tiers, the NL layer is pure-python (no embeddings/network). Tests
  must stay zero-network and the app ships **offline** — no external fonts/CDNs at
  runtime, only vendored Vega under `server/static/vendor/`.
- **uv, not system python.** `uv` is at `~/.local/bin`; system python here is 3.9
  (the project requires 3.12). Always `export PATH="$HOME/.local/bin:$PATH"` and use
  `uv run`. Run everything from the repo root.
- **Serving from a sandboxed shell:** the macOS preview/sandbox blocks `serve`'s
  process spawns — launch the server through the Bash tool (background) instead.
- **Warm theme is the default.** `:root` in `style.css` is the warm midcentury palette
  (oatmeal/cream grounds, espresso ink, marigold/teal atlas hues, warm-amber shadows —
  never black). A `data-theme="dark"` opt-in night theme is the *only* place
  `rgba(0,0,0,…)` shadows are legitimate. Don't reintroduce dark grounds to `:root`.
- **Security invariant (test-enforced):** API data enters the DOM only via
  `el()`/`svgEl()`/`createTextNode`. Never assign data to `innerHTML`/`outerHTML`
  (`tests/server/test_spa.py` greps for it). Non-vendor payload must stay **< 280 KB**
  (currently 286,125 bytes — only ~600 bytes of headroom; minify/trim before adding copy).
- **Engineer apply re-checks the join floor server-side.** `/api/engineer/apply` never
  trusts the client to have honored the confidently-wrong guard: for any link op
  (`AddProperty` with a `range_class`) `EngineerService.apply` re-measures coverage from
  the live HEARTH and refuses below `JOIN_LIKELY_FLOOR` (returns `ok=False, blocked=True`),
  so a hand-crafted op that skipped `/interpret` cannot assert a sub-floor join. Spine-gated
  merge/split DEFER (`ok=False, deferred=True`) — sent to review, never force-applied.
- **Threaded playground build owns its own sqlite.** The worker opens its OWN
  `SqliteLedger` and never shares the server's thread-affine handle. `world._drop_handles`
  only `.close()`s the server ledger from the thread that opened it (tracked in
  `_ledger_owner`); the worker thread flipping the active world just drops the reference.
- **Never weaken a gate.** The confidently-wrong guard (no wrong answer at
  confidence ≥ `tau_high`), the free-text robustness gate (≥70% answered-with-citations,
  0 confidently-wrong), the aviation competency suite, the OQIR type checker
  (`lodestone/typecheck.py` — rejects unit/grain/phantom traversals), and the Meridian
  gold gate are load-bearing. Fix root causes; if new markup invalidates a test, adapt
  it to the new behavior while keeping intent + every security/gate assertion.
- **LODESTONE answer contract:** below the soft-clarify floor → hard abstain; in the
  `[0.45, 0.6)` band with a strong class/prop + measure anchor and a candidate that
  executes non-empty → ask ONE disambiguating question (never an answer, so it can't be
  confidently wrong); only above `MIN_COVERAGE` does it answer through the spine.

## Conventions

- `uv run pytest tests/ -q` must exit 0 before any commit. The pre-existing ruff
  debt is confined to `src/ontoforge/temper/` — do not let it spread to other modules.
- Commit trailer: `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.
