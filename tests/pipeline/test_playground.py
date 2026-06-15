"""Playground live-build tests: event ordering, determinism, caps, world shape.

Fast: tiny synthetic tables, the build run INLINE (``run_sync``) — no thread, no
polling indirection — so the whole module runs in well under a second and adds
no wall-clock risk to the 1109-suite. Zero network, deterministic seeds.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from ontoforge.pipeline.playground import (
    MAX_DATASETS,
    PlaygroundBuildError,
    PlaygroundJob,
    build_playground,
)
from ontoforge.pipeline.playground_events import JobEventLog


def _write(tmp: Path) -> list[tuple[str, str, Path]]:
    """Two joinable tables: salelines.sku ⊆ catalog.sku (full coverage)."""
    src = tmp / "src"
    src.mkdir()
    pd.DataFrame(
        {"sku": ["s1", "s2", "s3"], "pname": ["Widget", "Gadget", "Gizmo"], "country": ["US", "UK", "US"]}
    ).to_csv(src / "catalog.csv", index=False)
    pd.DataFrame(
        {"line_id": ["l1", "l2", "l3", "l4"], "sku": ["s1", "s2", "s1", "s3"], "qty": ["1", "2", "3", "4"]}
    ).to_csv(src / "salelines.csv", index=False)
    return [
        ("wild:catalog", "catalog", src / "catalog.csv"),
        ("wild:salelines", "salelines", src / "salelines.csv"),
    ]


def test_build_completes_and_emits_the_discovery_narrative(tmp_path: Path) -> None:
    sels = _write(tmp_path)
    job = PlaygroundJob(job_id="j1", selections=sels, project_dir=tmp_path / "play")
    res = job.run_sync()

    snap = job.snapshot()
    assert snap["status"] == "done"
    assert snap["stage"] == "done"
    kinds = {e["kind"] for e in snap["events"]}
    # the contract's four streamed kinds (silo may be absent when all join)
    assert "stage" in kinds
    assert "join_found" in kinds
    assert "type_found" in kinds

    # the world is a real, readable project
    play = tmp_path / "play"
    assert (play / "atlas.json").is_file()
    assert (play / "config.json").is_file()
    assert (play / "ontology.materialized.json").is_file()
    assert res["stats"]["types"] >= 2
    # the sku overlap surfaces as a tiered arc (confirmed or likely on tiny data)
    atlas_stats = res["atlas"]["stats"]
    assert atlas_stats["confirmed"] + atlas_stats["likely"] >= 1


def test_join_events_precede_profiling_and_type_events(tmp_path: Path) -> None:
    """Joins animate FIRST: the early IND pass over RAW tables emits join_found
    events BEFORE the profiling stage and any type_found events."""
    sels = _write(tmp_path)
    log = JobEventLog()
    build_playground(sels, tmp_path / "play", log)
    events = log.snapshot()

    seqs = {e["seq"]: e for e in events}
    first_join = min((e["seq"] for e in events if e["kind"] == "join_found"), default=None)
    first_type = min((e["seq"] for e in events if e["kind"] == "type_found"), default=None)
    profiling = next((e["seq"] for e in events if e.get("stage") == "profiling"), None)
    assert first_join is not None
    assert profiling is not None
    # joins discovered before profiling begins; types only after induction
    assert first_join < profiling
    if first_type is not None:
        assert first_join < first_type


def test_join_event_payload_carries_columns_and_coverage(tmp_path: Path) -> None:
    sels = _write(tmp_path)
    log = JobEventLog()
    build_playground(sels, tmp_path / "play", log)
    joins = [e for e in log.snapshot() if e["kind"] == "join_found"]
    assert joins
    j = joins[0]
    for field in ("lhs_table", "lhs_col", "rhs_table", "rhs_col", "coverage", "tier"):
        assert field in j
    assert 0.0 <= j["coverage"] <= 1.0


def test_event_sequence_is_deterministic(tmp_path: Path) -> None:
    """The same selection builds the same event sequence (fixed seeds)."""
    sels = _write(tmp_path)
    log_a = JobEventLog()
    build_playground(sels, tmp_path / "a", log_a)
    log_b = JobEventLog()
    build_playground(sels, tmp_path / "b", log_b)
    a = [(e["kind"], e["msg"]) for e in log_a.snapshot()]
    b = [(e["kind"], e["msg"]) for e in log_b.snapshot()]
    assert a == b


def test_empty_selection_rejected(tmp_path: Path) -> None:
    log = JobEventLog()
    with pytest.raises(PlaygroundBuildError):
        build_playground([], tmp_path / "play", log)


def test_over_cap_selection_rejected(tmp_path: Path) -> None:
    # MAX_DATASETS+1 fake selections — rejected before any file is touched
    sels = [("id", "n", Path("/nonexistent.csv"))] * (MAX_DATASETS + 1)
    log = JobEventLog()
    with pytest.raises(PlaygroundBuildError):
        build_playground(sels, tmp_path / "play", log)


def test_event_log_monotonic_and_since(tmp_path: Path) -> None:
    log = JobEventLog()
    e1 = log.emit("stage", "one", stage="loading")
    e2 = log.emit("join_found", "two", coverage=1.0)
    assert e1.seq == 1 and e2.seq == 2
    assert [e["seq"] for e in log.since(1)] == [2]
    assert log.last_seq == 2
