"""Human Actions (§4.3.2): SHACL pre-validation against the gold ontology,
human-edit provenance in the ledger, rank-0 survivorship, and the Action
conflict matrix (§4.5)."""

from __future__ import annotations

import json

import pytest

from m6_helpers import mint_prov, vc

from ontoforge.contracts import Layer, Stance
from ontoforge.hearth import (
    HUMAN_EDIT_KIND,
    ActionValidationError,
    CreateObject,
    Link,
    SetProperty,
    Unlink,
)

AIRCRAFT = "onto://gold/aviation/Aircraft"
MODEL = "onto://gold/aviation/AircraftModel"
E = "e://aircraft/1"


def _create(h, now=1000):
    return h.action(
        "alice",
        CreateObject(AIRCRAFT, E, {"serial_number": "SN-001", "tail_number": "N123AB", "year_mfr": 1999}),
        now=now,
    )


# ------------------------------------------------------------- validation


def test_create_object_validates_and_writes_rank0(gold_hearth) -> None:
    receipt = _create(gold_hearth)
    assert receipt.cells_written == 3
    assert gold_hearth.read(E) == {"serial_number": "SN-001", "tail_number": "N123AB", "year_mfr": 1999}
    for cell in gold_hearth.history(E, "tail_number"):
        assert cell.src_rank == 0


def test_create_object_missing_required_prop(gold_hearth) -> None:
    with pytest.raises(ActionValidationError, match="serial_number"):
        gold_hearth.action("alice", CreateObject(AIRCRAFT, E, {"tail_number": "N123AB"}), now=1000)


@pytest.mark.parametrize(
    ("prop", "value", "why"),
    [
        ("tail_number", "X999", "pattern"),  # ^N[1-9][0-9A-Z]*$
        ("year_mfr", 1850, "min_value"),  # >= 1903
        ("year_mfr", 2150, "max_value"),  # <= 2026
        ("year_mfr", "nineteen99", "integer"),  # PropertyDef datatype
    ],
)
def test_set_property_shape_violations(gold_hearth, prop, value, why) -> None:
    _create(gold_hearth)
    with pytest.raises(ActionValidationError):
        gold_hearth.action("alice", SetProperty(AIRCRAFT, E, prop, value), now=2000)
    # nothing written
    assert gold_hearth.read(E)[prop] != value


def test_in_values_shape(gold_hearth) -> None:
    org = "onto://gold/aviation/Organization"
    gold_hearth.action("alice", CreateObject(org, "e://org/1", {"name": "AcmeAir", "org_kind": "airline"}))
    with pytest.raises(ActionValidationError, match="not in allowed values"):
        gold_hearth.action("alice", SetProperty(org, "e://org/1", "org_kind", "pirate"), now=5_000_000)


def test_unknown_class_rejected(gold_hearth) -> None:
    with pytest.raises(ActionValidationError, match="unknown class"):
        gold_hearth.action("alice", SetProperty("onto://gold/aviation/Nope", E, "x", 1), now=1000)


def test_link_predicate_must_be_object_property(gold_hearth) -> None:
    _create(gold_hearth)
    with pytest.raises(ActionValidationError, match="not a declared object property"):
        gold_hearth.action("alice", Link(AIRCRAFT, E, "serial_number", "e://model/1"), now=2000)
    with pytest.raises(ActionValidationError, match="object property"):
        gold_hearth.action("alice", SetProperty(AIRCRAFT, E, "model", "e://model/1"), now=3000)


def test_empty_actor_rejected(gold_hearth) -> None:
    with pytest.raises(ActionValidationError, match="actor"):
        gold_hearth.action("", SetProperty(AIRCRAFT, E, "serial_number", "SN-9"), now=1000)


# ------------------------------------------------- provenance of human edits


def test_action_provenance_is_a_ledger_registered_human_edit(gold_hearth, ledger) -> None:
    receipt = _create(gold_hearth)
    # the cell's prov_ref resolves to a Leaf over a registered atom
    citations = ledger.valuate_ref(receipt.prov_ref, "citations")
    assert len(citations) == 1
    (atom_id,) = citations
    atom = ledger.get_atom(atom_id)
    assert atom is not None and atom.uri.startswith(f"atom://{HUMAN_EDIT_KIND}/alice/")
    payload = json.loads(atom.value)
    assert payload["actor"] == "alice" and payload["op"] == "CreateObject"
    # and a human-edit artifact row exists, prov-linked (constraint H)
    rows = ledger.connection.execute(
        "SELECT kind, prov_ref FROM artifact WHERE kind = ?", (HUMAN_EDIT_KIND,)
    ).fetchall()
    assert (HUMAN_EDIT_KIND, receipt.prov_ref) in rows
    # every committed cell carries that prov_ref
    for cell in gold_hearth.history(E, "serial_number"):
        assert cell.prov_ref == receipt.prov_ref


# --------------------------------------------------- the conflict matrix


def test_conflict_pipeline_then_action_then_pipeline(gold_hearth, ledger) -> None:
    """Pipeline writes; human overrides; pipeline writes again -> dead on
    arrival, auditable, NOT current (§4.3.2)."""
    prov1 = mint_prov(ledger, "faa", 1)
    gold_hearth.commit(
        Layer.ENTITY, AIRCRAFT, [vc(E, "tail_number", "N111AA", prov1, rank=1)], now=1000
    )
    gold_hearth.action("alice", SetProperty(AIRCRAFT, E, "tail_number", "N222BB"), now=2000)
    prov2 = mint_prov(ledger, "faa", 2)
    gold_hearth.commit(
        Layer.ENTITY, AIRCRAFT, [vc(E, "tail_number", "N333CC", prov2, rank=1)], now=3000
    )
    assert gold_hearth.read(E)["tail_number"] == "N222BB"
    # the clobber attempt is in history with a CLOSED system interval
    doa = [c for c in gold_hearth.history(E, "tail_number") if c.value == "N333CC"]
    assert len(doa) == 1 and not doa[0].system.open
    # and the pre-action belief is reconstructable
    assert gold_hearth.read(E, Stance("as_known_at", known_at=1500))["tail_number"] == "N111AA"


def test_conflict_action_then_action_later_human_wins(gold_hearth) -> None:
    _create(gold_hearth, now=1000)
    gold_hearth.action("bob", SetProperty(AIRCRAFT, E, "tail_number", "N777ZZ"), now=2000)
    assert gold_hearth.read(E)["tail_number"] == "N777ZZ"
    assert gold_hearth.read(E, Stance("as_known_at", known_at=1500))["tail_number"] == "N123AB"


def test_action_link_and_unlink_roundtrip(gold_hearth) -> None:
    _create(gold_hearth, now=1000)
    gold_hearth.action("alice", Link(AIRCRAFT, E, "model", "e://model/737"), now=2000)
    assert gold_hearth.traverse(E, "model") == ["e://model/737"]
    gold_hearth.action("alice", Unlink(AIRCRAFT, E, "model", "e://model/737"), now=3000)
    assert gold_hearth.traverse(E, "model") == []
    assert gold_hearth.traverse(E, "model", Stance("as_of", valid_at=2500)) == ["e://model/737"]
    with pytest.raises(ActionValidationError, match="no current link"):
        gold_hearth.action("alice", Unlink(AIRCRAFT, E, "model", "e://model/737"), now=4000)


def test_actions_without_ontology_skip_validation(hearth) -> None:
    """Hearth built without an ontology: Actions still work (documented),
    shape validation is a no-op."""
    receipt = hearth.action("alice", SetProperty("onto://x/Free", "e://f/1", "anything", 42), now=1000)
    assert receipt.cells_written == 1
    assert hearth.read("e://f/1") == {"anything": 42}
