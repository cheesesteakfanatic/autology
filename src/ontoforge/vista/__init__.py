"""M13 — VISTA: vague-spec dashboard synthesis (whitepaper §6.3).

Minimal scope per AMD-0007: metric-layer derivation from O^(t), ranked
composition search, and Vega-Lite v5 emission. Usage/health priors, the
spine-gated acceptance loop, and TEMPER artifact migration are deferred.
Depends only on contracts.oqir types — never on LODESTONE internals.
"""

from .compose import Chart, Dashboard, propose
from .metrics import DimRef, MetricDef, candidate_dims, derive_metric_layer, is_measure
from .vega import (
    VEGA_LITE_SCHEMA,
    bar_spec,
    chart_spec,
    kpi_spec,
    line_spec,
    render_with_data,
    value_field,
)

__all__ = [
    "Chart",
    "Dashboard",
    "propose",
    "DimRef",
    "MetricDef",
    "candidate_dims",
    "derive_metric_layer",
    "is_measure",
    "VEGA_LITE_SCHEMA",
    "bar_spec",
    "chart_spec",
    "kpi_spec",
    "line_spec",
    "render_with_data",
    "value_field",
]
