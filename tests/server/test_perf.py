"""Latency gates on the demo world, asserted with generous CI margins.

Product targets (measured locally, reported in the test output):
- /api/search p95 < 150 ms        (asserted here at < 1 s)
- /api/ask cache hit < 50 ms      (asserted here at < 1 s)

The prints surface the real numbers in `pytest -s` / CI logs so regressions
are visible long before the loose assertion would trip."""

from __future__ import annotations

import time

QUERIES = ("aircraft", "N1", "landing", "review", "registration number", "work")
CACHED_QUESTION = "How many work orders have component 'LANDING GEAR'?"


def _percentile(samples: list[float], q: float) -> float:
    ordered = sorted(samples)
    return ordered[min(int(len(ordered) * q), len(ordered) - 1)]


def test_search_p95_is_fast_on_the_demo_world(client):
    client.get("/api/search", params={"q": "warm the value index"})
    samples: list[float] = []
    for i in range(48):
        t0 = time.perf_counter()
        out = client.get("/api/search", params={"q": QUERIES[i % len(QUERIES)]})
        samples.append(time.perf_counter() - t0)
        assert out.status_code == 200
    p50 = _percentile(samples, 0.50) * 1000
    p95 = _percentile(samples, 0.95) * 1000
    print(f"\n/api/search over {len(samples)} calls: p50={p50:.1f}ms p95={p95:.1f}ms "
          f"(product target p95 < 150ms)")
    assert p95 < 1000, f"search p95 blew even the generous CI margin: {p95:.1f}ms"


def test_ask_cache_hit_is_fast(client):
    first = client.post("/api/ask", json={"question": CACHED_QUESTION}).json()
    assert first["columns"], "the question answers on the demo world"
    samples: list[float] = []
    for _ in range(10):
        t0 = time.perf_counter()
        again = client.post("/api/ask", json={"question": CACHED_QUESTION})
        samples.append(time.perf_counter() - t0)
        assert again.json()["cached"] is True
    p95 = _percentile(samples, 0.95) * 1000
    print(f"\n/api/ask cache hit over {len(samples)} calls: p95={p95:.1f}ms "
          f"(product target < 50ms)")
    assert p95 < 1000, f"cache hit blew even the generous CI margin: {p95:.1f}ms"
