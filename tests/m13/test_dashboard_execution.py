"""M13 — VISTA dashboards must execute to real, well-shaped data.

Regression for the Build-mode bug: a "<measure> by <dim>" dashboard came back
with the KPI rendering NaN (the Vega text field did not match the executed
column) and every COUNT-by-<dim> breakdown carrying ZERO rows (VISTA named the
COUNT column ``count`` while the LODESTONE executor/typechecker name it
``count_rows`` — so ``TopK(by="count")`` failed the type check and raised a
KeyError that was swallowed into an empty breakdown).

These tests run the REAL LODESTONE execution path (``execute_candidate``) over a
small synthetic HEARTH world — the same callable the server hands to
``render_with_data`` — and assert the end-to-end contract:

  * the KPI chart carries a finite numeric value in the field its Vega spec reads;
  * at least one breakdown chart has >0 rows shaped ``{dim, value_field}``.
"""

from __future__ import annotations

import math

import pytest

from ontoforge.contracts import (
    ClassDef,
    Datatype,
    Layer,
    Ontology,
    PropertyDef,
)
from ontoforge.hearth import Hearth
from ontoforge.ledger import SqliteLedger
from ontoforge.lodestone.execute import ExecOutcome, execute_candidate
from ontoforge.lodestone.model import Candidate
from ontoforge.vista import propose, render_with_data
from ontoforge.vista.vega import value_field

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "m6"))
from m6_helpers import mint_prov, vc  # noqa: E402

CLASS = "of:Order"

# (region, status, amount) — a categorical dim, a second categorical dim, a measure
_FIXTURE = [
    ("ord1", "EMEA", "open", 100.0),
    ("ord2", "EMEA", "closed", 200.0),
    ("ord3", "APAC", "open", 50.0),
    ("ord4", "APAC", "closed", 75.0),
    ("ord5", "APAC", "open", 25.0),
    ("ord6", "AMER", "closed", 300.0),
]


@pytest.fixture()
def world(tmp_path):
    """A tiny real HEARTH world + ontology + the exact executor the server uses."""
    onto = Ontology(version=1)
    onto.add(
        ClassDef(
            uri=CLASS,
            name="Order",
            properties=(
                PropertyDef(uri=f"{CLASS}/region", name="region", datatype=Datatype.STRING),
                PropertyDef(uri=f"{CLASS}/status", name="status", datatype=Datatype.STRING),
                PropertyDef(
                    uri=f"{CLASS}/amount", name="amount", datatype=Datatype.FLOAT, unit="USD"
                ),
            ),
        )
    )
    ledger = SqliteLedger(":memory:")
    hearth = Hearth(tmp_path / "store", ledger)
    cells = []
    for uri, region, status, amount in _FIXTURE:
        e = f"of:order/{uri}"
        cells.append(vc(e, "region", region, mint_prov(ledger, uri, "region")))
        cells.append(vc(e, "status", status, mint_prov(ledger, uri, "status")))
        cells.append(vc(e, "amount", amount, mint_prov(ledger, uri, "amount")))
    hearth.commit(Layer.ENTITY, CLASS, cells, now=1000)

    def executor(term):
        out = execute_candidate(Candidate(cand_id="vista", term=term), onto, hearth)
        if not isinstance(out, ExecOutcome):
            return []
        return [dict(zip(out.columns, row)) for row in out.rows]

    yield onto, executor
    ledger.close()


def _kpi_and_breakdowns(dashboard):
    kpi, *breakdowns = dashboard.charts
    return kpi, breakdowns


def _finite_kpi_value(kpi_chart) -> float:
    """The numeric the KPI text mark binds to — read it the way Vega would."""
    field = kpi_chart.vega["encoding"]["text"]["field"]
    values = kpi_chart.vega["data"]["values"]
    assert values, f"KPI {kpi_chart.title!r} has no data rows"
    assert field in values[0], (
        f"KPI field {field!r} not in executed row {values[0]} "
        f"(Vega field desynced from the executor column)"
    )
    return values[0][field]


def _shaped_breakdown_rows(chart) -> list[dict]:
    """Rows that actually carry both the dim key and the value field."""
    enc = chart.vega["encoding"]
    dim = enc["x"]["field"]
    val = enc["y"]["field"]
    rows = chart.vega["data"]["values"]
    return [r for r in rows if dim in r and val in r]


def _count_dashboard(onto, executor):
    dashboards = propose("order count by region and status", onto, k=3)
    for d in dashboards:
        render_with_data(d, executor)
    # the COUNT dashboard is the one whose KPI metric has no measure (count)
    for d in dashboards:
        if "count" in d.title.lower():
            return d
    return dashboards[0]


def test_kpi_renders_a_finite_number_not_nan(world):
    """Root cause (b): KPI text field must match the executed column."""
    onto, executor = world
    d = _count_dashboard(onto, executor)
    kpi, _ = _kpi_and_breakdowns(d)
    value = _finite_kpi_value(kpi)
    assert isinstance(value, (int, float)) and math.isfinite(float(value))
    assert int(value) == len(_FIXTURE)  # COUNT(*) over the 6 committed orders


def test_count_by_dim_breakdowns_return_real_rows(world):
    """Root cause (a): grouped COUNT breakdowns must produce {dim, count} rows."""
    onto, executor = world
    d = _count_dashboard(onto, executor)
    _, breakdowns = _kpi_and_breakdowns(d)
    assert breakdowns, "no breakdown charts proposed"

    nonempty = [c for c in breakdowns if _shaped_breakdown_rows(c)]
    assert nonempty, (
        "every COUNT breakdown came back with zero {dim,count}-shaped rows "
        "(the TopK column/value-field desync)"
    )

    # the by-region breakdown must total back to the full population
    by_region = next(
        (c for c in breakdowns if c.vega["encoding"]["x"]["field"] == "region"), None
    )
    assert by_region is not None
    rows = _shaped_breakdown_rows(by_region)
    val_field = by_region.vega["encoding"]["y"]["field"]
    counts = {r["region"]: r[val_field] for r in rows}
    assert counts == {"EMEA": 2, "APAC": 3, "AMER": 1}


def test_measure_breakdowns_also_execute(world):
    """The pre-existing AVG/SUM path stays correct (no regression in the fix)."""
    onto, executor = world
    dashboards = propose("total order amount by region", onto, k=3)
    for d in dashboards:
        render_with_data(d, executor)
    measure_boards = [
        d for d in dashboards if any(
            c.vega.get("encoding", {}).get("y", {}).get("field", "").endswith("_amount")
            for c in d.charts
        )
    ]
    assert measure_boards, "no measure dashboard proposed for an amount utterance"
    d = measure_boards[0]
    kpi, breakdowns = _kpi_and_breakdowns(d)
    # KPI field is the executed measure column and resolves to a finite number
    kpi_val = _finite_kpi_value(kpi)
    assert math.isfinite(float(kpi_val))
    assert any(_shaped_breakdown_rows(c) for c in breakdowns)


def test_value_field_matches_executor_column(world):
    """The field oracle VISTA emits is exactly the column the executor produces —
    the contract whose drift caused both bugs. Checked for COUNT and a measure."""
    from ontoforge.vista.metrics import derive_metric_layer

    onto, executor = world
    layer = derive_metric_layer(onto)
    by_agg = {(m.agg.value, m.measure_name): m for m in layer}
    # the count metric and an avg-amount metric both exist for Order
    count_metric = next(m for m in layer if m.measure_prop is None)
    assert value_field(count_metric) == "count_rows"
    amount_metric = next(m for m in layer if m.measure_name == "amount")
    assert value_field(amount_metric) == f"{amount_metric.agg.value}_amount"
    assert by_agg  # layer is non-empty
