"""ModelRouter: task routing, keyless default, and EXPLICIT priority-ordered
fallback (the claim that libraries give fallback free was refuted — we test it)."""

from __future__ import annotations

import pytest

from ontoforge.aimodels.router import (
    ModelRouter,
    ModelSpec,
    RouterExhausted,
    default_router,
)
from ontoforge.contracts.models import ModelRequest, ModelResponse
from ontoforge.ledger.models import HeuristicAdapter


def _adapter(task_to_text):
    return HeuristicAdapter({k: (lambda req, t=v: t) for k, v in task_to_text.items()})


def test_routes_task_to_its_spec() -> None:
    router = ModelRouter()
    router.register_model(
        "join", ModelSpec(factory=lambda: _adapter({"join": "JOINED"}), model_id="h")
    )
    resp = router.complete("join", "link a to b")
    assert resp.text == "JOINED"
    assert resp.model_id == "heuristic"


def test_unregistered_task_raises_keyerror() -> None:
    router = ModelRouter()
    with pytest.raises(KeyError):
        router.complete("nope", "x")


def test_default_router_is_keyless_and_covers_de_tasks() -> None:
    router = default_router()
    for task in ("join", "merge", "retype", "name_concept", "answer"):
        assert router.has_task(task)
        # the echo handler returns the prompt deterministically; no key, no network
        assert router.complete(task, "hello").text == "hello"


class _Boom:
    """An adapter whose propose() always raises — stands in for an unavailable
    (e.g. keyless-but-key-required) provider."""

    def propose(self, req: ModelRequest) -> ModelResponse:  # noqa: D401
        raise RuntimeError("primary unavailable")


def test_explicit_priority_ordered_fallback() -> None:
    """A failing PRIMARY spec falls through to the next spec in priority order —
    asserted explicitly (this is implemented, not assumed from a library)."""
    router = ModelRouter()
    router.register_model(
        "join",
        ModelSpec(factory=lambda: _Boom(), model_id="frontier", tier="frontier", priority=0),
    )
    router.register_model(
        "join",
        ModelSpec(
            factory=lambda: _adapter({"join": "FALLBACK"}),
            model_id="det",
            tier="deterministic",
            priority=1,
        ),
    )
    # primary (priority 0) raises -> router falls to the deterministic spec
    resp = router.complete("join", "link a to b")
    assert resp.text == "FALLBACK"
    # chain is ordered by priority
    assert [s.priority for s in router.specs_for("join")] == [0, 1]


def test_factory_that_cannot_construct_is_skipped() -> None:
    """A spec whose FACTORY raises (e.g. AnthropicAdapter with no key) is skipped,
    not fatal — the lazy factory keeps registration key-free."""

    def _no_key_factory():
        raise RuntimeError("ANTHROPIC_API_KEY required")

    router = ModelRouter()
    router.register_model("answer", ModelSpec(factory=_no_key_factory, priority=0))
    router.register_model(
        "answer",
        ModelSpec(factory=lambda: _adapter({"answer": "OK"}), priority=1),
    )
    assert router.complete("answer", "q").text == "OK"


def test_exhausted_chain_raises_router_exhausted() -> None:
    router = ModelRouter()
    router.register_model("join", ModelSpec(factory=lambda: _Boom(), priority=0))
    router.register_model("join", ModelSpec(factory=lambda: _Boom(), priority=1))
    with pytest.raises(RouterExhausted):
        router.complete("join", "x")


def test_register_model_adds_live_spec_without_key() -> None:
    """Registering a (would-be live) spec requires no key at registration; the
    lazy factory is only invoked on use. Adding a live model is just this call."""
    router = default_router()
    called = {"n": 0}

    def _live_factory():
        called["n"] += 1
        return _adapter({"join": "LIVE"})

    # higher-priority live spec registered AFTER the keyless default
    router.register_model("join", ModelSpec(factory=_live_factory, model_id="kimi", tier="frontier", priority=-1))
    assert called["n"] == 0  # not constructed at registration
    assert router.complete("join", "x").text == "LIVE"
    assert called["n"] == 1
