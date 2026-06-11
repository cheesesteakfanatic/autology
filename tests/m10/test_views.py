"""Backward views / query rewriting: per-op views, composition across
versions, intermediate-version authorship, regroup view for DemoteClass."""

from __future__ import annotations

import pytest

from ontoforge.temper import (
    DemoteClass,
    Deref,
    Direct,
    PromoteProperty,
    RenameProperty,
    RetypeProperty,
    SplitClass,
    StructuredQuery,
    TemperEngine,
    mint_entity_uri,
)

from m10_helpers import G, auto_accept_spine


@pytest.fixture
def eng(gold, clone_store):
    return TemperEngine(gold, clone_store(), auto_accept_spine())


def test_retype_backward_is_inverse_conversion_view(eng, base_answers):
    q = StructuredQuery(f"{G}/IncidentReport", filters=(("altitude_agl", ">", 5000.0),),
                        projection=("acn", "altitude_agl"))
    eng.apply(RetypeProperty(class_uri=f"{G}/IncidentReport", prop_name="altitude_agl",
                             new_datatype="float", conversion_spec="linear:0.25:0.0", new_unit="qft"))
    # stored values are now quarter-scale; the OLD query still filters and
    # projects in its own units via the inverse view
    assert eng.answer(q, eng.base_version) == base_answers[q]
    plan = eng.rewrite(q, eng.base_version)
    acc = plan.branches[0].accessor("altitude_agl")
    assert isinstance(acc, Direct) and acc.fn is not None and acc.fn(250.0) == 1000.0


def test_rename_views_are_identity_but_version_scoped(eng):
    q_old = StructuredQuery(f"{G}/Aircraft", projection=("tail_number",))
    eng.apply(RenameProperty(class_uri=f"{G}/Aircraft", prop_name="tail_number", new_name="reg_mark"))
    q_new = StructuredQuery(f"{G}/Aircraft", projection=("reg_mark",))
    a_old = eng.answer(q_old, eng.base_version)               # authored pre-rename
    a_new = eng.answer(q_new, eng.ontology.version)           # authored post-rename
    assert a_old == a_new and len(a_old) == 40
    # the old name is NOT valid under the new version, and vice versa
    with pytest.raises(ValueError):
        eng.answer(q_new, eng.base_version)
    with pytest.raises(ValueError):
        eng.answer(q_old, eng.ontology.version)


def test_promote_rejoin_view_structure(eng):
    q = StructuredQuery(f"{G}/WorkOrder", filters=(("action", "==", "INSPECT"),),
                        projection=("work_order_id", "action"))
    before = eng.answer(q, eng.base_version)
    eng.apply(PromoteProperty(class_uri=f"{G}/WorkOrder", prop_name="action",
                              new_class_uri="onto://t/Action", new_class_name="Action"))
    assert eng.answer(q, eng.base_version) == before
    plan = eng.rewrite(q, eng.base_version)
    acc = plan.branches[0].accessor("action")
    assert isinstance(acc, Deref) and acc.targets == ("onto://t/Action",)


def test_intermediate_version_query_with_demote_regroup(eng):
    eng.apply(PromoteProperty(class_uri=f"{G}/WorkOrder", prop_name="action",
                              new_class_uri="onto://t/Action", new_class_name="Action"))
    v_promoted = eng.ontology.version
    q_cp = StructuredQuery("onto://t/Action", projection=("value",))
    answer_at_promote = eng.answer(q_cp, v_promoted)
    assert {vals[0] for _e, vals in answer_at_promote} == {'"REPLACE"', '"INSPECT"', '"REPAIR"'}
    eng.apply(DemoteClass(owner_class_uri=f"{G}/WorkOrder", link_prop="action",
                          class_uri="onto://t/Action"))
    # the class is gone, but the version-v_promoted query still answers via
    # the regroup view — same minted entity URIs, same values
    assert eng.answer(q_cp, v_promoted) == answer_at_promote
    uris = {e for e, _ in eng.answer(q_cp, v_promoted)}
    assert mint_entity_uri("onto://t/Action", '"REPLACE"') in uris


def test_views_compose_across_split_then_retype(eng, base_answers):
    q = StructuredQuery(f"{G}/WorkOrder", filters=(("cost", "<=", 50.0),),
                        projection=("work_order_id", "cost"))
    eng.apply(SplitClass(uri=f"{G}/WorkOrder",
                         parts=(("onto://t/woA", "WoA"), ("onto://t/woB", "WoB")),
                         discriminator=("action", "==", "REPLACE")))
    eng.apply(RetypeProperty(class_uri="onto://t/woA", prop_name="cost",
                             new_datatype="float", conversion_spec="linear:2.0:0.0"))
    plan = eng.rewrite(q, eng.base_version)
    assert {b.class_uri for b in plan.branches} == {"onto://t/woA", "onto://t/woB"}
    # only the retyped branch carries the inverse conversion
    fns = {b.class_uri: b.accessor("cost").fn for b in plan.branches}
    assert fns["onto://t/woA"] is not None and fns["onto://t/woB"] is None
    assert eng.answer(q, eng.base_version) == base_answers[q]


def test_unknown_from_version_raises(eng):
    q = StructuredQuery(f"{G}/WorkOrder", projection=("work_order_id",))
    with pytest.raises(ValueError, match="no snapshot"):
        eng.answer(q, 99)
