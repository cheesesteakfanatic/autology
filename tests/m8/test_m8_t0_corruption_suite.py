"""SEEDED CORRUPTION SUITE — the M8 hard gate.

Clean synthetic tables are corrupted programmatically (seeded), targets are
defined as ShapeConstraints, and ANVIL must synthesize transforms that fix
>= 70% of corruption instances end-to-end. Measured rates are recorded in
conftest.MEASURED_RATES and reported by test_zz_report.py.
"""

from __future__ import annotations

import pytest

import m8_helpers as H
from m8_helpers import MEASURED_RATES

from ontoforge.anvil import Anvil

GATE = 0.70


def _synthesize(df, *, seed=0):
    anvil = Anvil(seed=seed)
    onto = H.sensor_ontology()
    accepted = anvil.synthesize(df, H.profile(df), H.sensor_class(), onto)
    return anvil, accepted


def _gate_cellwise(corruptor, rate_key):
    clean = H.clean_sensors()
    corrupt, (target_col, spec) = corruptor(clean)
    anvil, accepted = _synthesize(corrupt)
    assert accepted, f"{rate_key}: no transform accepted"
    tdef, report = accepted[0]
    assert report.provenance_equivalent is True
    out = H.run_transform(tdef.sql, corrupt)
    rate = H.cell_fix_rate(out, target_col, spec)
    MEASURED_RATES[rate_key] = rate
    assert rate >= GATE, f"{rate_key}: fix rate {rate:.3f} < {GATE}"
    return tdef, report, rate


def test_null_token_normalization():
    tdef, _, rate = _gate_cellwise(H.corrupt_null_tokens, "null_tokens")
    assert "null_tokens" in tdef.description
    assert rate == 1.0


def test_trim_padding():
    tdef, _, _ = _gate_cellwise(H.corrupt_padding, "padding")
    assert "trim" in tdef.description


def test_case_normalization():
    tdef, _, _ = _gate_cellwise(H.corrupt_case, "mixed_case")
    assert "case" in tdef.description


def test_mixed_date_formats():
    tdef, _, _ = _gate_cellwise(H.corrupt_dates, "mixed_dates")
    assert "date_format" in tdef.description
    assert "STRPTIME" in tdef.sql.upper()


def test_currency_strings():
    tdef, _, _ = _gate_cellwise(H.corrupt_currency, "currency_strings")
    assert "numeric_string" in tdef.description


def test_unit_mix_meters_in_ft_column():
    tdef, _, rate = _gate_cellwise(H.corrupt_units, "unit_mix")
    assert "unit_convert" in tdef.description
    # zero silent failures: every converted instance must be exact
    assert rate == 1.0


def test_duplicate_rows():
    clean = H.clean_sensors()
    corrupt, (_, meta) = H.corrupt_dup_rows(clean)
    anvil, accepted = _synthesize(corrupt)
    assert accepted
    tdef, report = accepted[0]
    out = H.run_transform(tdef.sql, corrupt)
    fixed_dups = (len(corrupt) - len(out)) / meta["n_dup"]
    MEASURED_RATES["dup_rows"] = min(1.0, fixed_dups)
    assert len(out) == meta["n_distinct"], "dedupe must restore the clean grain"
    assert out["sensor_id"].nunique() == meta["n_distinct"]
    assert report.provenance_equivalent is True


def test_header_row_in_data():
    clean = H.clean_sensors()
    corrupt, (_, meta) = H.corrupt_header_rows(clean)
    anvil, accepted = _synthesize(corrupt)
    assert accepted
    tdef, _ = accepted[0]
    out = H.run_transform(tdef.sql, corrupt)
    removed = len(corrupt) - len(out)
    MEASURED_RATES["header_rows"] = removed / meta["n_header"]
    assert removed == meta["n_header"], "all repeated header rows must be filtered"
    assert not (out["sensor_id"].astype(str) == "SENSOR_ID").any()
    assert "header_row" in tdef.description


def test_constant_column_drop_detected():
    df = H.add_constant_column(H.clean_sensors())
    anvil, accepted = _synthesize(df)
    assert accepted
    kinds = {f.kind for f in anvil.last_run.fixes}
    assert "drop_constant" in kinds
    out = H.run_transform(accepted[0][0].sql, df)
    assert "BATCH" not in out.columns


def test_clean_table_synthesizes_plain_conformer():
    """No corruption -> a plain projection/cast program still verifies."""
    clean = H.clean_sensors()
    anvil, accepted = _synthesize(clean)
    assert accepted
    tdef, report = accepted[0]
    assert report.shapes_satisfied and report.provenance_equivalent
    assert report.holdout_pass_rate == 1.0
    assert tdef.synthesized_by == "anvil:T0"


@pytest.mark.parametrize("seed", [0, 1])
def test_holdout_verification_is_seed_stable(seed):
    clean = H.clean_sensors()
    corrupt, _ = H.corrupt_currency(clean)
    _, accepted = _synthesize(corrupt, seed=seed)
    assert accepted and accepted[0][1].shapes_satisfied
