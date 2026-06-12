# M13 — VISTA (minimal, AMD-0007)

Vague-spec dashboard synthesis (whitepaper §6.3): "give me a dashboard on
supplier risk" is an *intent region*, not a query. VISTA treats it as a ranking
problem over the semantic layer M^(t) derived from the induced ontology O^(t).

## What ships (minimal scope per AMD-0007)

| File | Role |
| --- | --- |
| `metrics.py` | **Metric-layer derivation.** `derive_metric_layer(ontology)` — M^(t) derived from O, never hand-authored (§1.2): one COUNT metric per class, AVG+SUM per numeric *dimensioned/united* property, candidate group-by dims = categorical props + temporal props + link-target names. Identifier-like columns (`*_id`, `*_code`, `*_number`, …) are excluded from dims. |
| `compose.py` | **Composition search.** `propose(utterance, ontology) -> top-3 Dashboard`. Grounds utterance tokens onto metrics/dims (exact + affix + difflib fuzzy), enumerates 1-primary-KPI + 2–4-breakdown dashboards under constraints (no redundant grain, pairwise-distinct dimensions), scores `2·grounding(primary) + Σ grounding(breakdowns) + 0.2·|dims| − 0.05·|charts|`. Each `Chart` carries an OQIR `Aggregate`/`TopK` term built from `contracts.oqir` plus a Vega-Lite spec. |
| `vega.py` | **Vega-Lite v5 emission.** Chart-type rules: no dimension → single-number KPI (text mark); temporal dim → line; categorical/link dim → bar (TopK-10). `render_with_data(dashboard, executor)` fills `data.values` through *any* `callable(oqir_term) -> rows` — the LODESTONE seam without the import. |
| `_pipeline.py` | Shared CLI helpers: ontology JSON round-trip, light gold matcher (CLI reporting only), wave-2-style HEARTH materialization with constraint-H provenance. |

## Deferred (full §6.3)

Historical usage priors (no query ledger exists in v0), WARDEN data-health
priors, spine-gated proposal/acceptance feedback, TEMPER migration of saved
dashboards, nvBench parity harness.

## Determinism

Everything is a pure function of `(utterance, ontology)`: metric order sorts on
`(class, measure, agg)`, candidate ranking ties break on names, no randomness,
no network, no model calls.

## Ontology persistence dialect (CLI)

`ontoforge induce` saves the induced ontology as `ontology.json` in a plain
field-complete JSON dump (`ontoforge.cli/ontology-v1`, see `_pipeline.py`) —
not the gold-loader dialect, because induced classes carry intent-hash URIs and
sub-1.0 confidences the gold dialect cannot represent.
