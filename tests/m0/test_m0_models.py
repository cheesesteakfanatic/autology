"""ModelClient adapters: heuristic dispatch, cassette record/replay, Anthropic
construction gate (§11.1 T3 access, §18.4 item 4). ZERO network access here:
the 'inner' below is a stand-in for a live API client — exactly the thing the
cassette exists to keep out of CI — and AnthropicAdapter.propose is never called.
"""

import json

import pytest

from ontoforge.contracts.models import ModelRequest, ModelResponse
from ontoforge.ledger import AnthropicAdapter, CassetteAdapter, HeuristicAdapter


# ---------------------------------------------------------------- heuristic


def test_heuristic_dispatch_and_zero_tokens():
    adapter = HeuristicAdapter(
        {
            "strata.name_concept": lambda req: req.prompt.split()[0].upper(),
            "er.explain": lambda req: {"verdict": "match", "why": len(req.prompt)},
        }
    )
    r1 = adapter.propose(ModelRequest(task="strata.name_concept", prompt="airline carrier code"))
    assert r1.text == "AIRLINE"
    assert r1.total_tokens == 0
    assert r1.model_id == "heuristic"
    r2 = adapter.propose(ModelRequest(task="er.explain", prompt="ab"))
    assert r2.parsed == {"verdict": "match", "why": 2}
    assert json.loads(r2.text) == r2.parsed


def test_heuristic_unknown_task_raises_keyerror():
    adapter = HeuristicAdapter({})
    with pytest.raises(KeyError):
        adapter.propose(ModelRequest(task="nope", prompt="x"))


def test_heuristic_parses_json_string_when_schema_given():
    adapter = HeuristicAdapter({"t": lambda req: '{"a": 1}'})
    resp = adapter.propose(ModelRequest(task="t", prompt="p", schema='{"type":"object"}'))
    assert resp.parsed == {"a": 1}
    resp2 = adapter.propose(ModelRequest(task="t", prompt="p"))  # no schema -> no parse
    assert resp2.parsed is None


# ----------------------------------------------------------------- cassette


class CountingInner:
    """Deterministic stand-in for a live API client (the thing cassettes wrap)."""

    def __init__(self):
        self.calls = 0

    def propose(self, req: ModelRequest) -> ModelResponse:
        self.calls += 1
        return ModelResponse(
            text=f"answer::{req.task}::{req.prompt}",
            parsed={"echo": req.prompt} if req.schema else None,
            input_tokens=11,
            output_tokens=7,
            model_id="fake-frontier-1",
        )


def test_cassette_record_then_replay_byte_identical(tmp_path):
    path = str(tmp_path / "cassette.json")
    inner = CountingInner()
    req = ModelRequest(task="qi.select", prompt="revenue by region", schema='{"type":"object"}')

    recorder = CassetteAdapter(path, inner=inner, mode="record")
    recorded = recorder.propose(req)
    assert inner.calls == 1

    replayer = CassetteAdapter(path, mode="replay")  # no inner: replay only
    replayed = replayer.propose(req)
    assert replayed.text.encode("utf-8") == recorded.text.encode("utf-8")
    assert replayed.parsed == recorded.parsed
    assert replayed.input_tokens == recorded.input_tokens
    assert replayed.output_tokens == recorded.output_tokens
    assert replayed.model_id == recorded.model_id
    assert replayed.cached is True
    assert inner.calls == 1  # replay made no live call


def test_cassette_replay_miss_without_inner_raises(tmp_path):
    path = str(tmp_path / "cassette.json")
    inner = CountingInner()
    CassetteAdapter(path, inner=inner, mode="record").propose(
        ModelRequest(task="a", prompt="recorded")
    )
    replayer = CassetteAdapter(path, mode="replay")
    with pytest.raises(KeyError):
        replayer.propose(ModelRequest(task="a", prompt="NEVER recorded"))
    # key includes task and schema, not just prompt
    with pytest.raises(KeyError):
        replayer.propose(ModelRequest(task="b", prompt="recorded"))
    with pytest.raises(KeyError):
        replayer.propose(ModelRequest(task="a", prompt="recorded", schema="{}"))


def test_cassette_replay_with_inner_records_on_miss(tmp_path):
    path = str(tmp_path / "cassette.json")
    inner = CountingInner()
    adapter = CassetteAdapter(path, inner=inner, mode="replay")
    req = ModelRequest(task="x", prompt="first time")
    adapter.propose(req)
    assert inner.calls == 1
    adapter.propose(req)  # now a hit: served from cassette
    assert inner.calls == 1
    fresh = CassetteAdapter(path, mode="replay")  # persisted across instances
    assert fresh.propose(req).cached is True


def test_cassette_record_mode_requires_inner(tmp_path):
    with pytest.raises(ValueError):
        CassetteAdapter(str(tmp_path / "c.json"), mode="record")
    with pytest.raises(ValueError):
        CassetteAdapter(str(tmp_path / "c.json"), mode="banana")


# ---------------------------------------------------------------- anthropic


def test_anthropic_adapter_requires_api_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        AnthropicAdapter()


def test_anthropic_adapter_constructs_with_key_but_is_never_called(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    adapter = AnthropicAdapter()  # construction does no network I/O
    assert adapter.model_id == "claude-sonnet-4-6"
    # propose() is deliberately NOT called: zero network access in tests.
