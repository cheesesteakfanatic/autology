# M8 — ANVIL: By-Ontology Transform Synthesis (§5.2, §11.2 M8)

The induced/gold ontology **O** plus its SHACL-style shapes **Σ** are a
machine-checkable target specification. ANVIL synthesizes RAW→CONFORMED
transforms whose outputs satisfy Σ for a target class, verified on seeded
holdouts with **provenance-equivalence** row-tag checks, and accepted through
**DecisionKind.TX** spine decisions as readable `contracts.TransformDef`
artifacts.

## Interface

```python
from ontoforge.anvil import Anvil, synthesize

anvil = Anvil(seed=0, beam=8, depth=4, ledger=ledger)   # spine optional
accepted = anvil.synthesize(
    df, table_profile, target_class, ontology,
    extra_tables={"sites": sites_df},   # optional join candidates
    inds=[ind, ...],                    # discovered INDs (M3)
)
# -> list[(contracts.TransformDef, contracts.VerificationReport)]
anvil.last_run   # SynthesisRun: outcomes, fixes, SearchStats, base program
```

M7 (`ontoforge.transforms`) is built in parallel; the only shared surface is
`contracts.TransformDef` — ANVIL never imports M7, and all synthesized SQL is
validated by direct DuckDB execution.

## Tiered algorithm

1. **T0 fix detectors** (`detectors.py`) — corruption taxonomy pattern-matched
   against `ColumnProfile`s and value evidence; each emits a parameterized SQL
   fragment composed into one per-column expression chain:
   null-token normalization, trim, case-folding on code-like columns, mixed
   date/locale formats (greedy minimal strptime cover, `%m/%d` vs `%d/%m`
   disambiguated by uniquely-identifying values), numeric-in-string
   (currency-prefix/thousands-separator stripping; **foreign-currency rows are
   NULLed and reported, never FX-converted**), unit conversion via the
   profiler's unit table (per-row suffix-sliced CASE or whole-column affine
   conversion; magnitude-mixed columns with no separator are flagged
   unresolved, never silently merged — §3.2), header-row-in-data filters,
   constant-column drops, exact-duplicate-row dedupe.
2. **T1 constrained search** (`search.py`) — residual gaps vs. the target
   class (normalized-name/synonym matching in `mapping.py`); beam ≤ 8,
   depth ≤ 4 over project/rename, cast, split_part / regexp_extract
   (PROSE-lite: induced from ≤ 5 input→output examples derived from the target
   shape pattern), LEFT JOIN along a discovered IND, group-by along a
   discovered FD, dedupe. **Auto-Pipeline pruning:** any candidate whose
   sampled intermediate output violates a discovered FD, key uniqueness under
   an IND join, or a target ShapeConstraint is discarded (counts in
   `SearchStats.pruned_fd` / `pruned_shape`).
3. **Verification** (`verify.py`) — seeded 70/30 synth/holdout split;
   admission evidence is (a) all target shapes on the holdout output and
   (b) **provenance equivalence**: the program re-executes with a threaded
   `__row_id` tag and every output row must derive only from the intended
   input rows (rowwise = bijection on survivors; join = no fan-out + each
   right tag actually satisfies the join condition; dedupe = one
   representative per independently-counted distinct key; group-by = tag
   lists partition the input). A static sqlglot guard also rejects
   expressions smuggling windows/joins/subqueries. This catches cross-row
   leakage invisible to value-equality testing (mutation-tested in
   `tests/m8/test_provenance.py`).
4. **TX acceptance** (`acceptance.py`) — features: holdout pass rate, sample
   coverage, bounded MDL prior `1/(1+complexity)`; a deterministic spine-T0
   rule accepts provenance-clean, Σ-satisfying candidates; ambiguous ones
   (pass rate ≥ 0.70 but Σ unsatisfied) defer to the human queue as ledger
   `review` artifacts with readable SQL. Accepted programs ship as
   `TransformDef` (sqlglot-pretty-printed SQL, `synthesized_by='anvil:T0'|
   'anvil:T1'`) plus a ledger `transform` artifact with provenance.

## Determinism & scope

Fixed seed ⇒ identical SQL/fingerprints; zero network, zero model spend (the
spine is wired but v0 acceptance resolves at spine-T0/T1; T2/T3 semantic
synthesis is out of v0 scope per §5.2 step 3). Candidate ordering, beam
tie-breaks, and detector format covers are all explicitly sorted.

## Known gaps / deviations

- Pivot/unpivot detection (named in §5.2's taxonomy) is not implemented in v0.
- Cross-currency conversion is intentionally unsupported (units table has no
  FX); such rows are nulled + noted for review rather than silently mixed.
- `ColumnProfile` has no mixed-unit/confidence channel (M3 contract gap), so
  unit detectors re-run `profiling.infer_unit` on the evidence sample.
- Auto-Pipeline-700 / TPC-DI harnesses (§11.2 M8 benchmarks) are deferred to
  the integration wave; the seeded corruption suite in `tests/m8/` is the v0
  gate (≥ 70% end-to-end fixes per corruption class; measured 100% on all ten
  classes).

## Tests

`uv run pytest tests/m8 -q` — seeded corruption suite (hard gate ≥ 70% per
class, rates printed by `test_zz_report.py`), provenance mutation tests,
pruning-effectiveness counts, readability round-trip invariant, real-estate
cases (maintenance_erp COST mix → decimal; ASRS altitude meters-wart → ft),
review-queue/ledger records, determinism.
