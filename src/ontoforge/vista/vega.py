"""M13 VISTA — Vega-Lite v5 emission (whitepaper §6.3 chart-type rules).

Standard NL2VIS encoding rules (measure cardinality × dimension type):

    no dimension            -> single-number KPI (text mark)
    temporal dimension      -> line  (x: temporal, y: quantitative)
    categorical / link dim  -> bar   (x: nominal,  y: quantitative)

Specs are plain dicts with the schema-correct structure ($schema, data.values,
mark, encoding with field/type). ``data.values`` is an empty placeholder until
``render_with_data`` fills it via an executor callable — VISTA never imports
LODESTONE; any ``callable(oqir_term) -> rows`` works.
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from ontoforge.contracts.oqir import Agg, OQIRTerm

from .metrics import DimRef, MetricDef

VEGA_LITE_SCHEMA = "https://vega.github.io/schema/vega-lite/v5.json"


def value_field(metric: MetricDef) -> str:
    """Column name the aggregate's result lands in (shared with OQIR TopK.by).

    Must match the column the LODESTONE executor/typechecker emit for the same
    Aggregate — ``f"{agg}_{measure_prop or 'rows'}"`` (lodestone.execute._aggregate
    and lodestone.typecheck). For COUNT (no measure_prop) that is ``count_rows``;
    naming it ``count`` here desynced the Vega field from the executed rows, so the
    KPI text mark and every COUNT breakdown read a missing field (rendered NaN) and
    the COUNT-by-<dim> ``TopK(by=...)`` failed the type check / raised KeyError
    (swallowed into zero-row breakdowns)."""
    measure = metric.measure_prop.name if metric.measure_prop is not None else "rows"
    return f"{metric.agg.value}_{measure}"


def _axis_title(metric: MetricDef) -> str:
    # human label for the axis; the binding field stays value_field(metric).
    if metric.agg is Agg.COUNT:
        title = "count"
    else:
        title = value_field(metric).replace("_", " ")
    return f"{title} ({metric.unit})" if metric.unit else title


def _base(title: str) -> dict[str, Any]:
    return {
        "$schema": VEGA_LITE_SCHEMA,
        "title": title,
        "data": {"values": []},
    }


def kpi_spec(title: str, metric: MetricDef) -> dict[str, Any]:
    """Scalar KPI: a single big number rendered with a text mark."""
    spec = _base(title)
    spec["mark"] = {"type": "text", "fontSize": 48, "fontWeight": "bold"}
    spec["encoding"] = {
        "text": {"field": value_field(metric), "type": "quantitative", "format": ",.2f"},
    }
    return spec


def bar_spec(title: str, metric: MetricDef, dim: DimRef) -> dict[str, Any]:
    spec = _base(title)
    spec["mark"] = "bar"
    spec["encoding"] = {
        "x": {"field": dim.name, "type": "nominal", "sort": "-y"},
        "y": {"field": value_field(metric), "type": "quantitative", "title": _axis_title(metric)},
    }
    return spec


def line_spec(title: str, metric: MetricDef, dim: DimRef) -> dict[str, Any]:
    spec = _base(title)
    spec["mark"] = "line"
    spec["encoding"] = {
        "x": {"field": dim.name, "type": "temporal"},
        "y": {"field": value_field(metric), "type": "quantitative", "title": _axis_title(metric)},
    }
    return spec


def chart_spec(title: str, metric: MetricDef, dim: Optional[DimRef]) -> dict[str, Any]:
    """Chart-type rule dispatch (§6.3 step 2: dimension type -> mark/encoding)."""
    if dim is None:
        return kpi_spec(title, metric)
    if dim.kind == "temporal":
        return line_spec(title, metric, dim)
    return bar_spec(title, metric, dim)


def render_with_data(dashboard: Any, executor: Callable[[OQIRTerm], list[dict[str, Any]]]) -> Any:
    """Fill every chart's ``data.values`` by executing its OQIR term.

    ``executor`` is any callable mapping an OQIR term to a list of row dicts —
    typically LODESTONE's execution path, but VISTA stays decoupled (AMD-0007):
    only the callable seam is shared. Mutates and returns the dashboard.
    """
    for chart in dashboard.charts:
        rows = executor(chart.oqir)
        chart.vega["data"]["values"] = [dict(r) for r in rows]
    return dashboard


__all__ = [
    "VEGA_LITE_SCHEMA",
    "value_field",
    "kpi_spec",
    "bar_spec",
    "line_spec",
    "chart_spec",
    "render_with_data",
]
