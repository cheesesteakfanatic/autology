"""Real aviation-estate cases (§17.2.1 documented warts are ANVIL bait):
maintenance_erp COST 'USD 1,234.56' mix -> clean decimal; asrs altitude
meters-wart -> ft conversion. Both verified on seeded holdouts."""

from __future__ import annotations

import re

import pytest

import m8_helpers as H

from ontoforge.anvil import Anvil
from ontoforge.profiling import profile_table

FT_PER_M = 1.0 / 0.3048


@pytest.fixture(scope="module")
def erp_run(estate, gold_ontology):
    df = estate["tables"]["maintenance_erp"]
    anvil = Anvil(seed=0)
    accepted = anvil.synthesize(
        df, profile_table(df, "erp", "maintenance_erp"),
        gold_ontology.by_name("WorkOrder"), gold_ontology,
    )
    return df, anvil, accepted


@pytest.fixture(scope="module")
def asrs_run(estate, gold_ontology):
    df = estate["tables"]["asrs_reports"]
    anvil = Anvil(seed=0)
    accepted = anvil.synthesize(
        df, profile_table(df, "asrs", "asrs_reports"),
        gold_ontology.by_name("IncidentReport"), gold_ontology,
    )
    return df, anvil, accepted


def test_erp_cost_mix_to_clean_decimal(erp_run):
    df, anvil, accepted = erp_run
    assert accepted
    tdef, report = accepted[0]
    assert report.shapes_satisfied and report.provenance_equivalent
    assert report.holdout_pass_rate == 1.0
    assert "numeric_string" in tdef.description
    out = H.run_transform(tdef.sql, df, table="maintenance_erp")
    styled = [(i, c) for i, c in enumerate(df["COST"]) if re.fullmatch(r"USD [0-9,]+\.[0-9]{2}", c)]
    assert styled, "wart precondition"
    fixed = sum(
        1 for i, c in styled
        if abs(out["cost"].iloc[i] - float(c[4:].replace(",", ""))) < 1e-9
    )
    assert fixed == len(styled), "every styled COST must parse to the exact decimal"
    assert out["cost"].notna().all()


def test_asrs_altitude_meters_wart_to_ft(asrs_run):
    df, anvil, accepted = asrs_run
    assert accepted
    tdef, report = accepted[0]
    assert report.shapes_satisfied and report.provenance_equivalent
    assert "unit_convert" in tdef.description
    out = H.run_transform(tdef.sql, df, table="asrs_reports")
    meters = [(i, v) for i, v in enumerate(df["ALTITUDE.AGL.SINGLE VALUE"])
              if re.fullmatch(r"[0-9]+(\.[0-9]+)?m", v)]
    assert len(meters) >= 20, "wart precondition"
    for i, v in meters:
        expected = float(v[:-1]) * FT_PER_M
        got = out["altitude_agl"].iloc[i]
        assert abs(got - expected) <= 1e-6 * expected, f"row {i}: {v} -> {got} != {expected}"
    # blanks remain permissible NULLs, never zero-filled
    blanks = [i for i, v in enumerate(df["ALTITUDE.AGL.SINGLE VALUE"]) if not v.strip()]
    if blanks:
        assert out["altitude_agl"].iloc[blanks].isna().all()


def test_asrs_shapes_carry_unit_expectations(asrs_run, gold_ontology):
    _, _, accepted = asrs_run
    tdef, _ = accepted[0]
    alt = [sc for sc in tdef.expectations if sc.prop == "altitude_agl"]
    assert alt and alt[0].unit == "ft"


def test_no_silent_unit_mixing_note_present(asrs_run):
    """§3.2: the conversion is explicit and documented in the artifact."""
    _, anvil, accepted = asrs_run
    tdef, _ = accepted[0]
    assert "converted to target unit 'ft'" in tdef.description
