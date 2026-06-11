"""PSI math sanity (hand-computed references) + EWMA control chart behavior."""

from __future__ import annotations

import math

import pytest

from ontoforge.warden import EwmaChart, population_stability_index, severity_of

EPS = 1e-4
UNIFORM = tuple(i / 10 for i in range(11))  # deciles of U(0,1)


def test_psi_identical_distributions_is_zero():
    psi = population_stability_index(UNIFORM, UNIFORM)
    assert psi == pytest.approx(0.0, abs=1e-9)


def test_psi_half_shift_matches_hand_computation():
    """Baseline U(0,1), current U(0.5,1.5). Hand derivation over the baseline's
    decile buckets (expected share 0.1 each, eps = 1e-4):

      buckets [0,0.1]..[0.4,0.5]: actual mass 0  -> a=eps,
          term = (eps - 0.1) * ln(eps / 0.1), five times;
      buckets [0.5,0.6]..[0.8,0.9]: actual mass exactly 0.1 -> term 0;
      bucket  [0.9,1.0]: actual in-bucket mass 0.1 plus ALL mass above the
          baseline max (CDF_cur(1.0)=0.5 -> 0.5 overflow) -> a=0.6,
          term = (0.6 - 0.1) * ln(0.6 / 0.1).
    """
    current = tuple(0.5 + i / 10 for i in range(11))
    expected = 5 * (EPS - 0.1) * math.log(EPS / 0.1) + 0.5 * math.log(6.0)
    psi = population_stability_index(UNIFORM, current)
    assert psi == pytest.approx(expected, rel=1e-9)


def test_psi_small_shift_is_moderate_and_monotone():
    """A 5% location shift lands well under the 0.2 alarm band; bigger shifts
    produce bigger PSI."""
    small = tuple(0.05 + i / 10 for i in range(11))
    medium = tuple(0.2 + i / 10 for i in range(11))
    psi_small = population_stability_index(UNIFORM, small)
    psi_medium = population_stability_index(UNIFORM, medium)
    assert 0.0 < psi_small < 0.2
    assert psi_small < psi_medium


def test_psi_scale_change_alarms():
    """A 2x scale change (the unit-swap signature) clears the 0.2 threshold."""
    doubled = tuple(2 * q for q in UNIFORM)
    assert population_stability_index(UNIFORM, doubled) > 0.2


def test_psi_handles_tied_deciles():
    """Zero-width buckets from tied baseline deciles merge instead of crashing
    or emitting infinities."""
    tied = (0.0, 0.0, 0.0, 0.0, 0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 1.0)
    psi = population_stability_index(tied, tied)
    assert psi is not None and psi == pytest.approx(0.0, abs=1e-9)
    shifted = tuple(q + 1.0 for q in tied)
    assert population_stability_index(tied, shifted) > 0.2


def test_psi_rejects_malformed_sketches():
    assert population_stability_index((), UNIFORM) is None
    assert population_stability_index(UNIFORM, (1.0, 2.0)) is None


# ----------------------------------------------------------------- EWMA


def test_ewma_stable_stream_never_alarms():
    chart = EwmaChart(warmup=3)
    for x in [0.05, 0.05, 0.05]:
        assert chart.update(x) is None  # warmup
    for _ in range(20):
        dev, limit = chart.update(0.05)
        assert dev <= limit


def test_ewma_catches_seeded_slow_drift_within_3_cycles():
    """A creeping null-rate drift (+0.01/cycle off a 0.05 baseline) must cross
    the 3-sigma EWMA limit within <= 3 post-onset cycles (§5.3 lag target)."""
    chart = EwmaChart(warmup=3)
    for _ in range(3):
        chart.update(0.05)
    caught_at = None
    for cycle, x in enumerate([0.06, 0.07, 0.08], start=1):
        dev, limit = chart.update(x)
        if dev > limit:
            caught_at = cycle
            break
    assert caught_at is not None and caught_at <= 3, f"slow drift not caught (caught_at={caught_at})"


def test_ewma_step_change_caught_immediately():
    chart = EwmaChart(warmup=3)
    for _ in range(3):
        chart.update(0.02)
    dev, limit = chart.update(0.4)  # null spike
    assert dev > limit


def test_ewma_sigma_floor_keeps_limits_finite_on_constant_baseline():
    chart = EwmaChart(warmup=3, sigma_floor=0.005)
    for _ in range(3):
        chart.update(0.0)
    dev, limit = chart.update(0.0)
    assert dev == 0.0 and limit > 0


# -------------------------------------------------------------- severity


def test_severity_mapping_anchors():
    assert severity_of(0.2, 0.2) == pytest.approx(0.5)        # at threshold
    assert severity_of(0.4, 0.2) == pytest.approx(0.99)       # 2x threshold saturates
    assert 0.5 < severity_of(0.25, 0.2) < 0.99                # monotone in between
