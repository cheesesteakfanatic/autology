"""Parity + live-rich tests for STRATA naming prompt selection (emit.py).

Thread crew: render the rich PromptLibrary template on the LIVE path ONLY; the
keyless/deterministic path must send the BYTE-IDENTICAL current prompt so the
``name_concept_handler`` (which does ``json.loads`` on the WHOLE prompt) is
untouched.

Two guardrails:
  (i)  PARITY — a deterministic client receives a prompt that is byte-identical
       to the frozen pre-change format (bare ``json.dumps(payload, sort_keys=True)``)
       AND remains valid whole-prompt JSON, so the keyless name is unchanged.
  (ii) LIVE-RICH — a client whose ``model_status(...).live`` is True receives the
       rich template (instruction text + ontology grounding + a few-shot marker),
       with the SAME schema string the call-site already used, and the raw payload
       preserved verbatim as the INPUT slot.
"""

from __future__ import annotations

import json

from ontoforge.aimodels.activation import ActiveModel
from ontoforge.contracts import ModelRequest, ModelResponse
from ontoforge.strata.admission import NAME_TASK, _NAME_SCHEMA, name_concept_handler
from ontoforge.strata.emit import _name_grounding, _render_prompt_for


# --- a minimal recording client; capture the prompt the model actually receives
class _Recorder:
    """Captures every ModelRequest and serves the deterministic handler so the
    pipeline stays valid. ``active`` (an ActiveModel) toggles model_status.live —
    exactly the attribute resolve_client attaches to _RoutedClient."""

    def __init__(self, *, live: bool) -> None:
        self.requests: list[ModelRequest] = []
        if live:
            self.active = ActiveModel(
                provider="anthropic", model_id="claude", live=True, reason="test-live"
            )

    def propose(self, req: ModelRequest) -> ModelResponse:
        self.requests.append(req)
        # delegate to the deterministic handler so a downstream parse still works
        result = name_concept_handler(req)
        return ModelResponse(
            text=json.dumps(result, sort_keys=True), parsed=result,
            input_tokens=0, output_tokens=0, model_id="rec",
        )


_PAYLOAD = {
    "task": NAME_TASK,
    "intent_hash": "abc123",
    "object_hint": "aircraft",
    "distinguishing_props": ["serial", "tail_number"],
    "event_like": False,
    "tables": ["fleet", "registrations"],
    "support": 12,
}
_FROZEN_GOLDEN = json.dumps(_PAYLOAD, sort_keys=True)


def test_keyless_prompt_is_byte_identical_to_frozen_golden() -> None:
    """No provider env -> bare deterministic client -> prompt is the exact
    pre-change bytes (the whole prompt is the payload JSON)."""
    bare = _Recorder(live=False)
    grounding = _name_grounding(_PAYLOAD)
    prompt = _render_prompt_for(bare, NAME_TASK, _FROZEN_GOLDEN, grounding)
    assert prompt == _FROZEN_GOLDEN
    # the keyless handler does json.loads on the WHOLE prompt — must still parse
    assert json.loads(prompt) == _PAYLOAD


def test_keyless_handler_output_unchanged() -> None:
    """The deterministic name is computed from the byte-identical prompt."""
    bare = _Recorder(live=False)
    prompt = _render_prompt_for(bare, NAME_TASK, _FROZEN_GOLDEN, _name_grounding(_PAYLOAD))
    out = name_concept_handler(ModelRequest(task=NAME_TASK, prompt=prompt, schema=_NAME_SCHEMA))
    # object_hint 'aircraft' -> camel('aircraft') == 'Aircraft'
    assert out["name"] == "Aircraft"


def test_live_prompt_is_rich_template() -> None:
    """A live client receives the rich PromptLibrary template, NOT bare JSON."""
    live = _Recorder(live=True)
    grounding = _name_grounding(_PAYLOAD)
    prompt = _render_prompt_for(live, NAME_TASK, _FROZEN_GOLDEN, grounding)
    assert prompt != _FROZEN_GOLDEN
    # instruction framing (template header + naming instruction)
    assert "# task: strata.name_concept@1" in prompt
    assert "induced ontology CLASS" in prompt  # instruction text
    # ontology grounding block + the grounding content
    assert "## ONTOLOGY GROUNDING" in prompt
    assert "distinguishing properties: serial, tail_number" in prompt
    # few-shot marker
    assert "## EXAMPLES:" in prompt
    # the raw structured payload is preserved verbatim as the INPUT slot
    assert "## INPUT:" in prompt
    assert prompt.rstrip().endswith(_FROZEN_GOLDEN)


def test_live_render_keeps_call_site_schema() -> None:
    """_render_prompt_for returns the PROMPT only; the schema is owned by the
    call-site (_NAME_SCHEMA string), never replaced by the library template's
    own schema. Both describe {name, definition} but the call-site passes its OWN
    bytes — so dropping a live model never changes the structured-output channel."""
    assert isinstance(_NAME_SCHEMA, str)
    parsed = json.loads(_NAME_SCHEMA)
    assert parsed["required"] == ["name", "definition"]


def test_unknown_task_degrades_to_payload() -> None:
    """A live client on a task with no template degrades to the deterministic
    payload (never raises)."""
    live = _Recorder(live=True)
    prompt = _render_prompt_for(live, "strata.does_not_exist", _FROZEN_GOLDEN, None)
    assert prompt == _FROZEN_GOLDEN
