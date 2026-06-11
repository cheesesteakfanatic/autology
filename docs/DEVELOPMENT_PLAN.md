# OntoForge — Development Plan (v0.1, 2026-06-11)

**Spec basis:** `ontoforge-whitepaper-v2-complete.md` (the contract) + `ontoforge-unified-mvp-plan-v2.md`
(the solo-buildable operating rules). Per whitepaper §18.1, every deviation from the spec is recorded in
`docs/DEVIATIONS.md` as a typed amendment with justification referencing affected acceptance tests.

## 1. Goal of this build pass

A **real, runnable vertical slice** of the platform — whitepaper Phases 0–4 compressed — proving the
closed loop the whitepaper claims no competitor has:

> induce → materialize → transform → validate → query → export

on the **aviation hero estate**, with real algorithms (not mocks), real tests, and measured numbers.

## 2. Module map (whitepaper §11.2 → this repo)

| Module | Whitepaper | Package | Wave | Status target |
|---|---|---|---|---|
| M0 | Atom & Ledger Core (§1.2, §9) | `ontoforge.ledger` | 1 | full |
| M1 | CDC & Ingestion | `ontoforge.cdc` | 1 | file/CSV/doc connectors + delta |
| M2 | Decision Spine (§8) | `ontoforge.spine` | 1 | full (economy + crucible profiles) |
| M3 | Profiler & Dependency Discovery (§3.1–3.2) | `ontoforge.profiling` | 1 | sketches, FD, IND, semantic types, units |
| M4 | STRATA type-lattice induction (§3.4) | `ontoforge.strata` | 2 | FCA iceberg + admission + stable URIs |
| M5 | ER Cascade & Clustering | `ontoforge.er` | 2 | blocking + Fellegi–Sunter EM + clustering |
| M6 | HEARTH bitemporal entity store (§4) | `ontoforge.hearth` | 2 | value cells, links, stances, survivorship |
| M7 | Transform Graph & Orchestrator (§5.1) | `ontoforge.transforms` | 3 | DSL-as-SQL-subset, lineage, scheduler |
| M8 | ANVIL transform synthesis (§5.2) | `ontoforge.anvil` | 3 | T0 detectors + pruned beam + verification |
| M9 | WARDEN expectations & drift (§5.3) | `ontoforge.warden` | 3 | Σ-compiler + drift sentinels |
| M10 | TEMPER evolution calculus (§3.6) | `ontoforge.temper` | 3 | operator set + migrations + morphism ledger |
| M11 | RDF export & round-trip | `ontoforge.export` | 4 | rdflib + pyoxigraph equivalence |
| M12 | LODESTONE query planning (§6.2) | `ontoforge.oqir`, `ontoforge.lodestone` | 4 | typed OQIR + checker + grounding + lowering + citations |
| M13 | VISTA dashboard synthesis (§6.3) | `ontoforge.vista` | 4 | minimal: metric layer → ranked Vega-Lite |
| M14 | AMBER freeze-frame (§7) | `ontoforge.amber` | 4 | bundle + manifest + verify + import |
| M15 | Governance | — | deferred | label valuation deferred |
| M16 | Distillation loop | — | deferred | needs live tenant traffic |

Shared, written first (architect-owned): `ontoforge.contracts` — the contract package of §18.1.

## 3. Build waves

Each wave is a parallel multi-agent workflow with **module ownership boundaries** (an agent edits only
its module dir + its test dir, per whitepaper §18.1 repository topology). Integration agent runs the full
suite after each wave.

- **Wave 1 (foundation):** M0 ∥ M2 ∥ M3 ∥ M1 ∥ fixtures+gold estate
- **Wave 2 (induction core):** M4 ∥ M5 ∥ M6
- **Wave 3 (autonomy of labor):** M7 ∥ M8 ∥ M9 ∥ M10
- **Wave 4 (payoff):** M12 ∥ M11+M14 ∥ M13+CLI
- **Wave 5 (integration):** end-to-end pipeline, competency suite, adversarial review

## 4. Hero estate (Tier-2 per §17.3 Estate A, scaled)

`fixtures/aviation/`: FAA-registry-shaped aircraft registry, ASRS-shaped incident narratives,
NTSB-shaped events, plus a synthetic maintenance/ERP source (per §12.4 cross-system pressure).
Real public downloads are used where access succeeds; otherwise fixtures are generated to the
*documented real schemas* with deliberately injected real-world warts (manufacturer-name variants,
N-number reuse, unit mixing, null tokens). Pinned and committed. Gold artifacts: mini-ontology
(~20 classes + SHACL), entity-match gold pairs, ≥15 competency questions with gold answers + citations.

## 5. Acceptance gates for this pass (scaled from §17.7)

| Gate | Target |
|---|---|
| Semiring/property suites (M0) | green under hypothesis |
| Spine calibration ECE (M2) | ≤ 0.05 on synthetic benchmark; conformal coverage ±2% |
| FD/IND recovery (M3) | exact on seeded schemas; key recovery on fixtures |
| STRATA vs gold mini-ontology (M4) | class P ≥ 0.85 / R ≥ 0.75; stable URIs under permuted input |
| ER F1 (M5) | ≥ 0.85 on fixture gold; URI churn ≤1/entity/cycle |
| Bitemporal suite (M6) | all stance scenarios green; export-import idempotent |
| Lineage correctness (M7) | column-level lineage on curated SQL corpus |
| ANVIL synthesis (M8) | ≥ 70% of seeded corruptions auto-fixed, holdout-verified |
| Competency questions (M12) | ≥ 70% correct, 100% citation coverage, 0 confidently-wrong |
| AMBER completeness (M14) | answer-equality replay on exported bundle |

## 6. Verification discipline (§18.3/§18.4)

- Property-based tests (hypothesis) for: semiring axioms, Galois connection laws, bitemporal interval
  invariants, incremental ≡ from-scratch.
- T2/T3 model calls go through `ModelClient` with deterministic adapters; **zero network/API-key
  dependency in tests** (cassette/heuristic mode).
- Per-operation cost counters wired to the ledger from day one.
- Integration agent (not implementers) runs cross-module suites.
