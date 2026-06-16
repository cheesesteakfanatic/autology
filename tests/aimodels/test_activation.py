"""activation: the SINGLE env -> live-client seam.

PARITY (headline): with NO provider env, resolve_client returns EXACTLY the
deterministic fallback object (identity) — keyless behavior is byte-identical and no
decorator/router ever runs. With a provider + key (env monkeypatched, live transport
MOCKED so zero network), resolve_client returns the secure+validating+router wrapper
and model_status reflects the active provider. A CassetteAdapter-backed live branch
proves SecureModelClient redacted the prompt and ValidatingModelClient degraded to
the deterministic fallback on a malformed cassette entry — all with ZERO network."""

from __future__ import annotations

import json
import os

import ontoforge.aimodels.activation as activation
from ontoforge.aimodels.activation import model_status, resolve_client
from ontoforge.contracts.models import ModelRequest, ModelResponse
from ontoforge.ledger.models import CassetteAdapter, HeuristicAdapter

_NAME_SCHEMA = json.dumps(
    {
        "type": "object",
        "required": ["name", "definition"],
        "properties": {"name": {"type": "string"}, "definition": {"type": "string"}},
    },
    sort_keys=True,
)


def _det_fallback() -> HeuristicAdapter:
    def _name(req: ModelRequest):
        return {"name": "FallbackConcept", "definition": "deterministic"}

    return HeuristicAdapter({"strata.name_concept": _name, "answer": lambda r: r.prompt})


# ---------------------------------------------------------------- PARITY (identity)


def _clear_provider_env(monkeypatch) -> None:
    for var in (
        "ONTOFORGE_MODEL_PROVIDER",
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "MOONSHOT_API_KEY",
        "QWEN_API_KEY",
        "OPENAI_BASE_URL",
        "ONTOFORGE_MODEL_ID",
    ):
        monkeypatch.delenv(var, raising=False)


def test_no_provider_returns_fallback_identity(monkeypatch) -> None:
    _clear_provider_env(monkeypatch)
    fb = _det_fallback()
    out = resolve_client("strata.name_concept", fallback=fb)
    assert out is fb, "keyless path must return the SAME object (byte-identical parity)"


def test_empty_provider_returns_fallback_identity(monkeypatch) -> None:
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv("ONTOFORGE_MODEL_PROVIDER", "")
    fb = _det_fallback()
    assert resolve_client("answer", fallback=fb) is fb


def test_provider_set_but_key_missing_returns_fallback_identity(monkeypatch) -> None:
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv("ONTOFORGE_MODEL_PROVIDER", "anthropic")  # no key
    fb = _det_fallback()
    out = resolve_client("strata.name_concept", fallback=fb)
    assert out is fb, "missing key must fall back to keyless, never raise"


def test_unknown_provider_returns_fallback_identity(monkeypatch) -> None:
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv("ONTOFORGE_MODEL_PROVIDER", "not-a-provider")
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    fb = _det_fallback()
    assert resolve_client("answer", fallback=fb) is fb


def test_model_status_keyless(monkeypatch) -> None:
    _clear_provider_env(monkeypatch)
    st = model_status()
    assert st.live is False
    assert st.label == "deterministic/keyless"
    # status of a bare fallback client is also keyless
    fb = _det_fallback()
    assert model_status(fb).live is False


# -------------------------------------------------- LIVE branch (mocked, zero network)


def test_live_branch_wraps_secure_validating_and_status(monkeypatch) -> None:
    """provider + key set, but the live adapter builder is monkeypatched to return a
    RECORDING mock — NO network. resolve_client returns the wrapper, model_status
    reflects the active provider, and the secure layer redacted the egress prompt."""
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv("ONTOFORGE_MODEL_PROVIDER", "moonshot")
    monkeypatch.setenv("MOONSHOT_API_KEY", "fake-key-not-used")

    seen: list[str] = []

    class _MockLive:
        model_id = "kimi-mock"

        def propose(self, req: ModelRequest) -> ModelResponse:
            seen.append(req.prompt)
            obj = {"name": "C", "definition": "d"}
            return ModelResponse(text=json.dumps(obj), parsed=obj, model_id="kimi-mock")

    monkeypatch.setattr(activation, "_build_live_adapter", lambda provider: _MockLive())

    fb = _det_fallback()
    client = resolve_client("strata.name_concept", fallback=fb)
    assert client is not fb  # live branch returns a wrapper, not the bare fallback

    st = model_status(client)
    assert st.live is True
    assert st.provider == "moonshot"
    assert st.model_id == "kimi-mock"
    assert st.label == "moonshot"

    raw = "Contact alice@example.com, ssn 123-45-6789"
    resp = client.propose(
        ModelRequest(task="strata.name_concept", prompt=raw, schema=_NAME_SCHEMA)
    )
    # the live mock got a REDACTED, spotlighted prompt (secure egress enforced)
    assert seen, "live adapter never called"
    assert "alice@example.com" not in seen[0] and "123-45-6789" not in seen[0]
    assert "[EMAIL]" in seen[0] and "[SSN]" in seen[0]
    assert "<<<UNTRUSTED_DATA" in seen[0]
    # valid live response passes through the validator
    assert resp.parsed == {"name": "C", "definition": "d"}


def test_live_branch_falls_back_on_malformed_cassette(monkeypatch, tmp_path) -> None:
    """CassetteAdapter-backed live branch: a malformed recorded entry drives
    ValidatingModelClient to DEGRADE to the deterministic fallback — proving
    safety+fallback with NO network call. The secure layer still redacted egress."""
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv("ONTOFORGE_MODEL_PROVIDER", "moonshot")
    monkeypatch.setenv("MOONSHOT_API_KEY", "fake-key")

    # record a MALFORMED cassette entry for whatever prompt the secure layer emits.
    seen: list[str] = []

    class _BadInner:
        def propose(self, req: ModelRequest) -> ModelResponse:
            seen.append(req.prompt)
            return ModelResponse(text="{ broken json", parsed=None, model_id="kimi-mock")

    cassette_path = os.path.join(tmp_path, "live.json")
    recorder = CassetteAdapter(cassette_path, inner=_BadInner(), mode="record")

    # secure wraps the cassette as the "live" adapter -> still zero network.
    monkeypatch.setattr(activation, "_build_live_adapter", lambda provider: recorder)

    fb = _det_fallback()
    client = resolve_client("strata.name_concept", fallback=fb)

    raw = "John ordered widgets; email john@corp.io"
    resp = client.propose(
        ModelRequest(task="strata.name_concept", prompt=raw, schema=_NAME_SCHEMA)
    )
    # egress was redacted before hitting the cassette inner
    assert seen and "john@corp.io" not in seen[0] and "John" not in seen[0]
    # malformed live output -> validator degraded to the deterministic fallback
    assert resp.model_id == "heuristic"
    assert resp.parsed == {"name": "FallbackConcept", "definition": "deterministic"}


def test_live_exception_router_degrades_to_fallback(monkeypatch) -> None:
    """A live adapter that raises (429/5xx/timeout) makes the router chain fall
    through to the deterministic fallback — byte-identical keyless output."""
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv("ONTOFORGE_MODEL_PROVIDER", "moonshot")
    monkeypatch.setenv("MOONSHOT_API_KEY", "fake-key")

    class _Boom:
        model_id = "kimi-mock"

        def propose(self, req: ModelRequest) -> ModelResponse:
            raise RuntimeError("429 rate limited")

    monkeypatch.setattr(activation, "_build_live_adapter", lambda provider: _Boom())

    fb = _det_fallback()
    client = resolve_client("answer", fallback=fb)
    resp = client.propose(ModelRequest(task="answer", prompt="hello"))
    # router priority-0 live spec raised -> fell to priority-1 deterministic fallback
    assert resp.text == "hello"
    assert resp.model_id == "heuristic"
