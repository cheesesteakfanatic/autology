"""UsageLog / UsageEvent behavior: deterministic seqs, watermark, since(), kinds."""

from __future__ import annotations

import pytest

from ontoforge.criticality import USAGE_KINDS, UsageEvent, UsageLog


def test_append_assigns_strictly_increasing_seq_from_one() -> None:
    log = UsageLog()
    e1 = log.append("a", "query")
    e2 = log.append("b", "join")
    e3 = log.append("a", "answer")
    assert (e1.seq, e2.seq, e3.seq) == (1, 2, 3)
    assert [e.seq for e in log.events] == [1, 2, 3]


def test_append_returns_event_with_fields() -> None:
    log = UsageLog()
    e = log.append("x", "materialize", weight=2.5)
    assert isinstance(e, UsageEvent)
    assert e.element_uri == "x"
    assert e.kind == "materialize"
    assert e.weight == 2.5
    assert e.seq == 1


def test_default_weight_is_one() -> None:
    log = UsageLog()
    e = log.append("x", "query")
    assert e.weight == 1.0


def test_max_seq_empty_is_zero() -> None:
    assert UsageLog().max_seq == 0


def test_max_seq_tracks_appends() -> None:
    log = UsageLog()
    assert log.max_seq == 0
    log.append("a", "query")
    assert log.max_seq == 1
    log.append("a", "query")
    assert log.max_seq == 2


def test_since_returns_events_strictly_after_watermark() -> None:
    log = UsageLog()
    for uri in ["a", "b", "c", "d"]:
        log.append(uri, "query")
    tail = log.since(2)
    assert [e.seq for e in tail] == [3, 4]
    assert [e.element_uri for e in tail] == ["c", "d"]


def test_since_zero_or_negative_returns_all() -> None:
    log = UsageLog()
    log.append("a", "query")
    log.append("b", "query")
    assert [e.seq for e in log.since(0)] == [1, 2]
    assert [e.seq for e in log.since(-5)] == [1, 2]


def test_since_at_or_past_max_returns_empty() -> None:
    log = UsageLog()
    log.append("a", "query")
    log.append("b", "query")
    assert log.since(2) == []
    assert log.since(99) == []


@pytest.mark.parametrize("kind", sorted(USAGE_KINDS))
def test_all_known_kinds_accepted(kind: str) -> None:
    log = UsageLog()
    e = log.append("u", kind)
    assert e.kind == kind


def test_unknown_kind_rejected() -> None:
    log = UsageLog()
    with pytest.raises(ValueError):
        log.append("u", "delete")


def test_usage_event_is_frozen() -> None:
    e = UsageEvent("u", "query", 1.0, 1)
    with pytest.raises(Exception):
        e.seq = 9  # type: ignore[misc]
