"""Application engine: versioning, URI stability / untouched-class identity,
commit accounting (migration cost ∝ touched extent)."""

from __future__ import annotations

import pytest

from ontoforge.temper import (
    AddClass,
    AddProperty,
    PreconditionError,
    RenameClass,
    RetypeProperty,
    SplitClass,
    TemperEngine,
)

from m10_helpers import G, auto_accept_spine


def test_version_bumps_and_snapshots(gold, clone_store):
    eng = TemperEngine(gold, clone_store(), auto_accept_spine())
    v0 = gold.version
    eng.apply(RenameClass(uri=f"{G}/WorkOrder", new_name="MO"))
    eng.apply(AddClass(uri="onto://t/x", name="X"))
    assert eng.ontology.version == v0 + 2
    assert sorted(eng.snapshots) == [v0, v0 + 1, v0 + 2]
    # snapshots are frozen copies of each version
    assert eng.snapshots[v0].classes[f"{G}/WorkOrder"].name == "WorkOrder"
    assert eng.snapshots[v0 + 1].classes[f"{G}/WorkOrder"].name == "MO"


def test_untouched_classes_are_the_identical_objects(gold, clone_store):
    eng = TemperEngine(gold, clone_store(), auto_accept_spine())
    before = dict(eng.ontology.classes)
    eng.apply(RenameClass(uri=f"{G}/WorkOrder", new_name="MO"))
    after = eng.ontology.classes
    for uri, cdef in before.items():
        if uri == f"{G}/WorkOrder":
            assert after[uri] is not cdef
        else:
            assert after[uri] is cdef  # bit-identical: URI stability structural


def test_precondition_failure_changes_nothing(gold, clone_store):
    eng = TemperEngine(gold, clone_store(), auto_accept_spine())
    with pytest.raises(PreconditionError):
        eng.apply(AddProperty(class_uri=f"{G}/WorkOrder", name="cost"))  # clash
    assert eng.ontology.version == gold.version
    assert eng.records() == [] and eng.commit_count == 0


def test_label_only_ops_zero_commits(gold, clone_store):
    eng = TemperEngine(gold, clone_store(), auto_accept_spine())
    eng.apply(RenameClass(uri=f"{G}/WorkOrder", new_name="MO"))
    eng.apply(AddClass(uri="onto://t/x", name="X", parent=f"{G}/Place"))
    eng.apply(AddProperty(class_uri=f"{G}/Airport", name="icao"))
    assert eng.commit_count == 0


def test_migration_cost_proportional_to_touched_extent(gold, clone_store):
    eng = TemperEngine(gold, clone_store(), auto_accept_spine())
    # retype over AccidentEvent (25 entities, 1 prop) -> exactly 25 cells, 1 commit
    rep = eng.apply(RetypeProperty(class_uri=f"{G}/AccidentEvent", prop_name="fatalities",
                                   new_datatype="float", conversion_spec="int_to_float"))
    assert rep.stats["cells_written"] == 25
    assert rep.stats["entities_touched"] == 25
    assert rep.commits == 1
    # a second retype on a 12-entity class costs exactly its extent
    rep2 = eng.apply(RetypeProperty(class_uri=f"{G}/AircraftModel", prop_name="seats",
                                    new_datatype="float", conversion_spec="int_to_float"))
    assert rep2.stats["cells_written"] == 12 and rep2.commits == 1


def test_engine_without_hearth_applies_ontology_only(gold):
    eng = TemperEngine(gold)
    rep = eng.apply(SplitClass(uri=f"{G}/Registration",
                               parts=(("onto://t/a", "A"), ("onto://t/b", "B")),
                               discriminator=("status_code", "==", "V")))
    assert rep.commits == 0 and rep.stats.get("cells_written") is None
    assert eng.ontology.get(f"{G}/Registration") is None
