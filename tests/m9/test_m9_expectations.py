"""Σ-compilation tests: coverage gate on the gold ontology + facet behaviors."""

from __future__ import annotations

import pandas as pd
import pytest

from ontoforge.contracts import ClassDef, Datatype, ShapeConstraint
from ontoforge.warden import compile_class, compile_constraint, compile_ontology, evaluate_class
from ontoforge.warden.expectations import MAX_VIOLATION_ROWS, compile_class_report


def _cls(*shapes: ShapeConstraint, name: str = "T") -> ClassDef:
    return ClassDef(uri=f"onto://test/{name}", name=name, shapes=tuple(shapes))


def _eval(sc: ShapeConstraint, batch: pd.DataFrame, facet: str):
    exps = [e for e in compile_class(_cls(sc)) if e.facet == facet]
    assert exps, f"facet {facet} did not compile from {sc}"
    return exps[0].evaluate(batch)


# ------------------------------------------------------------- coverage gate


def test_sigma_coverage_gate_on_gold_ontology(gold_ontology):
    """§5.3 target / §11.2 M9 acceptance: >= 95% of Σ-expressible constraints
    auto-compiled with zero human authoring (counted, not assumed)."""
    rep = compile_ontology(gold_ontology)
    assert rep.total_constraints >= 20  # the gold artifact really has shapes
    assert rep.compiled_constraints <= rep.total_constraints
    assert rep.coverage >= 0.95, f"coverage {rep.coverage:.3f} < 0.95; skipped: {rep.skipped}"
    assert len(rep.expectations) >= rep.total_constraints  # facets fan out


def test_uncompilable_pattern_counts_against_coverage():
    good = ShapeConstraint(prop="a", pattern="^A$")
    bad = ShapeConstraint(prop="b", pattern="[unclosed")
    rep = compile_class_report(_cls(good, bad))
    assert rep.total_constraints == 2
    assert rep.compiled_constraints == 1
    assert rep.coverage == 0.5
    assert any("unclosed" in s or "pattern" in s for s in rep.skipped)


# ---------------------------------------------------------------- facets


def test_pattern_expectation_flags_violating_rows():
    sc = ShapeConstraint(prop="tail_number", pattern=r"^N[1-9][0-9A-Z]*$")
    batch = pd.DataFrame({"tail_number": ["N123AB", "X999", "N5", "", "N0BAD"]})
    res = _eval(sc, batch, "pattern")
    assert res.violating_rows == (1, 4)  # null at row 3 is skipped (SHACL semantics)
    assert res.n_evaluated == 4
    assert res.severity == "error"
    assert res.pass_rate == pytest.approx(0.5)


def test_in_values_and_range():
    sc = ShapeConstraint(prop="status_code", in_values=("V", "D"))
    batch = pd.DataFrame({"status_code": ["V", "D", "Q", " V "]})
    res = _eval(sc, batch, "in_values")
    assert res.violating_rows == (2,)  # lexical forms are stripped

    sc2 = ShapeConstraint(prop="year_mfr", min_value=1903, max_value=2026)
    batch2 = pd.DataFrame({"year_mfr": ["1968", "1899", "2030", "n/a", "2020"]})
    res2 = _eval(sc2, batch2, "range")
    # 'n/a' is unparseable -> the datatype facet's problem, not a range violation
    assert res2.violating_rows == (1, 2)


def test_min_count_null_policy_and_missing_column():
    sc = ShapeConstraint(prop="serial_number", min_count=1)
    batch = pd.DataFrame({"serial_number": ["S1", "", None, "S2"]})
    res = _eval(sc, batch, "min_count")
    assert res.violating_rows == (1, 2)
    assert not res.passed

    missing = pd.DataFrame({"other": ["x", "y"]})
    res2 = _eval(sc, missing, "min_count")
    assert res2.n_violations == 2  # absent property: every row violates min_count


def test_max_count_multiplicity():
    sc = ShapeConstraint(prop="iata", max_count=1)
    batch = pd.DataFrame({"iata": [["SFO"], ["SFO", "OAK"], "LAX", None]})
    res = _eval(sc, batch, "max_count")
    assert res.violating_rows == (1,)


def test_datatype_conformance():
    sc = ShapeConstraint(prop="engine_count", datatype=Datatype.INTEGER)
    batch = pd.DataFrame({"engine_count": ["2", "two", "3.5", "", "4"]})
    res = _eval(sc, batch, "datatype")
    assert res.violating_rows == (1, 2)

    sc_date = ShapeConstraint(prop="cert_issue_date", datatype=Datatype.DATE)
    batch2 = pd.DataFrame({"cert_issue_date": ["2020-05-01", "20200501", "not a date"]})
    res2 = _eval(sc_date, batch2, "datatype")
    assert res2.violating_rows == (2,)


def test_unit_presence_expectation():
    """A foreign unit suffix violates; plain numbers are assumed canonical
    (the §17.2.1 meters-in-an-ft-column wart is exactly this check)."""
    sc = ShapeConstraint(prop="altitude_agl", unit="ft")
    batch = pd.DataFrame({"altitude_agl": ["3500", "3500 ft", "3500 m", "1066m", ""]})
    res = _eval(sc, batch, "unit")
    assert res.violating_rows == (2, 3)


def test_datatype_inherited_from_property_def():
    """A shape without an explicit datatype still gets a conformance check from
    the class's PropertyDef."""
    from ontoforge.contracts import PropertyDef

    c = ClassDef(
        uri="onto://test/P",
        name="P",
        properties=(PropertyDef(uri="onto://test/P/prop/n", name="n", datatype=Datatype.FLOAT),),
        shapes=(ShapeConstraint(prop="n", min_count=1),),
    )
    facets = {e.facet for e in compile_class(c)}
    assert facets == {"datatype", "min_count"}


def test_violating_rows_capped_at_100():
    sc = ShapeConstraint(prop="x", pattern=r"^A$")
    batch = pd.DataFrame({"x": ["B"] * 150})
    res = _eval(sc, batch, "pattern")
    assert res.n_violations == 150
    assert len(res.violating_rows) == MAX_VIOLATION_ROWS == 100


def test_evaluate_class_with_column_map():
    c = _cls(ShapeConstraint(prop="acn", min_count=1, pattern=r"^[0-9]{7}$"), name="IncidentReport")
    batch = pd.DataFrame({"ACN": ["1500059", "abc", ""]})
    results = evaluate_class(c, batch, column_map={"acn": "ACN"})
    by_facet = {r.facet: r for r in results}
    assert by_facet["pattern"].violating_rows == (1,)
    assert by_facet["min_count"].violating_rows == (2,)


def test_empty_constraint_reports_skip():
    exps, skipped = compile_constraint(_cls(ShapeConstraint(prop="z")), ShapeConstraint(prop="z"))
    assert exps == []
    assert skipped  # declares no executable facet


# ------------------------------------------- gold shapes against real fixtures


def test_gold_workorder_shapes_on_real_erp(estate, gold_ontology):
    """WorkOrder Σ-expectations over maintenance_erp: structurally clean facets
    pass, while the documented 'USD 1,234.56' lexical wart (ANVIL bait, §17.2.1)
    is caught — and ONLY caught — by the cost datatype facet. Range/unit facets
    parse through the currency prefix (magnitude is still in contract)."""
    wo = gold_ontology.by_name("WorkOrder")
    assert wo is not None
    batch = estate["tables"]["maintenance_erp"]
    cmap = {
        "work_order_id": "WORK_ORDER_ID",
        "action": "ACTION",
        "labor_hours": "LABOR_HOURS",
        "cost": "COST",
    }
    results = evaluate_class(wo, batch, column_map=cmap)
    own = [r for r in results if r.prop in cmap]
    assert own, "no WorkOrder expectations evaluated"
    failing = {(r.prop, r.facet) for r in own if not r.passed}
    assert failing == {("cost", "datatype")}, f"unexpected facet outcomes: {failing}"
