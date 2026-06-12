"""M12 — the competency harness over the REAL estate pipeline (§12.4; the
product's definition of done).

ask() runs against HEARTH populated from the estate tables under the frozen
gold mini-ontology, scored against fixtures/aviation/gold/
competency_questions.yaml. HARD GATES:

  * >= 70% of answerable questions fully correct;
  * 100% atom-level citation coverage on every non-abstained answer cell;
  * BOTH unanswerable questions abstain;
  * the trick-unit question (CQ-18) is rejected by the TYPE CHECKER;
  * 0 confidently-wrong: no wrong answer at confidence >= tau_high.
"""

from __future__ import annotations

import math

import pytest

from ontoforge.contracts import Answer, SpineProfile

TAU_HIGH = SpineProfile().tau_high


def _matches(expected, ans: Answer) -> bool:
    rows = ans.rows
    flat = [v for r in rows for v in r]
    if isinstance(expected, list):
        return sorted(str(x).strip() for x in expected) == sorted(
            str(v).strip() for v in flat
        )
    if isinstance(expected, dict):
        if len(rows) != 1:
            return False
        vals = [str(v).strip() for v in rows[0]]
        return all(str(x).strip() in vals for x in expected.values())
    if len(flat) != 1:
        return False
    v = flat[0]
    try:
        return abs(float(v) - float(expected)) < 1e-6
    except (TypeError, ValueError):
        return str(v).strip() == str(expected).strip()


@pytest.fixture(scope="module")
def scorecard(lodestone, competency, ledger):
    rows = []
    for q in competency["questions"]:
        ans = lodestone.ask(q["question"])
        if ans.clarification:
            status = "clarify"
        elif ans.abstained:
            status = "abstain"
        elif _matches(q["answer"], ans):
            status = "correct"
        else:
            status = "wrong"
        rows.append({"q": q, "answer": ans, "status": status})
    return rows


def test_print_scorecard(scorecard):
    print("\n--- M12 competency scorecard ---")
    for r in scorecard:
        a: Answer = r["answer"]
        extra = (
            f"rows={a.rows[:3]}{'...' if len(a.rows) > 3 else ''} conf={a.confidence}"
            if not a.abstained and not a.clarification
            else (a.abstain_reason[:70] if a.abstained else a.clarification)
        )
        print(f"{r['q']['id']:6s} {r['status']:8s} {extra}")


def test_gate_70_percent_answerable_fully_correct(scorecard):
    answerable = [r for r in scorecard if r["q"]["answerable"]]
    correct = [r for r in answerable if r["status"] == "correct"]
    need = math.ceil(0.7 * len(answerable))
    detail = {r["q"]["id"]: r["status"] for r in answerable}
    assert len(correct) >= need, f"{len(correct)}/{len(answerable)} correct (< {need}): {detail}"


def test_gate_100_percent_citation_coverage(scorecard, ledger):
    """Every cell of every non-abstained answer cites >= 1 atom, and every
    cited atom id resolves in the ledger to a registered source-cell atom."""
    checked_cells = 0
    for r in scorecard:
        a: Answer = r["answer"]
        if a.abstained or a.clarification:
            continue
        n_cells = sum(len(row) for row in a.rows)
        assert len(a.citations) == n_cells, f"{r['q']['id']}: citation list incomplete"
        for cell in a.citations:
            assert cell.atom_ids, f"{r['q']['id']}: uncited cell {cell.column}[{cell.row}]"
            for atom_id in cell.atom_ids:
                atom = ledger.get_atom(atom_id)
                assert atom is not None, f"{r['q']['id']}: dangling atom {atom_id}"
                assert atom.uri.startswith("atom://")
            checked_cells += 1
    assert checked_cells > 0


def test_gate_unanswerables_abstain(scorecard):
    for r in scorecard:
        if r["q"]["expected_behavior"] == "abstain":
            a: Answer = r["answer"]
            assert a.abstained, f"{r['q']['id']} did not abstain: rows={a.rows[:3]}"
            assert a.abstain_reason


def test_gate_trick_unit_rejected_by_type_checker(scorecard):
    [r] = [x for x in scorecard if x["q"]["expected_behavior"] == "reject_unit_mismatch"]
    a: Answer = r["answer"]
    assert a.abstained
    assert "type checker" in a.abstain_reason
    assert "unit" in a.abstain_reason.lower()


def test_gate_zero_confidently_wrong(scorecard):
    for r in scorecard:
        if r["status"] == "wrong":
            a: Answer = r["answer"]
            assert a.confidence < TAU_HIGH, (
                f"{r['q']['id']}: WRONG at confidence {a.confidence} >= tau_high {TAU_HIGH}"
            )
        if r["status"] in ("abstain", "clarify") and r["q"]["answerable"]:
            # abstaining/clarifying on an answerable question is a miss, never
            # a confident claim — nothing to check, by construction
            pass


def test_gate_clarification_rate_bounded(scorecard):
    """Design target (§6.2): clarification on <= 25% of questions."""
    n_clar = sum(1 for r in scorecard if r["status"] == "clarify")
    assert n_clar <= 0.25 * len(scorecard), f"{n_clar} clarifications"


def test_single_shot_ask_entry_point(gold_onto, hearth_world, ledger, spine, competency):
    """The §11.2 interface: ask(question, ...) -> Answer, one call."""
    from ontoforge.lodestone import ask

    q = next(q for q in competency["questions"] if q["id"] == "CQ-01")
    a = ask(q["question"], gold_onto, hearth_world, ledger, spine)
    assert not a.abstained
    assert _matches(q["answer"], a)
    assert a.oqir is not None
    assert all(c.atom_ids for c in a.citations)


def test_qi_decisions_route_through_the_spine(gold_onto, hearth_world, ledger, spine, competency):
    """Candidate selection is a DecisionKind.QI spine decision (§6.2 stage 3)."""
    from ontoforge.contracts import DecisionKind
    from ontoforge.lodestone import Lodestone

    seen: list[DecisionKind] = []

    class SpySpine:
        def decide(self, req):
            seen.append(req.kind)
            return spine.decide(req)

        def register_rule(self, kind, fn):
            spine.register_rule(kind, fn)

    eng = Lodestone(gold_onto, hearth_world, ledger, SpySpine())
    q = next(q for q in competency["questions"] if q["id"] == "CQ-10")
    a = eng.ask(q["question"])
    assert not a.abstained
    assert seen and all(k is DecisionKind.QI for k in seen)


def test_generation_routes_through_model_client(gold_onto, hearth_world, ledger, spine, competency):
    """Candidate enumeration is a ModelClient task (AMD-0002): a live T2/T3
    generator can swap in behind 'lodestone.generate'."""
    from ontoforge.ledger import HeuristicAdapter
    from ontoforge.lodestone import GENERATE_TASK, Lodestone, make_generate_handler

    calls: list[str] = []
    inner = HeuristicAdapter({GENERATE_TASK: make_generate_handler(gold_onto)})

    class SpyClient:
        def propose(self, req):
            calls.append(req.task)
            return inner.propose(req)

    eng = Lodestone(gold_onto, hearth_world, ledger, spine, model_client=SpyClient())
    q = next(q for q in competency["questions"] if q["id"] == "CQ-04")
    a = eng.ask(q["question"])
    assert not a.abstained
    assert calls == [GENERATE_TASK] * len(calls) and calls
