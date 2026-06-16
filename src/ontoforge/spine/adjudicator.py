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


def _payload(req: DecisionRequest, tier_label: str) -> dict[str, Any]:
    """The structured adjudication payload shared by the deterministic prompt and
    the live library-template INPUT slot (so both carry identical evidence)."""
    return {
        "tier": tier_label,
        "task": f"spine.adjudicate.{req.kind.value}",
        "decision_id": req.decision_id,
        "kind": req.kind.value,
        "impact": req.impact,
        "candidates": list(req.candidates),
        "features": {k: float(v) for k, v in req.features},
        "context": {str(k): _jsonable(v) for k, v in req.context},
    }


def build_prompt(req: DecisionRequest, tier_label: str) -> str:
    """Deterministic prompt: tier header + sorted-key JSON payload of the
    candidates, T1 features, and the opaque T2/T3 context evidence."""
    payload = _payload(req, tier_label)
    return (
        f"tier: {tier_label}\n"
        "You are the OntoForge decision-spine adjudicator. Pick exactly one of the\n"
        "listed candidates for this decision and state your confidence in [0,1].\n"
        + json.dumps(payload, sort_keys=True, default=str)
        + '\nRespond with JSON only: {"choice": "<one candidate>", "confidence": <float>}'
    )


#: model_id the deterministic HeuristicAdapter stamps; see strata/emit.py.
_DETERMINISTIC_MODEL_ID = "heuristic"


def _is_live(client: ModelClient) -> bool:
    """True iff ``client`` is a LIVE model (a ``_RoutedClient`` with a live adapter
    wired). Reads ONLY the public ``activation.model_status`` over the ``.active``
    attribute. Any failure degrades to False (keyless/deterministic)."""
    try:
        from ontoforge.aimodels.activation import model_status

        return bool(model_status(client).live)
    except Exception:  # noqa: BLE001 — never let the live-check break the pipeline
        return False


def _render_prompt_for(
    client: ModelClient,
    task: str,
    live_input: str,
    grounding: Optional[str] = None,
) -> Optional[str]:
    """Render the rich PromptLibrary template for ``task`` with its INPUT slot set
    to the RAW payload JSON (``live_input``) — NOT ``build_prompt``'s already-framed
    string — to avoid double-framing. Returns ``None`` when there is no template for
    ``task`` (or any failure), signalling 'use the deterministic prompt'. Never
    raises into the pipeline. (Caller decides live-vs-keyless; this only renders.)"""
    try:
        from ontoforge.aimodels.library import PromptLibrary

        return PromptLibrary().get(task).render(user_input=live_input, grounding=grounding)
    except Exception:  # noqa: BLE001 — no template / import edge -> deterministic prompt
        return None


def _adjudication_grounding(payload: dict[str, Any]) -> str:
    """Deterministic grounding subset for the live adjudication prompt: the
    candidate set, the decision kind, and the T1 feature names in evidence."""
    cands = ", ".join(str(c) for c in payload.get("candidates", [])) or "(none)"
    feats = ", ".join(sorted(str(k) for k in (payload.get("features") or {}))) or "(none)"
    return f"decision kind: {payload.get('kind')}\ncandidates: {cands}\nfeature signals: {feats}"


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


def _propose_adjudication(
    client: ModelClient, req: DecisionRequest, tier_label: str, task: str, max_tokens: int
) -> ModelResponse:
    """Propose with the rich prompt on LIVE, ``build_prompt`` output keyless —
    preserving the parity-sacred fall-through.

    ``build_prompt`` BOTH frames AND embeds the payload, and the deterministic
    admit/er handlers parse that exact string. On the deterministic path the
    handlers tolerate the framing only because they slice to the first '{' (admit)
    or read the first '{'-line (er) — a RICH prompt's few-shot braces appear BEFORE
    the payload, so a deterministic handler run on the rich prompt SILENTLY
    MISPARSES (no exception). Therefore: (keyless) send ``build_prompt``'s exact
    bytes. (live) render the rich template with INPUT = the RAW payload JSON (no
    double-framing) and try it; if it RAISES, or the response came from the
    DETERMINISTIC fallback (``model_id == 'heuristic'`` — it degraded and ran on the
    rich prompt, so its verdict is an untrustworthy misparse), re-issue
    ``build_prompt``'s bytes so the deterministic verdict is computed on the exact
    current prompt — byte-identical to today."""
    payload_prompt = build_prompt(req, tier_label)
    bare_req = ModelRequest(
        task=task, prompt=payload_prompt, schema=_RESPONSE_SCHEMA,
        temperature=0.0, max_tokens=max_tokens,
    )
    if not _is_live(client):
        return client.propose(bare_req)
    payload = _payload(req, tier_label)
    live_input = json.dumps(payload, sort_keys=True, default=str)
    rich_prompt = _render_prompt_for(
        client, task, live_input, grounding=_adjudication_grounding(payload)
    )
    if rich_prompt is None:  # no rich template for this kind -> deterministic prompt
        return client.propose(bare_req)
    rich_req = ModelRequest(
        task=task, prompt=rich_prompt, schema=_RESPONSE_SCHEMA,
        temperature=0.0, max_tokens=max_tokens,
    )
    try:
        resp = client.propose(rich_req)
    except Exception:  # noqa: BLE001 — deterministic fallback crashed on rich; degrade to bare
        return client.propose(bare_req)
    if getattr(resp, "model_id", "") == _DETERMINISTIC_MODEL_ID:
        # the live leg degraded to the deterministic fallback (which silently
        # misparsed the rich prompt): recompute on build_prompt's exact bytes.
        return client.propose(bare_req)
    return resp


def adjudicate(
    client: ModelClient,
    req: DecisionRequest,
    tier_label: str,
    max_tokens: int = ADJUDICATE_MAX_TOKENS,
) -> Adjudication:
    """One T2/T3 call. The caller is responsible for budget admission BEFORE
    calling (fail-closed quarantine, whitepaper §8 economy profile)."""
    task = f"spine.adjudicate.{req.kind.value}"
    resp = _propose_adjudication(client, req, tier_label, task, max_tokens)
    choice, conf = parse_adjudication(resp, req.candidates)
    return Adjudication(
        tier_label=tier_label,
        choice=choice,
        confidence=conf,
        tokens=int(resp.total_tokens),
        model_id=resp.model_id,
    )
