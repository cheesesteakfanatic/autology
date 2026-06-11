"""M2 acceptance: split-conformal coverage within +/-2% of nominal
(whitepaper §11.2 M2; §3.4 admission gating).

For 5 fixed seeds and alpha in {0.05, 0.1, 0.2}: fit via Spine.recalibrate()
on the two-Gaussian benchmark, then measure empirical coverage — the fraction
of 10k independent test points whose TRUE outcome lies in the conformal
prediction set — and require |coverage - (1-alpha)| <= 0.02 for every
(seed, alpha) pair. Also checks the §3.4 gate: a singleton set at level alpha
auto-decides even inside the threshold band.
"""

from __future__ import annotations

import pytest

from ontoforge.contracts import DecisionKind, DecisionRequest, SpineProfile, Tier
from ontoforge.spine import DecisionSpine

from m2_helpers import CANDS, GaussianWorld, gaussian_samples

SEEDS = (1, 2, 3, 4, 5)
ALPHAS = (0.05, 0.1, 0.2)
N_TRAIN = 20_000
N_TEST = 10_000
TOLERANCE = 0.02


@pytest.fixture(scope="module")
def fitted_calibrators():
    """One fit per seed, shared across the alpha matrix (split conformal banks
    one score set; the quantile is per-alpha)."""
    cals = {}
    for seed in SEEDS:
        spine = DecisionSpine(SpineProfile())
        spine.recalibrate(DecisionKind.ER, gaussian_samples(DecisionKind.ER, seed, N_TRAIN))
        cal = spine.calibrator(DecisionKind.ER)
        assert cal is not None and cal.fitted
        world = GaussianWorld()
        x1, x2, y, _ = world.sample(seed + 1000, N_TEST)
        feats = [world.features(a, b) for a, b in zip(x1, x2)]
        truths = ["yes" if t else "no" for t in y]
        cals[seed] = (cal, feats, truths)
    return cals


@pytest.mark.parametrize("alpha", ALPHAS)
@pytest.mark.parametrize("seed", SEEDS)
def test_empirical_coverage_within_2pct(fitted_calibrators, seed: int, alpha: float) -> None:
    cal, feats, truths = fitted_calibrators[seed]
    hits = 0
    for f, truth in zip(feats, truths):
        cset = cal.conformal_set(f, CANDS, alpha)
        assert cset is not None
        hits += truth in cset
    coverage = hits / len(truths)
    assert abs(coverage - (1.0 - alpha)) <= TOLERANCE, (
        f"seed={seed} alpha={alpha}: coverage {coverage:.4f} "
        f"outside {1 - alpha} +/- {TOLERANCE}"
    )


def test_quantile_monotone_in_alpha(fitted_calibrators) -> None:
    """Lower miscoverage => larger quantile => (weakly) larger sets."""
    cal, _, _ = fitted_calibrators[1]
    q05, q10, q20 = (cal.conformal_quantile(a) for a in ALPHAS)
    assert q05 >= q10 >= q20


def test_singleton_set_auto_decides_inside_band(fitted_calibrators) -> None:
    """§3.4 gate: with thresholds tightened so nearly everything is 'ambiguous',
    a singleton conformal set at level alpha still auto-decides at T1."""
    spine = DecisionSpine(SpineProfile(name="economy", tau_high=0.9999, tau_low=0.0001, alpha=0.1))
    spine.recalibrate(DecisionKind.ER, gaussian_samples(DecisionKind.ER, seed=1, n=N_TRAIN))
    cal = spine.calibrator(DecisionKind.ER)
    assert cal is not None

    world = GaussianWorld()
    # Strongly 'yes' region: calibrated conf < 0.9999 (in band) but the
    # conformal set is the singleton ('yes',).
    feats = world.features(3.0, 1.5)
    probs = cal.probabilities(feats, CANDS)
    assert probs is not None and probs["yes"] < 0.9999
    assert cal.conformal_set(feats, CANDS, 0.1) == ("yes",)

    res = spine.decide(
        DecisionRequest(kind=DecisionKind.ER, decision_id="single", candidates=CANDS, features=feats)
    )
    assert res.auto_decided and res.tier == Tier.T1
    assert res.outcome == "yes"
    assert res.conformal_set == ("yes",)
    assert "conformal singleton" in res.rationale


def test_ambiguous_set_does_not_use_singleton_gate(fitted_calibrators) -> None:
    """Near the decision boundary the set must contain both candidates and the
    decision must NOT auto-decide via the conformal gate (no client => defer)."""
    spine = DecisionSpine(SpineProfile(name="economy", tau_high=0.9999, tau_low=0.0001, alpha=0.1))
    spine.recalibrate(DecisionKind.ER, gaussian_samples(DecisionKind.ER, seed=1, n=N_TRAIN))
    cal = spine.calibrator(DecisionKind.ER)
    assert cal is not None

    feats = GaussianWorld().features(0.0, 0.0)  # exactly on the Bayes boundary
    cset = cal.conformal_set(feats, CANDS, 0.1)
    assert cset is not None and set(cset) == {"no", "yes"}

    res = spine.decide(
        DecisionRequest(kind=DecisionKind.ER, decision_id="amb", candidates=CANDS, features=feats)
    )
    assert res.deferred_to_human and res.tier == Tier.HUMAN
    assert set(res.conformal_set) == {"no", "yes"}


def test_unfitted_kind_has_no_conformal_sets() -> None:
    spine = DecisionSpine(SpineProfile())
    assert spine.calibrator(DecisionKind.TX) is None
