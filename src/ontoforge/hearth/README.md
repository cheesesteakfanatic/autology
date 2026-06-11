# M6 — HEARTH: Provenance-Anchored Bi-temporal Entity Store

Implements whitepaper §4 (entire section) and §11.2 M6, at AMD-0001 fixture
scale. Owner files: `src/ontoforge/hearth/`, tests in `tests/m6/` (60 tests).

## Layout (§4.2, AMD-0001)

**Canonical layer = plain Parquet, queried via DuckDB.** AMD-0001 replaces the
spec's Iceberg substrate with Parquet + DuckDB views; snapshotting comes from
the bi-temporal columns themselves (every historical state is reconstructable
from the cells), and the open-format constraint (P) is satisfied directly —
`export_canonical` ships the *same* schema the store runs on.

```
root/
  values/{raw|conformed|entity}/<shard_key>/   _meta.json + cells.parquet
  links/<class_key>/<pred_key>/                _meta.json + links.parquet
```

* One **value shard** per (layer, class URI); rows are `contracts.ValueCell` +
  a store-side `seq` column (dense 0..n-1 write order — the final survivorship
  tiebreak, preserved across reload/export so total order is canonical).
* Values are stored as a **canonical JSON string** (`value_json`, the
  round-trip-exact representation) plus typed mirror columns (`value_str`,
  `value_num`) for DuckDB predicate pushdown — §4.2's "typed columns where
  possible" without giving up a single canonical encoding.
* One **link shard** per (class URI, predicate) — §4.2's link store (a).
* `shard_key` = slug + xxh3(uri) so arbitrary URIs map to safe, collision-free
  directories; `_meta.json` makes the mapping reversible on discovery.

**Derived, disposable serving structures (§4.2(b))** — rebuilt from Parquet on
open, maintained *incrementally* per commit (only touched keys), excluded from
the export bundle (documented capability-neutral loss):

* per shard: `current` dict ((entity, prop) → seq) — the O(1) point-read fast
  path; `open_by_key` (system-open cells per key); `by_entity` (cell map);
* link adjacency: forward + reverse `{subject → predicate → {object: count}}`
  over **current** links (counted, so the same triple current in two class
  shards survives one retraction). `rebuild_adjacency()` reconstructs it
  exactly — property-tested (I4) and unit-tested by clearing it mid-flight.

## Commit path (§4.3)

`commit(layer, class_uri, cells, now=None)` — validate **all**, then apply,
then atomic rewrite (temp file + `os.replace`). Rejections (`CommitRejected`,
nothing written):

* **Constraint H:** empty `prov_ref`, a ref unknown to the ledger, or a ref
  whose term valuates to non-derivable (interned ZERO). Tests mint *real*
  atoms + interned Leaf terms; there are no fake refs anywhere in the suite.
* closed system interval (system time is store-stamped), confidence ∉ [0,1],
  `src_rank < 0`, and `src_rank == 0` on the public path (rank 0 is reserved
  for human Actions, §4.3.2).
* non-monotone system time: the store keeps a clock floor; `now` may never
  run backwards (append-monotone system time, also enforced after import).

**Supersession & survivorship.** Precedence = (lower `src_rank`, then higher
`confidence`, then newer `created_at`, then later `seq`) — *one* ordering,
implemented once and used at both call sites (commit-side `supersedes`,
read-side `survivorship_key`, and mirrored term-for-term in the DuckDB window
`ORDER BY`). For an incoming cell N over key (e,p):

* N beats every overlapping system-open cell → those cells' system intervals
  **close** (`expired_at = now`; never deleted), N lands open, and **residual
  segments** re-assert the old value outside N's valid window under the new
  system epoch. This is what makes a world-time correction (closed valid
  interval over an open cell) split into `[old_start, t1)` + corrected
  `[t1, t2)` + `[t2, ∞)` — scenario (b) of §4.5 — while leaving the *current*
  value untouched.
* N loses to any overlapping open cell → N is recorded **dead-on-arrival**:
  appended with system interval `[now, now+1)`. Append-only audit keeps the
  attempted write; it is never current and never clobbers the winner. The
  1-µs window is the deliberate representation of "received and retracted in
  the same tick" under half-open intervals (an `Interval` cannot be empty);
  `audit(known_at=now)` at the exact commit instant can therefore surface it —
  documented and pinned by the hypothesis suite (I5 checks at `now+1`).

**Invariant maintained (stronger than spec):** per (entity, prop), all
system-open cells have pairwise disjoint valid intervals — hence at most one
current cell ever (§4.5(e) is implied; both forms are property-tested).

## Reads (§4.4)

`read(entity, stance)` / `scan(class, stance, filters)` / `history(entity,
prop)`; visibility is `ValueCell.visible_under` (contracts), then survivorship
per prop. Two independent scan implementations cross-check each other in the
acceptance suite: the in-memory fast path and `scan_duckdb` (stance predicate
+ `ROW_NUMBER()` survivorship in SQL over the canonical Parquet), asserted
equal under every stance kind on the 1000-entity corpus.

`traverse(uri, predicate, stance, depth, reverse)` — BFS; current stance walks
the adjacency index, other stances fall back to filtering canonical link cells
(§4.5's documented read-path fallback). `unlink` is a belief change: expire
the current edge, re-assert it with valid time closed at now — `as_of(past)`
still sees the link.

## Actions (§4.3.2)

`action(actor, op)` with `SetProperty | Link | Unlink | CreateObject`:

1. SHACL-style pre-validation against the class's `ShapeConstraint`s (datatype,
   pattern, in_values, min/max, required-on-create, link-predicate checks)
   from the ontology supplied at construction (gold ontology in tests); no
   ontology → validation is a documented no-op.
2. Evidence: a synthetic `human-edit` atom (actor + canonical op payload +
   timestamp) registered in the M0 ledger, its Leaf interned as the cell's
   `prov_ref`, plus a `human-edit` artifact row — constraint H holds for
   human writes exactly as for pipeline writes.
3. Write at `src_rank = 0` through the same versioned-cell path. The conflict
   matrix is tested: action beats pipeline (either arrival order), a later
   pipeline write lands dead-on-arrival (auditable, not current), and a later
   human action supersedes an earlier one.

## Portability (the AMBER precursor)

`export_canonical(out_dir)` → plain Parquet (same schemas) + `manifest.json`
with class URIs, layers, predicates, row counts, and **content hashes computed
over the canonical row encoding** (not file bytes — stable across Parquet
writer metadata). `import_canonical(bundle, root, ledger)` verifies every hash
*before* building state, refuses non-empty targets, reconstructs shards,
rebuilds all derived indexes, restores the system-clock floor (the imported
store stays live and monotone), and re-derives the hashes as a decode/encode
bit-stability check. Gold gates in `tests/m6/test_portability.py`: cell-set
equality (`canonical_state`), export→import→export hash idempotence, tamper
detection, stance-answer equality after import.

## Measured numbers (Apple Silicon dev machine, 5,000 entities × 4 props = 20,000 cells + 500 supersessions)

| metric | measured | target (AMD-0001 rescaled) |
|---|---|---|
| point read (`current_value`, O(1) dict) | median 1.25 µs, p99 2.26 µs | p99 < 10 ms ✓ |
| full entity `read()` (current) | median 4.8 µs, p99 9.2 µs | — |
| commit throughput (batch) | ~19,900 cells/s | — |
| `scan()` fast path, 5,000 rows | 39 ms | — |
| `scan_duckdb()` same rows | 254 ms (agrees bit-for-bit) | — |
| 2-hop `traverse` over 5,000 edges | mean 6.9 µs | < 500 ms ✓ |
| prov_ref column, compressed | **0.01 B/cell** (3 distinct refs / 20.5 k cells, dictionary-encoded) | ≤ 8 B/cell median ✓ |
| whole shard file | 12.1 B/cell compressed | — |

The provenance overhead number reflects the §4.2 observation that refs are
massively repetitive (per-batch interning reuse); each distinct ref is a
16-char hash, so even pathological all-distinct shards stay ≈16–20 B/cell.

## Design decisions & trade-offs

1. **Whole-shard rewrite per commit** instead of delta files: supersession
   mutates `expired_at` of earlier rows, and at AMD-0001 fixture scale a
   single atomic rewrite is simpler and safer than delta+compaction.
   Append-only is a *logical* guarantee (cells are never removed and only ever
   close their system interval once — property-tested I2), not a
   physical-file guarantee.
2. **Total-rejection DOA** (a losing write is dead even where its valid window
   extends past the winner) rather than partial fragment acceptance:
   conservative, oscillation-resistant (§4.5 hysteresis spirit), and keeps the
   full attempted write in one auditable row.
3. **Store-stamped system time with an explicit `now` override**: tests are
   fully deterministic; wall-clock is only the default.
4. **Link supersession is latest-wins per (subject, object)** — edges carry no
   src_rank in the contract; human link edits still win in practice because
   `unlink`/`Link` actions operate on the current edge directly.
5. **`seq` in the canonical schema**: makes survivorship's last tiebreak
   reproducible after reload/export — without it, same-instant equal-rank
   writes would be order-ambiguous on import.
