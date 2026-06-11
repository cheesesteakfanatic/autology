"""Morphism ledger: append-only persistence (kind 'temper-op'), exact replay,
chain validation, inversion from records."""

from __future__ import annotations

import pytest

from ontoforge.ledger import SqliteLedger
from ontoforge.temper import (
    AddClass,
    RenameClass,
    RenameProperty,
    RetireClass,
    RetypeProperty,
    SplitClass,
    TemperEngine,
    invert_record,
    load_morphisms,
    replay,
)

from m10_helpers import G, auto_accept_spine


def _ops():
    return [
        AddClass(uri="onto://t/c1", name="C1", parent=f"{G}/Place"),
        RenameClass(uri=f"{G}/WorkOrder", new_name="MaintenanceOrder"),
        RenameProperty(class_uri=f"{G}/Aircraft", prop_name="tail_number", new_name="registration_mark"),
        RetireClass(uri=f"{G}/Component"),
        RetypeProperty(class_uri=f"{G}/AircraftModel", prop_name="cruise_speed",
                       new_datatype="float", conversion_spec="linear:2.0:0.0", new_unit="halfmph"),
    ]


def test_replay_reconstructs_exactly(gold, clone_store):
    eng = TemperEngine(gold, clone_store(), auto_accept_spine())
    for op in _ops():
        eng.apply(op, now=42)
    rebuilt = replay(eng.records(), gold)
    assert rebuilt.classes == eng.ontology.classes
    assert rebuilt.version == eng.ontology.version


def test_persistence_via_ledger_kind_temper_op(gold, clone_store):
    led = SqliteLedger()
    eng = TemperEngine(gold, clone_store(), auto_accept_spine(), ledger=led)
    for op in _ops():
        eng.apply(op, now=42)
    loaded = load_morphisms(led)
    assert loaded == eng.records()
    assert replay(loaded, gold).classes == eng.ontology.classes
    kinds = led.connection.execute("SELECT DISTINCT kind FROM artifact").fetchall()
    assert ("temper-op",) in kinds


def test_replay_rejects_broken_chain(gold, clone_store):
    eng = TemperEngine(gold, clone_store(), auto_accept_spine())
    for op in _ops():
        eng.apply(op, now=42)
    records = eng.records()
    with pytest.raises(ValueError, match="chain broken"):
        replay(records[1:], gold)  # missing first link
    with pytest.raises(ValueError, match="chain broken"):
        replay([records[0], records[0]], gold)


def test_invert_record_round_trip(gold, clone_store):
    eng = TemperEngine(gold, clone_store(), auto_accept_spine())
    pre = eng.ontology.clone()
    eng.apply(RenameClass(uri=f"{G}/WorkOrder", new_name="MO"), now=42)
    rec = eng.records()[-1]
    inv = invert_record(rec, pre)
    eng.apply(inv, now=43)
    assert eng.ontology.classes == pre.classes


def test_records_survive_serialization_for_structural_ops(gold, clone_store):
    eng = TemperEngine(gold, clone_store(), auto_accept_spine())
    eng.apply(SplitClass(uri=f"{G}/Registration",
                         parts=(("onto://t/ra", "RA"), ("onto://t/rb", "RB")),
                         discriminator=("status_code", "==", "V")), now=42)
    rec = eng.records()[-1]
    # payload -> record -> operator -> rewrite must reproduce the ontology
    from ontoforge.temper.morphism import MorphismRecord

    rt = MorphismRecord.from_payload(rec.to_payload())
    assert replay([rt], gold).classes == eng.ontology.classes
    assert rt.stats["commits"] == rec.stats["commits"] >= 1
