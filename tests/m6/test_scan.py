"""Scan correctness at small scale (§4.4, AMD-0001 fixture scale):

* 1000 synthetic entities with supersessions, corrections, rank-2 losers;
* scan() (in-memory fast path) row-equals read() for EVERY entity;
* the DuckDB SQL scan over canonical Parquet equals the pyarrow scan under
  every stance kind — the open-format layer and the serving layer agree;
* equality filters; current-value point reads via the O(1) dict.
"""

from __future__ import annotations

import random

import pyarrow as pa
import pytest

from m6_helpers import mint_prov, vc

from ontoforge.contracts import FOREVER, Layer, Stance
from ontoforge.hearth import Hearth
from ontoforge.ledger import SqliteLedger

CLASS = "onto://test/Synthetic"
N = 1000
T0 = 1_000_000  # base system time
SEED = 4242


@pytest.fixture(scope="module")
def loaded(tmp_path_factory):
    """1000 entities; then 100 same-rank supersessions, 60 rank-2 losers,
    40 retroactive window corrections. Deterministic (fixed seed)."""
    rng = random.Random(SEED)
    ledger = SqliteLedger()
    h = Hearth(tmp_path_factory.mktemp("scan") / "h", ledger)
    prov = mint_prov(ledger, "scan-base")
    cells = []
    for i in range(N):
        e = f"e://syn/{i}"
        cells.append(vc(e, "name", f"entity-{i}", prov, valid_from=10))
        cells.append(vc(e, "score", round(rng.uniform(0, 100), 3), prov, valid_from=10))
        cells.append(vc(e, "count", rng.randrange(10), prov, valid_from=10))
        cells.append(vc(e, "active", bool(i % 2), prov, valid_from=10))
    h.commit(Layer.ENTITY, CLASS, cells, now=T0)

    p2 = mint_prov(ledger, "scan-update")
    updates = [
        vc(f"e://syn/{i}", "score", round(rng.uniform(0, 100), 3), p2, valid_from=10)
        for i in rng.sample(range(N), 100)
    ]
    h.commit(Layer.ENTITY, CLASS, updates, now=T0 + 1000)

    p3 = mint_prov(ledger, "scan-loser")
    losers = [
        vc(f"e://syn/{i}", "count", 99, p3, valid_from=10, rank=2)
        for i in rng.sample(range(N), 60)
    ]
    h.commit(Layer.ENTITY, CLASS, losers, now=T0 + 2000)

    p4 = mint_prov(ledger, "scan-correction")
    corrections = [
        # retroactive: active was the OPPOSITE during world-time [20, 30)
        vc(f"e://syn/{i}", "active", i % 2 == 0, p4, valid_from=20, valid_to=30)
        for i in rng.sample(range(N), 40)
    ]
    h.commit(Layer.ENTITY, CLASS, corrections, now=T0 + 3000)
    return h


STANCES = [
    Stance(),
    Stance("as_of", valid_at=25),
    Stance("as_of", valid_at=5),
    Stance("as_known_at", known_at=T0 + 1500),
    Stance("audit", valid_at=25, known_at=T0 + 2500),
    Stance("audit", valid_at=25, known_at=T0 + 3500),
]


def _rows(table: pa.Table) -> dict[str, dict]:
    return {
        r["entity_uri"]: {k: v for k, v in r.items() if k != "entity_uri" and v is not None}
        for r in table.to_pylist()
    }


def test_scan_matches_read_on_all_entities(loaded: Hearth) -> None:
    table = loaded.scan(CLASS)
    assert table.num_rows == N
    rows = _rows(table)
    for i in range(N):
        e = f"e://syn/{i}"
        assert rows[e] == loaded.read(e), e


@pytest.mark.parametrize("stance", STANCES, ids=lambda s: f"{s.kind}-{s.valid_at}-{s.known_at}")
def test_duckdb_scan_equals_pyarrow_scan(loaded: Hearth, stance: Stance) -> None:
    """The SQL path over the canonical Parquet (stance predicate + window
    survivorship) must produce EXACTLY the fast-path scan, every stance."""
    mem = _rows(loaded.scan(CLASS, stance))
    sql = _rows(loaded.scan_duckdb(CLASS, stance))
    assert sql == mem


def test_stanced_scan_matches_stanced_read(loaded: Hearth) -> None:
    stance = Stance("as_of", valid_at=25)
    rows = _rows(loaded.scan(CLASS, stance))
    rng = random.Random(7)
    for i in rng.sample(range(N), 100):
        e = f"e://syn/{i}"
        assert rows.get(e, {}) == loaded.read(e, stance), e


def test_scan_filters(loaded: Hearth) -> None:
    table = loaded.scan(CLASS, filters={"count": 3})
    rows = _rows(table)
    assert rows  # seed guarantees non-empty
    for e, props in rows.items():
        assert props["count"] == 3 and loaded.read(e)["count"] == 3
    # cross-check cardinality against the unfiltered scan
    full = _rows(loaded.scan(CLASS))
    assert len(rows) == sum(1 for p in full.values() if p.get("count") == 3)
    # filters compose
    both = _rows(loaded.scan(CLASS, filters={"count": 3, "active": True}))
    assert len(both) == sum(1 for p in full.values() if p.get("count") == 3 and p.get("active") is True)
    # duckdb path honours filters identically
    assert _rows(loaded.scan_duckdb(CLASS, Stance(), filters={"count": 3})) == rows


def test_rank2_losers_never_visible_current(loaded: Hearth) -> None:
    full = _rows(loaded.scan(CLASS))
    assert all(p["count"] != 99 for p in full.values())
    # but they exist in history (append-only audit trail)
    doa = [
        c
        for i in range(N)
        for c in loaded.history(f"e://syn/{i}", "count")
        if c.value == 99
    ]
    assert len(doa) == 60 and all(not c.system.open for c in doa)


def test_point_read_fast_path(loaded: Hearth) -> None:
    """O(1) dict point reads agree with read(); measure latency at fixture
    scale (AMD-0001 rescale of the §4.4 p99 target)."""
    import time

    rng = random.Random(11)
    sample = [f"e://syn/{i}" for i in rng.sample(range(N), 200)]
    for e in sample:
        assert loaded.current_value(CLASS, e, "name") == loaded.read(e)["name"]
    t0 = time.perf_counter()
    for e in sample:
        loaded.current_value(CLASS, e, "score")
    per_read_us = (time.perf_counter() - t0) / len(sample) * 1e6
    # generous fixture-scale bound (measured ~1-3 us): well under 10 ms p99
    assert per_read_us < 10_000, f"point read too slow: {per_read_us:.1f} us"


def test_corrections_visible_only_in_window(loaded: Hearth) -> None:
    """The 40 corrected entities answer differently at as_of(25) vs current."""
    cur = _rows(loaded.scan(CLASS))
    asof = _rows(loaded.scan(CLASS, Stance("as_of", valid_at=25)))
    changed = {e for e in cur if cur[e].get("active") != asof.get(e, {}).get("active")}
    # the correction always flips active, so exactly the 40 corrected entities differ
    assert len(changed) == 40
    for e in changed:
        assert asof[e]["active"] == (not cur[e]["active"])
    # and OUTSIDE the corrected window nothing differs
    asof_outside = _rows(loaded.scan(CLASS, Stance("as_of", valid_at=35)))
    assert all(asof_outside[e].get("active") == cur[e].get("active") for e in cur)


def test_empty_scan_unknown_class(loaded: Hearth) -> None:
    t = loaded.scan("onto://test/DoesNotExist")
    assert t.num_rows == 0
    t2 = loaded.scan_duckdb("onto://test/DoesNotExist", Stance())
    assert t2.num_rows == 0


def test_scan_far_past_valid_time_is_empty(loaded: Hearth) -> None:
    assert loaded.scan(CLASS, Stance("as_of", valid_at=5)).num_rows == 0
    assert loaded.scan(CLASS, Stance("as_known_at", known_at=FOREVER - 1)).num_rows == N
