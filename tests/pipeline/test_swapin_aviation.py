"""STRATA swap-in evaluation (whitepaper §11.3 Phase 3, inverted gate):
the aviation competency suite over the world materialized from the INDUCED
ontology by the generic engine — no gold ontology, no hand-built mapping.

HARD GATES for the swap-in path:
  * >= 8/15 answerable questions fully correct over the induced world;
  * 100% atom-level citation coverage on every answered cell;
  * 0 confidently-wrong (no wrong answer at confidence >= tau_high);
  * both unanswerable questions abstain;
  * the trick-unit question is rejected by the type checker.

Per-question induced-vs-gold analysis lives in docs/SWAPIN_REPORT.md
(regenerate with ``uv run python scripts/swapin_eval.py``).
"""

from __future__ import annotations

import math

import pytest

from ontoforge.contracts import Answer, SpineProfile
from ontoforge.estates import load_competency_questions, load_estate
from ontoforge.hearth import Hearth
from ontoforge.ledger import SqliteLedger
from ontoforge.lodestone import Lodestone
from ontoforge.pipeline import induce_estate, materialize_induced
from ontoforge.spine import DecisionSpine

pytestmark = pytest.mark.slow

TAU_HIGH = SpineProfile().tau_high


def _matches(expected, ans: Answer) -> bool:
    rows = ans.rows
    flat = [v for r in rows for v in r]
    if isinstance(expected, list):
        return sorted(str(x).strip() for x in expected) == sorted(str(v).strip() for v in flat)
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
def induced_world(tmp_path_factory):
    estate = load_estate()
    ledger = SqliteLedger(":memory:")
    artifacts = induce_estate(estate, ledger)
    hearth = Hearth(tmp_path_factory.mktemp("swapin-hearth") / "store", ledger)
    stats = materialize_induced(estate, artifacts.ontology, artifacts, hearth, ledger)
    engine = Lodestone(
        artifacts.ontology, hearth, ledger, DecisionSpine(SpineProfile(), model_client=None)
    )
    yield engine, ledger, stats
    ledger.close()


@pytest.fixture(scope="module")
def scorecard(induced_world):
    engine, _, _ = induced_world
    rows = []
    for q in load_competency_questions()["questions"]:
        ans = engine.ask(q["question"])
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


def test_print_swapin_scorecard(scorecard):
    print("\n--- STRATA swap-in scorecard (induced world) ---")
    for r in scorecard:
        a: Answer = r["answer"]
        extra = (
            f"rows={a.rows[:2]} conf={a.confidence}"
            if not a.abstained and not a.clarification
            else (a.abstain_reason[:70] if a.abstained else str(a.clarification))
        )
        print(f"{r['q']['id']:6s} {r['status']:8s} {extra}")


def test_world_materialized_from_induced_ontology(induced_world):
    _, _, stats = induced_world
    assert stats["entities"] > 3000
    assert stats["links"] > 1000
    # the FAA registry class carries the identifier-variant exact resolution
    assert any(v["method"] == "exact-variant" for v in stats["er"].values())


def test_gate_swapin_8_of_15_answerable_correct(scorecard):
    answerable = [r for r in scorecard if r["q"]["answerable"]]
    correct = [r for r in answerable if r["status"] == "correct"]
    detail = {r["q"]["id"]: r["status"] for r in answerable}
    assert len(answerable) == 15
    assert len(correct) >= 8, f"{len(correct)}/15 correct over induced world: {detail}"


def test_gate_100_percent_citation_coverage_on_answered_cells(scorecard, induced_world):
    _, ledger, _ = induced_world
    checked = 0
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
                assert atom is not None and atom.uri.startswith("atom://")
            checked += 1
    assert checked > 0


def test_gate_zero_confidently_wrong(scorecard):
    for r in scorecard:
        if r["status"] == "wrong":
            a: Answer = r["answer"]
            assert a.confidence < TAU_HIGH, (
                f"{r['q']['id']}: WRONG at confidence {a.confidence} >= tau_high {TAU_HIGH}"
            )


def test_gate_unanswerables_abstain_over_induced_world(scorecard):
    unanswerable = [r for r in scorecard if r["q"]["expected_behavior"] == "abstain"]
    assert len(unanswerable) == 2
    for r in unanswerable:
        assert r["answer"].abstained, f"{r['q']['id']} did not abstain"


def test_gate_trick_unit_rejected_over_induced_world(scorecard):
    [r] = [x for x in scorecard if x["q"]["expected_behavior"] == "reject_unit_mismatch"]
    a: Answer = r["answer"]
    assert a.abstained
    assert "type checker" in a.abstain_reason and "unit" in a.abstain_reason.lower()


def test_clarification_rate_bounded(scorecard):
    n_clar = sum(1 for r in scorecard if r["status"] == "clarify")
    assert n_clar <= math.ceil(0.25 * len(scorecard))
