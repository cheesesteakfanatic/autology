"""Prompt templates: versioned, schema-constrained, grounding + few-shot slots,
deterministic rendering."""

from __future__ import annotations

import json

import pytest

from ontoforge.aimodels.prompts import PROMPTS, get_prompt, render


def test_every_task_has_a_versioned_schema_constrained_template() -> None:
    for task in ("join", "merge", "retype", "name_concept", "answer"):
        t = get_prompt(task)
        assert t.task == task
        assert t.version  # versioned
        # schema parses and is an object schema
        schema = json.loads(t.schema_json)
        assert schema["type"] == "object"


def test_render_is_deterministic_and_injects_grounding() -> None:
    prompt1, schema1 = render(
        "join", "link orders to customers", grounding="- Order(id, customer_id->Customer)"
    )
    prompt2, schema2 = render(
        "join", "link orders to customers", grounding="- Order(id, customer_id->Customer)"
    )
    assert prompt1 == prompt2  # deterministic bytes
    assert schema1 == schema2
    assert "ONTOLOGY GROUNDING" in prompt1
    assert "customer_id->Customer" in prompt1
    assert "## INPUT:" in prompt1
    assert "link orders to customers" in prompt1


def test_few_shot_examples_are_rendered_for_join() -> None:
    prompt, _ = render("join", "link a to b")
    assert "## EXAMPLES:" in prompt
    assert "near-total key overlap" in prompt  # the few-shot output text


def test_grounding_absent_omits_the_block() -> None:
    prompt, _ = render("merge", "merge duplicate suppliers")
    assert "ONTOLOGY GROUNDING" not in prompt


def test_unknown_task_raises() -> None:
    with pytest.raises(KeyError):
        get_prompt("does_not_exist")
