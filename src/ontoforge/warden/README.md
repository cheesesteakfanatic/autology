# `ontoforge.warden` — M9 WARDEN: Expectation & Drift Sentinel Synthesis

Whitepaper §5.3, §11.2 M9. Depends only on frozen Wave-1/2 surfaces:
`contracts`, `profiling`, `spine`, `ledger`, `estates`. M7/M8/M10 are built in
parallel — WARDEN's routing outputs are plain typed records those modules
consume later (no cross-module imports).

## What this module owns

| Path | Role |
|---|---|
| `expectations.py` | Σ-compilation: `ShapeConstraint` → executable streaming expectations; coverage metric |
| `drift.py` | sketch-drift sentinels: PSI, MinHash-Jaccard shift, EWMA control charts, schema diff |
| `routing.py` | drift → `TemperProposal` / `AnvilReverification` / `Quarantine`+`Alert` via spine decisions |
| `contracts_emit.py` | per-table implied data contract → markdown ledger artifact (kind `data-contract`) |
| `tests/m9/` | Σ-coverage gate, PSI/EWMA math, injected-corruption suite (P/R gates), routing, emission, determinism |

## 1. Σ-compilation (`expectations`)

`compile_class(class_def) -> list[Expectation]`; `compile_ontology(ontology) ->
CompilationReport` (with the **coverage** metric, gated ≥ 0.95 on the gold
aviation ontology — measured **1.00**: 26/26 constraints, 65 facet
expectations). One `ShapeConstraint` fans out into facet expectations:

| facet | semantics |
|---|---|
| `datatype` | lexical conformance to the declared (or PropertyDef-inherited) `Datatype` |
| `min_count` | null policy: `min_count ≥ 1` ⇒ cell must be present (evaluated on ALL rows) |
| `max_count` | per-cell value multiplicity (list-like cells counted) |
| `pattern` | regex on the lexical form, SHACL `sh:pattern` partial-match semantics |
| `in_values` | enum membership on the stripped lexical form |
| `range` | numeric `[min_value, max_value]`; magnitudes parse through thousands separators, currency prefixes, and short unit suffixes (unparseable values are the datatype facet's problem, not double-counted) |
| `unit` | foreign unit suffix violates (`"3500 m"` in an `ft` column — the §17.2.1 wart); plain numbers are assumed canonical |

`Expectation.evaluate(batch[, column]) -> ExpectationResult` over any
DataFrame-like batch: pass rate, violating row indices **capped at 100**,
severity (`ok`/`warn` ≤ 5% / `error`), null-skipping per SHACL semantics.
`evaluate_class(class_def, batch, column_map)` maps ontology property names to
physical columns. Coverage is honest: a facet that fails to compile (e.g. a
bad regex) makes its constraint count as *not* covered.

## 2. Drift sentinels (`drift`)

`DriftSentinel.observe(table_profile) -> list[DriftSignal]` over a stream of
`contracts.TableProfile`s per cycle. First profile = baseline; EWMA charts warm
up over `ewma_warmup` (default 3) further cycles.

- **PSI** (`population_stability_index`): proper population-stability index
  over the baseline's 10 decile buckets (expected share 0.1 each; tied deciles
  merge), actual shares read off the current 11-point quantile sketch via
  piecewise-linear CDF inversion, out-of-range mass folded into the edge
  buckets, ε-clamped log terms. Threshold 0.2 (industry banding).
- **MinHash-Jaccard shift**: distance `1 − Ĵ(baseline, current)` on the k=64
  signatures; threshold 0.5.
- **EWMA control charts** (`EwmaChart`): null-rate and cardinality-ratio
  (distinct/non-null) per column; classic EWMA with λ=0.3, L=3σ,
  time-dependent limits `μ₀ ± L·σ₀·√(λ/(2−λ)·(1−(1−λ)^{2t}))`, σ floored so a
  perfectly stable baseline still yields finite limits. Catches a seeded
  +0.01/cycle null-rate creep in ≤ 3 cycles (tested: 2).
- **Schema diff** vs previous profile: column added / removed / **renamed**
  (a removed+added pair whose value-set Jaccard ≥ 0.8 collapses to one rename)
  / retyped / format-signature-changed.

Signal kinds: `schema` (diff), `distribution` (PSI, Jaccard), `quality`
(EWMA charts). Every signal carries `(statistic, threshold)` normalized
bigger-is-worse plus `severity ∈ [0,1]` (0.5 at the threshold, 0.99 at 2×).

## 3. Routing (`routing`)

Per §5.3: **schema → `TemperProposal`** (suspected operator: `AddProperty`,
`RemoveProperty`, `RenameProperty`, `RetypeProperty`, `ReformatProperty` +
evidence), **distribution → `AnvilReverification`** (transform fingerprints
whose inputs drifted; the `(table, column) -> ids` mapping is a parameter since
M7/M8 are parallel builds), **quality → `Quarantine` + `Alert`**.

**A drift alarm is a spine decision.** `warden_spine()` registers a
deterministic T0 rule under `DecisionKind.SM` that scores the binary
`("no-alarm", "alarm")` request from the sentinel severity
(feature `warden.severity`). The default `WARDEN_PROFILE` sets
`tau_high = 0.6` / `tau_low = 0.4`: alert precision is **tunable via the spine
threshold** (tested: `tau_high = 0.995` suppresses everything into
`RoutingResult.suppressed`), and every adjudication lands in the append-only
decision ledger when a ledger is wired in.

## 4. Contract emission (`contracts_emit`)

`emit_contract(profile, key_columns=…, shapes=…, column_map=…, ledger=…) -> str`
renders the implied per-table data contract: expected schema (type/unit/null
policy/distinct/format signature per column), key columns, Σ value constraints,
and a freshness placeholder (cadence inference is fed by M1 CDC history later —
documented contract gap). Written to the ledger as kind **`data-contract`**
with a non-zero provenance leaf (`profile://{source}/{table}`), and returned.
`parse_contract` round-trips the key facts (test surface). No wall-clock
content: emission is byte-deterministic.

## Measured results (tests/m9, hard gates)

- **Σ-coverage**: 1.00 (gate ≥ 0.95) on the gold ontology's 26 constraints.
- **Injected-corruption suite** (40 corruption trials: 8 × {null spike, unit
  swap, value-set shift, cardinality explosion, schema change} over real
  estate tables; 20 negatives: 12 clean re-profiles + 8 benign appends with
  format-preserving fresh keys): **precision 1.000, recall 1.000**
  (gates P ≥ 0.8, R ≥ 0.9); per-type floor ≥ 7/8 also gated.
- **Routing**: rename → `RenameProperty` (and NOT drop+add), drop →
  `RemoveProperty`, retype → `RetypeProperty`, add → `AddProperty`; unit swap →
  `AnvilReverification` with the right fingerprints via PSI; value-set shift →
  Jaccard; null spike → `Quarantine`+`Alert`; cardinality explosion →
  `Quarantine`.
- **PSI math**: hand-computed half-shift reference matched to 1e-9 rel.;
  tied-decile merging; monotonicity.
- **Determinism**: end-to-end double-run equality (signals, decisions, records,
  contract markdown).

## Design decisions

1. **Severity → spine, not thresholds → alarms.** Sentinels only *describe*
   excursions; the alarm itself is a calibrated selective-classification
   decision. Marginal severities (0.4–0.6 band) defer rather than alarm —
   fail-quiet on ambiguity, which is the §5.3 alert-fatigue answer.
2. **PSI over sketches, not raw data.** Both sides of the PSI are 11-point
   KLL decile sketches from `ColumnProfile` — WARDEN never re-reads rows,
   keeping sentinels O(columns) per cycle (delta-proportionality, §1.3 Δ).
3. **Rename collapse.** A removed/added column pair with value-set Jaccard
   ≥ 0.8 emits ONE `column_renamed` signal; routing maps it to
   `RenameProperty` instead of a misleading Remove+Add pair.
4. **`max_count` is per-cell multiplicity.** In flat batches a scalar cell has
   multiplicity ≤ 1 by construction; list-valued cells (entity-layer batches)
   are counted. Cross-row key-cardinality violations are the FD/key monitors'
   job (M3 profiles), not a shape facet.
5. **Unit facet is conservative.** Plain numbers are assumed canonical (the
   contract documents the canonical unit); only a *conflicting* suffix
   violates. Mixed-unit *columns* are the drift/PSI sentinels' catch.
6. **No new dependencies**; pure-Python PSI/EWMA; fixed seeds inherited from
   M3 profiling; zero network.

## Contract gaps / deviations

- Freshness cadence in emitted contracts is a placeholder until M1 CDC arrival
  history is wired through (documented in the artifact itself).
- Link-integrity expectations (§5.3 lists them under Σ-compilation) need the
  HEARTH link graph; they belong to the M6/M9 integration pass and are not
  compiled here — they are not counted in the coverage denominator, which per
  §11.2 covers `contracts.ShapeConstraint` facets.

## Running

```bash
uv run pytest tests/m9 -q
```
