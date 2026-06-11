"""Unit/dimension inference suite (§3.2) — incl. mixed-unit detection: zero silent merges."""

from __future__ import annotations

import random

from ontoforge.contracts import CURRENCY, LENGTH, MASS, SPEED, COUNT
from ontoforge.profiling import (
    dimension_of,
    infer_unit,
    parse_value_suffix,
    resolve_token,
    split_name_tokens,
)


# ------------------------------------------------------------- name evidence


def test_name_evidence_aviation_columns():
    cases = {
        "altitude_ft": ("ft", LENGTH),
        "weight_lbs": ("lb", MASS),
        "speed_kt": ("kt", SPEED),
        "cost_usd": ("USD", CURRENCY),
        "distance_nm": ("nm", LENGTH),
        "fuel_qty": ("count", COUNT),
    }
    for name, (unit, dim) in cases.items():
        inf = infer_unit(name, [100, 200, 300, 250, 175])
        assert inf.unit == unit, name
        assert inf.dimension == dim, name
        assert not inf.mixed and not inf.conflict
        assert inf.confidence > 0.0
        assert dimension_of(name, [100, 200]) == dim


def test_camel_case_and_context_gated_tokens():
    assert split_name_tokens("altitudeFt") == ["altitude", "ft"]
    # 'f' alone is not Fahrenheit; 'temp_f' is
    assert resolve_token("f", context="f") is None
    hit = resolve_token("f", context="temp_f")
    assert hit is not None and hit[0].symbol == "F"


def test_no_unit_on_plain_columns():
    inf = infer_unit("o_orderkey", [1, 2, 3, 4])
    assert inf.unit is None and inf.dimension is None and inf.confidence == 0.0


# ------------------------------------------------------------ value suffixes


def test_parse_value_suffix():
    assert parse_value_suffix("250 kt") == (250.0, "kt")
    assert parse_value_suffix("1,200 ft") == (1200.0, "ft")
    assert parse_value_suffix("34.5") == (34.5, None)
    assert parse_value_suffix("N123AB") is None


def test_value_suffix_evidence():
    inf = infer_unit("airspeed", ["250 kt", "310 kt", "295 kt", "288 kt"])
    assert inf.unit == "kt" and inf.dimension == SPEED
    assert inf.source == "values" and inf.confidence >= 0.9


def test_name_and_values_corroborate():
    inf = infer_unit("speed_kt", ["250 kt", "310 kt", "295 kt"])
    assert inf.unit == "kt" and inf.source == "name+values"
    assert inf.confidence >= 0.95


# -------------------------------------------------- mixed units: never merged


def test_mixed_suffix_units_flagged_never_merged():
    vals = ["1200 ft", "3500 ft", "2200 ft", "800 m", "950 m", "600 m"]
    inf = infer_unit("altitude", vals)
    assert inf.mixed is True
    assert inf.unit is None                      # nothing silently asserted
    assert inf.dimension == LENGTH               # common dimension is still known
    syms = {s for s, _ in inf.observed_units}
    assert {"ft", "m"} <= syms


def test_mixed_units_across_dimensions_has_no_dimension():
    vals = ["250 kt", "260 kt", "1200 ft", "1300 ft"]
    inf = infer_unit("reading", vals)
    assert inf.mixed is True and inf.unit is None and inf.dimension is None


def test_magnitude_bimodality_catches_suffixless_unit_mixing():
    # column claims pounds; 40% of rows are actually kilograms (ratio ~2.2046)
    rng = random.Random(5)
    lbs = [round(rng.uniform(180.0, 220.0), 1) for _ in range(18)]
    kgs = [round(rng.uniform(82.0, 100.0), 1) for _ in range(12)]
    inf = infer_unit("weight_lb", lbs + kgs)
    assert inf.mixed is True and inf.conflict is True
    assert inf.unit is None                      # flagged, not merged
    assert inf.dimension == MASS
    assert inf.source == "magnitude"
    assert {s for s, _ in inf.observed_units} == {"lb", "kg"}


def test_unimodal_magnitudes_do_not_false_alarm():
    rng = random.Random(6)
    vals = [round(rng.uniform(180.0, 260.0), 1) for _ in range(40)]
    inf = infer_unit("weight_lb", vals)
    assert inf.mixed is False and inf.unit == "lb"


# ------------------------------------------------------------------ conflict


def test_name_value_conflict_escalates_with_value_winning():
    inf = infer_unit("distance_ft", ["12 km", "9 km", "15 km", "11 km"])
    assert inf.conflict is True
    assert inf.unit == "km"                      # data over documentation
    assert inf.confidence <= 0.5                 # spine must escalate (§3.2)


# ------------------------------------------------------------- conversions


def test_unit_table_conversions_are_affine_correct():
    from ontoforge.profiling import UNITS
    assert abs(UNITS["ft"].to_canonical(1.0) - 0.3048) < 1e-12
    assert abs(UNITS["kt"].to_canonical(1.0) - 0.514444) < 1e-9
    assert abs(UNITS["C"].to_canonical(100.0) - 373.15) < 1e-9
    assert abs(UNITS["F"].to_canonical(32.0) - 273.15) < 1e-9
    assert abs(UNITS["F"].from_canonical(UNITS["F"].to_canonical(98.6)) - 98.6) < 1e-9
