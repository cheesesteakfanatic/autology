"""M2 acceptance: post-calibration ECE <= 0.05 (whitepaper §11.2 M2).

Synthetic benchmark with KNOWN Bayes posterior: features drawn from two
overlapping Gaussians per outcome. For each of 5 fixed seeds we fit via
Spine.recalibrate() on 20k samples and measure ECE on an INDEPENDENT test set
of >= 2000 samples (we use 10k). A misspecified-feature variant checks that
the Platt/isotonic referee repairs a base model whose raw scores are badly
calibrated.
"""

from __future__ import annotations

import numpy as np
import pytest

from ontoforge.contracts import DecisionKind, SpineProfile
from ontoforge.spine import DecisionSpine, expected_calibration_error

from m2_helpers import (
    CANDS,
    GaussianWorld,
    MisspecifiedWorld,
    gaussian_samples,
    misspecified_samples,
)

SEEDS = (1, 2, 3, 4, 5)
N_TRAIN = 20_000
N_TEST = 10_000  # >= 2000 required by the brief


def _test_set_ece(spine: DecisionSpine, kind: DecisionKind, feats, truths) -> float:
    cal = spine.calibrator(kind)
    assert cal is not None and cal.fitted
    confs, corrs = [], []
    for f, truth in zip(feats, truths):
        probs = cal.probabilities(f, CANDS)
        assert probs is not None
        pick = max(CANDS, key=lambda c: probs[c])
        confs.append(probs[pick])
        corrs.append(pick == truth)
    return expected_calibration_error(confs, corrs)


@pytest.mark.parametrize("seed", SEEDS)
def test_post_calibration_ece_below_005(seed: int) -> None:
    spine = DecisionSpine(SpineProfile())
    spine.recalibrate(DecisionKind.ER, gaussian_samples(DecisionKind.ER, seed, N_TRAIN))

    world = GaussianWorld()
    x1, x2, y, _ = world.sample(seed + 1000, N_TEST)
    feats = [world.features(a, b) for a, b in zip(x1, x2)]
    truths = ["yes" if t else "no" for t in y]
    ece = _test_set_ece(spine, DecisionKind.ER, feats, truths)
    assert ece <= 0.05, f"seed {seed}: test-set ECE {ece:.4f} > 0.05"

    # The exposed held-out ECE must agree with the acceptance bar too.
    held_out = spine.ece(DecisionKind.ER)
    assert held_out is not None and held_out <= 0.05


@pytest.mark.parametrize("seed", SEEDS)
def test_calibrated_probabilities_track_bayes_posterior(seed: int) -> None:
    """Stronger than ECE: the calibrated P(yes) must track the KNOWN Bayes
    posterior on average (anti-reward-hacking: ground truth is analytic)."""
    spine = DecisionSpine(SpineProfile())
    spine.recalibrate(DecisionKind.ER, gaussian_samples(DecisionKind.ER, seed, N_TRAIN))
    cal = spine.calibrator(DecisionKind.ER)
    assert cal is not None

    world = GaussianWorld()
    x1, x2, _, bayes = world.sample(seed + 2000, 4000)
    devs = []
    for a, b, p_star in zip(x1, x2, bayes):
        probs = cal.probabilities(world.features(a, b), CANDS)
        assert probs is not None
        devs.append(abs(probs["yes"] - float(p_star)))
    assert float(np.mean(devs)) <= 0.05


@pytest.mark.parametrize("seed", SEEDS)
def test_misspecified_features_recalibrated(seed: int) -> None:
    """Cubed feature => raw logistic scores are miscalibrated; the selected
    recalibrator (lower held-out ECE of Platt vs isotonic) must repair it."""
    spine = DecisionSpine(SpineProfile())
    spine.recalibrate(DecisionKind.SM, misspecified_samples(DecisionKind.SM, seed, N_TRAIN))
    cal = spine.calibrator(DecisionKind.SM)
    assert cal is not None and cal.fitted
    assert cal.method in ("platt", "isotonic")

    world = MisspecifiedWorld()
    xc, y = world.sample(seed + 1000, N_TEST)
    feats = [world.features(v) for v in xc]
    truths = ["yes" if t else "no" for t in y]
    ece = _test_set_ece(spine, DecisionKind.SM, feats, truths)
    assert ece <= 0.05, f"seed {seed}: misspecified test-set ECE {ece:.4f} > 0.05"


def test_report_exposes_both_methods_and_splits() -> None:
    spine = DecisionSpine(SpineProfile())
    spine.recalibrate(DecisionKind.ER, gaussian_samples(DecisionKind.ER, seed=1, n=N_TRAIN))
    report = spine.calibration_report()["er"]
    assert report.fitted
    assert report.method in ("platt", "isotonic")
    assert report.ece_platt is not None and report.ece_isotonic is not None
    # The referee must have picked the lower held-out ECE.
    assert report.ece == min(report.ece_platt, report.ece_isotonic)
    assert report.n_train + report.n_cal_fit + report.n_select + report.n_conformal == N_TRAIN
    assert report.n_conformal > 0
    assert spine.ece(DecisionKind.ER) == report.ece


def test_uncalibrated_kind_reports_none_and_uses_heuristic() -> None:
    spine = DecisionSpine(SpineProfile())
    assert spine.ece(DecisionKind.REL) is None
    assert spine.calibrator(DecisionKind.REL) is None
    # decide() must still work pre-fit, clearly marked uncalibrated.
    from m2_helpers import heuristic_request

    res = spine.decide(heuristic_request(DecisionKind.REL, "u1", 0.97))
    assert res.outcome == "yes" and res.auto_decided
    assert "uncalibrated" in res.rationale


def test_too_few_samples_stays_uncalibrated() -> None:
    spine = DecisionSpine(SpineProfile())
    spine.recalibrate(DecisionKind.QI, gaussian_samples(DecisionKind.QI, seed=9, n=20))
    cal = spine.calibrator(DecisionKind.QI)
    assert cal is not None and not cal.fitted
    assert spine.ece(DecisionKind.QI) is None
