"""The micro-benchmark harness itself (profiling/bench.py) is deterministic and
reports equality on every path — it is the artifact the perf gates measure against."""

from __future__ import annotations

from ontoforge.profiling.bench import (
    BenchResult,
    make_corpus,
    make_fd_table,
    make_wide_corpus,
    run_bench,
)


def test_synthetic_generators_are_deterministic():
    assert make_fd_table(500, seed=0) == make_fd_table(500, seed=0)
    assert make_corpus(500, seed=0) == make_corpus(500, seed=0)
    assert make_wide_corpus(130, 200, seed=0) == make_wide_corpus(130, 200, seed=0)
    # different seed → different data (sanity that the seed is actually wired)
    assert make_fd_table(500, seed=0) != make_fd_table(500, seed=1)


def test_fd_table_has_the_planted_dependencies():
    t = make_fd_table(2000, seed=0)
    # order_id is unique; customer_id -> segment is exact
    assert len(set(t["order_id"])) == len(t["order_id"])
    seg = {}
    exact = True
    for c, s in zip(t["customer_id"], t["segment"]):
        if seg.setdefault(c, s) != s:
            exact = False
            break
    assert exact, "customer_id -> segment must be an exact FD by construction"


def test_run_bench_reports_equality_on_every_path():
    """Small run: every optimized path must be EQUAL to its reference (the gate)."""
    results = run_bench(3_000, repeat=1, seed=0)
    names = {r.name for r in results}
    assert {"discover_fds", "discover_inds", "discover_inds/wide", "minhash_build"} <= names
    for r in results:
        assert isinstance(r, BenchResult)
        assert r.equal, f"{r.name} diverged from reference"
        assert r.opt_seconds > 0 and r.ref_seconds > 0
