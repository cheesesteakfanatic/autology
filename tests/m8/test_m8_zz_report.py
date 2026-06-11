"""Final gate report: every measured corruption class must clear the 70% hard
gate. Named test_zz_* so it runs after the suites that fill MEASURED_RATES."""

from __future__ import annotations

from m8_helpers import MEASURED_RATES

GATE = 0.70

EXPECTED_CLASSES = {
    "null_tokens",
    "padding",
    "mixed_case",
    "mixed_dates",
    "currency_strings",
    "unit_mix",
    "dup_rows",
    "header_rows",
    "t1_extraction",
    "t1_ind_join",
}


def test_all_corruption_classes_measured_and_above_gate():
    missing = EXPECTED_CLASSES - MEASURED_RATES.keys()
    assert not missing, f"corruption classes not measured: {sorted(missing)}"
    report = " | ".join(f"{k}={MEASURED_RATES[k]:.3f}" for k in sorted(MEASURED_RATES))
    print(f"\nANVIL measured synthesis fix rates: {report}")
    below = {k: v for k, v in MEASURED_RATES.items() if v < GATE}
    assert not below, f"classes below the {GATE} hard gate: {below}"
