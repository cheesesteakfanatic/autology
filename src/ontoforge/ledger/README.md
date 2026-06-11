# M0 — Atom & Ledger Core

SQLite-backed reference implementation of `ontoforge.contracts.ledger.Ledger`
(class `SqliteLedger`), plus the three `ModelClient` adapters and a write-through
`CostMeter`. Spec: whitepaper §1.2 (atoms), §4.2 (interned provenance dictionaries),
§9 (one term, many valuations; exact invalidation), §11.2 M0 (interface, invariants,
tests).

## Files

- `sqlite_ledger.py` — `SqliteLedger`, `LedgerCostMeter`, the named valuation semirings.
- `models.py` — `HeuristicAdapter`, `CassetteAdapter`, `AnthropicAdapter`.

## Design decisions

### Storage: stdlib `sqlite3`, file or `:memory:`

One connection, schema created idempotently at construction. All seven tables
(`atom`, `prov_shape`, `prov_term`, `prov_leaf`, `artifact`, `decision`, `cost`)
are **append-only** (§11.2 M0 invariant): the implementation issues only
`INSERT`/`INSERT OR IGNORE`, and `BEFORE UPDATE`/`BEFORE DELETE` triggers
`RAISE(ABORT)` as defense-in-depth — even ad-hoc SQL through the exposed
connection cannot mutate history. Corrections supersede (new rows), never update.

### Atom registry (§1.2)

`atom_id` is the contracts' content address (xxh3 over `(uri, value_repr)`), so
dedup-on-content is simply `INSERT OR IGNORE` on the primary key: re-registering
identical content is a no-op; a changed value at the same coordinates is a new
atom_id and a new row (the old atom is superseded, not mutated). Each row stores
the canonical string (`contracts.atoms.value_repr`) **plus** a JSON-typed mirror
when the value is a cheap scalar (`None | bool | int | float | str`).
`get_atom` prefers the JSON mirror (lossless typed round-trip; floats survive via
`repr`-faithful JSON) and falls back to the canonical string for exotic values —
identity is always preserved because the stored `atom_id` is passed through.

### Two-level provenance interning (§4.2)

`intern(term)`:

1. Normalize by rebuilding through the contracts' smart constructors
   (`map_leaves(term, leaf)`): flattening, identity elimination, ZERO annihilation.
2. Abstract leaves to positional slots via `contracts.provenance.map_leaves`
   (deterministic left-to-right DFS; each leaf **occurrence** gets its own slot, so
   repeated atoms round-trip exactly). Slot ids live in a `"\x00slot:"` namespace
   that cannot collide with real atom ids.
3. The abstracted polynomial is the **derivation shape**, stored once in
   `PROV_SHAPE` keyed by its `term_hash` (the shape dictionary — tens of rows for
   thousands of terms, per the §4.2 observation that derivations are massively
   shape-repetitive; verified by the 1000-terms/3-shapes test).
4. The per-term record in `PROV_TERM` is just `(prov_ref, shape_hash, leaf_id_array)` —
   the compact level-two representation. `prov_ref = term_hash(normalized term)`,
   exactly the contracts' hash, so refs are stable across processes and ledgers.
5. `PROV_LEAF` records the distinct `(atom_id, prov_ref)` edges — the
   whitepaper's PROV_EDGE table and the substrate for exact invalidation.

`resolve(prov_ref)` re-instantiates the shape with the leaf array (the exact
inverse traversal) and returns a term equal to the normalized input, with
`term_hash(resolve(r)) == r`.

### Constraint H (§1.3)

`append_artifact` resolves the ref first: unknown refs raise `KeyError`, and a ref
that resolves to the ZERO polynomial raises `ValueError` before any row is written.
Because the smart constructors annihilate ZERO inside products and drop it inside
sums, normalized non-ZERO terms contain no hidden ZERO — checking the root is exact
(property-tested: `derivable(t) ⇔ t ≠ ZERO`).

### Exact invalidation (§4.2 dictionary-side join, §9 constraint Δ)

`invalidate(changed)` is the SQL join
`changed atoms → PROV_LEAF → ARTIFACT(prov_ref)`. It is exact in both directions:
`PROV_LEAF` holds precisely the leaf set of each interned term (no
over-invalidation), and every artifact's ref was interned through that same pass
(no under-invalidation). Composed derivations (child artifacts embedding parent
terms) are covered transitively because composition inlines the parent's leaves.
The `IN`-list is chunked (400/batch) for arbitrarily large change sets. Tested
against an externally tracked ground-truth DAG (~50 artifacts / 200 atoms / 40
random change subsets, fixed seed).

### Named valuations (§9)

`valuate_ref(ref, name)` runs `contracts.provenance.valuate` with:

- `citations` — `(P(atoms), ∪, ∪, ∅, ∅)`; returns the supporting `frozenset[str]`.
- `confidence` — Viterbi semiring `([0,1], max, ×, 0, 1)`; leaves default to 1.0,
  or are looked up in an optional keyword-only `atom_confidence` mapping
  (missing ids fall back to 1.0).
- `derivable` — Boolean semiring; ZERO → `False`.

### Model adapters (`models.py`, §11.1 T3 access, §18.4 item 4)

- `HeuristicAdapter(handlers)` — deterministic dispatch on `req.task`; `KeyError`
  on unknown tasks; zero tokens always. Handlers may return `str`,
  a JSON-serializable object, or a full `ModelResponse`.
- `CassetteAdapter(path, inner=None, mode)` — JSON cassette keyed by
  `sha256(task, prompt, schema)`. `record` requires an inner client and always
  records; `replay` serves hits byte-identically with `cached=True`, records
  through `inner` on miss when present, and raises `KeyError` on miss without one
  (deterministic CI, zero live calls). Writes are atomic (`tmp` + `os.replace`).
- `AnthropicAdapter` — raw HTTPS via `urllib` to `api.anthropic.com/v1/messages`
  (default model `claude-sonnet-4-6`, version header `2023-06-01`), **no SDK**.
  Constructible only when `ANTHROPIC_API_KEY` is set; construction does no I/O;
  never exercised by tests. Schema-constrained output is requested via a system
  instruction and parsed best-effort.

### Cost (§18.4 item 5)

`record_cost`/`total_cost_tokens` persist to the `COST` table. `LedgerCostMeter`
extends `contracts.models.CostMeter` so in-memory per-task counters and the
durable ledger move together.

## Deviations / notes

- **Bitemporal columns** (§11.2 data model): rows carry `created_at` (system time)
  only. Full four-timestamp bitemporality is a cell-level concern owned by HEARTH
  (M6) via `contracts.cells`; nothing in the M0 surface requires valid-time columns.
- **`CALIB_SAMPLE` table**: omitted — `contracts.decisions.CalibrationSample`
  persistence belongs to the Decision Spine (M2), which owns recalibration. Adding
  the table here would duplicate ownership without an M0 consumer.
- **`valuate_ref` signature**: the frozen protocol declares
  `valuate_ref(prov_ref, valuation)`. The atom-confidence map required by the M0
  brief is an extra **keyword-only argument with a default**
  (`atom_confidence=None`), which remains call-compatible with the protocol.
- **§11.2 sketch vs frozen contract**: the whitepaper sketches
  `register_atoms(source_id, batch)`; the frozen contract takes `Sequence[Atom]`
  (atoms already carry their source in the URI). The contract wins.
- **10^8-atom ingest throughput** (§11.2 test list): not asserted in CI — a
  hundred-million-row ingest is a benchmark, not a unit test. The schema choices
  that matter for it (PK-only dedup, `executemany` batches, WITHOUT ROWID edge
  table) are in place; the suite covers correctness at representative scale.
- **`artifact_id` is not unique**: re-appending the same artifact id is a
  supersession event (append-only), matching the decision ledger's semantics.
