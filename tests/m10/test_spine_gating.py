"""Spine gating (§3.6): Split/Merge over populated extents are decisions;
low-impact operators auto-apply."""

from __future__ import annotations

import pytest

from ontoforge.contracts import SpineProfile
from ontoforge.spine import DecisionSpine
from ontoforge.temper import (
    AddClass,
    MergeClasses,
    OperatorDeferred,
    RenameClass,
    SplitClass,
    TemperEngine,
)

from m10_helpers import G, auto_accept_spine

SPLIT = SplitClass(uri=f"{G}/WorkOrder",
                   parts=(("onto://t/woA", "WoA"), ("onto://t/woB", "WoB")),
                   discriminator=("action", "==", "REPLACE"))


def test_populated_split_defers_without_confident_spine(gold, clone_store):
    # no T0 rule, no calibration, no model client -> the spine defers to human
    eng = TemperEngine(gold, clone_store(), DecisionSpine(SpineProfile()))
    pre_version = eng.ontology.version
    with pytest.raises(OperatorDeferred):
        eng.apply(SPLIT)
    assert eng.ontology.version == pre_version       # nothing applied
    assert eng.commit_count == 0                     # nothing migrated
    assert eng.records() == []                       # nothing recorded


def test_populated_split_applies_when_spine_accepts(gold, clone_store):
    eng = TemperEngine(gold, clone_store(), auto_accept_spine())
    rep = eng.apply(SPLIT)
    assert rep.gated and rep.decision_id.startswith("temper:SplitClass:")
    assert rep.stats["entities_touched"] == 50


def test_populated_merge_is_gated_too(gold, clone_store):
    eng = TemperEngine(gold, clone_store(), DecisionSpine(SpineProfile()))
    with pytest.raises(OperatorDeferred):
        eng.apply(MergeClasses(c1_uri=f"{G}/IncidentReport", c2_uri=f"{G}/AccidentEvent",
                               new_uri="onto://t/SR", new_name="SR", origin_key="__temper_origin@g"))


def test_empty_extent_split_auto_applies(gold, clone_store):
    # a freshly added class has no extent: low impact -> no spine decision
    eng = TemperEngine(gold, clone_store(), DecisionSpine(SpineProfile()))
    eng.apply(AddClass(uri="onto://t/empty", name="Empty"))
    eng.apply(AddClass(uri="onto://t/discp", name="HasProp"))  # noqa: F841 (context)
    from ontoforge.temper import AddProperty

    eng.apply(AddProperty(class_uri="onto://t/empty", name="kind"))
    rep = eng.apply(SplitClass(uri="onto://t/empty",
                               parts=(("onto://t/e1", "E1"), ("onto://t/e2", "E2")),
                               discriminator=("kind", "==", "x")))
    assert not rep.gated and rep.commits == 0


def test_low_impact_ops_never_gated(gold, clone_store):
    eng = TemperEngine(gold, clone_store(), DecisionSpine(SpineProfile()))
    rep = eng.apply(RenameClass(uri=f"{G}/WorkOrder", new_name="MO"))
    assert not rep.gated and rep.commits == 0


def test_propose_reports_gating_and_impact(gold, clone_store):
    eng = TemperEngine(gold, clone_store(), auto_accept_spine())
    info = eng.propose(SPLIT)
    assert info["valid"] and info["spine_gated"] and info["impact_extent"] == 50
    bad = eng.propose(SplitClass(uri=f"{G}/Aircraft", parts=SPLIT.parts,
                                 discriminator=("year_mfr", "<=", 2000)))
    assert not bad["valid"] and "link range" in bad["reason"]
    assert eng.ontology.version == gold.version  # propose never applies
