# IP Architecture — closed core vs. open shell (v2.1 §18–20)

This document records OntoForge's intellectual-property boundary: which modules
are **closed-core IP** (the proprietary inventions that constitute the moat) and
which are the **open shell** (connectors, UI, scaffolding — the parts that are
commodity, customer-facing, or candidates for open-sourcing). It exists so that
a future private-repo split (or selective open-sourcing) is a *mechanical* move,
not an archaeology project — and so the boundary is **enforced in CI** rather
than living only in people's heads.

> **Human checkpoint (§20).** This file documents the boundary and ships a
> pragmatic import guard. The *actual* repository split / open-sourcing decision
> is a human checkpoint and is **out of scope for the engine** — nothing here
> physically separates the code; it labels it and guards the import direction.

## The closed-core ring

These packages are the proprietary engine. Each closed-core module carries a
`CLOSED-CORE IP per OntoForge_Build_Instructions.md §18` banner in its docstring.

| Package | Role | Why it's the moat |
|---|---|---|
| `relationships` | distribution-aware confidence **proxy** + typed relationship taxonomy + RoadSpy scout | the false-positive killer ("looks-similar-isn't-related") — §1.1/§1.2 |
| `validation` | SQL **synthesize-and-execute** backward join validation | the strongest correctness guarantee — validates joins against real data (§1.4) |
| `ensemble` | reasoning-path **typed voting** (schema/value/business) + the fire/hold DE gate | distinct-reasoning consensus, not temperature noise — §1.3 |
| `tenant` | per-tenant **isolated** prior learning | the compounding per-customer advantage, isolation-enforced — §1.5 |
| `discovery` | semantic retrieval over **cached DE work** (versioned + auto-described) | the flywheel: retrieve validated work, never re-derive — §5 |
| `strata` | FCA type-lattice ontology **induction** | the ontology-from-data invention (M4) |
| `temper` | ontology-**evolution** calculus (invertible operators) | safe schema change with exact undo (M10) |
| `hearth` | **bitemporal** entity store, per-cell provenance | time-travel + atom-level lineage (M6) |
| `anvil` | by-ontology **transform synthesis** | typed transform generation (M8) |
| `warden` | expectations + **drift** sentinels | continuous-correctness monitors (M9) |
| `lodestone` | NL → OQIR **query planning** (grounding, typecheck) | the cited-answer / honest-abstention spine (M12) |
| `vista` | **dashboard** synthesis | ontology-driven viz generation (M13) |
| `amber` | freeze-frame **snapshot** bundle | portable, verifiable estate export (M14) |
| `spine` | **calibration**, conformal sets, tier/budget decisions | the cost/confidence decision spine (M2) |
| `aimodels` | the model **router** / prompt library / context assembler | model-agnostic routing + the living prompt loop (§2/§3) |

The math substrate (`contracts`, `ledger` semiring) is shared infrastructure
both rings depend on — it is the typed interface surface, not an invention to
protect.

## The open shell

These are commodity / customer-facing / scaffolding and are the candidates for
open-sourcing or vendor-pluggability. They may consume the closed core **only
through its published entrypoints** (the package `__init__` and the shared
`contracts`), never by reaching into a closed-core *internal* submodule.

| Package / area | Role |
|---|---|
| `cdc` | source pull + RAW Parquet mirror (M1) — a connector |
| `estates` | swappable estate engines / dataset fetchers — connectors + fixtures |
| `server` | FastAPI REST API + the web UI (the product surface) |
| `pipeline` | stage orchestration / scaffolding (`atlas`, `discover`, `mapping`, `playground`) |
| `engineer` | the common-language DE layer that *composes* closed-core ops |
| anonymizer | the future client-side anonymization toolkit (§7) — not yet built |

`pipeline` and `engineer` are scaffolding that **orchestrate** the closed core:
they import its published entrypoints (`relationships.discover_relationships`,
`validation.validate_join`, `ensemble.RelationshipGate`) and the shared
`contracts`, but they are not themselves the inventions.

## What the import guard enforces (`tests/test_ip_boundary.py`)

The guard is deliberately **pragmatic**, not a full enforcement engine. It is an
AST-based scan that checks the *direction* of imports across the boundary:

1. **Open shell must not reach into NEW closed-core engine internals.** The
   open-shell packages (`server`, `cdc`) may import the Wave-1 engine packages
   (`relationships`, `validation`, `ensemble`, `tenant`, `discovery`) only via
   their **package entrypoint** (`from ontoforge.relationships import …`), never
   a submodule (`from ontoforge.relationships.signals import …`). Importing a
   submodule would couple the product surface to a private implementation detail
   and defeat a clean repo split.

2. **`contracts` is always allowed.** The shared typed-interface surface is the
   sanctioned cross-boundary channel — both rings depend on it.

3. **Documented carve-out.** A small allow-list records pre-existing internal
   imports of *established* closed-core modules that predate this boundary (e.g.
   `server` importing `vista._pipeline` / `lodestone.model`). These are honest
   debt: they are recorded here and in the test, not silently ignored, so a
   future cleanup pass (or the repo split) has the exact list to resolve.

What the guard intentionally does **not** do: it does not police closed-core ↔
closed-core imports, does not build a full module dependency graph, and does not
fail on dynamic/`importlib` imports. It is a fast, readable tripwire on the one
direction that matters for the IP split — open shell reaching into new private
engine internals.
