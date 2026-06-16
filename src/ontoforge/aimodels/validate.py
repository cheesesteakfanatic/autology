"""ValidatingModelClient — schema-validate a live response, retry, then degrade.

The scout found validation lives in exactly ONE bespoke place today
(``spine/adjudicator.py``) and that ``lodestone.generate`` consumes ``resp.parsed``
with NO schema enforcement and NO retry — a live model returning malformed JSON
would raise. This is the generic safety net: it validates ``resp.parsed`` against
the requested JSON schema and, on malformed/invalid output, retries deterministically
(temperature 0) up to ``schema_retries`` times, then DEGRADES to the deterministic
``fallback`` client so a bad LLM response can never crash or corrupt a decision.

Dependency-light by design: we do NOT pull in ``jsonschema``. The validator does a
structural check against the parsed schema dict covering the constructs OntoForge's
task schemas actually use (``type`` incl. object/array/string/number/integer/boolean,
``required``, nested ``properties``, ``items``, ``enum``, numeric ``minimum``/
``maximum``, ``additionalProperties: false``). A request with no schema is a
pass-through (nothing to validate). Deterministic and keyless: the retry re-sends
the SAME request at temperature 0, so replaying a deterministic/cassette adapter is
unchanged.

PARITY: constructed ONLY around a live adapter (see :mod:`activation`). The keyless
deterministic path is never wrapped.
"""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any, Optional

from ontoforge.contracts.models import ModelClient, ModelRequest, ModelResponse

__all__ = ["ValidatingModelClient", "validate_against_schema"]

#: numeric JSON-schema types we structurally enforce.
_PY_TYPES: dict[str, tuple[type, ...]] = {
    "object": (dict,),
    "array": (list, tuple),
    "string": (str,),
    "boolean": (bool,),
    # int and float; bool is a subtype of int in python and handled explicitly below
    "number": (int, float),
    "integer": (int,),
    "null": (type(None),),
}


def _type_ok(value: Any, expected: str) -> bool:
    if expected not in _PY_TYPES:
        return True  # unknown type keyword -> do not fail closed on it
    if expected in ("number", "integer") and isinstance(value, bool):
        return False  # a bool is not a JSON number/integer
    if expected == "boolean":
        return isinstance(value, bool)
    return isinstance(value, _PY_TYPES[expected])


def validate_against_schema(value: Any, schema: Any) -> bool:
    """Structurally validate ``value`` against a (parsed) JSON-schema ``schema``.

    Returns ``True`` when valid. Deterministic, dependency-light: covers the
    constructs OntoForge task schemas use. A non-dict schema (or empty) accepts
    anything (nothing constrained)."""
    if not isinstance(schema, dict) or not schema:
        return True

    expected_type = schema.get("type")
    if isinstance(expected_type, str) and not _type_ok(value, expected_type):
        return False
    if isinstance(expected_type, list):
        if not any(_type_ok(value, t) for t in expected_type if isinstance(t, str)):
            return False

    if "enum" in schema:
        if value not in schema["enum"]:
            return False

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        minimum = schema.get("minimum")
        maximum = schema.get("maximum")
        if isinstance(minimum, (int, float)) and value < minimum:
            return False
        if isinstance(maximum, (int, float)) and value > maximum:
            return False

    if isinstance(value, dict):
        for req in schema.get("required", ()) or ():
            if req not in value:
                return False
        props = schema.get("properties")
        if isinstance(props, dict):
            for key, sub in props.items():
                if key in value and not validate_against_schema(value[key], sub):
                    return False
            if schema.get("additionalProperties") is False:
                if any(k not in props for k in value):
                    return False

    if isinstance(value, (list, tuple)):
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for item in value:
                if not validate_against_schema(item, item_schema):
                    return False

    return True


def _parse_schema(schema: Optional[str]) -> Any:
    """The contract serializes ``ModelRequest.schema`` as a JSON string. Parse it;
    a non-JSON / absent schema yields ``None`` (nothing to validate)."""
    if not schema:
        return None
    try:
        return json.loads(schema)
    except (TypeError, ValueError):
        return None


def _response_value(resp: ModelResponse, schema_obj: Any) -> Any:
    """The value a schema validates: ``resp.parsed`` when populated, else a
    best-effort JSON parse of ``resp.text`` (a live model may return raw JSON text
    the adapter did not parse)."""
    if resp.parsed is not None:
        return resp.parsed
    if schema_obj is not None and isinstance(resp.text, str) and resp.text.strip():
        try:
            return json.loads(resp.text)
        except ValueError:
            return None
    return None


class ValidatingModelClient:
    """Validate a live response against ``req.schema``; retry, then degrade.

    ``inner`` is the (live, already secure-wrapped) client. ``fallback`` is the
    deterministic client to degrade to when the live output cannot be made valid.
    ``schema_retries`` is the number of EXTRA deterministic re-sends (temperature 0)
    attempted after the first failure before degrading (default 1).
    """

    __slots__ = ("_inner", "_fallback", "_retries")

    def __init__(
        self,
        inner: ModelClient,
        *,
        fallback: ModelClient,
        schema_retries: int = 1,
    ) -> None:
        self._inner = inner
        self._fallback = fallback
        self._retries = max(0, int(schema_retries))

    @property
    def inner(self) -> ModelClient:
        return self._inner

    @property
    def fallback(self) -> ModelClient:
        return self._fallback

    def propose(self, req: ModelRequest) -> ModelResponse:
        schema_obj = _parse_schema(req.schema)

        # No schema requested -> nothing to validate; pass through but still guard
        # against a live exception by degrading to the fallback.
        if schema_obj is None:
            try:
                return self._inner.propose(req)
            except Exception:  # noqa: BLE001 — never let a live failure crash a decision
                return self._fallback.propose(req)

        # Validate at temperature 0; retry the SAME request deterministically.
        det_req = req if req.temperature == 0.0 else replace(req, temperature=0.0)
        last: Optional[ModelResponse] = None
        attempts = 1 + self._retries
        for _ in range(attempts):
            try:
                resp = self._inner.propose(det_req)
            except Exception:  # noqa: BLE001 — a live error degrades to fallback
                break
            value = _response_value(resp, schema_obj)
            if value is not None and validate_against_schema(value, schema_obj):
                if resp.parsed is None:
                    resp.parsed = value  # surface the salvaged JSON for the caller
                return resp
            last = resp  # keep the last (invalid) live response for context only

        # DEGRADE: a bad LLM response can never crash or corrupt a decision.
        _ = last
        return self._fallback.propose(req)
