# M7 — Transform Graph & Orchestrator

Implements whitepaper §5.1 and §11.2 M7. Owner files: `src/ontoforge/transforms/`,
tests in `tests/m7/` (86 tests). Depends on frozen M0 (`SqliteLedger`),
M6 (`Hearth`), `contracts.transforms`, and the aviation estate fixtures.

## Surface (what Wave 4 / M8–M10 consume)

```python
from ontoforge.transforms import (
    validate_sql, DslError,                     # dsl.py
    lineage_for_sql, lineage_for_transform,     # lineage.py  -> list[contracts.ColumnLineage]
    TransformRegistry, RegisteredTransform,     # registry.py
    Orchestrator, RunResult, NodeResult,        # orchestrator.py
    CycleError, DagError,
    affected_transforms,                        # delta.py
    fingerprint_dataframe, memo_key,            # fingerprints.py
    dataframes_from_hearth, commit_dataframe_to_hearth,  # hearth_io.py
)
```

* `TransformRegistry(ledger).register(tdef, *, prov_ref="") -> fingerprint`
  — validates the DSL, persists the serialized def as a ledger artifact
  (kind `"transform"`, artifact_id `transform:<fp>`), returns the contract's
  content fingerprint. Idempotent on identical content. "Changing" a
  transform = registering a new body under the same `name` (new fingerprint;
  `active()` returns the latest def per name).
* `Orchestrator(registry, ledger)`:
  * `dag()` / `topo_order()` — dependency DAG from input/output table names;
    raises `CycleError` (with the cycle's node names) and `DagError` on
    duplicate outputs or missing inputs.
  * `plan(inputs, changed_tables=None)` — topologically ordered
    `(transform, action)` with action ∈ `{"execute", "memo", "outside-delta"}`.
  * `run(inputs, *, changed_tables=None, retries=0, on_execute=None)
    -> RunResult` — executes via DuckDB over pandas inputs; every visited
    node appends a `RunRecord` to the ledger (kind `"run"`, prov_ref = the
    transform's own provenance term).
* `commit_dataframe_to_hearth(...)` — lands a materialization in a HEARTH
  layer: one content-addressed cell atom per (row, column), interned as a
  Leaf term, so constraint H holds for pipeline outputs end to end.

## DSL (dsl.py)

A transform body is a single restricted SQL SELECT (DuckDB dialect), parsed
with sqlglot and checked against an explicit allowlist. Allowed: projection
(computed columns **must** be aliased), WHERE, INNER/LEFT JOIN, GROUP BY,
ORDER BY, CASE, CAST, arithmetic `+ - * /`, comparisons/boolean logic/IN,
subqueries to depth 2, and the vetted scalar functions
`upper lower trim replace substr split_part concat coalesce nullif
regexp_extract regexp_replace round abs strptime strftime date_part`,
plus the aggregates that make GROUP BY meaningful
(`count sum min max avg` — GROUP BY is in the §5.1 grammar, so its standard
aggregates come with it; documented design decision).

Everything else — DDL, DML, set operations, window functions (v0),
RIGHT/FULL/CROSS joins, HAVING/LIMIT/DISTINCT, any unvetted function,
subquery depth > 2, multiple statements — raises `DslError` with a stable
`.code` (`ddl`, `dml`, `window_function`, `subquery_depth`, `bad_join`,
`disallowed_function`, `set_operation`, `disallowed_construct`,
`unaliased_projection`, `multiple_statements`). The node allowlist is
exact-type (subclasses don't slip through).

## Column-level lineage (lineage.py)

From the validated sqlglot AST (the SQLMesh-proven approach, §5.1): each
output column → sorted set of input `(table, column)` pairs + the operation
chain, as `contracts.ColumnLineage`. Handles column/table aliases, qualified
refs, CASE branches (all branches + condition columns), multi-column
functions, JOIN provenance, `*` and `t.*` expansion (declaration order), and
FROM-subquery composition (outer ops prepended to inner ops). Operation
chains are the pre-order (outer-to-inner, left-to-right) label sequence:
canonical function names (`SUBSTR`, `STRPTIME`, …), `CAST`, `CASE`, and
`+ - * /`. Requires input schemas (`table -> [columns]`) to expand `*` and to
resolve/reject ambiguous unqualified columns. Lineage covers **projections**
(output columns); WHERE/GROUP BY columns are change-impact, not output
lineage, in v0.

## Virtual environments & memoization (§5.1)

An output materialization is keyed by
`memo_key(transform_fingerprint, {input_table: data_fingerprint})`.
On `run()`, a node whose key is unchanged is **not executed**: it emits a
`skipped(memo)` RunRecord and reuses the cached materialization — so changing
one transform in a DAG re-runs exactly it and its descendants while unchanged
upstream is reused by fingerprint (tested via execution counters in the
persisted RunRecords). Data fingerprints are row-order- and
column-order-insensitive (hash of sorted row hashes; O(rows×cols) formatting
+ O(n log n) sort — fine at AMD-0001 fixture scale, vectorize/sample later if
needed).

## Orchestration (orchestrator.py)

Topological execution through an in-memory DuckDB connection over registered
pandas inputs. Layer-qualified table names (`raw.faa_master`) are rewritten
onto sanitized registered names with the bare table name preserved as alias
(qualify columns by alias or bare name, not the full dotted name). Failure
isolation: a failed node's transitive consumers get
`skipped(upstream_failed)` records; independent branches still run. Retries:
`retries` extra attempts per node; outputs are replaced **only** after a fully
successful execution (idempotent commit — a retried run is bit-identical to
an undisturbed one). RunRecord instants come from an internal monotone
counter (M6's store-stamped-time pattern), so run history is deterministic.

## Delta hook (delta.py)

`affected_transforms(defs, changed_tables)` = transitive closure of consumers
over the DAG. `run(..., changed_tables={...})` visits exactly that cone
(records emitted only for it — work ∝ affected set, constraint Δ at **DAG
granularity**); everything outside the cone reuses its previous
materialization. Row-level incremental computation (Z-set deltas / DBSP, the
§9 ambition) is deferred per **AMD-0001**; this hook freezes the granularity
contract the row-level engine will refine.

## Provenance discipline

* Human-authored transform: a ONE-leaf term over a synthetic authorship atom
  (`make_cell_atom("human-author", "transforms", name, "v<version>", sql)`).
* Synthesized transform (`synthesized_by` set): registration **requires** the
  synthesizer's interned term via `prov_ref` (ANVIL/M8 passes its own
  derivation).
* RunRecords are ledger artifacts carrying the transform's prov_ref;
  HEARTH commits mint per-cell atoms (constraint H end to end).

## Design decisions & trade-offs

1. **Aggregates admitted alongside GROUP BY** (not separately listed in the
   build sheet's scalar list) — GROUP BY without aggregates is vacuous.
2. **Computed projections must be aliased** — keeps output column names (and
   hence lineage keys) explicit and dialect-independent.
3. **Memoized materializations live in the Orchestrator** (in-memory dict per
   process); the durable record is the RunRecord + transform artifacts in the
   ledger. Cross-process memo persistence belongs with a real table store
   (Iceberg swap in the spec; Parquet cache here would be the upgrade).
4. **`skipped(upstream_failed)`** extends the RunRecord status vocabulary
   (the contract documents the three §5.1 statuses as examples, not an enum).
5. **Deterministic clocks** for RunRecords; wall-clock is deliberately absent.
