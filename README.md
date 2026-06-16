# OntoForge

**An autonomous semantic data platform** — point it at messy, heterogeneous data sources and it
induces a validated ontology, resolves entities across sources, materializes them in a bitemporal
entity store with per-value provenance, synthesizes its own cleaning transforms, monitors drift,
answers natural-language questions with atom-level citations, and exports the entire estate as a
portable open-format bundle at any moment.

Built to the spec in `ontoforge-whitepaper-v2-complete.md` (the eight named techniques:
STRATA, TEMPER, HEARTH, ANVIL, WARDEN, LODESTONE, VISTA, AMBER), with deviations recorded as
typed amendments in [docs/DEVIATIONS.md](docs/DEVIATIONS.md).

## Quick start

```bash
uv sync --all-extras
uv run pytest tests/ -q          # full suite (1186 tests)

# One line, zero setup — the Meridian enterprise estate (10 tables, ~9,000 rows
# of supply-chain/retail/quality data, regenerated from code, full pipeline):
uv run ontoforge demo meridian /tmp/meridian
uv run ontoforge serve -p /tmp/meridian      # → http://localhost:8765 — OntoForge OS
# Three modes in the top bar: Ask (cited answers) · Build (measure + extract/export) ·
# Studio (the live data playground — add datasets, watch joins form, edit in plain English)

# Point it at YOUR data — any directory of CSV/Parquet files:
uv run ontoforge init myproject --source /path/to/your/data
uv run ontoforge ingest -p myproject && uv run ontoforge profile -p myproject
uv run ontoforge induce -p myproject && uv run ontoforge resolve -p myproject
uv run ontoforge materialize -p myproject
uv run ontoforge serve -p myproject        # → http://localhost:8765 — the full web app

# Or the bundled aviation demo estate:
uv run ontoforge init demo
uv run ontoforge ingest -p demo --limit 300   # CDC pull into the ledger + RAW mirror
uv run ontoforge profile -p demo              # sketches, FDs, INDs, units
uv run ontoforge induce -p demo               # STRATA: FCA lattice -> ontology
uv run ontoforge resolve -p demo              # ER cascade -> clusters (F1 vs gold printed)
uv run ontoforge materialize -p demo          # commit entities + links into HEARTH
uv run ontoforge ask -p demo "What is the average labor hours of work orders?"
uv run ontoforge dashboard -p demo "incident overview by operator and phase"
uv run ontoforge snapshot -p demo demo/amber_bundle   # the freeze-frame export
uv run ontoforge status -p demo
```

Every `ask` answer carries per-cell citations resolving to content-addressed source atoms through
the provenance semiring; unanswerable questions are abstained, not guessed; unit-incoherent
questions ("altitude in dollars") are rejected statically by the OQIR type checker.

**OntoForge OS** (`ontoforge serve`) is the web surface, organized as **three modes** in a top-bar
switcher (no reload between them):

- **Ask** — the default landing. A centered question box with suggested/recent questions; answers
  come back as a cited card whose source-hue "Where this came from" dots resolve to per-cell source
  atoms, and ungroundable questions return a dignified abstention card (never a guess), with a
  not-ready CTA into Studio. Backed by `GET /api/ask`.
- **Build** — plain-language analytics: pick a *measure* and *break it down by* dimension, or type
  free text; get warm-Vega dashboard proposals (`/api/dashboards`) plus two cleanly separated
  outputs — **Extract** (`/api/extract` → a filtered CSV slice with per-cell citations) and
  **Export** (`/api/export` → the whole portable AMBER bundle).
- **Studio** — the live **data playground** over a window-managed desktop: a **Data Catalog**
  (every downloadable dataset, grouped by domain, add up to 25), a **Data Map** that animates the
  build in real time from `/api/workspace/build/{job_id}` events (types appear, joins arc into place
  as `join_found` fires — "found a join: airports ↔ routes on iata_code"), and a plain-English
  **Console** that turns data-engineering imperatives into preview → apply with exact undo. The
  Console clarifies one question when ambiguous, falls to worked examples when unsupported, and
  **refuses a confidently-wrong join** (sub-floor coverage) rather than asserting it. Studio's
  Confirm / Activity / Record / Where-this-came-from apps round out the workbench.

**Spotlight** (⌘K, `/`, or just start typing) is the front door: one search box over classes,
entities, properties, saved questions, and apps, backed by `GET /api/search` — with an "Ask the
estate" fallback so no query dead-ends.

The look is a **warm midcentury-modern system**: oatmeal/cream paper grounds, espresso ink, a
locked atomic-age 8-hue atlas wheel (each app, island, and chart series owns a deterministic hue),
marigold accents, warm-amber shadows (never black), a 270° arc confidence gauge, and a quiet
calm-dark night theme as an opt-in. Vanilla ES modules, no build chain, ships fully offline
(vendored Vega only); the non-vendor payload is **287,370 bytes — under the 290 KB budget**
(test-enforced), with API data reaching the DOM only through `createTextNode`/`el()` (no
`innerHTML`). Design system + the full de-jargon naming map in [docs/UI_DESIGN.md](docs/UI_DESIGN.md);
shell internals in [docs/UI_SHELL_README.md](docs/UI_SHELL_README.md); competitive positioning in
[docs/MARKET_EDGE.md](docs/MARKET_EDGE.md).

## Portability

OntoForge ships as a **wheel** (`uv build` — no fixture data inside; the Meridian estate
regenerates byte-identically from code, so `ontoforge demo meridian` works from a bare install
in a clean venv), as a **Docker image** (`docker build -t ontoforge . && docker run -p 8765:8765
-v ontoforge-data:/data ontoforge` — materializes the Meridian demo on first start, then serves
OntoForge OS), and any materialized world exports as an **AMBER bundle** — an open-format
freeze-frame with full provenance, replayable without OntoForge. Details and verification story
in [docs/PORTABILITY.md](docs/PORTABILITY.md).

## AI-native (keyless today, LLM-ready)

The data-engineering layer is built **AI-native but ships keyless** — every "AI" decision runs on
deterministic adapters today, with the live-model path stubbed and ready, so the app and engine
need **no API key and make no network call at runtime**. The scaffolding (`src/ontoforge/aimodels/`):

- **`router`** — a task-scoped `ModelSpec` registry over the frozen `ModelClient` seam with
  *explicit*, priority-ordered fallback. The default tier for every task is the deterministic
  `HeuristicAdapter`; a live model (Kimi K2 / Qwen / Opus, OpenAI-compatible) is added by
  registering one more `ModelSpec` via a lazy factory — no key is needed at import or run, and the
  routing/fallback logic is unchanged.
- **`prompts`** — versioned, task-scoped templates (join/merge/retype/name_concept/answer) emitting
  a constrained `{decision, confidence, rationale}` JSON schema, with few-shot and ontology-grounding
  slots; `render()` is byte-deterministic.
- **`context`** — extractive, bidirectional schema linking that prunes a large induced ontology into
  a token budget at high recall (so a model sees only the relevant subset).
- **`secure`** — PII redaction, stratified sampling (send a sample, never bulk rows),
  untrusted-text spotlighting, and injection scanning — injection is defended **architecturally**
  (data is kept out of the instruction channel), not by fine-tuning.

The headline mechanism is the **weighted-voting decision gate** (`src/ontoforge/ensemble/`): a join /
merge / retype proposal is decided by a diverse ensemble of deterministic experts (coverage,
value-overlap, name-similarity, type-compatibility) via per-expert Weighted-Majority Aggregation, a
label-free aggregation temperature, and Soft-Self-Consistency scoring against a calibrated threshold.
An **execution-grounded verifier veto runs first and unconditionally** — the engineer's join-coverage
floor can refuse a join regardless of a unanimous "fire" vote (the gate's confidently-wrong guard),
and the hard floor is *also* re-checked before the gate is ever consulted, so a gate can only ever
make a decision **more** conservative, never assert a sub-floor join. A human Confirm/Reject applies a
Littlestone–Warmuth multiplicative penalty (ε = √(ln N / T)) to the experts that disagreed, so the
ensemble self-improves over time. Every gated apply records its vote tally + per-expert weights as
ledger provenance and surfaces them on `/api/engineer/apply` (`result.gate`) — an auditable answer to
"why did this join fire or hold?". Adding a live model is registering it as one more expert speaking
the same `Vote` protocol; the gate math does not change. Full architecture and the copy-paste
live-model layering guide in [docs/AI_NATIVE.md](docs/AI_NATIVE.md).

## Typed relationship inference engine (§1)

The central technical risk in autonomous data engineering is **"looks-similar-isn't-related"**: two
columns can share a name, a type and a cardinality and still be unrelated. OntoForge ships a
distribution-aware, evidence-bearing engine that kills those false positives and emits a **typed**
relationship — not a binary join. All of it is **keyless, deterministic, zero-network** today (the
"AI" adjudication seam routes through the `aimodels` router / `ensemble` gate but runs on
deterministic adapters), and it is **CLOSED-CORE IP** (see [docs/IP_ARCHITECTURE.md](docs/IP_ARCHITECTURE.md)).

- **`relationships/`** — a confidence **proxy** that fuses value OVERLAP (containment / MinHash
  Jaccard) with value **DISTRIBUTION** alignment (Jensen-Shannon for categoricals, quantile divergence
  for numerics), key uniqueness, entropy, cardinality and type compatibility into an
  **EvidenceArtifact** trail (which signals *fired*, which *conflicted*), then classifies the pair into
  the taxonomy the doc requires: **FK-join · lookup/dimension · many-to-many bridge · denormalization ·
  derived field · unrelated-despite-similarity**. `RoadSpy` packages the evidence (never bulk data) for
  an adjudicator.
- **`validation/`** — **SQL synthesize-and-EXECUTE backward validation**: it doesn't trust a score, it
  runs the join in DuckDB (in-process, `:memory:`) and measures match-rate / orphan-rate / fan-out /
  null-key over real cells, then derives a typed verdict. The strongest correctness guarantee in the
  system.
- **`ensemble/` RelationshipGate** — three **distinct reasoning paths** (schema-centric,
  value-centric, business-logic) vote on the relationship TYPE by plurality, with **median-of-path**
  confidence and the executed validation as a strong **booster / veto**; commit only on consensus,
  else route to a human. `should_vote` is a scalpel — confident FKs skip the vote.
- **`tenant/`** — per-tenant **isolated** prior learning (naming conventions, accepted/rejected join
  shapes) that nudges ranking within a bounded budget — **never cross-tenant**.
- **`discovery/`** — semantic retrieval over **cached** validated DE work (versioned + auto-described,
  keyless TF-IDF) for humans *and* a model RAG bootstrap ("what we already know about these joins").

This engine is **wired into the product, not just present**: the **Connection Atlas** (`/api/atlas`)
now carries an additive `rel_type` + evidence summary on every arc — so a same-name, distribution-
disagreeing pair shows as **`unrelated`** right on the map, while a real FK shows as `fk_join`. The
**common-language engineer** (`/api/engineer/apply`) consults the proxy *and* runs the backward
validation before committing a link, records the EvidenceArtifacts + JoinValidation + the reasoning-
path verdict as ledger provenance (`result.typed_relationship`), and adds one more veto on top of the
existing coverage floor: if the **executed** data refuses the inferred type, the link is routed to
review (the floor is never weakened).

## Architecture

| Layer | Module | Package |
|---|---|---|
| Provenance ledger N[X], atoms, model adapters | M0 | `ontoforge.ledger` |
| Decision spine (calibration, conformal, budget) | M2 | `ontoforge.spine` |
| CDC connectors + RAW mirror | M1 | `ontoforge.cdc` |
| Profiler, FD/IND discovery, units | M3 | `ontoforge.profiling` |
| STRATA type-lattice induction (FCA) | M4 | `ontoforge.strata` |
| ER cascade (blocking, Fellegi–Sunter, clustering) | M5 | `ontoforge.er` |
| HEARTH bitemporal entity store | M6 | `ontoforge.hearth` |
| Transform graph + lineage + orchestrator | M7 | `ontoforge.transforms` |
| ANVIL by-ontology transform synthesis | M8 | `ontoforge.anvil` |
| WARDEN expectations + drift sentinels | M9 | `ontoforge.warden` |
| TEMPER ontology-evolution calculus | M10 | `ontoforge.temper` |
| RDF/OWL/SHACL export + round-trip | M11 | `ontoforge.export` |
| LODESTONE NL query planning over OQIR | M12 | `ontoforge.lodestone` |
| VISTA dashboard synthesis (minimal) | M13 | `ontoforge.vista` |
| AMBER freeze-frame snapshot | M14 | `ontoforge.amber` |
| Shared typed contracts (frozen interfaces) | — | `ontoforge.contracts` |

Test estates: a schema-faithful aviation corpus (FAA registry / ASRS narratives / NTSB events /
maintenance ERP layouts) under `fixtures/aviation/` with gold ontology, gold ER pairs, and an
18-question competency suite including abstention and trick-unit traps; and **Meridian**, a
10-table enterprise estate (~9,000 rows of POs, contracts, quality notifications, shipments,
leases, tickets) generated deterministically by `ontoforge.estates.meridian_gen` (seed 7) with a
full wart program — unit mixes, date locales, name variants, stripped vendor ids, mojibake,
re-keyed double entries — and a 12-question gold suite under `fixtures/meridian/gold/` (9
answerable, 2 abstention traps, 1 trick-unit) that the generic engine answers fully cited with
zero estate-specific code.

The **Wild corpus** under `fixtures/wild/` is **450 real internet datasets** (4.8 MB, gates: ≥380
datasets, 20–150 rows, 2–60 columns each) pulled deterministically from seven open sources —
datasets-org, Our World in Data, FiveThirtyEight, Plotly, Vega, seaborn, OpenFlights — with full
SHA-256 pinning and per-dataset license attribution (see [docs/WILD_CORPUS.md](docs/WILD_CORPUS.md)).
Together with Meridian and the aviation corpus this is what the Studio Data Catalog surfaces (`GET
/api/catalog` enumerates **465** datasets with deterministic domain + description), so a playground
build can union real cross-source tables and watch genuine joins form.

## Measured results (fixture scale, deterministic, zero network)

| Gate | Target | Measured |
|---|---|---|
| Spine calibration ECE | ≤ 0.05 | met (5 seeds) |
| Conformal coverage | ±2% of nominal | worst dev 1.45% |
| STRATA class precision/recall vs gold | ≥ 0.70 / ≥ 0.60 | **0.938 / 0.647** |
| ER pairwise F1 (held-out gold) | ≥ 0.85 | **0.997** |
| ANVIL corruption-fix rate (10 classes) | ≥ 0.70 | **1.00** |
| WARDEN drift precision/recall | ≥ 0.8 / ≥ 0.9 | **1.00 / 1.00** |
| TEMPER snapshot-queryability (300 random op sequences) | 100% | **100%** |
| Competency questions (answerable) | ≥ 70% correct | **15/15** |
| Citation coverage on answers | 100% | **100%** |
| Confidently-wrong answers | 0 | **0** |
| AMBER executable completeness (bundle-only replay) | 100% equality | **100%** |
| Meridian gold questions (generic engine, induced ontology) | ≥ 7/9 cited-correct | **9/9** + both unanswerables abstained |
| `/api/search` p95 on the demo world | < 150 ms | **104 ms** |

## Development

Python 3.12, `uv`-managed. The spec is the contract; module ownership boundaries and the
amendment ledger follow whitepaper §18. T2/T3 model tiers run through a `ModelClient`
abstraction with deterministic heuristic/cassette adapters (live Anthropic adapter activates
when `ANTHROPIC_API_KEY` is set). License: Apache-2.0.

## Company & go-to-market

- **Marketing site** under [`site/`](site/) — an offline, no-build static landing page
  (`index.html`), a canned deterministic product demo (`demo.html` + `demo.js`), and an
  interactive compute-ledger pricing calculator (`pricing.html`). See [`site/README.md`](site/README.md)
  for local run + Cloudflare Pages deploy. Founder-ready collateral lives in
  [`docs/PITCH_ONEPAGER.md`](docs/PITCH_ONEPAGER.md), [`docs/DEMO_SCRIPT.md`](docs/DEMO_SCRIPT.md),
  and [`docs/COMPETITIVE_BATTLECARD.md`](docs/COMPETITIVE_BATTLECARD.md).
- **Reusable agent roster** under [`.claude/agents/`](.claude/agents/) — 12 Claude Code subagents
  (7 dev: orchestrator-planner, implementer, adversarial-tester, reviewer, integrator,
  research-agent, ip-security-warden; 5 business: competitive-monitor, gtm-strategist, pitch-writer,
  pricing-analyst, support-success) that operationalize the solo-founder-plus-AI team with built-in
  anti-reward-hacking discipline (implementer never sees the holdout; the adversarial-tester is a
  separate identity; editing a test to pass is a hard failure). The per-task build loop is in
  [`docs/AGENTIC_BUILD_RUNBOOK.md`](docs/AGENTIC_BUILD_RUNBOOK.md).
