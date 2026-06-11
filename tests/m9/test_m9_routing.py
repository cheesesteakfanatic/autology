"""Routing correctness (§5.3): schema drift -> TemperProposal with the RIGHT
suspected operator; distribution drift -> AnvilReverification carrying the
dependent transform fingerprints; quality drift -> Quarantine + Alert; and
every alarm is a ledgered spine decision."""

from __future__ import annotations


from m9_corruptions import (
    make_trials,
    null_spike,
    run_trial,
    schema_drop,
    schema_rename,
    schema_retype,
    unit_swap,
    value_set_shift,
)
from ontoforge.warden import WardenRouter, warden_spine


def _trial(corruption: str, predicate=lambda t: True):
    return next(t for t in make_trials() if t.corruption == corruption and predicate(t))


# ----------------------------------------------------------- schema -> TEMPER


def test_rename_routes_to_temper_with_rename_operator(estate):
    t = _trial("schema_change", lambda t: "RenameProperty" in t.name)
    res = run_trial(t, estate)
    ops = {(p.column, p.suspected_operator) for p in res.proposals}
    assert (t.target, "RenameProperty") in ops, f"got {ops}"
    # a rename must NOT additionally surface as drop + add
    assert ("RemoveProperty" not in {o for _, o in ops})
    assert ("AddProperty" not in {o for _, o in ops})


def test_drop_routes_to_temper_remove_property(estate):
    t = _trial("schema_change", lambda t: "RemoveProperty" in t.name)
    res = run_trial(t, estate)
    assert any(
        p.column == t.target and p.suspected_operator == "RemoveProperty" for p in res.proposals
    ), res.proposals


def test_retype_routes_to_temper_retype_property(estate):
    t = _trial("schema_change", lambda t: "RetypeProperty" in t.name)
    res = run_trial(t, estate)
    assert any(
        p.column == t.target and p.suspected_operator == "RetypeProperty" for p in res.proposals
    ), res.proposals


def test_added_column_routes_to_add_property(estate):
    import pandas as pd

    from m9_corruptions import Trial

    def add_col(df: pd.DataFrame) -> pd.DataFrame:
        out = df.copy()
        out["CARBON_OFFSET_FLAG"] = ["Y" if i % 2 else "N" for i in range(len(out))]
        return out

    t = Trial(
        name="schema_change/add", corruption="schema_change", table="ntsb_events",
        columns=("EV_ID", "DAMAGE"), target="CARBON_OFFSET_FLAG", inject=add_col,
        is_positive=True, expected_route="temper",
    )
    res = run_trial(t, estate)
    assert any(
        p.column == "CARBON_OFFSET_FLAG" and p.suspected_operator == "AddProperty"
        for p in res.proposals
    ), res.proposals


# ----------------------------------------------------- distribution -> ANVIL


def test_unit_swap_routes_to_anvil_with_transform_fingerprints(estate):
    t = _trial("unit_swap", lambda t: t.target == "SPEED")
    index = {
        ("faa_acftref", "SPEED"): ("tx:conform_speed@v3",),
        "faa_acftref": ("tx:acftref_full_refresh@v1",),
    }
    res = run_trial(t, estate, router=WardenRouter(transform_index=index))
    revs = [r for r in res.reverifications if r.column == "SPEED"]
    assert revs, f"no AnvilReverification for SPEED: {res.reverifications}"
    assert revs[0].transform_ids == ("tx:conform_speed@v3", "tx:acftref_full_refresh@v1")
    assert revs[0].signal.kind == "distribution"
    assert revs[0].signal.detector == "psi"


def test_value_set_shift_routes_to_anvil(estate):
    t = _trial("value_set_shift", lambda t: t.target == "FLIGHT PHASE")
    res = run_trial(t, estate)
    revs = [r for r in res.reverifications if r.column == "FLIGHT PHASE"]
    assert revs and revs[0].signal.detector == "jaccard"
    # no transform index supplied -> empty fingerprints, record still typed
    assert revs[0].transform_ids == ()


# ---------------------------------------------------------- quality -> hold


def test_null_spike_routes_to_quarantine_plus_alert(estate):
    t = _trial("null_spike", lambda t: t.target == "OPERATOR_NAME")
    res = run_trial(t, estate)
    qs = [q for q in res.quarantines if q.column == "OPERATOR_NAME"]
    assert qs and qs[0].signal.kind == "quality" and qs[0].signal.detector == "null_rate_ewma"
    alerts = [a for a in res.alerts if a.column == "OPERATOR_NAME"]
    assert alerts and alerts[0].decision_id == qs[0].decision_id


def test_cardinality_explosion_routes_to_quarantine(estate):
    t = _trial("cardinality_explosion", lambda t: t.target == "ACTION")
    res = run_trial(t, estate)
    assert any(
        q.column == "ACTION" and q.signal.detector == "cardinality_ewma" for q in res.quarantines
    ), res.quarantines


# ------------------------------------------------------- spine integration


def test_alarm_decisions_land_in_the_decision_ledger(estate, ledger):
    """Drift alarms are decisions (§5.3): the spine writes every adjudication
    to the append-only decision ledger."""
    router = WardenRouter(warden_spine(ledger=ledger))
    t = _trial("null_spike", lambda t: t.target == "AIRCRAFT 1 OPERATOR")
    res = run_trial(t, estate, router=router)
    assert res.alarm_count >= 1
    rows = ledger._conn.execute(
        "SELECT decision_id, outcome FROM decision WHERE decision_id LIKE 'warden.alarm/%'"
    ).fetchall()
    assert rows, "no warden.alarm decisions ledgered"
    assert any(outcome == "alarm" for _, outcome in rows)


def test_decision_ids_are_stable_and_descriptive(estate):
    t = _trial("null_spike", lambda t: t.target == "AIRCRAFT 1 OPERATOR")
    res = run_trial(t, estate)
    q = next(q for q in res.quarantines if q.column == "AIRCRAFT 1 OPERATOR")
    assert q.decision_id == (
        "warden.alarm/asrs_reports/AIRCRAFT 1 OPERATOR/null_rate_ewma/c4"
    )


def test_injector_helpers_are_pure(estate):
    """Injectors never mutate the source frame (other trials share it)."""
    df = estate["tables"]["maintenance_erp"][["WORK_ORDER_ID", "ACTION"]].copy()
    snapshot = df.copy()
    for fn in (
        lambda d: null_spike(d, "ACTION"),
        lambda d: unit_swap(d, "ACTION"),
        lambda d: value_set_shift(d, "ACTION"),
        lambda d: schema_rename(d, "ACTION"),
        lambda d: schema_drop(d, "ACTION"),
        lambda d: schema_retype(d, "ACTION"),
    ):
        fn(df)
    assert df.equals(snapshot)
