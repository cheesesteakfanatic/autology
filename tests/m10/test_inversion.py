"""Inversion round-trips: op then invert(op) restores the ontology exactly
(Rename, Add/Retire pairs, Split<->Merge with retained discriminator,
Promote<->Demote, Retype with inverse conversion, Generalize<->Specialize)."""

from __future__ import annotations

import pytest

from ontoforge.temper import (
    AddClass,
    AddProperty,
    Generalize,
    MergeClasses,
    PromoteProperty,
    RenameClass,
    RenameProperty,
    RetireClass,
    RetypeProperty,
    Specialize,
    SplitClass,
    TemperEngine,
)

from m10_helpers import G, auto_accept_spine, ent


def _round_trip(gold, store, op):
    eng = TemperEngine(gold, store, auto_accept_spine())
    pre_classes = dict(eng.ontology.classes)
    rep = eng.apply(op)
    assert rep.inverse is not None, f"{op.op_type} should be invertible"
    eng.apply(rep.inverse)
    assert eng.ontology.classes == pre_classes
    assert eng.ontology.version == gold.version + 2
    return eng


CASES = [
    RenameClass(uri=f"{G}/WorkOrder", new_name="MO"),
    RenameProperty(class_uri=f"{G}/WorkOrder", prop_name="cost", new_name="total_cost"),
    AddClass(uri="onto://t/new", name="New", parent=f"{G}/Place"),
    RetireClass(uri=f"{G}/Component"),
    AddProperty(class_uri=f"{G}/Airport", name="icao"),
    RetypeProperty(class_uri=f"{G}/WorkOrder", prop_name="cost", new_datatype="float",
                   conversion_spec="linear:4.0:0.0", new_unit="cUSD"),
    Generalize(class_uri=f"{G}/IncidentReport", parent_uri=f"{G}/SafetyEvent", prop_name="acn"),
    Specialize(parent_uri=f"{G}/Place", child_uri=f"{G}/Airport", prop_name="city"),
    SplitClass(uri=f"{G}/Registration",
               parts=(("onto://t/RegV", "ValidReg"), ("onto://t/RegD", "DeregReg")),
               discriminator=("status_code", "==", "V")),
    PromoteProperty(class_uri=f"{G}/WorkOrder", prop_name="action",
                    new_class_uri="onto://t/Action", new_class_name="Action"),
]


@pytest.mark.parametrize("op", CASES, ids=lambda op: op.op_type)
def test_round_trip_restores_ontology(gold, clone_store, op):
    _round_trip(gold, clone_store(), op)


def test_retype_round_trip_restores_data(gold, clone_store):
    store = clone_store()
    before = store.current_value(f"{G}/WorkOrder", ent("wo", 3), "cost")
    _round_trip(gold, store, RetypeProperty(class_uri=f"{G}/WorkOrder", prop_name="cost",
                                            new_datatype="float", conversion_spec="linear:4.0:0.0"))
    assert store.current_value(f"{G}/WorkOrder", ent("wo", 3), "cost") == before


def test_promote_demote_round_trip_restores_data(gold, clone_store):
    store = clone_store()
    before = store.current_value(f"{G}/WorkOrder", ent("wo", 5), "action")
    _round_trip(gold, store, PromoteProperty(class_uri=f"{G}/WorkOrder", prop_name="action",
                                             new_class_uri="onto://t/Action", new_class_name="Action"))
    assert store.current_value(f"{G}/WorkOrder", ent("wo", 5), "action") == before


def test_merge_of_split_parts_round_trips(gold, clone_store, base_answers):
    """Split -> (invert = Merge with retained discriminator) -> Split again:
    the merge of split products is itself invertible."""
    eng = TemperEngine(gold, clone_store(), auto_accept_spine())
    split = SplitClass(uri=f"{G}/WorkOrder",
                       parts=(("onto://t/woA", "WoA"), ("onto://t/woB", "WoB")),
                       discriminator=("cost", "<=", 100.0))
    rep = eng.apply(split)
    merge = rep.inverse
    assert isinstance(merge, MergeClasses) and merge.new_uri == f"{G}/WorkOrder"
    after_split = dict(eng.ontology.classes)
    rep2 = eng.apply(merge)
    assert eng.ontology.classes == dict(gold.classes)
    # invert(Merge) with total alignment = the Split on the retained origin key
    split_back = rep2.inverse
    assert isinstance(split_back, SplitClass)
    assert split_back.discriminator == (merge.origin_key, "==", "onto://t/woA")
    eng.apply(split_back)
    assert eng.ontology.classes == after_split
    # snapshot-queryability survives the whole zig-zag
    for q in base_answers:
        if q.class_uri == f"{G}/WorkOrder":
            assert eng.answer(q, eng.base_version) == base_answers[q]


def test_non_total_merge_is_not_invertible(gold, clone_store):
    eng = TemperEngine(gold, clone_store(), auto_accept_spine())
    rep = eng.apply(MergeClasses(c1_uri=f"{G}/IncidentReport", c2_uri=f"{G}/AccidentEvent",
                                 new_uri="onto://t/SR", new_name="SR", origin_key="__temper_origin@i"))
    assert rep.inverse is None  # AccidentEvent has unaligned residual properties
