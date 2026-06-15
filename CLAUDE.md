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
uv run pytest tests/ -q                 # full suite — 1109 tests, must stay green
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
any-data builder + Meridian generator). `pipeline/` orchestrates stages end-to-end.

**Server** (`server/`): FastAPI REST API + the web UI. `serve` mounts `server/static/`.
The SPA is vanilla ES modules (no build chain): `core.js` is the shared kernel
(`el`/`svgEl`/`createTextNode` DOM helpers, `confGauge`, `toast`, the locked
`ATLAS_HUES`/`hueFor`/`APP_HUE`), `wm.js` the window manager, `dock.js`, `spotlight.js`,
`constellation.js`, and `js/apps/*` the eight micro-apps. Static-UI tests live in
`tests/server/test_spa.py`.

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
  (`tests/server/test_spa.py` greps for it). Non-vendor payload must stay **< 250 KB**
  (currently 217,742 bytes).
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
