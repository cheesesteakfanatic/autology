# M3 — Profiler & Dependency Discovery

Whitepaper §3.1–3.2, §11.2 M3; amendments AMD-0003 (TANE baseline), AMD-0001
(fixture scale). One profiling pass produces the universal feature object φ(p)
consumed by STRATA (M4), ANVIL (M8), and WARDEN (M9). All outputs are the frozen
contract types `ColumnProfile` / `TableProfile` / `FD` / `IND`.

## §11.2 interface mapping

| Spec interface                  | Implementation                                  |
|---------------------------------|-------------------------------------------------|
| `profile(stream) → φ`           | `profile_table` / `profile_column` (`profile` is an alias; the table is the v0 stream unit) |
| `discover_fds(table) → [FD]`    | `fds.discover_fds` (+ `candidate_keys`)         |
| `discover_inds(corpus) → [IND]` | `inds.discover_inds`                            |
| `dimension(column) → vector`    | `units_infer.dimension_of` (full detail: `infer_unit`) |

Inputs: pyarrow Table, pandas DataFrame, or a mapping of column lists, normalized
in `_values.py` (NaN/NaT/`pd.NA` all become `None`; `value_key` is the shared
canonical hashing form — integral floats collapse to integer keys so a BIGINT FK
matches a DOUBLE PK across engines).

## Algorithm choices

**Sketches (`sketches.py`, §3.1)** — dependency-free, streaming, seeded.
- *KLL-lite quantiles*: compactor stack with random-offset half-promotion and
  exact min/max. Rank-error std dev is O(√H·n/k), H = log₂(n/k); at k=256,
  n=10⁴ this is well under one decile (test budget: ≤ 2 deciles on skewed data).
- *HyperLogLog*: p=14 index bits over xxhash64, αₘ bias correction,
  linear-counting small-range correction, exact-set fallback below 4096 distinct
  (stored hashes replay into registers on overflow, so nothing is lost).
  Expected error 1.04/√m ≈ 0.8% (test budget: ≤ 5% at 50k distinct).
- *k-MinHash (k=64)*: lane i = min over xxhash64(seed+i); the fraction of equal
  lanes is an unbiased Jaccard estimator (std err ≤ 0.0625), consistent with
  `contracts.minhash_jaccard`. Insert dedup makes it multiset-insensitive.

**Format signatures (`format_signature.py`, §3.1 "regex lattice")** — tokenize
over classes D/A/a + punct literals with run-length collapsing, then fold samples
pairwise through Needleman–Wunsch alignment whose substitution cost is the
lattice distance (D,A,a → L → X → ANY) and whose gaps make tokens optional.
Invariant (property-tested): every sampled value matches `to_regex(signature)`.
Cost ties can produce an optional-token form instead of the widest class
(`A? L{2,3}` vs `L{3}`) — both are minimal covers in the lattice.

**Datatype + semantic typing (`semantic_types.py`, §3.2)** — value parsing with a
97% agreement threshold (dirty cells must not flip a column type); zero-padded
pure-digit strings are identifier codes, never INTEGER **or FLOAT** (a leak
through the float regex was found and fixed during this pass). Semantic rules are
a registry (tail numbers, ICAO, emails, US states, currency, dates, narrative)
with priors × match-fraction + name-hint bonuses; ICAO is deliberately
low-prior (any 4 uppercase letters) and only clears the floor with a name hint.
The classifier hook (`SemanticClassifier` protocol, `SklearnSemanticHook`
adapter over `extract_semantic_features`) is consulted only when no rule clears
the 0.6 floor — mirroring the spine's T0/T1 tiering.

**Units & dimensions (`units_table.py`, `units_infer.py`, §3.2)** — every unit is
a `contracts.UnitDef` with an affine conversion to its dimension's canonical
unit; currencies convert only to themselves (FX is time-varying market data, not
a static conversion — ANVIL's job). Aliases are context-gated (`temp_f` is
Fahrenheit, bare `f` is not; `nm` defaults to nautical mile — aviation is the
hero estate, §17.2.1). Three evidence sources: column-name tokens, value
suffixes (`250 kt`), and magnitude bimodality (exact 1-D 2-means whose cluster
ratio matches a known same-dimension conversion, e.g. lb↔kg ≈ 2.2046).
**Mixed-unit columns are flagged, never merged**: ≥2 significant suffix units or
a magnitude hit ⇒ `mixed=True`, `unit=None`; name/value disagreement ⇒
`conflict=True` with confidence ≤ 0.5 so the spine escalates (§3.2).

**FDs & keys (`fds.py`, §3.1 amended by AMD-0003)** — exact TANE-class
partition refinement: stripped partitions, probe-table partition products,
level-wise lattice ascent with TANE C+ rhs pruning (guarantees lhs-minimal exact
FDs), lhs capped at 3. Approximate FDs use the g3 measure
(1 − violations/rows) and are emitted at confidence ≥ 0.98, suppressed when a
subset lhs already determines the same rhs. Nulls are one value for FD
agreement; for *keys* the SQL entity-integrity rule applies (null-bearing
columns are excluded). Candidate keys are minimal uniqueness sets capped at
size 2. Canonical form: lhs tuples and composite keys sorted alphabetically.
`FD(lhs=())` means "column is (near-)constant" — genuine lattice output.

**INDs (`inds.py`, §3.1)** — §3.1 names BINDER; at fixture scale (AMD-0001) a
direct value-set-hash intersection over all type-compatible (table, column)
pairs is exact and simpler; BINDER's out-of-core partitioning is a registered
scale challenger (§19.1). Coverage = |lhs ∩ rhs|/|lhs| ≥ 0.95. Join-candidate
score (weights pinned by tests):
`0.40·coverage + 0.20·name-token-Jaccard + 0.15·type-match + 0.25·rhs-uniqueness`.
BOOLEAN and near-constant columns are excluded (vacuous inclusions). n-ary
apriori refinement (v1 G3) is deferred with that scope.

**Orchestrator (`profile.py`)** — `profile_table` runs everything in one pass;
`profile_table_detailed` additionally returns per-column `UnitInference`.
Append-mostly (§3.5) is a hook (`append_detector` parameter): the default
compares successive profiles — rows must grow and per shared column the MinHash
Jaccard must be consistent with old-value-set containment (expected
J ≈ d_prev/d_cur under pure append; in-place updates drag J far below).

## Determinism

Fixed seeds everywhere (sketches seed 0, KLL's RNG seeded); deterministic
sampling (`sample_evenly` = sorted + even strides); all output orderings are
explicitly sorted with tie-breaks. Asserted by tests, including a derandomized
Hypothesis property for the partition product and the signature-covering
invariant.

## Contract gaps / deviations (reported, not worked around in contracts)

1. **`ColumnProfile` has no unit-confidence / mixed / conflict fields.** The
   frozen contract carries only `unit`/`dimension`. Mapping is conservative:
   mixed or conflicted columns get `unit=None` (never a silent assert), the
   dimension is kept when all observed units share one, and the full
   `UnitInference` (mixed/conflict/observed_units/source/confidence) is
   available via `profile_table_detailed`. Suggest adding
   `unit_confidence: float` and `unit_mixed: bool` to `ColumnProfile` in a
   future contract rev.
2. **`TableProfile.append_mostly` is a bare bool** — there is no field for the
   evidence (per-column containment scores). The hook returns only the flag.
3. §11.2 M3 names an *incremental re-profile cost ∝ Δ* test. The sketches are
   streaming (updatable in place), but profile-level incremental merge is not
   wired in v0 — it lands with the M1→M3 delta plumbing; HyFD-parity runtime is
   deferred per AMD-0003.

## Tests (`tests/m3/`, 80 tests)

Seeded mini TPC-H-like trio (customers/orders/lineitems with declared PK/FKs)
in `m3_helpers.py` with brute-force FD/uniqueness oracles: declared-key
recovery, FD soundness (every confidence-1.0 FD re-verified by scan), lhs
minimality, approximate-FD confidence (~0.99 injected), lhs cap; IND FK
recovery, reverse-direction coverage gating, FK-vs-accidental scoring,
int-FK-in-float-PK; sketch error budgets (KLL ≤ 2 deciles / 10k skewed,
HLL ≤ 5% / 50k distinct, MinHash ± 0.15); format signatures on tail
numbers/dates/ICAO + covering property; semantic typing on aviation columns +
classifier hook with a real sklearn estimator; unit suite including mixed-suffix,
cross-dimension mixing, magnitude bimodality, false-alarm guard, and
name/value conflict; orchestrator end-to-end, pandas/pyarrow/dict input
equivalence, NaN==None null handling, append-mostly true/false cases, and
full-profile determinism.
