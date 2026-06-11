"""T2 adjudication for ER: deterministic weighted-field-agreement scorer
(M5 step 4; AMD-0002 HeuristicAdapter serving in place of a vLLM specialist).

The handler is registered for both the spine's escalation task
(``spine.adjudicate.er`` — what DecisionSpine actually calls for
DecisionKind.ER) and the module-level task name ``er.adjudicate``.

The logic is deliberately DISTINCT from the T1 path: T1 is a calibrated
logistic model over the continuous feature vector; T2 is a rule-based
weighted-field-agreement score over the raw pair context with an explicit
temporal-reuse guard — serial mismatch + disjoint registration/event date
ranges adjudicates 'no' regardless of how well the tail and model agree
(§17.2.1: "N-numbers reused across aircraft over time").
"""

from __future__ import annotations

import json
from typing import Any, Optional

from ontoforge.contracts import ModelRequest

from .records import core_tokens
from .similarity import (
    char_ngrams,
    fuzzy_token_containment,
    fuzzy_token_jaccard,
    jaro_winkler,
    token_jaccard,
)

__all__ = ["er_adjudicate_handler", "ER_HEURISTIC_TASKS", "build_pair_context"]

ER_HEURISTIC_TASKS = ("spine.adjudicate.er", "er.adjudicate")

_GUARD_GAP_DAYS = 365  # disjoint-by-more-than-a-year => temporal-reuse guard


def build_pair_context(kind: str, fa: dict, fb: dict) -> tuple[tuple[str, Any], ...]:
    """Serialize the raw pair evidence for the T2/T3 prompt (flat, jsonable)."""
    if kind == "aircraft":
        return (
            ("er_kind", "aircraft"),
            ("tail_a", str(fa.get("tail", ""))),
            ("tail_b", str(fb.get("tail", ""))),
            ("serial_a", str(fa.get("serial", ""))),
            ("serial_b", str(fb.get("serial", ""))),
            ("model_a", str(fa.get("model", ""))),
            ("model_b", str(fb.get("model", ""))),
            ("name_a", str(fa.get("name", ""))),
            ("name_b", str(fb.get("name", ""))),
            ("date_lo_a", -1 if fa.get("date_lo") is None else int(fa["date_lo"])),
            ("date_hi_a", -1 if fa.get("date_hi") is None else int(fa["date_hi"])),
            ("date_lo_b", -1 if fb.get("date_lo") is None else int(fb["date_lo"])),
            ("date_hi_b", -1 if fb.get("date_hi") is None else int(fb["date_hi"])),
            ("is_registry_a", str(fa.get("is_registry", "0"))),
            ("is_registry_b", str(fb.get("is_registry", "0"))),
        )
    return (
        ("er_kind", "operator"),
        ("name_a", str(fa.get("name_norm", ""))),
        ("name_b", str(fb.get("name_norm", ""))),
        ("shared_tail", "1" if set(fa.get("tails") or ()) & set(fb.get("tails") or ()) else "0"),
    )


def _payload_from_prompt(prompt: str) -> Optional[dict]:
    """Recover the spine adjudicator's JSON payload line from the prompt."""
    for line in prompt.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if isinstance(obj, dict) and "context" in obj:
            return obj
    return None


# ---------------------------------------------------------------- scoring


def _score_aircraft(ctx: dict) -> tuple[str, float]:
    tail_a, tail_b = str(ctx.get("tail_a", "")), str(ctx.get("tail_b", ""))
    ser_a, ser_b = str(ctx.get("serial_a", "")), str(ctx.get("serial_b", ""))
    mod_a, mod_b = str(ctx.get("model_a", "")), str(ctx.get("model_b", ""))
    nam_a, nam_b = str(ctx.get("name_a", "")), str(ctx.get("name_b", ""))

    def _d(key: str) -> Optional[int]:
        v = int(ctx.get(key, -1))
        return None if v < 0 else v

    lo_a, hi_a, lo_b, hi_b = _d("date_lo_a"), _d("date_hi_a"), _d("date_lo_b"), _d("date_hi_b")
    any_registry = ctx.get("is_registry_a") == "1" or ctx.get("is_registry_b") == "1"

    serial_mismatch = bool(ser_a) and bool(ser_b) and ser_a != ser_b and jaro_winkler(ser_a, ser_b) < 0.88
    serial_match = bool(ser_a) and bool(ser_b) and ser_a == ser_b

    dates_disjoint = False
    gap_days = 0
    dates_known = (
        any_registry
        and lo_a is not None
        and hi_a is not None
        and lo_b is not None
        and hi_b is not None
    )
    if dates_known:
        assert lo_a is not None and hi_a is not None and lo_b is not None and hi_b is not None
        lo = max(lo_a, lo_b)
        hi = min(hi_a, hi_b)
        if lo > hi:
            gap_days = lo - hi
            dates_disjoint = gap_days > _GUARD_GAP_DAYS

    # ---- temporal-reuse guard (M5 step 4): same tail is NOT identity.
    if serial_mismatch and dates_disjoint:
        return "no", 0.98
    if serial_mismatch:
        return "no", 0.95
    if dates_disjoint and not serial_match:
        return "no", 0.93

    # ---- weighted field agreement
    score = 0.0
    weight = 0.0
    if tail_a and tail_b:
        score += 2.0 * (1.0 if tail_a == tail_b else -1.0)
        weight += 2.0
    if serial_match:
        score += 3.0
        weight += 3.0
    if mod_a and mod_b:
        sim = max(
            fuzzy_token_jaccard(mod_a.split(), mod_b.split()),
            token_jaccard(char_ngrams(mod_a), char_ngrams(mod_b)),
            0.7 if fuzzy_token_containment(mod_a.split(), mod_b.split()) else 0.0,
        )
        score += 2.0 * (2.0 * sim - 1.0)
        weight += 2.0
    if nam_a and nam_b:
        sim = max(jaro_winkler(nam_a, nam_b), fuzzy_token_jaccard(core_tokens(nam_a), core_tokens(nam_b)))
        score += 2.0 * (2.0 * sim - 1.0)
        weight += 2.0
    if dates_known and not dates_disjoint:
        score += 1.0 if gap_days == 0 else 0.0
        weight += 1.0

    if weight == 0.0:
        return "no", 0.55
    norm = score / weight  # in [-1, 1]
    if norm >= 0.30:
        return "yes", min(0.97, 0.90 + 0.10 * norm)
    if norm >= 0.10:
        return "yes", 0.85  # stays inside the band -> escalates further
    if norm >= -0.10:
        return "no", 0.60
    return "no", min(0.97, 0.75 + 0.25 * (-norm))


def _score_operator(ctx: dict) -> tuple[str, float]:
    na, nb = str(ctx.get("name_a", "")), str(ctx.get("name_b", ""))
    shared_tail = str(ctx.get("shared_tail", "0")) == "1"
    toks_a, toks_b = core_tokens(na), core_tokens(nb)

    jw = jaro_winkler(na, nb)
    tj = fuzzy_token_jaccard(toks_a, toks_b)
    contained = fuzzy_token_containment(toks_a, toks_b)

    score = 0.0
    weight = 0.0
    score += 3.0 * (2.0 * max(jw, tj) - 1.0)
    weight += 3.0
    if contained:
        score += 2.0
        weight += 2.0
    if shared_tail:
        score += 1.5
        weight += 1.5

    norm = score / weight
    if norm >= 0.35:
        return "yes", min(0.97, 0.90 + 0.10 * norm)
    if norm >= 0.15:
        return "yes", 0.85
    if norm >= -0.15:
        return "no", 0.62
    return "no", min(0.97, 0.75 + 0.25 * (-norm))


def er_adjudicate_handler(req: ModelRequest) -> dict:
    """HeuristicAdapter handler -> {"choice": "yes"|"no", "confidence": float}."""
    payload = _payload_from_prompt(req.prompt)
    if payload is None:
        return {"choice": "no", "confidence": 0.5}
    ctx = payload.get("context") or {}
    if ctx.get("er_kind") == "aircraft":
        choice, conf = _score_aircraft(ctx)
    else:
        choice, conf = _score_operator(ctx)
    return {"choice": choice, "confidence": conf}
