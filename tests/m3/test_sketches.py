"""Sketch-suite acceptance tests (brief: KLL <= 2 deciles on 10k skewed values;
HLL <= 5% at 50k distinct; MinHash Jaccard within 0.15 of truth; determinism)."""

from __future__ import annotations

import random

from ontoforge.contracts import minhash_jaccard
from ontoforge.profiling import HyperLogLog, KLLSketch, MinHash


# --------------------------------------------------------------------- KLL


def _decile_position_error(data: list[float], sketch: KLLSketch) -> float:
    """Max |estimated decile - true decile| measured in decile units."""
    s = sorted(data)
    n = len(s)
    worst = 0.0
    for i in range(1, 10):
        est = sketch.quantile(i / 10)
        # rank of the estimate within the true data (fraction below)
        lo = _bisect_left(s, est)
        hi = _bisect_right(s, est)
        pos = ((lo + hi) / 2) / n * 10
        worst = max(worst, abs(pos - i))
    return worst


def _bisect_left(a, x):
    import bisect
    return bisect.bisect_left(a, x)


def _bisect_right(a, x):
    import bisect
    return bisect.bisect_right(a, x)


def test_kll_deciles_within_two_deciles_on_10k_skewed():
    rng = random.Random(42)
    data = [rng.lognormvariate(0.0, 1.5) for _ in range(10_000)]
    sk = KLLSketch(seed=0)
    sk.extend(data)
    assert sk.n == 10_000
    assert _decile_position_error(data, sk) <= 2.0


def test_kll_min_max_exact_and_monotone():
    rng = random.Random(7)
    data = [rng.uniform(-50, 50) for _ in range(5_000)]
    sk = KLLSketch(seed=0)
    sk.extend(data)
    d = sk.deciles()
    assert len(d) == 11
    assert d[0] == min(data) and d[-1] == max(data)
    assert all(a <= b for a, b in zip(d, d[1:]))


def test_kll_small_input_is_near_exact():
    data = [float(i) for i in range(100)]
    sk = KLLSketch(seed=0)
    sk.extend(data)
    assert abs(sk.quantile(0.5) - 49.5) <= 1.0


# --------------------------------------------------------------------- HLL


def test_hll_exact_below_fallback_limit():
    h = HyperLogLog(seed=0)
    for i in range(3_000):
        h.add(f"v{i % 1000}")
    assert h.is_exact
    assert h.estimate() == 1000


def test_hll_within_5pct_at_50k_distinct():
    h = HyperLogLog(seed=0)
    for i in range(50_000):
        h.add(f"value-{i}")
    for i in range(10_000):  # duplicates must not inflate the estimate
        h.add(f"value-{i}")
    assert not h.is_exact
    est = h.estimate()
    assert abs(est - 50_000) / 50_000 <= 0.05


# ----------------------------------------------------------------- MinHash


def test_minhash_jaccard_within_015_of_truth():
    a, b = MinHash(k=64, seed=0), MinHash(k=64, seed=0)
    for i in range(1_000):
        a.add(f"k{i}")
    for i in range(500, 1_500):
        b.add(f"k{i}")
    truth = 500 / 1_500
    est = minhash_jaccard(a.signature(), b.signature())
    assert abs(est - truth) <= 0.15


def test_minhash_identical_and_disjoint_sets():
    a, b, c = MinHash(seed=0), MinHash(seed=0), MinHash(seed=0)
    for i in range(200):
        a.add(f"x{i}")
        b.add(f"x{i}")
        c.add(f"y{i}")
    assert minhash_jaccard(a.signature(), b.signature()) == 1.0
    assert minhash_jaccard(a.signature(), c.signature()) <= 0.1
    assert minhash_jaccard(a.signature(), ()) == 0.0  # empty-set contract behavior


def test_minhash_is_order_and_multiplicity_insensitive():
    a, b = MinHash(seed=0), MinHash(seed=0)
    keys = [f"k{i}" for i in range(300)]
    for k in keys:
        a.add(k)
    for k in reversed(keys):
        b.add(k)
        b.add(k)  # multiset == set
    assert a.signature() == b.signature()


# ------------------------------------------------------------- determinism


def test_sketches_deterministic_under_fixed_seed():
    rng1, rng2 = random.Random(99), random.Random(99)
    data1 = [rng1.gauss(0, 10) for _ in range(4_000)]
    data2 = [rng2.gauss(0, 10) for _ in range(4_000)]

    k1, k2 = KLLSketch(seed=3), KLLSketch(seed=3)
    k1.extend(data1)
    k2.extend(data2)
    assert k1.deciles() == k2.deciles()

    h1, h2 = HyperLogLog(seed=3), HyperLogLog(seed=3)
    m1, m2 = MinHash(seed=3), MinHash(seed=3)
    for i in range(9_000):
        h1.add(f"d{i}")
        h2.add(f"d{i}")
        m1.add(f"d{i}")
        m2.add(f"d{i}")
    assert h1.estimate() == h2.estimate()
    assert m1.signature() == m2.signature()
