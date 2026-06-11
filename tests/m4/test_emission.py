"""End-to-end emission on the synthetic micro estate (§3.4 Output, §3.5).

Three tables -> exactly three admitted classes; checks events, link
properties, functional flags, datatypes, and ShapeConstraints — all derived
by the real pipeline (real M3 profiler, real spine, heuristic tiers).
"""

from __future__ import annotations

import json

import pytest

from ontoforge.contracts import Datatype, ModelRequest
from ontoforge.strata import Strata
from ontoforge.strata.admission import name_concept_handler

from m4_helpers import micro_profiles_and_inds


@pytest.fixture(scope="module")
def micro():
    profiles, inds = micro_profiles_and_inds()
    strata = Strata()
    return strata.induce(profiles, inds)


def _by_name(result):
    return {c.name: c for c in result.ontology.classes.values()}


def test_exactly_the_three_real_types_are_admitted(micro):
    assert sorted(_by_name(micro)) == ["Aircraft", "Flight", "Site"]
    # the lattice held more concepts (top + shared-property intersections);
    # admission folded them away rather than inventing classes
    assert len(micro.lattice) > 3


def test_event_rule(micro):
    """§3.5: timestamp + >=2 link properties + append-mostly -> is_event."""
    classes = _by_name(micro)
    assert classes["Flight"].is_event is True
    assert classes["Aircraft"].is_event is False
    assert classes["Site"].is_event is False


def test_event_rule_requires_append_mostly():
    """Same tables, but no append-mostly CDC signal -> no event class."""
    profiles, inds = micro_profiles_and_inds()
    for tp in profiles:
        tp.append_mostly = False
    result = Strata().induce(profiles, inds)
    assert all(not c.is_event for c in result.ontology.classes.values())


def test_link_properties_target_admitted_classes(micro):
    classes = _by_name(micro)
    flight = classes["Flight"]
    links = {p.name: p for p in flight.properties if p.is_link}
    assert len(links) == 2
    ranges = {micro.ontology.classes[p.range_class].name for p in links.values()}
    assert ranges == {"Aircraft", "Site"}
    # non-event reference classes carry no link properties here
    assert not [p for p in classes["Aircraft"].properties if p.is_link]


def test_datatypes_and_functional_flags(micro):
    aircraft = _by_name(micro)["Aircraft"]
    props = {p.name: p for p in aircraft.properties}
    assert props["year_built"].datatype is Datatype.INTEGER
    assert props["seats"].datatype is Datatype.INTEGER
    assert props["model"].datatype is Datatype.STRING
    # every property is FD-determined by the candidate key -> functional
    assert all(p.functional for p in aircraft.properties)
    flight = _by_name(micro)["Flight"]
    assert {p.name for p in flight.properties} >= {"flight_id", "event_date", "severity"}
    assert flight.prop("event_date").datatype is Datatype.DATE


def test_shapes_compiled_from_profile_stats(micro):
    classes = _by_name(micro)
    aircraft = classes["Aircraft"]
    shapes = {s.prop: s for s in aircraft.shapes}
    # no nulls anywhere in the micro corpus -> min_count 1
    assert all(s.min_count == 1 for s in shapes.values())
    assert all(s.max_count == 1 for s in shapes.values())
    # numeric ranges from the sketch quantiles
    assert shapes["year_built"].min_value == 1979.0
    assert shapes["year_built"].max_value == 2015.0
    # stable code-like formats become regex patterns
    assert shapes["code"].pattern is not None
    import re

    for sample in ("AC1", "AC8"):
        assert re.match(shapes["code"].pattern, sample)
    flight_shapes = {s.prop: s for s in classes["Flight"].shapes}
    assert re.match(flight_shapes["flight_id"].pattern, "F001")


def test_definitions_and_confidence_attached(micro):
    for c in micro.ontology.classes.values():
        assert c.definition, "T2 naming must attach a definition"
        assert 0.0 < c.confidence <= 1.0
        assert c.prov_ref


def test_name_concept_handler_is_deterministic_and_hint_driven():
    payload = {
        "task": "strata.name_concept",
        "intent_hash": "abc",
        "object_hint": "asrs_reports",
        "distinguishing_props": ["acn", "narrative"],
        "event_like": True,
        "tables": ["asrs_reports"],
        "support": 1,
    }
    req = ModelRequest(task="strata.name_concept", prompt=json.dumps(payload, sort_keys=True))
    out1 = name_concept_handler(req)
    out2 = name_concept_handler(req)
    assert out1 == out2
    assert out1["name"] == "AsrsReport"          # camel + singularized hint
    assert out1["definition"]
    # hint-less concepts are named from distinguishing properties
    payload2 = dict(payload, object_hint="", distinguishing_props=["tail_number", "date"])
    out3 = name_concept_handler(
        ModelRequest(task="strata.name_concept", prompt=json.dumps(payload2, sort_keys=True))
    )
    assert out3["name"] == "TailNumberDateEvent"
