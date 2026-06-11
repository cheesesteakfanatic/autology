"""Per-operator unit tests: semantics + precondition rejections (§3.6 table)."""

from __future__ import annotations

import pytest

from ontoforge.contracts import Datatype, Layer
from ontoforge.temper import (
    AddClass,
    AddFacet,
    AddProperty,
    DemoteClass,
    DropClass,
    Generalize,
    MergeClasses,
    PreconditionError,
    PromoteProperty,
    QUARANTINE_PROP,
    RenameClass,
    RenameProperty,
    RetireClass,
    RetireFacet,
    RetypeProperty,
    Specialize,
    SplitClass,
    StructuredQuery,
    TemperEngine,
    UnretireClass,
    conversion,
    facet_params,
    is_retired,
    mint_entity_uri,
)

from m10_helpers import G, auto_accept_spine, ent


@pytest.fixture
def eng(gold, clone_store):
    return TemperEngine(gold, clone_store(), auto_accept_spine())


@pytest.fixture
def dry(gold):
    """Engine without a Hearth: ontology-only application."""
    return TemperEngine(gold)


# ------------------------------------------------------------ add / rename


def test_addclass_and_duplicate_rejected(dry):
    dry.apply(AddClass(uri="onto://temper/cls/x", name="X", parent=f"{G}/Place"))
    assert dry.ontology.get("onto://temper/cls/x").parents == (f"{G}/Place",)
    with pytest.raises(PreconditionError):
        dry.apply(AddClass(uri="onto://temper/cls/x", name="X2"))
    with pytest.raises(PreconditionError):
        dry.apply(AddClass(uri="onto://temper/cls/y", name="Y", parent="onto://nope"))


def test_rename_class_uri_stable_zero_migration(eng):
    pre = eng.ontology.classes[f"{G}/WorkOrder"]
    rep = eng.apply(RenameClass(uri=f"{G}/WorkOrder", new_name="MaintenanceOrder"))
    post = eng.ontology.classes[f"{G}/WorkOrder"]
    assert post.uri == pre.uri and post.name == "MaintenanceOrder"
    assert rep.commits == 0
    assert post.properties == pre.properties  # only the label moved


def test_rename_property_label_only_and_shape_follow(dry):
    c = dry.ontology.classes[f"{G}/WorkOrder"]
    p_pre = c.prop("cost")
    rep = dry.apply(RenameProperty(class_uri=f"{G}/WorkOrder", prop_name="cost", new_name="total_cost"))
    c2 = dry.ontology.classes[f"{G}/WorkOrder"]
    p_post = c2.prop("total_cost")
    assert p_post is not None and p_post.uri == p_pre.uri  # URI (and cell key) stable
    assert c2.prop("cost") is None
    assert all(s.prop != "cost" for s in c2.shapes)
    assert rep.commits == 0
    with pytest.raises(PreconditionError):
        dry.apply(RenameProperty(class_uri=f"{G}/WorkOrder", prop_name="nope", new_name="x"))


# --------------------------------------------------------------- retire


def test_retire_tombstones_and_blocks_further_ops(eng):
    eng.apply(RetireClass(uri=f"{G}/Component"))
    assert is_retired(eng.ontology.classes[f"{G}/Component"])
    with pytest.raises(PreconditionError):
        eng.apply(AddProperty(class_uri=f"{G}/Component", name="newp"))
    with pytest.raises(PreconditionError):
        eng.apply(RetireClass(uri=f"{G}/Component"))  # already retired
    # extent remains readable: a base-version query still answers
    q = StructuredQuery(f"{G}/Component", projection=("component_name",))
    assert len(eng.answer(q, eng.base_version)) == 5
    eng.apply(UnretireClass(uri=f"{G}/Component"))
    assert not is_retired(eng.ontology.classes[f"{G}/Component"])


def test_dropclass_requires_untouched(eng):
    with pytest.raises(PreconditionError):  # populated extent
        eng.apply(DropClass(uri=f"{G}/Component"))
    eng.apply(AddClass(uri="onto://temper/cls/z", name="Z"))
    eng.apply(DropClass(uri="onto://temper/cls/z"))
    assert eng.ontology.get("onto://temper/cls/z") is None


# ---------------------------------------------------------------- facets


def test_facet_add_retire_round_trip(dry):
    c = dry.ontology.classes[f"{G}/Aircraft"]
    pre_shapes = c.shapes
    assert pre_shapes, "gold Aircraft should carry shapes"
    target = pre_shapes[0]
    rep = dry.apply(RetireFacet(class_uri=f"{G}/Aircraft", shape=facet_params(target)))
    assert target not in dry.ontology.classes[f"{G}/Aircraft"].shapes
    dry.apply(rep.inverse)  # AddFacet with retained index
    assert dry.ontology.classes[f"{G}/Aircraft"].shapes == pre_shapes
    with pytest.raises(PreconditionError):
        dry.apply(AddFacet(class_uri=f"{G}/Aircraft", shape=facet_params(target.__class__(prop="ghost"))))


# ---------------------------------------------------------------- retype


def test_retype_converts_cells_and_rejects_bad_specs(eng):
    h = eng.adapter.hearth
    before = h.current_value(f"{G}/WorkOrder", ent("wo", 0), "cost")
    rep = eng.apply(RetypeProperty(class_uri=f"{G}/WorkOrder", prop_name="cost",
                                   new_datatype="float", conversion_spec="linear:2.0:0.0", new_unit="USD2"))
    after = h.current_value(f"{G}/WorkOrder", ent("wo", 0), "cost")
    assert after == before * 2.0
    assert rep.stats["cells_written"] == 50 and rep.commits == 1
    assert eng.ontology.classes[f"{G}/WorkOrder"].prop("cost").unit == "USD2"
    with pytest.raises(PreconditionError):
        eng.apply(RetypeProperty(class_uri=f"{G}/WorkOrder", prop_name="cost",
                                 new_datatype="float", conversion_spec="warp-drive"))
    with pytest.raises(PreconditionError):  # link property
        eng.apply(RetypeProperty(class_uri=f"{G}/WorkOrder", prop_name="aircraft",
                                 new_datatype="float", conversion_spec="linear:2.0:0.0"))
    with pytest.raises(PreconditionError):  # int cast on a non-integer
        eng.apply(RetypeProperty(class_uri=f"{G}/WorkOrder", prop_name="action",
                                 new_datatype="float", conversion_spec="int_to_float"))


def test_conversion_registry():
    fwd, inv, spec = conversion("linear:0.25:0.0")
    assert inv(fwd(8.0)) == 8.0 and spec == "linear:4.0:-0.0"
    fwd, inv, _ = conversion("int_to_float")
    assert fwd(3) == 3.0 and inv(3.0) == 3
    with pytest.raises(PreconditionError):
        conversion("linear:0.0:1.0")


# --------------------------------------------------- generalize / specialize


def test_generalize_widen_no_data_move(eng):
    rep = eng.apply(Generalize(class_uri=f"{G}/IncidentReport", parent_uri=f"{G}/SafetyEvent", prop_name="acn"))
    assert rep.commits == 0
    assert eng.ontology.classes[f"{G}/SafetyEvent"].prop("acn") is not None
    assert eng.ontology.classes[f"{G}/IncidentReport"].prop("acn") is None
    with pytest.raises(PreconditionError):  # no longer an own property of the child
        eng.apply(Generalize(class_uri=f"{G}/IncidentReport", parent_uri=f"{G}/SafetyEvent", prop_name="acn"))


def test_specialize_quarantines_violators(eng):
    # event_date lives on SafetyEvent; AccidentEvent instances also hold it ->
    # narrowing to IncidentReport must quarantine all 25 accident entities.
    rep = eng.apply(Specialize(parent_uri=f"{G}/SafetyEvent", child_uri=f"{G}/IncidentReport",
                               prop_name="event_date"))
    assert len(rep.stats["quarantined"]) == 25
    assert rep.commits == 1  # one shard (AccidentEvent) touched
    h = eng.adapter.hearth
    val = h.current_value(f"{G}/AccidentEvent", ent("ae", 0), QUARANTINE_PROP)
    assert val.startswith("specialize:event_date->")
    assert eng.ontology.classes[f"{G}/IncidentReport"].prop("event_date") is not None
    assert eng.ontology.classes[f"{G}/SafetyEvent"].prop("event_date") is None


def test_specialize_no_violators_zero_commits(eng):
    rep = eng.apply(Specialize(parent_uri=f"{G}/Place", child_uri=f"{G}/Airport", prop_name="city"))
    assert rep.commits == 0 and rep.stats["quarantined"] == []


# ------------------------------------------------------------------- split


def test_split_routes_by_discriminator(eng):
    rep = eng.apply(SplitClass(
        uri=f"{G}/Registration",
        parts=(("onto://t/RegV", "ValidReg"), ("onto://t/RegD", "DeregReg")),
        discriminator=("status_code", "==", "V"),
    ))
    routed = rep.stats["routed"]
    assert routed["onto://t/RegV"] == 12 and routed["onto://t/RegD"] == 3
    assert eng.ontology.get(f"{G}/Registration") is None
    assert eng.ontology.get("onto://t/RegV").properties == eng.ontology.get("onto://t/RegD").properties
    # backward union view answers the base-version query identically
    q = StructuredQuery(f"{G}/Registration", filters=(("status_code", "==", "V"),), projection=("aircraft",))
    assert len(eng.answer(q, eng.base_version)) == 12


def test_split_rejects_non_total_discriminator(eng):
    eng.apply(AddProperty(class_uri=f"{G}/Registration", name="sparse"))
    with pytest.raises(PreconditionError, match="not TOTAL"):
        eng.apply(SplitClass(
            uri=f"{G}/Registration",
            parts=(("onto://t/a", "A"), ("onto://t/b", "B")),
            discriminator=("sparse", "==", "x"),
        ))


def test_split_rejects_referenced_or_parented_class(eng):
    with pytest.raises(PreconditionError, match="link range"):
        eng.apply(SplitClass(uri=f"{G}/Aircraft",
                             parts=(("onto://t/a", "A"), ("onto://t/b", "B")),
                             discriminator=("year_mfr", "<=", 2000)))
    with pytest.raises(PreconditionError, match="subclasses"):
        eng.apply(SplitClass(uri=f"{G}/SafetyEvent",
                             parts=(("onto://t/a", "A"), ("onto://t/b", "B")),
                             discriminator=("event_date", "<=", "2024")))


# ------------------------------------------------------------------- merge


def test_merge_backward_view_splits_correctly(eng, base_answers):
    rep = eng.apply(MergeClasses(c1_uri=f"{G}/IncidentReport", c2_uri=f"{G}/AccidentEvent",
                                 new_uri="onto://t/SafetyReport", new_name="SafetyReport",
                                 origin_key="__temper_origin@m"))
    assert rep.stats["entities_touched"] == 65
    merged = eng.ontology.get("onto://t/SafetyReport")
    assert merged is not None and merged.parents == (f"{G}/SafetyEvent",)
    # queries against EITHER source class answer identically via the
    # retained-discriminator split view
    q_inc = StructuredQuery(f"{G}/IncidentReport", filters=(("altitude_agl", ">", 5000.0),),
                            projection=("acn", "altitude_agl"))
    q_acc = StructuredQuery(f"{G}/AccidentEvent", filters=(("fatalities", ">=", 1),),
                            projection=("ntsb_number", "fatalities"))
    assert eng.answer(q_inc, eng.base_version) == base_answers[q_inc]
    assert len(eng.answer(q_acc, eng.base_version)) == 18
    # the rewritten plan carries the origin filter
    plan = eng.rewrite(q_inc, eng.base_version)
    assert any(f for b in plan.branches for f in b.extra_filters)


def test_merge_rejects_overlapping_or_typed_mismatch(eng):
    with pytest.raises(PreconditionError):
        eng.apply(MergeClasses(c1_uri=f"{G}/WorkOrder", c2_uri=f"{G}/WorkOrder",
                               new_uri="onto://t/m", new_name="M"))
    # fold type mismatch: align a float prop onto a string prop
    with pytest.raises(PreconditionError, match="type-compatible|datatype"):
        eng.apply(MergeClasses(c1_uri=f"{G}/WorkOrder", c2_uri=f"{G}/Registration",
                               new_uri="onto://t/m2", new_name="M2",
                               alignment=(("status_code", "cost"),),
                               origin_key="__temper_origin@x"))


# --------------------------------------------------------- promote / demote


def test_promote_deduplicates_values(eng):
    rep = eng.apply(PromoteProperty(class_uri=f"{G}/WorkOrder", prop_name="action",
                                    new_class_uri="onto://t/Action", new_class_name="Action"))
    assert rep.stats["minted"] == 3  # REPLACE / INSPECT / REPAIR deduplicate
    h = eng.adapter.hearth
    link = h.current_value(f"{G}/WorkOrder", ent("wo", 0), "action")
    assert link == mint_entity_uri("onto://t/Action", '"REPLACE"')
    assert h.current_value("onto://t/Action", link, "value") == "REPLACE"
    p = eng.ontology.classes[f"{G}/WorkOrder"].prop("action")
    assert p.is_link and p.range_class == "onto://t/Action"
    # rejoin view: base-version filter on the promoted property still answers
    q = StructuredQuery(f"{G}/WorkOrder", filters=(("action", "==", "REPLACE"),),
                        projection=("work_order_id",))
    assert len(eng.answer(q, eng.base_version)) == 17
    with pytest.raises(PreconditionError):  # already a link
        eng.apply(PromoteProperty(class_uri=f"{G}/WorkOrder", prop_name="action",
                                  new_class_uri="onto://t/Action2", new_class_name="A2"))


def test_demote_flattens_back(eng):
    eng.apply(PromoteProperty(class_uri=f"{G}/WorkOrder", prop_name="action",
                              new_class_uri="onto://t/Action", new_class_name="Action"))
    eng.apply(DemoteClass(owner_class_uri=f"{G}/WorkOrder", link_prop="action",
                          class_uri="onto://t/Action"))
    h = eng.adapter.hearth
    assert h.current_value(f"{G}/WorkOrder", ent("wo", 1), "action") == "INSPECT"
    p = eng.ontology.classes[f"{G}/WorkOrder"].prop("action")
    assert not p.is_link and p.datatype is Datatype.STRING
    assert eng.ontology.get("onto://t/Action") is None
