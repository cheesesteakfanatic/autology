"""ValidatingModelClient: schema-validate -> retry -> degrade to deterministic.

A bad LLM response (malformed JSON / schema-invalid) can never crash or corrupt a
decision: the validator retries once at temperature 0, then falls back to the
deterministic client and returns a schema-valid result. Zero network."""

from __future__ import annotations

import json

from ontoforge.aimodels.validate import ValidatingModelClient, validate_against_schema
from ontoforge.contracts.models import ModelRequest, ModelResponse

_SCHEMA = json.dumps(
    {
        "type": "object",
        "required": ["name", "definition"],
        "properties": {"name": {"type": "string"}, "definition": {"type": "string"}},
    },
    sort_keys=True,
)


class _Malformed:
    """A mock LIVE adapter that ALWAYS returns malformed/invalid output and counts
    how many times it was called (to prove the retry happened)."""

    def __init__(self) -> None:
        self.calls = 0

    def propose(self, req: ModelRequest) -> ModelResponse:
        self.calls += 1
        return ModelResponse(text="{not json", parsed=None, model_id="mock-live")


class _DetFallback:
    """A deterministic fallback returning a schema-valid object."""

    def propose(self, req: ModelRequest) -> ModelResponse:
        obj = {"name": "Concept", "definition": "fallback"}
        return ModelResponse(text=json.dumps(obj), parsed=obj, model_id="heuristic")


class _Valid:
    def propose(self, req: ModelRequest) -> ModelResponse:
        obj = {"name": "Customer", "definition": "a buyer"}
        return ModelResponse(text=json.dumps(obj), parsed=obj, model_id="mock-live")


def test_malformed_response_retries_then_degrades_to_fallback() -> None:
    live = _Malformed()
    fallback = _DetFallback()
    vc = ValidatingModelClient(live, fallback=fallback, schema_retries=1)
    resp = vc.propose(ModelRequest(task="strata.name_concept", prompt="p", schema=_SCHEMA))
    # retried once -> two live attempts before degrading
    assert live.calls == 2
    # returned the deterministic, schema-valid fallback result (never raised)
    assert resp.model_id == "heuristic"
    assert resp.parsed == {"name": "Concept", "definition": "fallback"}
    assert validate_against_schema(resp.parsed, json.loads(_SCHEMA))


def test_valid_live_response_passes_through_without_fallback() -> None:
    live = _Valid()
    fallback = _DetFallback()
    vc = ValidatingModelClient(live, fallback=fallback)
    resp = vc.propose(ModelRequest(task="strata.name_concept", prompt="p", schema=_SCHEMA))
    assert resp.model_id == "mock-live"
    assert resp.parsed == {"name": "Customer", "definition": "a buyer"}


def test_live_exception_degrades_to_fallback() -> None:
    class _Boom:
        def propose(self, req: ModelRequest) -> ModelResponse:
            raise RuntimeError("429 rate limited")

    vc = ValidatingModelClient(_Boom(), fallback=_DetFallback(), schema_retries=1)
    resp = vc.propose(ModelRequest(task="strata.name_concept", prompt="p", schema=_SCHEMA))
    assert resp.model_id == "heuristic"  # degraded, did not raise


def test_no_schema_passes_through_but_guards_exceptions() -> None:
    class _Boom:
        def propose(self, req: ModelRequest) -> ModelResponse:
            raise RuntimeError("5xx")

    vc = ValidatingModelClient(_Boom(), fallback=_DetFallback())
    # no schema -> still must not crash; degrades to fallback on a live error
    resp = vc.propose(ModelRequest(task="answer", prompt="q"))
    assert resp.model_id == "heuristic"


def test_salvages_json_from_text_when_parsed_is_none() -> None:
    class _TextOnly:
        def propose(self, req: ModelRequest) -> ModelResponse:
            obj = {"name": "X", "definition": "y"}
            return ModelResponse(text=json.dumps(obj), parsed=None, model_id="mock-live")

    vc = ValidatingModelClient(_TextOnly(), fallback=_DetFallback())
    resp = vc.propose(ModelRequest(task="strata.name_concept", prompt="p", schema=_SCHEMA))
    assert resp.model_id == "mock-live"
    assert resp.parsed == {"name": "X", "definition": "y"}  # surfaced from text


# ----------------------------------------------------------------- structural validator


def test_validate_against_schema_constructs() -> None:
    schema = {
        "type": "object",
        "required": ["decision", "confidence"],
        "properties": {
            "decision": {"type": "string", "enum": ["fire", "hold"]},
            "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        },
        "additionalProperties": False,
    }
    assert validate_against_schema({"decision": "fire", "confidence": 0.9}, schema)
    assert not validate_against_schema({"decision": "maybe", "confidence": 0.9}, schema)  # enum
    assert not validate_against_schema({"decision": "fire", "confidence": 2.0}, schema)  # max
    assert not validate_against_schema({"decision": "fire"}, schema)  # missing required
    assert not validate_against_schema(
        {"decision": "fire", "confidence": 0.5, "extra": 1}, schema
    )  # additionalProperties: false
    assert not validate_against_schema({"decision": "fire", "confidence": True}, schema)  # bool!=num
    # empty/non-dict schema accepts anything
    assert validate_against_schema({"anything": 1}, {})
