"""THE MERIDIAN GATE: the GENERIC engine over fixtures/meridian answers the
gold competency questions.

This is the product claim under test — `ontoforge init -p X --source
fixtures/meridian`: no estate module, no gold ontology, no hand mapping. The
world is discovered, induced, resolved, and materialized by the generic
pipeline, and LODESTONE answers over the ontology OntoForge built for itself.

HARD GATES:
  * >= 7 of the 9 answerable gold questions fully correct WITH atom-level
    citations on every answered cell (currently 9/9 — the floor is the gate,
    the scorecard test prints the actual number);
  * both unanswerable questions abstain (no improvised proxies);
  * the trick-unit question (multi-currency NET_PRICE 'in dollars') is
    rejected by the OQIR type checker, never coerced;
  * zero confidently-wrong: no wrong answer at confidence >= tau_high.

Runtime is the full corpus (~9k rows, ~90s); marked slow per repo convention.
If the gate ever regresses, fix question phrasing realism or generator overlap
in ontoforge.estates.meridian_gen — never hardcode engine answers.
"""

from __future__ import annotations

import pytest

from ontoforge.contracts import Answer, SpineProfile
from ontoforge.hearth import Hearth
from ontoforge.ledger import SqliteLedger
from ontoforge.lodestone import Lodestone
from ontoforge.pipeline import discover_sources, induce_estate, materialize_induced
from ontoforge.spine import DecisionSpine

from meridian_helpers import FIXTURES

pytestmark = pytest.mark.slow

TAU_HIGH = SpineProfile().tau_high
GATE_FLOOR = 7


def _matches(expected, ans: Answer) -> bool:
    flat = [v for r in ans.rows for v in r]
    if len(flat) != 1:
        return False
    v = flat[0]
    try:
        fe, fv = float(expected), float(v)
        return abs(fv - fe) <= 1e-6 * max(1.0, abs(fe))
    except (TypeError, ValueError):
        return str(v).strip() == str(expected).strip()


@pytest.fixture(scope="module")
def induced_world(tmp_path_factory):
    estate = discover_sources(FIXTURES)
    assert estate["metadata"]["estate"] == "generic"
    ledger = SqliteLedger(":memory:")
    artifacts = induce_estate(estate, ledger)
    hearth = Hearth(tmp_path_factory.mktemp("meridian-hearth") / "store", ledger)
    stats = materialize_induced(estate, artifacts.ontology, artifacts, hearth, ledger)
    engine = Lodestone(
        artifacts.ontology, hearth, ledger, DecisionSpine(SpineProfile(), model_client=None)
    )
    yield engine, ledger, stats
    ledger.close()


@pytest.fixture(scope="module")
def scorecard(induced_world, gold):
    engine, _, _ = induced_world
    rows = []
    for q in gold["questions"]:
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


def test_print_meridian_scorecard(scorecard):
    print("\n--- MERIDIAN gate scorecard (generic engine, induced world) ---")
    for r in scorecard:
        a: Answer = r["answer"]
        extra = (
            f"rows={a.rows[:2]} conf={a.confidence}"
            if not a.abstained and not a.clarification
            else (a.abstain_reason[:80] if a.abstained else str(a.clarification))
        )
        print(f"{r['q']['id']:6s} {r['status']:8s} {extra}")


def test_world_materialized_from_induced_ontology(induced_world):
    _, _, stats = induced_world
    assert stats["entities"] > 6000
    assert stats["links"] > 5000
    # every source table with a candidate key materialized a class
    for cls in ("SupplierContract", "PurchaseOrderLine", "QualityNotification",
                "Product", "Lease", "Shipment", "SupportTicket"):
        assert stats["classes"].get(cls, 0) > 200, cls
    # generic ER resolved at least one cross-table identity domain
    assert any(v["method"] == "er-cascade" for v in stats["er"].values())


def test_gate_7_of_9_answerable_correct_with_citations(scorecard, induced_world):
    _, ledger, _ = induced_world
    answerable = [r for r in scorecard if r["q"]["answerable"]]
    assert len(answerable) == 9
    correct = [r for r in answerable if r["status"] == "correct"]
    detail = {r["q"]["id"]: r["status"] for r in answerable}
    assert len(correct) >= GATE_FLOOR, f"{len(correct)}/9 correct over induced world: {detail}"
    # WITH citations: every answered cell of every correct answer carries
    # atom-level provenance that resolves in the ledger
    for r in correct:
        a: Answer = r["answer"]
        n_cells = sum(len(row) for row in a.rows)
        assert len(a.citations) == n_cells, f"{r['q']['id']}: citation list incomplete"
        for cell in a.citations:
            assert cell.atom_ids, f"{r['q']['id']}: uncited cell {cell.column}[{cell.row}]"
            for atom_id in cell.atom_ids:
                atom = ledger.get_atom(atom_id)
                assert atom is not None and atom.uri.startswith("atom://")


def test_gate_unanswerables_abstain(scorecard):
    unanswerable = [r for r in scorecard if r["q"]["expected_behavior"] == "abstain"]
    assert len(unanswerable) == 2
    for r in unanswerable:
        a: Answer = r["answer"]
        assert a.abstained, f"{r['q']['id']} did not abstain: rows={a.rows[:2]}"
        assert not a.rows  # no improvised numeric proxy


def test_gate_trick_unit_rejected_by_type_checker(scorecard):
    [r] = [x for x in scorecard if x["q"]["expected_behavior"] == "reject_unit_mismatch"]
    a: Answer = r["answer"]
    assert a.abstained
    assert "type checker" in a.abstain_reason and "unit" in a.abstain_reason.lower()


def test_gate_zero_confidently_wrong(scorecard):
    for r in scorecard:
        if r["status"] == "wrong":
            a: Answer = r["answer"]
            assert a.confidence < TAU_HIGH, (
                f"{r['q']['id']}: WRONG at confidence {a.confidence} >= tau_high {TAU_HIGH}"
            )
