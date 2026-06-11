"""Injected-corruption suite (§11.2 M9 acceptance, §5.3 design targets).

40 corruption trials (null spike / unit swap / value-set shift / cardinality
explosion / schema change) + 20 negative trials (clean re-profiles and benign
appends) over real estate tables. HARD GATES: alert precision >= 0.8 and
recall >= 0.9 at the default warden spine threshold; actuals are printed in
the assertion message of the reporting test.
"""

from __future__ import annotations

import pytest

from m9_corruptions import make_trials, run_trial

PRECISION_GATE = 0.8
RECALL_GATE = 0.9


@pytest.fixture(scope="module")
def suite_outcomes(estate):
    trials = make_trials()
    positives = [t for t in trials if t.is_positive]
    negatives = [t for t in trials if not t.is_positive]
    assert len(positives) == 40, "spec: N=40 corruption trials"
    assert len(negatives) == 20
    outcomes = {}
    for t in trials:
        result = run_trial(t, estate)
        outcomes[t.name] = (t, result)
    return outcomes


def _confusion(outcomes):
    tp = fn = fp = tn = 0
    missed, false_alarms = [], []
    for name, (t, res) in outcomes.items():
        alarmed = res.alarm_count > 0
        if t.is_positive:
            tp += alarmed
            fn += not alarmed
            if not alarmed:
                missed.append(name)
        else:
            fp += alarmed
            tn += not alarmed
            if alarmed:
                false_alarms.append(name)
    return tp, fn, fp, tn, missed, false_alarms


def test_detection_recall_gate(suite_outcomes):
    tp, fn, _, _, missed, _ = _confusion(suite_outcomes)
    recall = tp / (tp + fn)
    assert recall >= RECALL_GATE, f"recall {recall:.3f} < {RECALL_GATE}; missed: {missed}"


def test_alert_precision_gate(suite_outcomes):
    tp, _, fp, _, _, false_alarms = _confusion(suite_outcomes)
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    assert precision >= PRECISION_GATE, (
        f"precision {precision:.3f} < {PRECISION_GATE}; false alarms: {false_alarms}"
    )


def test_report_actuals(suite_outcomes):
    """Always-true reporter: makes measured P/R visible in -rA output and keeps
    the numbers honest (both confusion cells must be populated)."""
    tp, fn, fp, tn, missed, false_alarms = _confusion(suite_outcomes)
    precision = tp / (tp + fp) if (tp + fp) else 1.0
    recall = tp / (tp + fn)
    print(
        f"\n[M9 corruption suite] TP={tp} FN={fn} FP={fp} TN={tn} "
        f"precision={precision:.3f} recall={recall:.3f} "
        f"(gates: P>={PRECISION_GATE}, R>={RECALL_GATE})"
    )
    assert tp + fn == 40 and fp + tn == 20


def test_per_corruption_type_coverage(suite_outcomes):
    """No corruption type may be systematically blind: each of the 5 types
    detects at least 7 of its 8 trials."""
    by_type: dict[str, list[bool]] = {}
    for t, res in suite_outcomes.values():
        if t.is_positive:
            by_type.setdefault(t.corruption, []).append(res.alarm_count > 0)
    assert set(by_type) == {
        "null_spike", "unit_swap", "value_set_shift", "cardinality_explosion", "schema_change",
    }
    for kind, hits in by_type.items():
        assert sum(hits) >= 7, f"{kind}: only {sum(hits)}/8 detected"


def test_alarms_are_spine_decisions(suite_outcomes):
    """§5.3 calibration: every routed alarm carries a spine DecisionResult
    ('a drift alarm is a decision with FP/FN costs')."""
    for t, res in suite_outcomes.values():
        records = list(res.proposals) + list(res.reverifications) + list(res.quarantines)
        decision_ids = {d.decision_id for d in res.decisions}
        for rec in records:
            assert rec.decision_id in decision_ids
            assert rec.decision_id.startswith("warden.alarm/")


def test_precision_tunable_via_spine_threshold(estate):
    """Raising tau_high suppresses marginal alarms: with an absurdly strict
    threshold (tau_high > max severity) nothing alarms at all."""
    from ontoforge.contracts import SpineProfile
    from ontoforge.warden import WardenRouter, warden_spine

    strict = warden_spine(SpineProfile(name="economy", tau_high=0.995, tau_low=0.001))
    trial = next(t for t in make_trials() if t.corruption == "null_spike")
    res = run_trial(trial, estate, router=WardenRouter(strict))
    assert res.alarm_count == 0
    assert res.suppressed  # the signal still existed; the spine held it back
