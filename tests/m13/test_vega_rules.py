"""M13 — chart-type rules: dimension type drives mark/encoding (§6.3)."""

from __future__ import annotations

import pytest

from ontoforge.estates import load_gold_ontology
from ontoforge.vista import chart_spec, derive_metric_layer, value_field
from ontoforge.vista.metrics import DimRef


@pytest.fixture(scope="module")
def cost_metric():
    layer = derive_metric_layer(load_gold_ontology())
    return next(m for m in layer if m.name == "avg_cost")


def test_scalar_kpi_is_single_number(cost_metric):
    spec = chart_spec("t", cost_metric, None)
    assert spec["mark"]["type"] == "text"
    assert spec["encoding"]["text"]["field"] == "avg_cost"
    assert spec["encoding"]["text"]["type"] == "quantitative"


def test_categorical_dim_is_bar(cost_metric):
    dim = next(d for d in cost_metric.dims if d.name == "action")
    spec = chart_spec("t", cost_metric, dim)
    assert spec["mark"] == "bar"
    assert spec["encoding"]["x"] == {"field": "action", "type": "nominal", "sort": "-y"}
    assert spec["encoding"]["y"]["type"] == "quantitative"
    assert "(USD)" in spec["encoding"]["y"]["title"]   # unit surfaces on the axis


def test_temporal_dim_is_line(cost_metric):
    dim = next(d for d in cost_metric.dims if d.name == "open_date")
    spec = chart_spec("t", cost_metric, dim)
    assert spec["mark"] == "line"
    assert spec["encoding"]["x"] == {"field": "open_date", "type": "temporal"}


def test_link_dim_is_bar(cost_metric):
    dim = next(d for d in cost_metric.dims if d.kind == "link")
    spec = chart_spec("t", cost_metric, dim)
    assert spec["mark"] == "bar"
    assert spec["encoding"]["x"]["type"] == "nominal"


def test_value_field_naming(cost_metric):
    assert value_field(cost_metric) == "avg_cost"


def test_data_values_placeholder(cost_metric):
    spec = chart_spec("t", cost_metric, DimRef(name="action", kind="categorical"))
    assert spec["data"] == {"values": []}
