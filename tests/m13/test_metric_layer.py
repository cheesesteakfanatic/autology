"""M13 — metric-layer derivation from the gold ontology (§6.3, §1.2 M^(t))."""

from __future__ import annotations

import pytest

from ontoforge.contracts.oqir import Agg
from ontoforge.estates import load_gold_ontology
from ontoforge.vista import derive_metric_layer


@pytest.fixture(scope="module")
def gold():
    return load_gold_ontology()


@pytest.fixture(scope="module")
def layer(gold):
    return derive_metric_layer(gold)


def by_name(layer):
    return {m.name: m for m in layer}


def test_expected_measures_present_with_units(layer):
    """The hero-estate measures (§17.2.1) must surface with canonical units."""
    m = by_name(layer)
    assert m["avg_altitude_agl"].unit == "ft"
    assert m["avg_cost"].unit == "USD"
    assert m["avg_labor_hours"].unit == "h"
    assert m["sum_cost"].unit == "USD"
    assert m["avg_cruise_speed"].unit == "mph"


def test_measure_metrics_carry_the_property_def(layer):
    m = by_name(layer)["avg_cost"]
    assert m.agg is Agg.AVG
    assert m.measure_prop is not None
    assert m.measure_prop.name == "cost"
    assert m.measure_prop.dimension is not None  # currency dimension
    assert m.class_name == "WorkOrder"


def test_count_metric_per_class(layer, gold):
    names = {m.name for m in layer}
    assert "workorder_count" in names
    assert "incidentreport_count" in names
    count_metrics = [m for m in layer if m.agg is Agg.COUNT]
    for cm in count_metrics:
        assert cm.measure_prop is None
        assert cm.unit is None


def test_dims_are_categorical_temporal_or_link(layer):
    m = by_name(layer)["avg_cost"]
    kinds = {d.name: d.kind for d in m.dims}
    # categorical prop, link props, temporal props of WorkOrder
    assert kinds["action"] == "categorical"
    assert kinds["aircraft"] == "link"
    assert kinds["operator"] == "link"
    assert kinds["open_date"] == "temporal"
    # link dims carry the target class name
    aircraft_dim = next(d for d in m.dims if d.name == "aircraft")
    assert aircraft_dim.target == "Aircraft"


def test_identifier_and_text_columns_excluded_from_dims(layer):
    m = by_name(layer)["avg_altitude_agl"]
    names = {d.name for d in m.dims}
    assert "flight_phase" in names
    assert "acn" not in names          # identifier
    assert "narrative" not in names    # TEXT prop: textJoin surface, not a dim
    wo = by_name(layer)["avg_cost"]
    assert "work_order_id" not in {d.name for d in wo.dims}


def test_derived_not_hand_authored(gold, layer):
    """Every metric references a real class/property in O — nothing invented."""
    from ontoforge.vista.metrics import effective_properties

    for m in layer:
        cls = gold.get(m.class_uri)
        assert cls is not None
        if m.measure_prop is not None:
            assert cls.prop(m.measure_prop.name) is not None
        names = {p.name for p in effective_properties(cls, gold)}
        for d in m.dims:
            assert d.name in names  # own or inherited via subsumption


def test_deterministic(gold):
    a = derive_metric_layer(gold)
    b = derive_metric_layer(gold)
    assert a == b
    assert [m.name for m in a] == sorted(set(m.name for m in a), key=[m.name for m in a].index)
    assert len({m.name for m in a}) == len(a)  # names unique
