"""M2: determinism — same seed => identical calibrators and identical decisions.

Two independently constructed spines, fed the same seeded calibration data and
the same workload (with deterministic fake clients), must produce byte-equal
DecisionResults, equal spend, and equal calibration reports.
"""

from __future__ import annotations

from ontoforge.contracts import DecisionKind, DecisionRequest, SpineProfile
from ontoforge.spine import DecisionSpine

from m2_helpers import (
    CANDS,
    GaussianWorld,
    ScriptedModelClient,
    gaussian_samples,
    heuristic_request,
)

SEED = 7


def _build_and_run(profile_name: str) -> tuple[list, int, object]:
    client = ScriptedModelClient(t2=("yes", 0.85), t3=("no", 0.6))
    spine = DecisionSpine(SpineProfile(name=profile_name, budget_tokens=50_000), client)
    spine.recalibrate(DecisionKind.ER, gaussian_samples(DecisionKind.ER, SEED, 12_000))

    world = GaussianWorld()
    x1, x2, _, _ = world.sample(SEED + 500, 40)
    workload: list[DecisionRequest] = [
        DecisionRequest(
            kind=DecisionKind.ER,
            decision_id=f"cal{i:03d}",
            candidates=CANDS,
            features=world.features(a, b),
        )
        for i, (a, b) in enumerate(zip(x1, x2))
    ]
    # Uncalibrated kind decisions exercise heuristic + escalation paths too.
    workload += [heuristic_request(DecisionKind.REL, f"rel{i}", s) for i, s in
                 enumerate((0.05, 0.5, 0.65, 0.91, 0.99))]
    results = [spine.decide(r) for r in workload]
    report = spine.calibration_report()["er"]
    return results, spine.spent_tokens(), report


def test_same_seed_identical_decisions_economy() -> None:
    r1, spent1, rep1 = _build_and_run("economy")
    r2, spent2, rep2 = _build_and_run("economy")
    assert r1 == r2  # frozen dataclass equality: every field of every result
    assert spent1 == spent2
    assert rep1 == rep2


def test_same_seed_identical_decisions_crucible() -> None:
    r1, spent1, _ = _build_and_run("crucible")
    r2, spent2, _ = _build_and_run("crucible")
    assert r1 == r2
    assert spent1 == spent2


def test_refit_same_samples_identical_calibrator() -> None:
    samples = gaussian_samples(DecisionKind.SM, SEED, 8_000)
    s1, s2 = DecisionSpine(SpineProfile()), DecisionSpine(SpineProfile())
    s1.recalibrate(DecisionKind.SM, samples)
    s2.recalibrate(DecisionKind.SM, samples)
    c1, c2 = s1.calibrator(DecisionKind.SM), s2.calibrator(DecisionKind.SM)
    assert c1 is not None and c2 is not None
    assert c1.report == c2.report
    for alpha in (0.05, 0.1, 0.2):
        assert c1.conformal_quantile(alpha) == c2.conformal_quantile(alpha)
    f = GaussianWorld().features(0.37, -0.21)
    assert c1.probabilities(f, CANDS) == c2.probabilities(f, CANDS)


def test_different_seeds_differ() -> None:
    """Sanity: the generator is actually seed-sensitive (no trivially constant data)."""
    a = gaussian_samples(DecisionKind.ER, 1, 100)
    b = gaussian_samples(DecisionKind.ER, 2, 100)
    assert a != b
