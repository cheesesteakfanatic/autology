"""Deterministic command-parser tests: every kind parses to the right op, an
ambiguous slot clarifies (never guesses), an unknown verb is unsupported."""

from __future__ import annotations

from ontoforge.engineer.commands import (
    ClarificationNeeded,
    ProposedCommand,
    SchemaView,
    UnsupportedCommand,
    parse_command,
)


def test_unknown_verb_is_unsupported(schema: SchemaView) -> None:
    out = parse_command("do a barrel roll", schema)
    assert isinstance(out, UnsupportedCommand)
    assert out.supported_examples  # actionable examples surfaced


def test_empty_command_is_unsupported(schema: SchemaView) -> None:
    assert isinstance(parse_command("   ", schema), UnsupportedCommand)


def test_link_parses_to_link_with_on_columns(schema: SchemaView) -> None:
    out = parse_command("link salelines to catalog on sku", schema)
    assert isinstance(out, ProposedCommand)
    assert out.kind == "link"
    assert out.params["on_left"] == "sku"
    assert out.params["on_right"] == "sku"
    assert out.params["left_class"] is not None
    assert out.params["right_class"] is not None


def test_link_handles_join_and_using_phrasings(schema: SchemaView) -> None:
    out = parse_command("join salelines and catalog using sku", schema)
    assert isinstance(out, ProposedCommand)
    assert out.kind == "link"
    assert out.params["on_left"] == "sku"


def test_link_with_unknown_endpoint_clarifies(schema: SchemaView) -> None:
    out = parse_command("link salelines to nonsuch on sku", schema)
    assert isinstance(out, ClarificationNeeded)
    assert "nonsuch" in out.clarification


def test_link_missing_second_endpoint_clarifies(schema: SchemaView) -> None:
    out = parse_command("link salelines to", schema)
    assert isinstance(out, ClarificationNeeded)


def test_rename_property_parses(schema: SchemaView) -> None:
    out = parse_command("rename country to nation", schema)
    assert isinstance(out, ProposedCommand)
    assert out.kind == "rename"
    assert out.params["new_name"] == "nation"
    assert out.params["kind_target"] == "property"


def test_rename_unknown_target_clarifies(schema: SchemaView) -> None:
    out = parse_command("rename frobnicator to widget", schema)
    assert isinstance(out, ClarificationNeeded)


def test_retype_parses_with_type(schema: SchemaView) -> None:
    # 'qty' conforms to 'quantity' at materialization — use the induced name
    out = parse_command("treat quantity as number", schema)
    assert isinstance(out, ProposedCommand)
    assert out.kind == "retype"
    assert out.params["target_type"] == "number"


def test_retype_unknown_type_clarifies(schema: SchemaView) -> None:
    out = parse_command("treat quantity as a banana", schema)
    assert isinstance(out, ClarificationNeeded)
    assert "type" in out.clarification.lower()


def test_merge_parses_to_class(schema: SchemaView) -> None:
    out = parse_command("merge duplicate salelines", schema)
    assert isinstance(out, ProposedCommand)
    assert out.kind == "merge_entities"
    assert out.params["class_uri"] is not None


def test_split_parses_with_parts_and_delim(schema: SchemaView) -> None:
    out = parse_command("split pname into first and last on space", schema)
    assert isinstance(out, ProposedCommand)
    assert out.kind == "split"
    assert out.params["parts"] == ["first", "last"]
    assert out.params["delimiter"] == " "


def test_synonym_parses(schema: SchemaView) -> None:
    out = parse_command("sku means the same as pname", schema)
    assert isinstance(out, ProposedCommand)
    assert out.kind == "synonym"


def test_parse_is_deterministic(schema: SchemaView) -> None:
    a = parse_command("link salelines to catalog on sku", schema)
    b = parse_command("link salelines to catalog on sku", schema)
    assert isinstance(a, ProposedCommand) and isinstance(b, ProposedCommand)
    assert a.params == b.params and a.human_summary == b.human_summary
