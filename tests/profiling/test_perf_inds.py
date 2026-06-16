"""P2 perf gate — vectorized IND intersect + containment prefilter (profiling/inds.py).

* DETERMINISM / BYTE-IDENTITY (always): ``discover_inds`` (sorted-int64
  ``np.intersect1d`` + the cardinality/MinHash containment prefilter) returns the
  exact same IND set as the no-prefilter ``frozenset`` reference on random corpora,
  across coverage floors, in BOTH the small-column (prefilter off) and wide-column
  (prefilter on) regimes — the prefilter must have ZERO false negatives.
* SPEEDUP (measured, soft): >= 1.3x on the wide-column bench where the prefilter
  amortizes; skipped if the machine times unstably. Equality always asserted.
"""

from __future__ import annotations

import random

import pytest

from ontoforge.profiling.bench import make_wide_corpus, run_bench
from ontoforge.profiling.inds import _PREFILTER_MIN_COLS, discover_inds
from ontoforge.profiling._bench_ref import discover_inds_ref


def _random_corpus(rng: random.Random, *, wide: bool) -> dict:
    """Random multi-table corpus; ``wide`` forces past _PREFILTER_MIN_COLS columns."""
    corpus: dict[str, dict[str, list]] = {}
    if wide:
        table: dict[str, list] = {}
        n_cols = _PREFILTER_MIN_COLS + rng.randint(2, 20)
        rows = rng.randint(20, 80)
        for c in range(n_cols):
            base = c * rows * 2 if rng.random() < 0.7 else 0  # some columns overlap
            table[f"c{c}"] = [base + rng.randrange(rows) for _ in range(rows)]
        corpus["wide"] = table
        # a small dim that several wide columns can include into
        corpus["dim"] = {"id": list(range(rng.randint(5, rows)))}
        return corpus
    n_tables = rng.randint(2, 4)
    for t in range(n_tables):
        table = {}
        for c in range(rng.randint(1, 4)):
            n = rng.randint(2, 50)
            hi = rng.randint(2, 40)
            table[f"t{t}c{c}"] = [rng.randrange(hi) for _ in range(n)]
        corpus[f"table{t}"] = table
    return corpus


@pytest.mark.parametrize("seed", range(60))
def test_discover_inds_byte_identical_small(seed):
    """Few columns → prefilter MinHash off; only the exact cardinality bound gates."""
    rng = random.Random(seed)
    corpus = _random_corpus(rng, wide=False)
    for cov in (0.95, 0.8, 1.0, 0.5):
        assert discover_inds(corpus, min_coverage=cov) == discover_inds_ref(corpus, min_coverage=cov)


@pytest.mark.parametrize("seed", range(30))
def test_discover_inds_byte_identical_wide(seed):
    """Many columns → MinHash containment prefilter ON; must drop NO true positive."""
    rng = random.Random(seed)
    corpus = _random_corpus(rng, wide=True)
    for cov in (0.95, 0.7):
        got = discover_inds(corpus, min_coverage=cov)
        ref = discover_inds_ref(corpus, min_coverage=cov)
        assert got == ref, (
            f"prefilter changed the IND set at cov={cov}: "
            f"only-ref={set(map(_key, ref)) - set(map(_key, got))}"
        )


def _key(i):
    return (i.lhs_table, i.lhs_column, i.rhs_table, i.rhs_column)


def test_discover_inds_self_deterministic():
    corpus = make_wide_corpus(n_cols=_PREFILTER_MIN_COLS + 5, rows=300, seed=2)
    assert discover_inds(corpus) == discover_inds(corpus)


def test_inds_prefilter_speedup_wide():
    """>= 1.3x on the wide-column bench where the prefilter amortizes (skip if slow)."""
    res = next(r for r in run_bench(120_000, repeat=2, seed=0) if r.name == "discover_inds/wide")
    assert res.equal, "prefilter must keep discover_inds byte-identical to the reference"
    if res.ref_seconds < 0.5:
        pytest.skip(f"machine too fast for a stable timing (ref={res.ref_seconds:.3f}s)")
    assert res.speedup >= 1.3, f"expected >=1.3x, got {res.speedup:.2f}x"
