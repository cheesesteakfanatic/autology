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
uv run pytest tests/ -q          # full suite (~880 tests)

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

Test estate: a schema-faithful aviation corpus (FAA registry / ASRS narratives / NTSB events /
maintenance ERP layouts) under `fixtures/aviation/` with gold ontology, gold ER pairs, and an
18-question competency suite including abstention and trick-unit traps.

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

## Development

Python 3.12, `uv`-managed. The spec is the contract; module ownership boundaries and the
amendment ledger follow whitepaper §18. T2/T3 model tiers run through a `ModelClient`
abstraction with deterministic heuristic/cassette adapters (live Anthropic adapter activates
when `ANTHROPIC_API_KEY` is set). License: Apache-2.0.
