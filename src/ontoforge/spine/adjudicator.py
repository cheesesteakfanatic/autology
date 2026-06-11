"""T2/T3 adjudication through the ModelClient abstraction (MVP plan §5.2).

Every escalated decision is serialized into a prompt for the task
``spine.adjudicate.<kind>`` with the tier label on the first line (so cassette
and fake adapters can key on it deterministically). The model must answer with
JSON ``{"choice": <candidate>, "confidence": <float in [0,1]>}``; malformed or
off-candidate answers degrade to an abstention (choice=None, confidence=0)
rather than raising — fail-closed per whitepaper §8.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Optional

from ontoforge.contracts import DecisionRequest, ModelClient, ModelRequest, ModelResponse

ADJUDICATE_MAX_TOKENS = 256

_RESPONSE_SCHEMA = json.dumps(
    {
        "type": "object",
        "properties": {
            "choice": {"type": "string"},
            "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        },
        "required": ["choice", "confidence"],
    },
    sort_keys=True,
)

_JSON_OBJ_RE = re.compile(r"\{[^{}]*\}", re.DOTALL)


@dataclass(frozen=True, slots=True)
class Adjudication:
    """One tier's parsed verdict on an escalated decision."""

    tier_label: str            # "T2" | "T3"
    choice: Optional[str]      # validated candidate, or None on parse failure
    confidence: float          # clipped to [0, 1]
    tokens: int                # total tokens charged by the ModelResponse
    model_id: str = ""


def _jsonable(v: Any) -> Any:
    if isinstance(v, (str, int, float, bool)) or v is None:
        return v
    return str(v)


def build_prompt(req: DecisionRequest, tier_label: str) -> str:
    """Deterministic prompt: tier header + sorted-key JSON payload of the
    candidates, T1 features, and the opaque T2/T3 context evidence."""
    payload = {
        "tier": tier_label,
        "task": f"spine.adjudicate.{req.kind.value}",
        "decision_id": req.decision_id,
        "kind": req.kind.value,
        "impact": req.impact,
        "candidates": list(req.candidates),
        "features": {k: float(v) for k, v in req.features},
        "context": {str(k): _jsonable(v) for k, v in req.context},
    }
    return (
        f"tier: {tier_label}\n"
        "You are the OntoForge decision-spine adjudicator. Pick exactly one of the\n"
        "listed candidates for this decision and state your confidence in [0,1].\n"
        + json.dumps(payload, sort_keys=True, default=str)
        + '\nRespond with JSON only: {"choice": "<one candidate>", "confidence": <float>}'
    )


def _extract_json(text: str) -> Optional[dict]:
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except (ValueError, TypeError):
        pass
    m = _JSON_OBJ_RE.search(text or "")
    if m:
        try:
            obj = json.loads(m.group(0))
            return obj if isinstance(obj, dict) else None
        except (ValueError, TypeError):
            return None
    return None


def parse_adjudication(resp: ModelResponse, candidates: tuple[str, ...]) -> tuple[Optional[str], float]:
    """Parse {choice, confidence}; validate choice against the candidate set
    (exact, then case-insensitive). Anything malformed -> (None, 0.0)."""
    obj = resp.parsed if isinstance(resp.parsed, dict) else _extract_json(resp.text)
    if not isinstance(obj, dict):
        return None, 0.0
    raw_choice = obj.get("choice")
    try:
        conf = float(obj.get("confidence", 0.0))
    except (TypeError, ValueError):
        conf = 0.0
    conf = min(max(conf, 0.0), 1.0)
    if not isinstance(raw_choice, str):
        return None, 0.0
    if raw_choice in candidates:
        return raw_choice, conf
    lowered = {c.strip().lower(): c for c in candidates}
    choice = lowered.get(raw_choice.strip().lower())
    if choice is None:
        return None, 0.0
    return choice, conf


def adjudicate(
    client: ModelClient,
    req: DecisionRequest,
    tier_label: str,
    max_tokens: int = ADJUDICATE_MAX_TOKENS,
) -> Adjudication:
    """One T2/T3 call. The caller is responsible for budget admission BEFORE
    calling (fail-closed quarantine, whitepaper §8 economy profile)."""
    prompt = build_prompt(req, tier_label)
    resp = client.propose(
        ModelRequest(
            task=f"spine.adjudicate.{req.kind.value}",
            prompt=prompt,
            schema=_RESPONSE_SCHEMA,
            temperature=0.0,
            max_tokens=max_tokens,
        )
    )
    choice, conf = parse_adjudication(resp, req.candidates)
    return Adjudication(
        tier_label=tier_label,
        choice=choice,
        confidence=conf,
        tokens=int(resp.total_tokens),
        model_id=resp.model_id,
    )
