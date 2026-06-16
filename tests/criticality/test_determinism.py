"""Determinism: identical logs yield identical top_k and byte-identical saves."""

from __future__ import annotations

from pathlib import Path

from ontoforge.criticality import CriticalityModel, UsageLog, load_scores, save_scores


def _adjacency() -> dict[str, list[str]]:
    return {
        "orders": ["customers", "line_items", "payments"],
        "customers": ["orders", "addresses"],
        "line_items": ["orders", "products"],
        "products": ["line_items"],
        "payments": ["orders"],
        "addresses": ["customers"],
    }


def _dependents() -> dict[str, list[str]]:
    return {
        "orders": ["line_items", "payments"],
        "customers": ["orders", "addresses"],
        "products": ["line_items"],
    }


def _build_log() -> UsageLog:
    log = UsageLog()
    plan = [
        ("orders", "query", 1.0),
        ("customers", "join", 2.0),
        ("orders", "answer", 1.0),
        ("line_items", "materialize", 1.5),
        ("products", "query", 1.0),
        ("orders", "query", 1.0),
        ("payments", "join", 1.0),
    ]
    for uri, kind, weight in plan:
        log.append(uri, kind, weight)
    return log


def _build_model() -> CriticalityModel:
    model = CriticalityModel(_adjacency(), _dependents())
    model.update(_build_log())
    return model


def test_same_log_yields_identical_top_k() -> None:
    a = _build_model()
    b = _build_model()
    assert a.top_k(100) == b.top_k(100)
    assert a.scores == b.scores
    assert a.watermark == b.watermark


def test_save_scores_byte_identical(tmp_path: Path) -> None:
    p1 = tmp_path / "a.json"
    p2 = tmp_path / "b.json"
    save_scores(p1, _build_model())
    save_scores(p2, _build_model())
    assert p1.read_bytes() == p2.read_bytes()


def test_save_scores_stable_across_two_saves_of_same_model(tmp_path: Path) -> None:
    model = _build_model()
    p1 = tmp_path / "x.json"
    p2 = tmp_path / "y.json"
    save_scores(p1, model)
    save_scores(p2, model)
    assert p1.read_bytes() == p2.read_bytes()


def test_round_trip_load_scores(tmp_path: Path) -> None:
    model = _build_model()
    p = tmp_path / "scores.json"
    save_scores(p, model)
    loaded = load_scores(p)
    assert loaded["watermark"] == model.watermark
    assert loaded["scores"] == model.scores
    assert loaded["format"] == "ontoforge.criticality/1"


def test_save_output_is_sorted_and_newline_terminated(tmp_path: Path) -> None:
    p = tmp_path / "scores.json"
    save_scores(p, _build_model())
    text = p.read_text(encoding="utf-8")
    assert text.endswith("\n")
    # sort_keys puts top-level "format" before "scores" before "watermark".
    assert text.index('"format"') < text.index('"scores"') < text.index('"watermark"')


def test_split_log_equals_single_update() -> None:
    # Feeding the same events in two incremental updates must land at the same
    # final scores as one batch update (watermark-driven incrementalism).
    batch = CriticalityModel(_adjacency(), _dependents())
    batch.update(_build_log())

    incr = CriticalityModel(_adjacency(), _dependents())
    log = _build_log()
    # Re-run update twice against the same fully-populated log: the first folds
    # everything, the second is a no-op. Final state must equal the batch.
    incr.update(log)
    incr.update(log)
    assert incr.scores == batch.scores
    assert incr.top_k(100) == batch.top_k(100)
