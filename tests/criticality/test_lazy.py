"""Laziness proofs: an update recomputes ONLY touched elements + their neighbors,
never the whole graph, and a no-op update recomputes nothing.
"""

from __future__ import annotations

from ontoforge.criticality import CriticalityModel, UsageLog


def _large_graph(n: int = 200) -> dict[str, list[str]]:
    """A large graph: each node connected only to its index-neighbors so a
    single node's adjacency is tiny relative to the node count.
    """
    adjacency: dict[str, list[str]] = {}
    for i in range(n):
        neighbors = []
        if i > 0:
            neighbors.append(f"u{i - 1}")
        if i < n - 1:
            neighbors.append(f"u{i + 1}")
        adjacency[f"u{i}"] = neighbors
    return adjacency


def test_update_recomputes_only_touched_plus_neighbors() -> None:
    adjacency = _large_graph(200)
    model = CriticalityModel(adjacency)
    log = UsageLog()
    # Touch exactly one element in the middle of the chain.
    log.append("u100", "query")
    recomputed = model.update(log)
    # u100 has neighbors u99 and u101.
    assert recomputed == {"u100", "u99", "u101"}
    assert model.last_recomputed() == {"u100", "u99", "u101"}
    # The dirty set is strictly smaller than the full node set.
    assert len(recomputed) < len(model.nodes)
    assert len(recomputed) == 3
    # Only those three carry scores; everything else is untouched (0.0).
    assert model.score("u100") > 0.0
    assert model.score("u50") == 0.0
    assert model.is_dirty("u100") is True
    assert model.is_dirty("u50") is False


def test_isolated_node_recomputes_only_itself() -> None:
    model = CriticalityModel({"a": [], "b": [], "c": []})
    log = UsageLog()
    log.append("a", "query")
    recomputed = model.update(log)
    assert recomputed == {"a"}


def test_second_update_touching_nothing_recomputes_nothing() -> None:
    adjacency = _large_graph(50)
    model = CriticalityModel(adjacency)
    log = UsageLog()
    log.append("u10", "query")
    first = model.update(log)
    assert first == {"u10", "u9", "u11"}
    # No new events appended -> the watermark already covers everything.
    second = model.update(log)
    assert second == set()
    assert model.last_recomputed() == set()
    # Prior scores survive a no-op update.
    assert model.score("u10") > 0.0


def test_watermark_advances_to_log_max_seq() -> None:
    model = CriticalityModel(_large_graph(20))
    log = UsageLog()
    log.append("u5", "query")
    log.append("u6", "query")
    assert model.watermark == 0
    model.update(log)
    assert model.watermark == log.max_seq == 2


def test_incremental_updates_only_touch_new_tail() -> None:
    model = CriticalityModel(_large_graph(100))
    log = UsageLog()
    log.append("u10", "query")
    model.update(log)
    # Append a new, far-away event; the next update must recompute only the new
    # element + its neighbors, NOT u10's region again.
    log.append("u80", "query")
    recomputed = model.update(log)
    assert recomputed == {"u80", "u79", "u81"}
    assert "u10" not in recomputed
    # Both regions retain scores.
    assert model.score("u10") > 0.0
    assert model.score("u80") > 0.0
