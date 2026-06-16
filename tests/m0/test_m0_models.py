"""ModelClient adapters: heuristic dispatch, cassette record/replay, the Anthropic
and OpenAI-compatible live-adapter construction gates + mock-transport behavior
(§11.1 T3 access, §18.4 item 4). ZERO network access here: the 'inner' below is a
stand-in for a live API client — exactly the thing the cassette exists to keep out
of CI — and the live adapters' transport is monkeypatched to a canned body; no real
endpoint is ever contacted, and no test touches the wall clock.
"""

import io
import json
import urllib.error
import urllib.request

import pytest

from ontoforge.contracts.models import ModelRequest, ModelResponse
from ontoforge.ledger import (
    AnthropicAdapter,
    CassetteAdapter,
    HeuristicAdapter,
    OpenAICompatAdapter,
)


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
    # propose() against a REAL endpoint is deliberately NOT called; the
    # mock-transport tests below exercise propose() with zero network access.


# ----------------------------------------------------- mock urllib transport
#
# These tests NEVER hit a network: urllib.request.urlopen is monkeypatched to a
# canned response (or a transient error). They assert request shape, parsing,
# bounded retry, and the construction key-gate for both live adapters.


class _FakeHTTPResponse:
    """Minimal context-manager standing in for the object urlopen returns."""

    def __init__(self, body: dict):
        self._raw = json.dumps(body).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self._raw


def _capturing_urlopen(captured, response_body):
    """urlopen replacement that records the outgoing Request and returns a body."""

    def _urlopen(request, timeout=None):  # signature mirrors urllib.request.urlopen
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        captured["method"] = request.get_method()
        # urllib lowercases header keys via Request.add_header bookkeeping.
        captured["headers"] = {k.lower(): v for k, v in request.header_items()}
        captured["body"] = json.loads(request.data.decode("utf-8"))
        return _FakeHTTPResponse(response_body)

    return _urlopen


def _flaky_urlopen(fail_times, exc, response_body, counter):
    """Raise ``exc`` the first ``fail_times`` calls, then return a good body."""

    def _urlopen(request, timeout=None):
        counter["calls"] += 1
        if counter["calls"] <= fail_times:
            raise exc
        return _FakeHTTPResponse(response_body)

    return _urlopen


_OPENAI_OK = {
    "model": "moonshot-v1-8k",
    "choices": [{"message": {"role": "assistant", "content": '{"name": "AIRLINE"}'}}],
    "usage": {"prompt_tokens": 31, "completion_tokens": 5},
}

_ANTHROPIC_OK = {
    "model": "claude-sonnet-4-6",
    "content": [{"type": "text", "text": '{"name": "AIRLINE"}'}],
    "usage": {"input_tokens": 31, "output_tokens": 5},
}


# ------------------------------------------------ OpenAI-compatible adapter


def test_openai_compat_refuses_without_its_key_env(monkeypatch):
    monkeypatch.delenv("MOONSHOT_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="MOONSHOT_API_KEY"):
        OpenAICompatAdapter(
            base_url="https://api.moonshot.cn/v1",
            model_id="moonshot-v1-8k",
            api_key_env="MOONSHOT_API_KEY",
        )


def test_openai_compat_validates_constructor_args(monkeypatch):
    monkeypatch.setenv("K", "secret")
    with pytest.raises(ValueError):
        OpenAICompatAdapter(base_url="", model_id="m", api_key_env="K")
    with pytest.raises(ValueError):
        OpenAICompatAdapter(base_url="https://x/v1", model_id="", api_key_env="K")
    with pytest.raises(ValueError):
        OpenAICompatAdapter(base_url="https://x/v1", model_id="m", api_key_env="")


def test_openai_compat_builds_request_and_parses_response(monkeypatch):
    monkeypatch.setenv("MOONSHOT_API_KEY", "sk-secret")
    captured: dict = {}
    monkeypatch.setattr(
        urllib.request, "urlopen", _capturing_urlopen(captured, _OPENAI_OK)
    )
    adapter = OpenAICompatAdapter(
        base_url="https://api.moonshot.cn/v1/",  # trailing slash should be trimmed
        model_id="moonshot-v1-8k",
        api_key_env="MOONSHOT_API_KEY",
        sleep=lambda _s: None,
    )
    req = ModelRequest(
        task="strata.name_concept",
        prompt="airline carrier code",
        schema='{"type":"object"}',
        temperature=0.0,
        max_tokens=256,
    )
    resp = adapter.propose(req)

    # request shape
    assert captured["url"] == "https://api.moonshot.cn/v1/chat/completions"
    assert captured["method"] == "POST"
    assert captured["headers"]["authorization"] == "Bearer sk-secret"
    assert captured["headers"]["content-type"] == "application/json"
    assert captured["body"]["model"] == "moonshot-v1-8k"
    assert captured["body"]["max_tokens"] == 256
    # json-mode requested because a schema was set
    assert captured["body"]["response_format"] == {"type": "json_object"}
    # schema delivered as a system message; user prompt last
    roles = [m["role"] for m in captured["body"]["messages"]]
    assert roles == ["system", "user"]
    assert captured["body"]["messages"][-1]["content"] == "airline carrier code"

    # response parsing
    assert resp.text == '{"name": "AIRLINE"}'
    assert resp.parsed == {"name": "AIRLINE"}
    assert resp.input_tokens == 31
    assert resp.output_tokens == 5
    assert resp.model_id == "moonshot-v1-8k"


def test_openai_compat_no_schema_omits_json_mode_and_system(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-qwen")
    captured: dict = {}
    body = {
        "model": "qwen-plus",
        "choices": [{"message": {"content": "plain text answer"}}],
        "usage": {"prompt_tokens": 4, "completion_tokens": 3},
    }
    monkeypatch.setattr(urllib.request, "urlopen", _capturing_urlopen(captured, body))
    adapter = OpenAICompatAdapter(
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        model_id="qwen-plus",
        api_key_env="DASHSCOPE_API_KEY",
        sleep=lambda _s: None,
    )
    resp = adapter.propose(ModelRequest(task="t", prompt="hi"))
    assert "response_format" not in captured["body"]
    assert [m["role"] for m in captured["body"]["messages"]] == ["user"]
    assert resp.text == "plain text answer"
    assert resp.parsed is None  # no schema -> no parse


def test_openai_compat_retries_then_succeeds(monkeypatch):
    monkeypatch.setenv("MOONSHOT_API_KEY", "sk-secret")
    counter = {"calls": 0}
    transient = urllib.error.HTTPError(
        url="u", code=503, msg="Service Unavailable", hdrs=None, fp=None
    )
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        _flaky_urlopen(2, transient, _OPENAI_OK, counter),
    )
    slept: list = []
    adapter = OpenAICompatAdapter(
        base_url="https://api.moonshot.cn/v1",
        model_id="moonshot-v1-8k",
        api_key_env="MOONSHOT_API_KEY",
        max_retries=2,
        backoff=0.5,
        sleep=slept.append,
    )
    resp = adapter.propose(ModelRequest(task="t", prompt="p"))
    assert resp.text == '{"name": "AIRLINE"}'
    assert counter["calls"] == 3  # 2 failures + 1 success
    assert slept == [0.5, 1.0]  # deterministic exponential backoff, no jitter


def test_openai_compat_surfaces_clean_error_after_exhausting_retries(monkeypatch):
    monkeypatch.setenv("MOONSHOT_API_KEY", "sk-secret")
    counter = {"calls": 0}
    transient = urllib.error.URLError("connection reset")
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        _flaky_urlopen(99, transient, _OPENAI_OK, counter),
    )
    adapter = OpenAICompatAdapter(
        base_url="https://api.moonshot.cn/v1",
        model_id="moonshot-v1-8k",
        api_key_env="MOONSHOT_API_KEY",
        max_retries=2,
        sleep=lambda _s: None,
    )
    with pytest.raises(RuntimeError, match="OpenAICompatAdapter") as ei:
        adapter.propose(ModelRequest(task="t", prompt="p"))
    # a bare urllib exception must NOT escape, but the cause is preserved
    assert isinstance(ei.value.__cause__, urllib.error.URLError)
    assert counter["calls"] == 3  # initial + 2 retries


def test_openai_compat_does_not_retry_non_transient_error(monkeypatch):
    monkeypatch.setenv("MOONSHOT_API_KEY", "sk-secret")
    counter = {"calls": 0}
    auth_err = urllib.error.HTTPError(
        url="u", code=401, msg="Unauthorized", hdrs=None, fp=io.BytesIO(b"{}")
    )
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        _flaky_urlopen(99, auth_err, _OPENAI_OK, counter),
    )
    adapter = OpenAICompatAdapter(
        base_url="https://api.moonshot.cn/v1",
        model_id="moonshot-v1-8k",
        api_key_env="MOONSHOT_API_KEY",
        max_retries=3,
        sleep=lambda _s: None,
    )
    with pytest.raises(RuntimeError):
        adapter.propose(ModelRequest(task="t", prompt="p"))
    assert counter["calls"] == 1  # 401 is fatal: no retry


# ----------------------------------------- hardened Anthropic adapter (mock)


def test_anthropic_propose_parses_via_mock_transport(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-secret")
    captured: dict = {}
    monkeypatch.setattr(
        urllib.request, "urlopen", _capturing_urlopen(captured, _ANTHROPIC_OK)
    )
    adapter = AnthropicAdapter(sleep=lambda _s: None)
    resp = adapter.propose(
        ModelRequest(task="t", prompt="hello", schema='{"type":"object"}')
    )
    assert captured["url"] == AnthropicAdapter.API_URL
    assert captured["headers"]["x-api-key"] == "sk-ant-secret"
    assert captured["headers"]["anthropic-version"] == AnthropicAdapter.API_VERSION
    assert captured["body"]["model"] == "claude-sonnet-4-6"
    assert "system" in captured["body"]  # schema delivered via system prompt
    assert resp.parsed == {"name": "AIRLINE"}
    assert resp.input_tokens == 31
    assert resp.output_tokens == 5
    assert resp.model_id == "claude-sonnet-4-6"


def test_anthropic_retries_then_surfaces_clean_error(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-secret")
    counter = {"calls": 0}
    transient = urllib.error.HTTPError(
        url="u", code=429, msg="Too Many Requests", hdrs=None, fp=None
    )
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        _flaky_urlopen(99, transient, _ANTHROPIC_OK, counter),
    )
    adapter = AnthropicAdapter(max_retries=2, sleep=lambda _s: None)
    with pytest.raises(RuntimeError, match="AnthropicAdapter") as ei:
        adapter.propose(ModelRequest(task="t", prompt="p"))
    assert isinstance(ei.value.__cause__, urllib.error.HTTPError)
    assert counter["calls"] == 3  # initial + 2 retries
