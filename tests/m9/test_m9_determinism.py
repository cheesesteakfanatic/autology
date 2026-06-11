"""Determinism (§18.4): identical inputs -> byte-identical WARDEN outputs
across compilation, sentinels, routing, and contract emission."""

from __future__ import annotations

from m9_corruptions import make_trials, quick_profile, run_trial
from ontoforge.warden import compile_ontology, emit_contract


def test_compilation_order_and_coverage_stable(gold_ontology):
    r1 = compile_ontology(gold_ontology)
    r2 = compile_ontology(gold_ontology)
    assert r1.coverage == r2.coverage
    key = lambda e: (e.class_uri, e.prop, e.facet)  # noqa: E731
    assert [key(e) for e in r1.expectations] == [key(e) for e in r2.expectations]


def test_expectation_results_repeatable(estate, gold_ontology):
    from ontoforge.warden import evaluate_class

    wo = gold_ontology.by_name("WorkOrder")
    batch = estate["tables"]["maintenance_erp"]
    cmap = {"work_order_id": "WORK_ORDER_ID", "action": "ACTION", "labor_hours": "LABOR_HOURS"}
    r1 = evaluate_class(wo, batch, column_map=cmap)
    r2 = evaluate_class(wo, batch, column_map=cmap)
    assert r1 == r2


def test_trial_pipeline_deterministic_end_to_end(estate):
    """Profile -> sentinel -> spine -> routing twice; identical signals,
    decisions, and routed records."""
    trial = next(t for t in make_trials() if t.corruption == "unit_swap" and t.target == "SPEED")

    def run():
        res = run_trial(trial, estate)
        return (
            [(r.signal, r.decision_id, r.confidence) for r in res.reverifications],
            [(p.signal, p.suspected_operator) for p in res.proposals],
            [(q.signal, q.reason) for q in res.quarantines],
            [(d.decision_id, d.outcome, d.confidence) for d in res.decisions],
        )

    assert run() == run()


def test_profiles_and_contracts_byte_identical(estate):
    df = estate["tables"]["ntsb_events"]
    p1 = quick_profile(df, "ntsb", "ntsb_events")
    p2 = quick_profile(df, "ntsb", "ntsb_events")
    assert p1.columns == p2.columns
    assert emit_contract(p1, key_columns=("EV_ID",)) == emit_contract(p2, key_columns=("EV_ID",))
