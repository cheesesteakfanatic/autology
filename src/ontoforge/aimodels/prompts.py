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

# --------------------------------------------------------------------------
# Schemas for the REAL engine escalation tasks. These MUST be byte-identical to
# the schema each call-site already enforces (so the existing structured-output
# parser keeps working when a live model is dropped in). The originals are:
#   * strata.name_concept      -> strata/admission.py::_NAME_SCHEMA
#   * lodestone.generate       -> lodestone/candidates.py::GENERATE_SCHEMA
#   * spine.adjudicate.admit   -> spine/adjudicator.py::_RESPONSE_SCHEMA
#   * spine.adjudicate.er      -> spine/adjudicator.py::_RESPONSE_SCHEMA
# We hold the SAME schema objects here (Prompts crew owns only this module); the
# tests below assert they match the call-site bytes so drift is caught.

#: ``strata.name_concept`` call-site: required {name, definition} (both strings).
#: NOTE: distinct from the generic ``name_concept`` template's {name, rationale}.
_STRATA_NAME_SCHEMA = {
    "type": "object",
    "properties": {"name": {"type": "string"}, "definition": {"type": "string"}},
    "required": ["name", "definition"],
}

#: ``lodestone.generate`` call-site: an ARRAY of objects each carrying an OQIR
#: ``term`` object. The array is the structured-output root (not an object).
_GENERATE_SCHEMA = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {"term": {"type": "object"}},
        "required": ["term"],
    },
}

#: ``spine.adjudicate.*`` call-site: required {choice, confidence}; ``choice`` is
#: validated against the live candidate set by ``parse_adjudication`` (so the
#: schema keeps it a plain string here — the candidate enum is request-specific).
_ADJUDICATION_SCHEMA = {
    "type": "object",
    "properties": {
        "choice": {"type": "string"},
        "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
    },
    "required": ["choice", "confidence"],
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
    # ----------------------------------------------------------------------
    # REAL engine escalation tasks (the names the spine actually escalates
    # under). These coexist with the generic templates above; the live model
    # path renders these, the keyless/deterministic path is untouched.
    # ----------------------------------------------------------------------
    "strata.name_concept": PromptTemplate(
        task="strata.name_concept",
        version="1",
        instruction=(
            "You name an induced ontology CLASS (a type), never an instance, from "
            "Formal Concept Analysis evidence. Choose a PascalCase, SINGULAR, "
            "domain-neutral noun that captures what the distinguishing properties "
            "have in common; ignore table-specific or vendor-specific jargon. If "
            "the concept is event_like (it carries a timestamp / records an "
            "occurrence), append the suffix 'Event' (e.g. 'MaintenanceEvent'). "
            "Prefer the object_hint only when it is itself a clean class noun; "
            "otherwise derive the name purely from the distinguishing properties. "
            "Write a one-sentence 'definition' stating which real-world things are "
            "members of the class and what distinguishes them from the parent and "
            "siblings. Respond with {name, definition}, both plain strings."
        ),
        schema=_STRATA_NAME_SCHEMA,
        few_shot=(
            FewShot(
                input=(
                    '{"distinguishing_props":["serial","tail_number"],'
                    '"event_like":false,"object_hint":"aircraft",'
                    '"parent":"Asset","siblings":["GroundVehicle"],'
                    '"tables":["fleet","registrations"]}'
                ),
                output={
                    "name": "Aircraft",
                    "definition": (
                        "A flying asset uniquely identified by an airframe serial and "
                        "a tail number, distinguished from other Assets by carrying an "
                        "aviation registration."
                    ),
                },
            ),
            FewShot(
                input=(
                    '{"distinguishing_props":["has-timestamp","work_order",'
                    '"technician"],"event_like":true,"object_hint":"",'
                    '"parent":"Activity","siblings":["Inspection"],'
                    '"tables":["maint_log"]}'
                ),
                output={
                    "name": "MaintenanceEvent",
                    "definition": (
                        "A timestamped occurrence in which a technician performs a "
                        "work order against an asset, distinguished from other "
                        "Activities by being a dated maintenance record."
                    ),
                },
            ),
            FewShot(
                input=(
                    '{"distinguishing_props":["iata_code","icao_code","runway_count"],'
                    '"event_like":false,"object_hint":"",'
                    '"parent":"Place","siblings":["City"],"tables":["airports"]}'
                ),
                output={
                    "name": "Airport",
                    "definition": (
                        "A place with IATA and ICAO codes and one or more runways, "
                        "distinguished from other Places by being an aviation facility "
                        "where aircraft operate."
                    ),
                },
            ),
        ),
    ),
    "lodestone.generate": PromptTemplate(
        task="lodestone.generate",
        version="1",
        instruction=(
            "You enumerate well-typed OQIR query candidates for a natural-language "
            "question. Given the question, its grounded bindings, and an ontology "
            "digest of the reachable classes/properties/links, emit an ARRAY of at "
            "most 8 candidate terms. Each element is an object {\"term\": <spec>} "
            "where <spec> uses the compositional grammar below. Use ONLY classes, "
            "properties, and links present in the grounding; NEVER invent a link or "
            "traversal that the ontology does not declare. Prefer grounded anchors; "
            "order candidates most-plausible first. Grammar (op-tagged objects): "
            "select{op,class,conds}; traverse{op,src,link,reverse,conds}; "
            "agg{op,src,agg,measure,group_by,having}; topk{op,src,by,k,descending}; "
            "textjoin{op,src,prop,pattern}; asof{op,src,kind,valid_at,known_at}. A "
            "cond is {prop,op,value,value2,unit}."
        ),
        schema=_GENERATE_SCHEMA,
        few_shot=(
            FewShot(
                input=(
                    '{"question":"active aircraft for operator Acme",'
                    '"bindings":[{"kind":"class","target":"Aircraft"},'
                    '{"kind":"value","target":"status","value":"active"}]}'
                ),
                output=[
                    {
                        "term": {
                            "op": "select",
                            "class": "Aircraft",
                            "conds": [
                                {
                                    "prop": "status",
                                    "op": "=",
                                    "value": "active",
                                    "value2": None,
                                    "unit": None,
                                }
                            ],
                        }
                    }
                ],
            ),
            FewShot(
                input=(
                    '{"question":"which operators fly the A320",'
                    '"bindings":[{"kind":"class","target":"Aircraft"},'
                    '{"kind":"link","target":"operated_by"},'
                    '{"kind":"value","target":"model","value":"A320"}]}'
                ),
                output=[
                    {
                        "term": {
                            "op": "traverse",
                            "src": {
                                "op": "select",
                                "class": "Aircraft",
                                "conds": [
                                    {
                                        "prop": "model",
                                        "op": "=",
                                        "value": "A320",
                                        "value2": None,
                                        "unit": None,
                                    }
                                ],
                            },
                            "link": "operated_by",
                            "reverse": False,
                            "conds": [],
                        }
                    }
                ],
            ),
            FewShot(
                input=(
                    '{"question":"operators with more than 10 aircraft",'
                    '"bindings":[{"kind":"class","target":"Aircraft"},'
                    '{"kind":"link","target":"operated_by"}]}'
                ),
                output=[
                    {
                        "term": {
                            "op": "agg",
                            "src": {"op": "select", "class": "Aircraft", "conds": []},
                            "agg": "count",
                            "measure": None,
                            "group_by": ["operated_by"],
                            "having": [
                                {
                                    "prop": "count",
                                    "op": ">",
                                    "value": 10,
                                    "value2": None,
                                    "unit": None,
                                }
                            ],
                        }
                    }
                ],
            ),
        ),
    ),
    "spine.adjudicate.admit": PromptTemplate(
        task="spine.adjudicate.admit",
        version="1",
        instruction=(
            "You are the OntoForge decision-spine adjudicator for STRATA concept "
            "admission. Pick EXACTLY ONE of the provided candidate strings and a "
            "confidence in [0,1]. For concept-admission the candidates are "
            "'merge', 'admit', 'discard': choose 'admit' only when the concept has "
            "genuinely distinctive intent (multiple non-shared object properties) "
            "versus its admitted ancestors; choose 'merge' when it differs from an "
            "ancestor by a single shared property or is not a real object type; "
            "choose 'discard' for noise. For hub-review the candidates are "
            "'discard', 'admit': 'discard' a coincidental value-range hub, 'admit' "
            "a genuine referenced class. Fail closed: when distinctiveness is weak, "
            "prefer 'merge' (or 'discard' for hubs). Respond {choice, confidence}."
        ),
        schema=_ADJUDICATION_SCHEMA,
        few_shot=(
            FewShot(
                input=(
                    '{"task":"spine.adjudicate.admit","kind":"admit",'
                    '"candidates":["merge","admit","discard"],'
                    '"context":{"distinguishing_props":["serial","tail_number","model"],'
                    '"admitted_ancestor_intent":["asset_id"],"n_props":3}}'
                ),
                output={"choice": "admit", "confidence": 0.92},
            ),
            FewShot(
                input=(
                    '{"task":"spine.adjudicate.admit","kind":"admit",'
                    '"candidates":["merge","admit","discard"],'
                    '"context":{"distinguishing_props":["currency"],'
                    '"admitted_ancestor_intent":["amount","currency"],"n_props":1}}'
                ),
                output={"choice": "merge", "confidence": 0.81},
            ),
            FewShot(
                input=(
                    '{"task":"spine.adjudicate.admit","kind":"hub_review",'
                    '"candidates":["discard","admit"],'
                    '"context":{"hub_unity":0.2,"semtype":"generic_integer",'
                    '"value_range_coincidence":true}}'
                ),
                output={"choice": "discard", "confidence": 0.88},
            ),
        ),
    ),
    "spine.adjudicate.er": PromptTemplate(
        task="spine.adjudicate.er",
        version="1",
        instruction=(
            "You are the OntoForge decision-spine adjudicator for ENTITY "
            "RESOLUTION. Given a candidate PAIR's raw evidence, decide whether the "
            "two records denote the SAME real-world entity. Choose EXACTLY ONE of "
            "the candidate strings ('no', 'yes') and a confidence in [0,1]. Honor "
            "the TEMPORAL-REUSE GUARD: a tail number or other reusable identifier "
            "shared across DISJOINT date ranges WITH a serial / hard-identifier "
            "mismatch is NOT the same entity -> answer 'no'. Match on a stable "
            "identifier (serial) plus tail -> 'yes' with high confidence. For "
            "operators, a fuzzy normalized-name match with shared tails is "
            "moderate 'yes'. Prefer 'no' for low-margin pairs. Respond "
            "{choice, confidence}."
        ),
        schema=_ADJUDICATION_SCHEMA,
        few_shot=(
            FewShot(
                input=(
                    '{"task":"spine.adjudicate.er","kind":"er","er_kind":"aircraft",'
                    '"candidates":["no","yes"],'
                    '"context":{"serial":["MSN-4412","MSN-4412"],'
                    '"tail":["N512AA","N512AA"],"model":["A320","A320"]}}'
                ),
                output={"choice": "yes", "confidence": 0.96},
            ),
            FewShot(
                input=(
                    '{"task":"spine.adjudicate.er","kind":"er","er_kind":"aircraft",'
                    '"candidates":["no","yes"],'
                    '"context":{"tail":["N771UA","N771UA"],'
                    '"serial":["MSN-1002","MSN-8890"],'
                    '"date_ranges":[["1998-01-01","2004-12-31"],'
                    '["2011-03-01","2019-09-30"]]}}'
                ),
                output={"choice": "no", "confidence": 0.9},
            ),
            FewShot(
                input=(
                    '{"task":"spine.adjudicate.er","kind":"er","er_kind":"operator",'
                    '"candidates":["no","yes"],'
                    '"context":{"name_norm":["acme air","acme airlines"],'
                    '"shared_tails":["N512AA","N771UA"]}}'
                ),
                output={"choice": "yes", "confidence": 0.66},
            ),
        ),
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
