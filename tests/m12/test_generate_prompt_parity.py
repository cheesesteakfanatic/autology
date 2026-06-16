"""Parity + live-rich tests for LODESTONE candidate-generation prompt selection.

The ``lodestone.generate`` deterministic handler does ``json.loads`` on the WHOLE
prompt (question/bindings/coverage), so any rich framing on the keyless path would
break enumeration. Thread crew renders the rich PromptLibrary template ONLY on the
live path; keyless must be byte-identical.

  (i)  PARITY — a recording deterministic client receives the bare
       ``json.dumps({question,coverage,bindings,ontology_digest}, sort_keys=True)``
       prompt (whole-prompt JSON), and the enumerated candidate set is unchanged
       versus the same generation through a plain heuristic client.
  (ii) LIVE-RICH — a client flagged live (ActiveModel attached) receives the rich
       template (instruction + grounding + few-shot), with the SAME GENERATE_SCHEMA
       and the raw payload preserved verbatim as the INPUT slot.
"""

from __future__ import annotations

import json

from ontoforge.aimodels.activation import ActiveModel
from ontoforge.contracts import ModelRequest, ModelResponse
from ontoforge.lodestone.candidates import (
    GENERATE_SCHEMA,
    GENERATE_TASK,
    generate_candidates,
    make_generate_handler,
    term_to_spec,
)
from ontoforge.lodestone.grounding import ValueIndex, ground


class _Recorder:
    """Captures each ModelRequest. ``live`` toggles model_status(client).live via
    the ``active`` attribute.

    KEYLESS recorder delegates to the deterministic generate handler (which does
    json.loads on the WHOLE prompt) — so it ONLY works when the prompt is the bare
    payload JSON; that is exactly the parity guarantee under test. The LIVE
    recorder does NOT run the keyless handler (a live model parses the rich prompt
    itself); it returns a canned valid empty array so generate_candidates does not
    raise — the test asserts on the captured PROMPT, not the candidate set."""

    def __init__(self, onto, *, live: bool) -> None:
        self._live = live
        self._handler = make_generate_handler(onto)
        self.requests: list[ModelRequest] = []
        if live:
            self.active = ActiveModel(
                provider="anthropic", model_id="claude", live=True, reason="test-live"
            )

    def propose(self, req: ModelRequest) -> ModelResponse:
        self.requests.append(req)
        if self._live:
            return ModelResponse(
                text="[]", parsed=[], input_tokens=0, output_tokens=0, model_id="rec"
            )
        text = self._handler(req)
        return ModelResponse(
            text=text, parsed=json.loads(text), input_tokens=0, output_tokens=0, model_id="rec"
        )


_QUESTION = "what is the registration number of active aircraft"


def _ground(onto):
    # Empty value index keeps the test self-contained (no HEARTH needed); class +
    # property bindings come from the ontology, which is all generate_candidates
    # needs to enumerate a non-empty candidate set deterministically.
    return ground(_QUESTION, onto, ValueIndex())


def test_keyless_prompt_is_bare_payload_json(gold_onto) -> None:
    """No provider env -> the prompt passed to propose() is the bare payload JSON
    (whole-prompt-is-JSON), parseable by the deterministic handler."""
    rec = _Recorder(gold_onto, live=False)
    grounding = _ground(gold_onto)
    generate_candidates(_QUESTION, grounding, gold_onto, rec)
    assert len(rec.requests) == 1
    prompt = rec.requests[0].prompt
    # whole prompt parses as the structured payload (no framing prepended)
    payload = json.loads(prompt)
    assert payload["question"] == _QUESTION
    assert "bindings" in payload and "coverage" in payload and "ontology_digest" in payload
    # byte-identical to the frozen pre-change format
    frozen = json.dumps(
        {
            "question": _QUESTION,
            "coverage": grounding.coverage,
            "bindings": payload["bindings"],
            "ontology_digest": payload["ontology_digest"],
        },
        sort_keys=True,
    )
    assert prompt == frozen
    # schema unchanged
    assert rec.requests[0].schema == GENERATE_SCHEMA


def test_keyless_candidate_set_unchanged(gold_onto) -> None:
    """The deterministic candidate set is identical whether produced through the
    recorder (parity wrapper) or a plain heuristic client."""
    from ontoforge.ledger.models import HeuristicAdapter

    grounding = _ground(gold_onto)
    plain = HeuristicAdapter({GENERATE_TASK: make_generate_handler(gold_onto)})
    rec = _Recorder(gold_onto, live=False)

    cs_plain = generate_candidates(_QUESTION, grounding, gold_onto, plain)
    cs_rec = generate_candidates(_QUESTION, grounding, gold_onto, rec)

    terms_plain = [json.dumps(term_to_spec(c.term), sort_keys=True) for c in cs_plain.candidates]
    terms_rec = [json.dumps(term_to_spec(c.term), sort_keys=True) for c in cs_rec.candidates]
    assert terms_plain == terms_rec
    assert [c.score for c in cs_plain.candidates] == [c.score for c in cs_rec.candidates]


def test_live_prompt_is_rich_template(gold_onto) -> None:
    """A live client receives the rich PromptLibrary template, NOT bare JSON."""
    rec = _Recorder(gold_onto, live=True)
    grounding = _ground(gold_onto)
    generate_candidates(_QUESTION, grounding, gold_onto, rec)
    prompt = rec.requests[0].prompt
    # NOT bare JSON anymore
    try:
        json.loads(prompt)
        is_bare_json = True
    except ValueError:
        is_bare_json = False
    assert not is_bare_json
    # instruction framing + grounding + few-shot markers
    assert "# task: lodestone.generate@1" in prompt
    assert "well-typed OQIR query candidates" in prompt
    assert "## ONTOLOGY GROUNDING" in prompt
    assert "reachable classes:" in prompt
    assert "## EXAMPLES:" in prompt
    assert "## INPUT:" in prompt
    # the raw structured payload is still the INPUT slot (so the model gets it)
    assert f'"question":"{_QUESTION}"' in prompt or f'"question": "{_QUESTION}"' in prompt
    # schema unchanged even on the live path
    assert rec.requests[0].schema == GENERATE_SCHEMA
