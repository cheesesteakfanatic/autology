"""TX acceptance through the Decision Spine: accepted -> TransformDef + ledger
'transform' artifact; ambiguous -> ledger 'review' record; provenance failure
-> rejected. Readability invariant on all accepted SQL."""

from __future__ import annotations

import json

import sqlglot

import m8_helpers as H

from ontoforge.anvil import Acceptor, Anvil, CandidateProgram, ColumnExpr, pretty_sql
from ontoforge.contracts import DecisionKind, VerificationReport
from ontoforge.ledger import SqliteLedger


def _program():
    return CandidateProgram(
        source_table="sensors",
        columns=[ColumnExpr("sensor_id", 's."SENSOR_ID"', ("SENSOR_ID",), ("project",))],
    )


def _report(rate: float, shapes: bool, prov: bool) -> VerificationReport:
    return VerificationReport(
        holdout_rows=90,
        holdout_pass_rate=rate,
        shapes_satisfied=shapes,
        provenance_equivalent=prov,
        program_complexity=3,
    )


def test_verified_candidate_is_accepted_with_transform_def():
    ledger = SqliteLedger()
    acc = Acceptor(ledger=ledger)
    out = acc.decide(_program(), _report(1.0, True, True), H.sensor_class(), coverage=1.0)
    assert out.status == "accepted"
    assert out.transform is not None
    assert out.transform.synthesized_by.startswith("anvil:")
    assert out.transform.output == "conformed.sensor"
    assert out.decision.outcome == "yes" and out.decision.auto_decided


def test_ambiguous_candidate_lands_in_review_queue():
    ledger = SqliteLedger()
    acc = Acceptor(ledger=ledger)
    out = acc.decide(_program(), _report(0.85, False, True), H.sensor_class(), coverage=1.0)
    assert out.status == "review"
    assert out.transform is None
    assert out.decision.deferred_to_human
    # the review artifact is a readable record (SQL included)
    cur = ledger._conn.execute("SELECT kind, payload FROM artifact WHERE kind='review'")
    rows = cur.fetchall()
    assert len(rows) == 1
    payload = json.loads(rows[0][1])
    assert "SELECT" in payload["sql"].upper()
    assert payload["holdout_pass_rate"] == 0.85


def test_provenance_failure_is_rejected():
    out = Acceptor().decide(_program(), _report(1.0, True, False), H.sensor_class(), coverage=1.0)
    assert out.status == "rejected"
    assert out.decision.outcome == "no"


def test_low_pass_rate_is_rejected():
    out = Acceptor().decide(_program(), _report(0.4, False, True), H.sensor_class(), coverage=1.0)
    assert out.status == "rejected"


def test_accepted_transform_recorded_in_ledger_with_provenance():
    ledger = SqliteLedger()
    acc = Acceptor(ledger=ledger)
    out = acc.decide(_program(), _report(1.0, True, True), H.sensor_class(), coverage=1.0)
    cur = ledger._conn.execute("SELECT artifact_id, kind, payload FROM artifact WHERE kind='transform'")
    rows = cur.fetchall()
    assert len(rows) == 1
    assert rows[0][0] == out.transform.fingerprint
    payload = json.loads(rows[0][2])
    assert payload["verification"]["provenance_equivalent"] is True
    # TX decision row landed too
    dec = ledger._conn.execute("SELECT decision_id FROM decision").fetchall()
    assert any(d[0].startswith("tx:") for d in dec)


def test_mdl_prior_prefers_shorter_programs():
    from ontoforge.anvil.acceptance import tx_rule
    from ontoforge.contracts import DecisionRequest

    def conf(complexity):
        req = DecisionRequest(
            kind=DecisionKind.TX, decision_id="t", candidates=("no", "yes"),
            features=(
                ("holdout_pass_rate", 1.0), ("shapes_satisfied", 1.0),
                ("provenance_equivalent", 1.0), ("sample_coverage", 1.0),
                ("mdl_prior", 1.0 / (1.0 + float(complexity))),
            ),
        )
        return tx_rule(req).scores["yes"]

    assert conf(2) > conf(40)


# ------------------------------------------------------ readability invariant


def test_all_accepted_sql_round_trips_through_sqlglot():
    """parse -> pretty -> parse must be a fixed point for every accepted SQL,
    across T0 and T1 syntheses on the corruption suite."""
    clean = H.clean_sensors()
    sqls = []
    for corruptor in (H.corrupt_currency, H.corrupt_units, H.corrupt_dates, H.corrupt_dup_rows):
        df, _ = corruptor(clean)
        anvil = Anvil(seed=0)
        accepted = anvil.synthesize(df, H.profile(df), H.sensor_class(), H.sensor_ontology())
        sqls.extend(t.sql for t, _ in accepted)
    assert sqls
    for sql in sqls:
        ast1 = sqlglot.parse_one(sql, read="duckdb")
        again = pretty_sql(sql)
        ast2 = sqlglot.parse_one(again, read="duckdb")
        assert ast1 == ast2, "pretty-printing must not change the program"
        assert again.strip() == sql.strip(), "accepted SQL ships already pretty"
