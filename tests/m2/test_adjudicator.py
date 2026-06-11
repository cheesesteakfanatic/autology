"""M2: T2/T3 adjudication prompt contract and fail-closed response parsing."""

from __future__ import annotations

import json

from ontoforge.contracts import DecisionKind, ModelResponse
from ontoforge.spine import build_prompt, parse_adjudication

from m2_helpers import heuristic_request

CANDS = ("no", "yes")


def test_prompt_serializes_candidates_features_context_and_tier() -> None:
    req = heuristic_request(DecisionKind.EX, "p1", 0.4)
    prompt = build_prompt(req, "T2")
    assert prompt.startswith("tier: T2\n")
    payload = json.loads(next(ln for ln in prompt.splitlines() if ln.startswith("{")))
    assert payload["task"] == "spine.adjudicate.ex"
    assert payload["candidates"] == ["no", "yes"]
    assert payload["features"] == {"s": 0.4}
    assert payload["context"] == {"note": "synthetic case p1"}
    assert payload["decision_id"] == "p1"
    # Deterministic: identical request -> identical prompt (memo/cassette-safe).
    assert prompt == build_prompt(req, "T2")
    assert build_prompt(req, "T3").startswith("tier: T3\n")


def test_parse_valid_json() -> None:
    resp = ModelResponse(text='{"choice": "yes", "confidence": 0.87}')
    assert parse_adjudication(resp, CANDS) == ("yes", 0.87)


def test_parse_json_embedded_in_prose() -> None:
    resp = ModelResponse(text='Sure! Here is my answer: {"choice": "no", "confidence": 0.7} hope it helps')
    assert parse_adjudication(resp, CANDS) == ("no", 0.7)


def test_parse_prefers_schema_validated_parsed_field() -> None:
    resp = ModelResponse(text="ignored", parsed={"choice": "yes", "confidence": 0.5})
    assert parse_adjudication(resp, CANDS) == ("yes", 0.5)


def test_parse_case_insensitive_choice() -> None:
    resp = ModelResponse(text='{"choice": " YES ", "confidence": 0.9}')
    assert parse_adjudication(resp, CANDS) == ("yes", 0.9)


def test_parse_confidence_clipped_to_unit_interval() -> None:
    assert parse_adjudication(ModelResponse(text='{"choice": "yes", "confidence": 1.7}'), CANDS) == (
        "yes",
        1.0,
    )
    assert parse_adjudication(ModelResponse(text='{"choice": "yes", "confidence": -3}'), CANDS) == (
        "yes",
        0.0,
    )


def test_parse_failures_abstain() -> None:
    """Malformed output degrades to abstention (None, 0.0) — never raises."""
    bad = [
        "not json at all",
        "{broken json",
        '{"confidence": 0.9}',                       # missing choice
        '{"choice": 42, "confidence": 0.9}',         # non-string choice
        '{"choice": "maybe", "confidence": 0.9}',    # off-candidate choice
        '{"choice": "yes", "confidence": "high"}',   # non-numeric conf -> conf 0
        "[1, 2, 3]",
        "",
    ]
    for text in bad:
        choice, conf = parse_adjudication(ModelResponse(text=text), CANDS)
        if text == '{"choice": "yes", "confidence": "high"}':
            assert (choice, conf) == ("yes", 0.0)
        else:
            assert choice is None and conf == 0.0
