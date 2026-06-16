"""P1 perf gate — vectorized TANE partition refinement (profiling/fds.py).

Two guarantees, both load-bearing for the engine-speed wave:

* DETERMINISM / BYTE-IDENTITY (always asserted): the numpy-vectorized
  ``discover_fds`` / ``candidate_keys`` are byte-identical to the pure-python
  reference (``profiling/_bench_ref.discover_fds_ref`` + the reference partition
  primitives) on random tables — same FDs, same confidences, same order, same keys.
* SPEEDUP (measured, soft): >= 2x on the 100k-row FD bench. Skipped (not failed)
  when the machine is too slow for a stable timing, so it never flakes on slow CI;
  equality is asserted unconditionally.
"""

from __future__ import annotations

import random

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from ontoforge.profiling import _fd_kernels as k
from ontoforge.profiling import candidate_keys, discover_fds
from ontoforge.profiling._bench_ref import discover_fds_ref
from ontoforge.profiling.bench import make_fd_table, run_bench
from ontoforge.profiling.fds import (
    _row_keys,
    partition_product,
    stripped_partition,
)
from ontoforge.profiling.fds import _violations as _violations_ref


# ---------------------------------------------------- kernel-primitive parity


def _norm(p):
    return sorted(tuple(sorted(c)) for c in p)


@settings(derandomize=True, max_examples=200, deadline=None)
@given(
    st.lists(st.tuples(st.integers(0, 5), st.integers(0, 5)), min_size=0, max_size=60),
)
def test_kernel_primitives_byte_identical(pairs):
    """stripped_partition / partition_product (membership) / _violations parity."""
    n = len(pairs)
    sa = [str(a) for a, _ in pairs]
    sb = [str(b) for _, b in pairs]

    ref_sa = stripped_partition(sa)
    ker_sa = k.stripped_partition_coded(k.codes_of(sa))
    assert ref_sa == k.to_tuple(ker_sa)  # first-occurrence order preserved exactly

    ref_p = partition_product(stripped_partition(sa), stripped_partition(sb), n)
    ker_p = k.partition_product_coded(
        k.stripped_partition_coded(k.codes_of(sa)),
        k.stripped_partition_coded(k.codes_of(sb)),
        n,
    )
    assert _norm(ref_p) == _norm(k.to_tuple(ker_p))   # class membership identical
    assert (not ref_p) == k.is_empty(ker_p)           # emptiness identical (key test)

    cb = k.codes_of(sb)
    max_code = int(cb.max()) + 1 if cb.size else 0
    assert _violations_ref(stripped_partition(sa), sb) == k.violations_coded(ker_sa, cb, max_code)


# ------------------------------------------------------ end-to-end FD parity


def _random_table(rng: random.Random) -> dict[str, list]:
    n = rng.randint(0, 60)
    n_cols = rng.randint(1, 5)
    table: dict[str, list] = {}
    for c in range(n_cols):
        card = rng.randint(1, 8)
        vals: list = []
        for _ in range(n):
            x = rng.randint(0, card)
            vals.append(None if x == 0 and rng.random() < 0.2 else x)
        table[f"col{c}"] = vals
    return table


@pytest.mark.parametrize("seed", range(40))
def test_discover_fds_byte_identical_to_reference(seed):
    rng = random.Random(seed)
    table = _random_table(rng)
    assert discover_fds(table, "t") == discover_fds_ref(table, "t")


@pytest.mark.parametrize("seed", range(40))
def test_candidate_keys_match_reference_partition(seed):
    """candidate_keys via coded kernels == the pure-python partition definition."""
    rng = random.Random(seed)
    from itertools import combinations

    from ontoforge.profiling._values import is_null

    table = _random_table(rng)
    got = set(candidate_keys(table, max_key_size=2))

    # reference: a set is a key iff its stripped partition is empty; nulls exclude.
    cols = list(table)
    n = max((len(v) for v in table.values()), default=0)
    if n == 0 or not cols:
        assert got == set()  # empty table → no keys (matches _keys_from_columns)
        return
    padded = {c: list(table[c]) + [None] * (n - len(table[c])) for c in cols}
    eligible = sorted(c for c in cols if not any(is_null(v) for v in padded[c]))
    parts = {c: stripped_partition(_row_keys(padded[c])) for c in eligible}
    expect = {(c,) for c in eligible if not parts[c]}
    rest = [c for c in eligible if parts[c]]
    for c1, c2 in combinations(rest, 2):
        if not partition_product(parts[c1], parts[c2], n):
            expect.add((c1, c2))
    assert got == expect


def test_discover_fds_self_deterministic():
    table = make_fd_table(2000, seed=1)
    assert discover_fds(table, "orders") == discover_fds(table, "orders")


# ----------------------------------------------------------- speedup (soft)


def test_fds_speedup_100k():
    """>= 2x vs the Counter/dict reference on the 100k-row FD bench (skip if slow).

    Best-of-3 (``_time`` keeps the min) so JIT/import/GC warmup doesn't depress the
    ratio; the assert is skipped — not failed — on a machine too slow to time stably.
    """
    res = next(r for r in run_bench(100_000, repeat=3, seed=0) if r.name == "discover_fds")
    assert res.equal, "vectorized discover_fds must be byte-identical to the reference"
    if res.ref_seconds < 0.5:
        pytest.skip(f"machine too fast/slow for a stable timing (ref={res.ref_seconds:.3f}s)")
    assert res.speedup >= 2.0, f"expected >=2x, got {res.speedup:.2f}x (ref={res.ref_seconds:.3f}s opt={res.opt_seconds:.3f}s)"
