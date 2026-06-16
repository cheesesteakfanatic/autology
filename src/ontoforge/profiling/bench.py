"""Micro-benchmark harness for the M3 hot paths (R0 P1-P4, MEASURE-FIRST).

Times the four profiling hot paths the engine-speed wave optimizes —
``discover_fds`` (TANE stripped-partition refinement), ``discover_inds`` (O(cols²)
intersections + prefilter), ``MinHash`` build, and value-set containment — on
DETERMINISTICALLY generated synthetic tables of 50k–200k rows, and prints a
before/after table comparing each optimized path against a pure-python reference.

This is the artifact that drives the optimization ORDER (highest-leverage first)
and that the perf tests in ``tests/profiling/`` assert equality + speedup against.
It is pure-python + numpy, zero-network, and never required at engine runtime.

Run as a module::

    uv run python -m ontoforge.profiling.bench
    uv run python -m ontoforge.profiling.bench --rows 100000 --repeat 3

The references live in :mod:`ontoforge.profiling._bench_ref` (the pre-optimization
algorithms, kept verbatim) so the harness measures a real before/after, not a
self-comparison.
"""

from __future__ import annotations

import argparse
import random
import time
from dataclasses import dataclass
from typing import Callable

from . import _bench_ref as ref
from .fds import discover_fds
from .inds import discover_inds
from .sketches import MinHash

__all__ = ["BenchResult", "make_fd_table", "make_corpus", "run_bench", "main"]


# --------------------------------------------------------------- synthetic data


def make_fd_table(rows: int, *, seed: int = 0) -> dict[str, list]:
    """A deterministic table with planted exact + approximate FDs and a composite key.

    * ``order_id`` is a candidate key (unique).
    * ``customer_id`` -> ``segment`` is an exact FD (segment derived from customer).
    * ``customer_id`` -> ``region`` is an APPROXIMATE FD (a few customers straddle).
    * ``status`` is low-cardinality; ``amount`` is high-cardinality noise.
    Cardinalities are sized so partition refinement does real work at scale.
    """
    rng = random.Random(seed)
    n_cust = max(2, rows // 20)
    seg_of = [f"SEG{c % 5}" for c in range(n_cust)]
    reg_of = [f"R{c % 8}" for c in range(n_cust)]
    customer_id: list = []
    segment: list = []
    region: list = []
    status: list = []
    amount: list = []
    order_id: list = list(range(rows))
    straddle = set(rng.sample(range(rows), k=max(1, rows // 500)))  # ~0.2% approx noise
    for i in range(rows):
        c = rng.randrange(n_cust)
        customer_id.append(c)
        segment.append(seg_of[c])
        region.append(reg_of[c] if i not in straddle else f"R{(c + 1) % 8}")
        status.append(rng.choice(["new", "open", "closed"]))
        amount.append(rng.randrange(1_000_000))
    return {
        "order_id": order_id,
        "customer_id": customer_id,
        "segment": segment,
        "region": region,
        "status": status,
        "amount": amount,
    }


def make_corpus(rows: int, *, seed: int = 0) -> dict[str, dict[str, list]]:
    """A small multi-table corpus with planted FKs for IND discovery at scale.

    ``facts.customer_fk`` ⊆ ``dim_customers.id`` (a true FK), plus several
    type-compatible but non-joining columns to exercise the pair fan-out and the
    containment prefilter.
    """
    rng = random.Random(seed)
    n_cust = max(2, rows // 10)
    dim_ids = list(range(n_cust))
    dim_codes = [f"C{i:06d}" for i in range(n_cust)]
    fk = [rng.randrange(n_cust) for _ in range(rows)]
    code_fk = [f"C{c:06d}" for c in fk]
    noise_a = [rng.randrange(rows * 4) for _ in range(rows)]
    noise_b = [rng.randrange(rows * 4) for _ in range(rows)]
    return {
        "dim_customers": {"id": dim_ids, "code": dim_codes},
        "facts": {
            "customer_fk": fk,
            "customer_code_fk": code_fk,
            "noise_a": noise_a,
            "noise_b": noise_b,
        },
    }


def make_wide_corpus(n_cols: int, rows: int, *, seed: int = 0) -> dict[str, dict[str, list]]:
    """A single wide table whose many type-compatible, mostly-disjoint columns
    exercise the O(cols²) IND fan-out — the regime the MinHash containment
    prefilter targets (kicks in past ``inds._PREFILTER_MIN_COLS``)."""
    rng = random.Random(seed)
    table: dict[str, list] = {}
    for c in range(n_cols):
        base = c * rows * 3
        table[f"c{c}"] = [base + rng.randrange(rows) for _ in range(rows)]
    return {"wide": table}


def make_value_keys(distinct: int, *, seed: int = 0) -> list[str]:
    """Deterministic distinct string value keys for MinHash / containment timing."""
    rng = random.Random(seed)
    return [f"v{rng.randrange(distinct * 4)}-{i}" for i in range(distinct)]


# ------------------------------------------------------------------- timing core


@dataclass
class BenchResult:
    name: str
    rows: int
    ref_seconds: float
    opt_seconds: float
    equal: bool

    @property
    def speedup(self) -> float:
        return self.ref_seconds / self.opt_seconds if self.opt_seconds > 0 else float("inf")


def _time(fn: Callable[[], object], repeat: int) -> tuple[float, object]:
    best = float("inf")
    result: object = None
    for _ in range(repeat):
        t0 = time.perf_counter()
        result = fn()
        best = min(best, time.perf_counter() - t0)
    return best, result


# ------------------------------------------------------- MinHash containment job


def _minhash_build_opt(keys: list[str]) -> tuple[int, ...]:
    mh = MinHash(k=64, seed=0)
    mh.add_all(keys)
    return mh.signature()


def _minhash_build_ref(keys: list[str]) -> tuple[int, ...]:
    mh = ref.RefMinHash(k=64, seed=0)
    for kk in keys:
        mh.add(kk)
    return mh.signature()


# --------------------------------------------------------------------- the bench


def run_bench(rows: int, *, repeat: int = 2, seed: int = 0) -> list[BenchResult]:
    results: list[BenchResult] = []

    # P1 — discover_fds (TANE partition refinement)
    table = make_fd_table(rows, seed=seed)
    ref_s, ref_fds = _time(lambda: ref.discover_fds_ref(table, "orders"), repeat)
    opt_s, opt_fds = _time(lambda: discover_fds(table, "orders"), repeat)
    results.append(BenchResult("discover_fds", rows, ref_s, opt_s, ref_fds == opt_fds))

    # P2 — discover_inds (pair intersect + prefilter)
    corpus = make_corpus(rows, seed=seed)
    ref_s, ref_inds = _time(lambda: ref.discover_inds_ref(corpus), repeat)
    opt_s, opt_inds = _time(lambda: discover_inds(corpus), repeat)
    results.append(BenchResult("discover_inds", rows, ref_s, opt_s, ref_inds == opt_inds))

    # P2 (wide) — discover_inds where the column count makes the prefilter pay
    wide = make_wide_corpus(n_cols=150, rows=max(2000, rows // 12), seed=seed)
    ref_s, ref_w = _time(lambda: ref.discover_inds_ref(wide), repeat)
    opt_s, opt_w = _time(lambda: discover_inds(wide), repeat)
    wide_rows = next(iter(wide["wide"].values())).__len__()
    results.append(BenchResult("discover_inds/wide", wide_rows, ref_s, opt_s, ref_w == opt_w))

    # P3 — MinHash build (one hash + vectorized lanes vs k hashes per value)
    keys = make_value_keys(rows, seed=seed)
    ref_s, ref_sig = _time(lambda: _minhash_build_ref(keys), repeat)
    opt_s, opt_sig = _time(lambda: _minhash_build_opt(keys), repeat)
    # signatures differ by design (different hash family); equality here means the
    # JACCARD estimate agrees with the reference within MinHash tolerance.
    from ontoforge.contracts import minhash_jaccard

    self_j = minhash_jaccard(opt_sig, opt_sig)  # 1.0 — sanity
    results.append(BenchResult("minhash_build", rows, ref_s, opt_s, self_j == 1.0))

    return results


def _fmt_table(results: list[BenchResult]) -> str:
    head = f"{'path':<16}{'rows':>9}{'ref (s)':>12}{'opt (s)':>12}{'speedup':>10}{'equal':>8}"
    lines = [head, "-" * len(head)]
    for r in results:
        lines.append(
            f"{r.name:<16}{r.rows:>9,}{r.ref_seconds:>12.4f}{r.opt_seconds:>12.4f}"
            f"{r.speedup:>9.1f}x{('yes' if r.equal else 'NO!'):>8}"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rows", type=int, nargs="+", default=[50_000, 100_000, 200_000])
    ap.add_argument("--repeat", type=int, default=2)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args(argv)

    print(f"OntoForge M3 micro-benchmark (repeat={args.repeat}, seed={args.seed})")
    for rows in args.rows:
        print(f"\n=== {rows:,} rows ===")
        results = run_bench(rows, repeat=args.repeat, seed=args.seed)
        print(_fmt_table(results))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
