"""Production-grade templates for the REAL engine escalation tasks.

The spine escalates under four task names — ``strata.name_concept``,
``lodestone.generate``, ``spine.adjudicate.admit`` and ``spine.adjudicate.er``.
These tests assert each task now has a versioned, schema-constrained, few-shot
template that:

  * renders deterministically (equal inputs -> identical bytes),
  * carries a schema whose OBJECT is byte-identical to the schema the live
    call-site already enforces (so structured output + the existing parser keep
    working when a model is dropped in), and
  * is retrievable from a freshly seeded :class:`PromptLibrary` (the library
    seeds from ``PROMPTS``, so no extra registration is needed).

We do NOT assert anything about engine wiring — that is the next crew's job. We
also assert the generic templates (join/merge/retype/name_concept/answer) still
exist for backward-compat.
"""

from __future__ import annotations

import json

import pytest

from ontoforge.aimodels.library import PromptLibrary
from ontoforge.aimodels.prompts import PROMPTS, FewShot, get_prompt, render

# The four task names the engine actually escalates under.
ENGINE_TASKS = (
    "strata.name_concept",
    "lodestone.generate",
    "spine.adjudicate.admit",
    "spine.adjudicate.er",
)

# The generic templates that must survive for backward-compat.
GENERIC_TASKS = ("join", "merge", "retype", "name_concept", "answer")


# --------------------------------------------------------------------------
# presence + versioning + few-shot
# --------------------------------------------------------------------------


@pytest.mark.parametrize("task", ENGINE_TASKS)
def test_engine_task_has_versioned_few_shot_template(task: str) -> None:
    t = get_prompt(task)
    assert t.task == task
    assert t.version  # versioned (a prompt change is a tracked artifact)
    assert len(t.few_shot) >= 2  # 2-3 worked examples per the spec
    assert all(isinstance(ex, FewShot) for ex in t.few_shot)
    assert t.instruction.strip()  # a real instruction framing, not empty


def test_generic_templates_are_preserved_for_backward_compat() -> None:
    for task in GENERIC_TASKS:
        assert task in PROMPTS
        assert get_prompt(task).task == task


def test_strata_name_concept_is_distinct_from_generic_name_concept() -> None:
    """The real task and the generic one coexist under different keys and carry
    different schemas (definition vs rationale)."""
    real = get_prompt("strata.name_concept")
    generic = get_prompt("name_concept")
    assert real.task != generic.task
    assert set(real.schema["required"]) == {"name", "definition"}
    assert set(generic.schema["required"]) == {"name"}


# --------------------------------------------------------------------------
# schema parity with the live call-sites (byte-identical schema OBJECTS)
# --------------------------------------------------------------------------


def test_strata_name_concept_schema_matches_call_site() -> None:
    from ontoforge.strata.admission import _NAME_SCHEMA as CALL_SITE

    ours = get_prompt("strata.name_concept").schema
    # the call-site serializes with sort_keys=True (default separators)
    assert json.dumps(ours, sort_keys=True) == CALL_SITE


def test_lodestone_generate_schema_matches_call_site() -> None:
    from ontoforge.lodestone.candidates import GENERATE_SCHEMA as CALL_SITE

    ours = get_prompt("lodestone.generate").schema
    # the schema root is an ARRAY (not an object) per the candidate contract
    assert ours["type"] == "array"
    assert ours["items"]["required"] == ["term"]
    # the call-site serializes with sort_keys + compact separators
    assert json.dumps(ours, sort_keys=True, separators=(",", ":")) == CALL_SITE


@pytest.mark.parametrize("task", ("spine.adjudicate.admit", "spine.adjudicate.er"))
def test_adjudicate_schema_matches_call_site(task: str) -> None:
    from ontoforge.spine.adjudicator import _RESPONSE_SCHEMA as CALL_SITE

    ours = get_prompt(task).schema
    assert set(ours["required"]) == {"choice", "confidence"}
    # the call-site serializes with sort_keys=True (default separators)
    assert json.dumps(ours, sort_keys=True) == CALL_SITE


# --------------------------------------------------------------------------
# few-shot outputs actually conform to the declared schema (so the examples
# teach the model the SAME shape the parser will validate)
# --------------------------------------------------------------------------


def test_few_shot_outputs_conform_to_schema_shape() -> None:
    name = get_prompt("strata.name_concept")
    for ex in name.few_shot:
        assert isinstance(ex.output, dict)
        assert set(ex.output) >= {"name", "definition"}
        assert isinstance(ex.output["name"], str) and ex.output["name"]
        # PascalCase, singular, event_like -> 'Event' suffix is taught
        assert ex.output["name"][0].isupper()

    gen = get_prompt("lodestone.generate")
    for ex in gen.few_shot:
        assert isinstance(ex.output, list) and ex.output  # array of candidates
        assert len(ex.output) <= 8
        for cand in ex.output:
            assert isinstance(cand["term"], dict)
            assert "op" in cand["term"]  # uses the compositional grammar

    for task in ("spine.adjudicate.admit", "spine.adjudicate.er"):
        adj = get_prompt(task)
        for ex in adj.few_shot:
            assert set(ex.output) == {"choice", "confidence"}
            assert isinstance(ex.output["choice"], str)
            assert 0.0 <= ex.output["confidence"] <= 1.0


def test_er_template_teaches_the_temporal_reuse_guard() -> None:
    """The ER few-shot must contain the reused-tail / serial-mismatch -> 'no'
    case so the live model learns the temporal-reuse guard."""
    er = get_prompt("spine.adjudicate.er")
    no_examples = [ex for ex in er.few_shot if ex.output["choice"] == "no"]
    yes_examples = [ex for ex in er.few_shot if ex.output["choice"] == "yes"]
    assert no_examples and yes_examples  # both verdicts demonstrated
    assert any("date_ranges" in ex.input or "serial" in ex.input for ex in no_examples)


# --------------------------------------------------------------------------
# deterministic rendering (byte-stable) + grounding injection
# --------------------------------------------------------------------------


@pytest.mark.parametrize("task", ENGINE_TASKS)
def test_render_is_byte_deterministic(task: str) -> None:
    grounding = "Aircraft(serial:str, tail:str, operated_by->Operator)"
    p1, s1 = render(task, "the live input", grounding=grounding)
    p2, s2 = render(task, "the live input", grounding=grounding)
    assert p1 == p2  # byte-identical bytes for equal inputs
    assert s1 == s2
    # the task header, the schema, the grounding subset, the examples and the
    # live input all appear, in the fixed render layout
    assert f"# task: {task}@" in p1
    assert "## OUTPUT SCHEMA" in p1
    assert "## ONTOLOGY GROUNDING" in p1
    assert "operated_by->Operator" in p1  # only the injected subset
    assert "## EXAMPLES:" in p1
    assert "## INPUT:" in p1
    assert "the live input" in p1


@pytest.mark.parametrize("task", ENGINE_TASKS)
def test_render_carries_the_schema_json(task: str) -> None:
    _, schema_json = render(task, "x")
    parsed = json.loads(schema_json)
    assert parsed == get_prompt(task).schema  # render hands back the live schema


def test_grounding_absent_omits_the_block() -> None:
    prompt, _ = render("lodestone.generate", "a question with no grounding")
    assert "ONTOLOGY GROUNDING" not in prompt


# --------------------------------------------------------------------------
# PromptLibrary seeds the real tasks (zero extra registration needed)
# --------------------------------------------------------------------------


@pytest.mark.parametrize("task", ENGINE_TASKS)
def test_library_get_returns_the_engine_task_template(task: str) -> None:
    lib = PromptLibrary()  # seeds from PROMPTS
    tmpl = lib.get(task)  # would raise KeyError before this crew's work
    assert tmpl is PROMPTS[task]  # byte-identical to the seed
    assert lib.champion(task) == tmpl.version
    assert tmpl.version in lib.versions(task)


def test_library_holds_both_generic_and_engine_tasks() -> None:
    lib = PromptLibrary()
    known = set(lib.tasks())
    assert known >= set(ENGINE_TASKS)
    assert known >= set(GENERIC_TASKS)
    # no key collision: the real strata task and the generic one are separate
    assert "strata.name_concept" in known and "name_concept" in known
