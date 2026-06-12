"""M12 — clarification and execution-guided repair (§6.2).

Clarification: two ambiguous questions whose top candidates differ
STRUCTURALLY (entity scope; filter time-field) -> exactly one multiple-choice
question; answering it re-ranks to a singleton with the correct, cited answer.

Repair: a case-mismatched literal first executes empty, the failing leaf is
re-grounded (case-insensitive matching), and the plan succeeds.

Expected numbers are computed from the estate tables inside the test (no
hardcoded answers).
"""

from __future__ import annotations

import re

import pytest

from ontoforge.lodestone.lower import normalize_name


def _fold(name: str) -> str:
    return normalize_name(name)


# ----------------------------------------------------------- clarification


def test_scope_ambiguity_yields_one_clarification_then_singleton(lodestone, estate):
    """'events' could be NTSB accident events or all safety events — a
    structural entity-scope diff -> ONE multiple-choice question."""
    a = lodestone.ask("How many events are recorded for DELTA AIR LINES INC?")
    assert a.clarification is not None, f"expected clarification, got rows={a.rows[:3]}"
    assert len(a.clarification_options) >= 2
    assert a.rows == [] and not a.abstained
    joined = " ".join(a.clarification_options)
    assert "AccidentEvent" in joined and "SafetyEvent" in joined

    # answering re-ranks to a singleton: a real, cited answer, no second question
    b = lodestone.answer_clarification("AccidentEvent")
    assert b.clarification is None and not b.abstained

    ntsb = estate["tables"]["ntsb_events"]
    expected = sum(1 for _, r in ntsb.iterrows() if _fold(r["OPERATOR"]) == "DELTA AIR LINES")
    assert b.rows == [[expected]]
    assert all(c.atom_ids for c in b.citations)

    # the OTHER branch answers differently (the clarification carried information)
    a2 = lodestone.ask("How many events are recorded for DELTA AIR LINES INC?")
    assert a2.clarification is not None
    c = lodestone.answer_clarification("SafetyEvent")
    asrs = estate["tables"]["asrs_reports"]
    expected_all = expected + sum(
        1 for _, r in asrs.iterrows() if _fold(r["AIRCRAFT 1 OPERATOR"]) == "DELTA AIR LINES"
    )
    assert c.rows == [[expected_all]]
    assert expected_all != expected  # structurally different readings, different answers


def test_time_field_ambiguity_yields_one_clarification_then_singleton(lodestone, estate):
    """'after 2016-05-01' with no opened/closed anchor: the candidates differ
    only in WHICH date field filters — a time-window structural diff."""
    a = lodestone.ask(
        "What was the total maintenance cost for tail number N79946 after 2016-05-01?"
    )
    assert a.clarification is not None, f"expected clarification, got rows={a.rows[:3]}"
    opts = " ".join(a.clarification_options)
    assert "open" in opts and "close" in opts

    b = lodestone.answer_clarification("open date")
    assert b.clarification is None and not b.abstained

    erp = estate["tables"]["maintenance_erp"]
    expected = round(
        sum(
            float(re.sub(r"[^0-9.]", "", r["COST"]))
            for _, r in erp.iterrows()
            if r["TAIL_NUMBER"] == "N79946" and r["OPEN_DATE"] > "2016-05-01"
        ),
        2,
    )
    assert len(b.rows) == 1 and abs(float(b.rows[0][0]) - expected) < 1e-6
    assert all(c.atom_ids for c in b.citations)


def test_anchored_date_question_needs_no_clarification(lodestone):
    """CQ-09 style: 'OPENED AFTER' anchors the date field -> no question asked."""
    a = lodestone.ask(
        "For tail number N79946, what was the total maintenance cost in USD across "
        "work orders OPENED AFTER its NTSB event of 2017-04-13?"
    )
    assert a.clarification is None and not a.abstained


def test_clarification_answer_must_match_an_option(lodestone):
    a = lodestone.ask("How many events are recorded for DELTA AIR LINES INC?")
    assert a.clarification is not None
    b = lodestone.answer_clarification("teapot")
    assert b.abstained and "no offered option" in b.abstain_reason


def test_no_pending_clarification_is_an_abstention(lodestone):
    lodestone.ask("How many work orders are there for tail number N79946?")  # no clarification
    b = lodestone.answer_clarification(0)
    assert b.abstained


# ------------------------------------------------------------------ repair


def test_case_mismatched_literal_succeeds_via_repair(lodestone, hearth_world, estate):
    """'Landing Gear' vs stored 'LANDING GEAR': strict equality finds nothing;
    the failing leaf is re-grounded case-insensitively and the plan succeeds."""
    # precondition: the literal genuinely mismatches every stored value
    from ontoforge.contracts import Layer

    stored = set()
    for shard in hearth_world.value_shard_items():
        if shard.layer is Layer.ENTITY and shard.class_uri.endswith("/Component"):
            stored |= {c.value for c in shard.cells if c.prop == "component_name"}
    assert "Landing Gear" not in stored and "LANDING GEAR" in stored

    a = lodestone.ask("How many work orders have component 'Landing Gear'?")
    assert not a.abstained and a.clarification is None

    erp = estate["tables"]["maintenance_erp"]
    expected = sum(1 for _, r in erp.iterrows() if r["COMPONENT"] == "LANDING GEAR")
    assert a.rows == [[expected]]
    assert all(c.atom_ids for c in a.citations)


def test_repair_exhaustion_abstains_with_failed_leaf(lodestone, estate):
    """A well-grounded plan whose population is genuinely empty (the tail has
    no work orders) stays empty after every relaxation -> abstain, leaf shown."""
    assert "N4669X" not in set(estate["tables"]["maintenance_erp"]["TAIL_NUMBER"])
    a = lodestone.ask(
        "How many work orders have component 'LANDING GEAR' for tail number N4669X?"
    )
    assert a.abstained
    assert "failed at" in a.abstain_reason


# -------------------------------------------------------------- determinism


def test_ask_is_deterministic(lodestone, competency):
    for qid in ("CQ-01", "CQ-07", "CQ-10"):
        q = next(x for x in competency["questions"] if x["id"] == qid)
        a1 = lodestone.ask(q["question"])
        a2 = lodestone.ask(q["question"])
        assert a1.columns == a2.columns
        assert a1.rows == a2.rows
        assert a1.confidence == a2.confidence
        assert [c.atom_ids for c in a1.citations] == [c.atom_ids for c in a2.citations]


def test_fresh_engine_agrees(gold_onto, hearth_world, ledger, spine, lodestone, competency):
    from ontoforge.lodestone import Lodestone

    q = next(x for x in competency["questions"] if x["id"] == "CQ-12")
    a1 = lodestone.ask(q["question"])
    a2 = Lodestone(gold_onto, hearth_world, ledger, spine).ask(q["question"])
    assert a1.rows == a2.rows and a1.columns == a2.columns
