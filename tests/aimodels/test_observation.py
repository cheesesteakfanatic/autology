"""ObservationLog + make_observer: stable fingerprints, monotonic seq, correct
aggregation, and ADDITIVE router wiring (observer=None == prior behavior)."""

from __future__ import annotations

from ontoforge.aimodels.library import PromptLibrary
from ontoforge.aimodels.observation import (
    FIRE,
    Observation,
    ObservationLog,
    fingerprint_prompt,
)
from ontoforge.aimodels.router import default_router, make_observer
from ontoforge.contracts.models import ModelRequest, ModelResponse
from ontoforge.ledger.models import HeuristicAdapter


def test_equal_prompts_produce_equal_fingerprints() -> None:
    a = fingerprint_prompt("link orders to customers")
    b = fingerprint_prompt("link orders to customers")
    c = fingerprint_prompt("link orders to suppliers")
    assert a == b
    assert a != c
    # 16 hex chars
    assert len(a) == 16
    assert all(ch in "0123456789abcdef" for ch in a)


def test_seq_is_monotonic() -> None:
    log = ObservationLog()
    for i in range(5):
        obs = log.append("join", "1", f"fp{i}", "heuristic", "deterministic", "fire", 0.5)
        assert obs.seq == i
    assert [o.seq for o in log.records] == [0, 1, 2, 3, 4]


def test_observation_is_frozen() -> None:
    obs = Observation("join", "1", "fp", "heuristic", "deterministic", "fire", 0.5, 0)
    import dataclasses

    try:
        obs.confidence = 0.9  # type: ignore[misc]
    except dataclasses.FrozenInstanceError:
        pass
    else:  # pragma: no cover - guards the invariant
        raise AssertionError("Observation must be frozen")


def test_summarize_aggregates_correctly() -> None:
    log = ObservationLog()
    # task "join", version "a": 3 obs, 2 fire, confidences 0.2/0.4/0.6 -> mean 0.4
    log.append("join", "a", "f0", "heuristic", "deterministic", FIRE, 0.2)
    log.append("join", "a", "f1", "heuristic", "deterministic", FIRE, 0.4)
    log.append("join", "a", "f2", "heuristic", "deterministic", "hold", 0.6)
    # task "join", version "b": 1 obs, 0 fire
    log.append("join", "b", "f3", "heuristic", "deterministic", "hold", 1.0)
    # an unrelated task that must not leak in
    log.append("merge", "a", "f4", "heuristic", "deterministic", FIRE, 0.9)

    summary = log.summarize("join")
    assert set(summary) == {"a", "b"}
    assert summary["a"]["count"] == 3
    assert summary["a"]["mean_confidence"] == (0.2 + 0.4 + 0.6) / 3
    assert summary["a"]["fire_rate"] == 2 / 3
    assert summary["b"]["count"] == 1
    assert summary["b"]["mean_confidence"] == 1.0
    assert summary["b"]["fire_rate"] == 0.0
    # unknown task -> empty summary
    assert log.summarize("nope") == {}


def _decision_handlers():
    """Heuristic handlers returning a structured decision dict (becomes parsed)."""

    def _join(req: ModelRequest):
        return {"decision": "fire", "confidence": 0.83, "rationale": "key overlap"}

    def _merge(req: ModelRequest):
        return {"decision": "hold", "confidence": 0.41, "rationale": "low margin"}

    return {"join": _join, "merge": _merge}


def test_router_with_make_observer_records_exactly_one_per_complete() -> None:
    library = PromptLibrary()
    log = ObservationLog()
    observer = make_observer(log, library)
    router = default_router(handlers=_decision_handlers(), observer=observer)

    prompt = "link orders.customer_id to customers.id; overlap 0.98"
    resp = router.complete("join", prompt, schema="{}")

    # the response is unchanged by observation
    assert resp.parsed == {"decision": "fire", "confidence": 0.83, "rationale": "key overlap"}

    # exactly one observation recorded
    assert len(log.records) == 1
    obs = log.records[0]
    assert obs.task == "join"
    # champion version came from the library (seeded from PROMPTS -> "1")
    assert obs.version == library.champion("join")
    # decision/confidence extracted from the real parsed field
    assert obs.decision == "fire"
    assert obs.confidence == 0.83
    assert obs.model_id == "heuristic"
    assert obs.tier == "deterministic"
    # fingerprint matches the prompt that was sent
    assert obs.input_fingerprint == fingerprint_prompt(prompt)

    # a second complete() appends exactly one more
    router.complete("merge", "merge dup suppliers", schema="{}")
    assert len(log.records) == 2
    assert log.records[1].task == "merge"
    assert log.records[1].decision == "hold"
    assert log.records[1].confidence == 0.41


def test_observer_none_records_nothing_and_matches_prior_behavior() -> None:
    """A router built with observer=None (the default) behaves exactly as before:
    no Observation is recorded, and the response is identical to a plain router."""
    log = ObservationLog()
    plain = default_router(handlers=_decision_handlers())  # observer defaults to None
    resp = plain.complete("join", "link a to b", schema="{}")
    assert resp.parsed == {"decision": "fire", "confidence": 0.83, "rationale": "key overlap"}
    assert log.records == []  # nothing recorded; observer was never built/called


def test_make_observer_defensive_on_non_decision_response() -> None:
    """A response with no structured decision (e.g. plain text / no parsed dict)
    records decision='' and confidence=0.0 rather than raising."""
    library = PromptLibrary()
    log = ObservationLog()
    observer = make_observer(log, library)

    # name_concept echo handler returns a plain string -> parsed stays None
    router = default_router(observer=observer)
    router.complete("name_concept", "class with cols a,b,c")
    assert len(log.records) == 1
    obs = log.records[0]
    assert obs.decision == ""
    assert obs.confidence == 0.0
    assert obs.task == "name_concept"


def test_make_observer_unknown_task_records_empty_version() -> None:
    """If the library does not know the task, the observer records version=''
    rather than raising (keyless, never blows up the propose path)."""
    from ontoforge.aimodels.router import ModelRouter, ModelSpec

    library = PromptLibrary(seed=False)  # knows no tasks
    log = ObservationLog()
    observer = make_observer(log, library)

    adapter = HeuristicAdapter({"exotic": lambda req: ModelResponse(text="x", parsed=None)})
    router = ModelRouter(observer=observer)
    router.register_model("exotic", ModelSpec(factory=lambda: adapter, model_id="heuristic"))

    router.complete("exotic", "payload")
    assert len(log.records) == 1
    assert log.records[0].version == ""
    assert log.records[0].task == "exotic"
