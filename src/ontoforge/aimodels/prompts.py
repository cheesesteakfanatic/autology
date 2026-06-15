"""Versioned, task-scoped prompt templates for the DE/AI-native layer.

Pure templates (docs/AI_NATIVE_AND_UI_PLAN.md §B): NO network, NO state. Each
template carries:

* a stable **task** name and a **version** (so a prompt change is a tracked
  artifact, never a silent drift),
* a JSON-**schema** string for constrained/structured output (handed to the
  ``ModelRequest.schema`` channel so the adapter constrains the model),
* zero or more **few-shot** examples ({input, output} pairs) injected verbatim,
* an **ontology-grounding** slot: ONLY the relevant class/property subset is
  injected (the schema-linking output from ``context.py``), never the whole
  induced model — this is what keeps a huge ontology inside a small budget.

``render()`` returns the final prompt string deterministically; equal inputs
always produce the byte-identical prompt (so the cassette/heuristic adapters are
reproducible and tests are stable).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Optional

__all__ = [
    "PROMPTS",
    "FewShot",
    "PromptTemplate",
    "get_prompt",
    "render",
]


@dataclass(frozen=True, slots=True)
class FewShot:
    """One in-context example. ``output`` is the JSON value the model should emit
    (rendered as compact JSON so it conforms to the constrained-output channel)."""

    input: str
    output: Any


@dataclass(frozen=True, slots=True)
class PromptTemplate:
    """A versioned, task-scoped template. ``instruction`` is the task framing;
    ``schema`` is the JSON schema for the constrained output; ``few_shot`` are
    the worked examples. Grounding + the live input are injected at render time."""

    task: str
    version: str
    instruction: str
    schema: dict[str, Any]
    few_shot: tuple[FewShot, ...] = ()

    @property
    def schema_json(self) -> str:
        return json.dumps(self.schema, sort_keys=True, separators=(",", ":"))

    def render(
        self,
        user_input: str,
        grounding: Optional[str] = None,
        extra: Optional[dict[str, str]] = None,
    ) -> str:
        """Deterministically assemble the full prompt.

        Layout (fixed order so equal inputs -> identical bytes):
          header (task@version) | instruction | OUTPUT SCHEMA |
          [ONTOLOGY GROUNDING] | [few-shot EXAMPLES] | [extra slots] | INPUT.
        The grounding block holds ONLY the linked class/property subset.
        """
        parts: list[str] = [
            f"# task: {self.task}@{self.version}",
            self.instruction.strip(),
            "## OUTPUT SCHEMA (respond with JSON conforming exactly to this):",
            self.schema_json,
        ]
        if grounding:
            parts.append("## ONTOLOGY GROUNDING (only the relevant subset):")
            parts.append(grounding.strip())
        if self.few_shot:
            parts.append("## EXAMPLES:")
            for ex in self.few_shot:
                parts.append(f"INPUT: {ex.input}")
                parts.append("OUTPUT: " + json.dumps(ex.output, sort_keys=True, separators=(",", ":")))
        if extra:
            for k in sorted(extra):
                parts.append(f"## {k.upper()}:")
                parts.append(extra[k].strip())
        parts.append("## INPUT:")
        parts.append(user_input.strip())
        return "\n".join(parts)


# --------------------------------------------------------------------------
# The task registry (versioned). Add a template => bump its version.
# --------------------------------------------------------------------------

_DECISION_SCHEMA = {
    "type": "object",
    "required": ["decision", "confidence", "rationale"],
    "properties": {
        "decision": {"type": "string", "enum": ["fire", "hold"]},
        "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "rationale": {"type": "string"},
    },
    "additionalProperties": False,
}

_NAME_SCHEMA = {
    "type": "object",
    "required": ["name"],
    "properties": {"name": {"type": "string"}, "rationale": {"type": "string"}},
    "additionalProperties": False,
}

_ANSWER_SCHEMA = {
    "type": "object",
    "required": ["answer", "citations"],
    "properties": {
        "answer": {"type": "string"},
        "citations": {"type": "array", "items": {"type": "string"}},
    },
    "additionalProperties": False,
}


PROMPTS: dict[str, PromptTemplate] = {
    "join": PromptTemplate(
        task="join",
        version="1",
        instruction=(
            "Decide whether two classes should be LINKED by a join on the given "
            "columns. Fire only if the columns plausibly reference the same "
            "identities; the data verifier independently checks coverage, so when "
            "unsure prefer 'hold'."
        ),
        schema=_DECISION_SCHEMA,
        few_shot=(
            FewShot(
                input="link orders.customer_id to customers.id; overlap 0.98",
                output={"decision": "fire", "confidence": 0.95, "rationale": "near-total key overlap"},
            ),
            FewShot(
                input="link orders.qty to customers.id; overlap 0.02",
                output={"decision": "hold", "confidence": 0.9, "rationale": "no shared key"},
            ),
        ),
    ),
    "merge": PromptTemplate(
        task="merge",
        version="1",
        instruction=(
            "Decide whether two entity records refer to the SAME real-world "
            "entity and should be merged. Prefer 'hold' for low-margin pairs — "
            "human review is cheap, a wrong merge is not."
        ),
        schema=_DECISION_SCHEMA,
    ),
    "retype": PromptTemplate(
        task="retype",
        version="1",
        instruction=(
            "Decide whether a property should be RETYPED to the target datatype. "
            "Fire only if (near) all values parse and the conversion is "
            "reversible; otherwise 'hold'."
        ),
        schema=_DECISION_SCHEMA,
    ),
    "name_concept": PromptTemplate(
        task="name_concept",
        version="1",
        instruction=(
            "Propose a concise, human-readable name for an induced class given "
            "its properties and a few sample values."
        ),
        schema=_NAME_SCHEMA,
    ),
    "answer": PromptTemplate(
        task="answer",
        version="1",
        instruction=(
            "Answer the question using ONLY the grounded facts. Cite the atom "
            "ids you used. If the facts do not support an answer, say so."
        ),
        schema=_ANSWER_SCHEMA,
    ),
}


def get_prompt(task: str) -> PromptTemplate:
    if task not in PROMPTS:
        raise KeyError(f"no prompt template for task {task!r}; have {sorted(PROMPTS)}")
    return PROMPTS[task]


def render(
    task: str,
    user_input: str,
    grounding: Optional[str] = None,
    extra: Optional[dict[str, str]] = None,
) -> tuple[str, str]:
    """Convenience: (prompt, schema_json) for a task. The schema_json is what to
    pass as ``ModelRequest.schema`` / ``router.complete(..., schema=...)``."""
    t = get_prompt(task)
    return t.render(user_input, grounding=grounding, extra=extra), t.schema_json
