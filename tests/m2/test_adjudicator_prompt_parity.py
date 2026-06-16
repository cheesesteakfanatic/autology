"""Parity + live-rich tests for the spine adjudicator prompt selection.

``build_prompt`` BOTH frames and embeds the payload, and the deterministic
handlers parse that exact string:
  * strata admit (``_payload_from_prompt``) slices to the first '{' then to the
    literal sentinel '\\nRespond with JSON only' — a rich few-shot's earlier '{'
    makes it SILENTLY MISPARSE (no exception);
  * er (``er_adjudicate_handler``) scans LINES for the first line starting with
    '{' that parses to a dict with 'context' — a rich few-shot '{'-line breaks it.

So Thread crew keeps ``build_prompt``'s output verbatim on the deterministic path
and renders the rich template ONLY when live, with INPUT = the RAW payload JSON
(no double-framing). Because the spine fallback misparses RATHER than raises, the
``_propose_adjudication`` guard ALSO re-issues the bare prompt whenever the
response came from the deterministic fallback (``model_id == 'heuristic'``).

  (i)  PARITY — keyless prompt == build_prompt(req, tier) byte-for-byte (admit +
       er); when a live model degrades to the deterministic fallback the verdict is
       recomputed on the bare prompt, so it equals the keyless verdict exactly.
  (ii) LIVE-RICH — a genuine live model receives the rich template (instruction +
       grounding + few-shot), NOT double-framed, with the SAME _RESPONSE_SCHEMA.
"""

from __future__ import annotations

import json

from ontoforge.aimodels.activation import ActiveModel
from ontoforge.contracts import DecisionKind, DecisionRequest, ModelRequest, ModelResponse
from ontoforge.er.heuristics import er_adjudicate_handler
from ontoforge.spine.adjudicator import (
    _RESPONSE_SCHEMA,
    _propose_adjudication,
    _render_prompt_for,
    adjudicate,
    build_prompt,
)
from ontoforge.strata.admission import admit_adjudication_handler


class _Recorder:
    """Captures each ModelRequest and serves a registered deterministic handler.
    ``model_id`` mimics a genuine live adapter ('rec') vs the deterministic
    fallback ('heuristic'); ``live`` toggles model_status(client).live."""

    def __init__(self, handler, *, live: bool, model_id: str = "rec") -> None:
        self._handler = handler
        self._model_id = model_id
        self.requests: list[ModelRequest] = []
        if live:
            self.active = ActiveModel(
                provider="anthropic", model_id="claude", live=True, reason="test-live"
            )

    def propose(self, req: ModelRequest) -> ModelResponse:
        self.requests.append(req)
        result = self._handler(req)
        return ModelResponse(
            text=json.dumps(result, sort_keys=True), parsed=result,
            input_tokens=0, output_tokens=0, model_id=self._model_id,
        )


def _admit_req() -> DecisionRequest:
    return DecisionRequest(
        kind=DecisionKind.ADMIT,
        decision_id="d-admit",
        candidates=("merge", "admit", "discard"),
        features=(("support_n", 0.9), ("stability", 0.9), ("intent_distinct_w", 3.0),
                  ("n_distinguishing_props", 3.0), ("gen_prior", 0.9)),
        context=(("phase", "concept-admission"),),
        impact=1.0,
    )


def _er_req() -> DecisionRequest:
    return DecisionRequest(
        kind=DecisionKind.ER,
        decision_id="d-er",
        candidates=("no", "yes"),
        features=(("tail_match", 1.0),),
        context=(("er_kind", "aircraft"), ("tail_a", "N512AA"), ("tail_b", "N512AA"),
                 ("serial_a", "MSN-4412"), ("serial_b", "MSN-4412"),
                 ("model_a", "A320"), ("model_b", "A320")),
        impact=1.0,
    )


# --------------------------------------------------------------- admit parity

def test_admit_keyless_prompt_is_build_prompt() -> None:
    req = _admit_req()
    rec = _Recorder(admit_adjudication_handler, live=False)
    adj = adjudicate(rec, req, "T2")
    prompt = rec.requests[0].prompt
    assert prompt == build_prompt(req, "T2")  # byte-identical to frozen format
    assert "\nRespond with JSON only" in prompt
    body = prompt[: prompt.find("\nRespond with JSON only")]
    assert json.loads(body[body.find("{"):])  # first-brace slice still parses
    assert adj.choice in req.candidates
    assert rec.requests[0].schema == _RESPONSE_SCHEMA


def test_admit_live_prompt_is_rich_and_not_double_framed() -> None:
    req = _admit_req()
    rec = _Recorder(admit_adjudication_handler, live=True)
    adjudicate(rec, req, "T2")
    prompt = rec.requests[0].prompt
    det = build_prompt(req, "T2")
    assert prompt != det
    assert "# task: spine.adjudicate.admit@1" in prompt
    assert "decision-spine adjudicator for STRATA concept" in prompt  # instruction
    assert "## ONTOLOGY GROUNDING" in prompt
    assert "candidates: merge, admit, discard" in prompt  # grounding content
    assert "## EXAMPLES:" in prompt  # few-shot marker
    assert "## INPUT:" in prompt
    assert not prompt.startswith("tier: T2")  # NOT double-framed
    payload = _render_input_payload(req)
    assert payload in prompt  # raw payload is the INPUT slot
    assert rec.requests[0].schema == _RESPONSE_SCHEMA


# ------------------------------------------------------------------ er parity

def test_er_keyless_prompt_is_build_prompt_single_line() -> None:
    req = _er_req()
    rec = _Recorder(er_adjudicate_handler, live=False)
    adj = adjudicate(rec, req, "T2")
    prompt = rec.requests[0].prompt
    assert prompt == build_prompt(req, "T2")
    payload_lines = [ln for ln in prompt.splitlines() if ln.strip().startswith("{") and '"context"' in ln]
    assert len(payload_lines) == 1
    assert json.loads(payload_lines[0].strip())
    assert adj.choice in req.candidates
    assert rec.requests[0].schema == _RESPONSE_SCHEMA


def test_er_live_prompt_is_rich_template() -> None:
    req = _er_req()
    rec = _Recorder(er_adjudicate_handler, live=True)
    adjudicate(rec, req, "T2")
    prompt = rec.requests[0].prompt
    assert prompt != build_prompt(req, "T2")
    assert "# task: spine.adjudicate.er@1" in prompt
    assert "ENTITY" in prompt and "TEMPORAL-REUSE GUARD" in prompt  # instruction
    assert "## EXAMPLES:" in prompt
    assert "## INPUT:" in prompt
    assert rec.requests[0].schema == _RESPONSE_SCHEMA


# ------------------------------------------------ deterministic-fallback guard

def test_live_degrade_to_deterministic_recomputes_on_bare_prompt() -> None:
    """When a live model degrades to the deterministic fallback (model_id ==
    'heuristic'), the spine handler would MISPARSE the rich prompt. The
    _propose_adjudication guard re-issues the BARE prompt, so the verdict is
    byte-identical to the keyless deterministic verdict — never a silent misparse."""
    req = _admit_req()
    task = "spine.adjudicate.admit"

    # keyless deterministic verdict (the parity target)
    keyless = _Recorder(admit_adjudication_handler, live=False)
    keyless_verdict = adjudicate(keyless, req, "T2")

    # a LIVE client whose responses carry model_id='heuristic' (i.e. it always
    # degrades to the deterministic fallback). The guard must recompute on bare.
    degrading = _Recorder(admit_adjudication_handler, live=True, model_id="heuristic")
    _propose_adjudication(degrading, req, "T2", task, 256)
    # the LAST request the recorder served is the BARE build_prompt (the guard's
    # re-issue), not the rich one — proving the deterministic verdict used bare bytes
    assert degrading.requests[-1].prompt == build_prompt(req, "T2")
    guarded = adjudicate(degrading, req, "T2")
    assert (guarded.choice, round(guarded.confidence, 6)) == (
        keyless_verdict.choice, round(keyless_verdict.confidence, 6)
    ), "degraded live verdict diverged from the keyless deterministic baseline"


def test_render_prompt_for_returns_none_for_unknown_task() -> None:
    """No template for a kind -> _render_prompt_for returns None (use deterministic)."""
    assert _render_prompt_for(object(), "spine.adjudicate.qi", "{}", None) is None


def _render_input_payload(req: DecisionRequest) -> str:
    from ontoforge.spine.adjudicator import _payload

    return json.dumps(_payload(req, "T2"), sort_keys=True, default=str)
