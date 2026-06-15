"""M12 — DETERMINISTIC NL free-text robustness gate (whitepaper §6.2).

The KEYLESS (no-LLM) natural-language path must answer real PARAPHRASED
questions, not just the canonical competency phrasings. These questions reword
the aviation gold competency set — different verbs, abbreviations, cue synonyms,
clause order, magnitude/written forms — over the SAME frozen gold world that the
competency suite proves (tests/m12/conftest.py). LODESTONE never sees the
canonical strings.

HARD GATES (mirror the competency contract, on paraphrases):

  * >= 70% of the answerable paraphrases are fully correct WITH atom-level
    citations on every answered cell;
  * 0 confidently-wrong: no wrong answer at confidence >= tau_high;
  * the 2 unanswerable paraphrases abstain and the 1 trick-unit paraphrase is
    rejected by the OQIR TYPE CHECKER (not coerced).

The measured free-text answer rate is printed by ``test_print_freetext_rate``.

Each paraphrase is a deliberate REWORDING of a canonical CQ, annotated with the
source id and the rewording technique it exercises (abbreviation expansion,
cue-synonym routing, fuzzy schema linking, lowercase categorical value probing,
numeric/temporal parsing, clause reorder). Expected answers match the gold
``competency_questions.yaml`` answers.
"""

from __future__ import annotations

import math

import pytest

from ontoforge.contracts import Answer, SpineProfile

TAU_HIGH = SpineProfile().tau_high

# (id, source_cq, technique, question, expected, behavior)
# behavior in {"answer", "abstain", "reject"}.
# expected: scalar | list (set-equal) | dict (all values present) | None (any
# non-empty / non-zero answer is accepted — used for genuinely open counts).
PARAPHRASES: list[tuple] = [
    (
        "FT-01", "CQ-01", "wh-adjacency link + 'maker' is avoided; clause reorder",
        "According to the FAA aircraft reference, which manufacturer made the "
        "model of the aircraft whose tail number is N4669X?",
        "GULFSTREAM AEROSPACE", "answer",
    ),
    (
        "FT-02", "CQ-02", "'built by' cue + name-variant fold reworded",
        "How many registry aircraft were built by Rockwell, folding the "
        "'ROCKWELL INTERNATIONAL CORP' and 'ROCKWELL INTL' spellings together?",
        23, "answer",
    ),
    (
        "FT-03", "CQ-03", "temporal as-of, leading 'who was'",
        "Who was the registrant of tail number N44304 as of 1987-05-20?",
        "WELLS FARGO TRUST CO NA TRUSTEE", "answer",
    ),
    (
        "FT-04", "CQ-04", "temporal as-of, 'airframe serial number' synonym",
        "Which airframe serial number did tail number N44304 refer to as of "
        "2024-06-01?",
        "17238197", "answer",
    ),
    (
        "FT-05", "CQ-05", "lowercase categorical value probe ('Descent') + "
        "'beneath' comparison cue + 'logged' verb",
        "How many ASRS reports in the Descent flight phase logged an altitude "
        "AGL beneath 10,000 ft?",
        34, "answer",
    ),
    (
        "FT-06", "CQ-06", "source-unit filter ('recorded in meters') vs output "
        "unit ('expressed in feet') disambiguation + round cue",
        "Among ASRS reports whose altitude AGL was recorded in meters, what is "
        "the lowest altitude expressed in feet, rounded to the nearest foot?",
        1010, "answer",
    ),
    (
        "FT-07", "CQ-07", "textJoin narrative predicate, 'turn up' + 'mention'",
        "Which registry tail numbers turn up in ASRS narratives that mention a "
        "bird strike?",
        ["N2001Y", "N252QM", "N36RW", "N56485", "N5651A"], "answer",
    ),
    (
        "FT-08", "CQ-07", "count variant of the bird-strike textJoin",
        "How many ASRS reports mention a bird strike in their narrative?",
        5, "answer",
    ),
    (
        "FT-09", "CQ-08", "single narrative -> manufacturer, 'who manufactured'",
        "ASRS report ACN 1501323 names a registry aircraft in its narrative. Per "
        "the FAA aircraft reference, who manufactured that aircraft?",
        "CIRRUS DESIGN CORP", "answer",
    ),
    (
        "FT-10", "CQ-09", "multi-filter sum, 'opened after' temporal + currency",
        "For tail number N79946, what was the total maintenance cost in USD "
        "across work orders opened after its NTSB event of 2017-04-13?",
        "29644.75", "answer",
    ),
    (
        "FT-11", "CQ-10", "'combined' sum cue + UPPERCASE component value",
        "What is the combined LABOR_HOURS across all maintenance work orders "
        "whose component is LANDING GEAR?",
        "1631.4", "answer",
    ),
    (
        "FT-12", "CQ-10", "'add up' sum cue + lowercase component value probe",
        "Add up the labor hours on every work order for the landing gear "
        "component.",
        "1631.4", "answer",
    ),
    (
        "FT-13", "CQ-11", "operator name-variant fold, leading 'how many'",
        "How many maintenance work orders belong to Delta Air Lines once all its "
        "ERP operator-name spellings are folded together?",
        18, "answer",
    ),
    (
        "FT-14", "CQ-11", "'tally' count cue + multiword proper-noun value probe",
        "Tally the maintenance work orders for Delta Air Lines Inc.",
        18, "answer",
    ),
    (
        "FT-15", "CQ-12", "'associated with more than one' reuse detection",
        "Which tail numbers in the FAA registry fixture are associated with more "
        "than one airframe serial number?",
        ["N26726", "N3484Z", "N37213", "N44304", "N6435S", "N744JY", "N812TP",
         "N9733M"], "answer",
    ),
    (
        "FT-16", "CQ-13", "textJoin cause narrative + leading-N normalization",
        "Which NTSB event cites fuel exhaustion in its cause narrative, and what "
        "is the aircraft's registration number with its leading 'N'?",
        {"ntsb": "ANC03FA200", "tail": "N57AR"}, "answer",
    ),
    (
        "FT-17", "CQ-14", "two-clause damage + registrant multi-hop",
        "What damage level did the NTSB record for event DCA21CA106, and who is "
        "the registry registrant of the involved aircraft?",
        {"damage": "SUBS", "reg": "GULF COAST AERIAL SURVEY INC"}, "answer",
    ),
    (
        "FT-18", "CQ-15", "'how many total fatalities' count + state value probe",
        "How many total fatalities are recorded across NTSB events in AK?",
        2, "answer",
    ),
    (
        "FT-19", "CQ-15", "open count: NTSB events in AK (any positive count)",
        "Count the NTSB events that happened in AK.",
        None, "answer",
    ),
    (
        "FT-20", "agg", "average aggregation over a measure (open answer)",
        "What is the average altitude AGL across ASRS reports?",
        None, "answer",
    ),
    (
        "FT-21", "count", "bare population count (open answer)",
        "How many work orders are there in total?",
        None, "answer",
    ),
    (
        "FT-22", "CQ-05", "'show me the number of' count cue + categorical "
        "value probe ('Cruise') (open answer)",
        "Show me the number of incident reports in the Cruise flight phase.",
        None, "answer",
    ),
    # --- the abstention contract on paraphrases ---
    (
        "FT-23", "CQ-16", "unanswerable: airframe hours appear in no source",
        "What was the total airframe time in flight hours for N398UK at its most "
        "recent annual inspection?",
        None, "abstain",
    ),
    (
        "FT-24", "CQ-17", "unanswerable: insurance data appears in no source",
        "Which insurance company underwrote the hull policy for N398UK in 2022?",
        None, "abstain",
    ),
    (
        "FT-25", "CQ-18", "trick unit: altitude (length) in dollars (currency)",
        "What is the total altitude in dollars across all ASRS incident reports?",
        None, "reject",
    ),
]


def _matches(expected, ans: Answer) -> bool:
    flat = [v for r in ans.rows for v in r]
    if expected is None:
        return len(flat) >= 1 and str(flat[0]).strip() not in ("", "0")
    if isinstance(expected, list):
        return sorted(str(x).strip() for x in expected) == sorted(
            str(v).strip() for v in flat
        )
    if isinstance(expected, dict):
        vals = [str(v).strip() for v in flat]
        return all(any(str(x).strip() == vv for vv in vals) for x in expected.values())
    if len(flat) != 1:
        return False
    v = flat[0]
    try:
        return abs(float(v) - float(expected)) < 1e-3
    except (TypeError, ValueError):
        return str(v).strip() == str(expected).strip()


@pytest.fixture(scope="module")
def freetext_scorecard(lodestone):
    rows = []
    for fid, src, technique, q, expected, behavior in PARAPHRASES:
        ans = lodestone.ask(q)
        if ans.clarification:
            status = "clarify"
        elif ans.abstained:
            status = "abstain"
        elif _matches(expected, ans):
            status = "correct"
        else:
            status = "wrong"
        rows.append({
            "id": fid, "src": src, "technique": technique, "q": q,
            "expected": expected, "behavior": behavior, "answer": ans,
            "status": status,
        })
    return rows


def test_print_freetext_rate(freetext_scorecard):
    answerable = [r for r in freetext_scorecard if r["behavior"] == "answer"]
    correct = [r for r in answerable if r["status"] == "correct"]
    rate = len(correct) / len(answerable) if answerable else 0.0
    print("\n--- M12 free-text robustness scorecard ---")
    for r in freetext_scorecard:
        a: Answer = r["answer"]
        if a.abstained:
            extra = "ABSTAIN " + a.abstain_reason[:60]
        elif a.clarification:
            extra = "CLARIFY " + a.clarification[:60]
        else:
            extra = f"rows={a.rows[:2]} conf={a.confidence}"
        print(f"{r['id']:6s} {r['src']:6s} {r['status']:8s} {extra}")
    print(f"\nFREE-TEXT ANSWER RATE: {len(correct)}/{len(answerable)} = {rate:.1%}")


def test_gate_freetext_70_percent_correct_with_citations(freetext_scorecard, ledger):
    """>= 70% of answerable paraphrases fully correct, and every answered cell
    carries >= 1 atom citation resolving in the ledger."""
    answerable = [r for r in freetext_scorecard if r["behavior"] == "answer"]
    correct = [r for r in answerable if r["status"] == "correct"]
    need = math.ceil(0.7 * len(answerable))
    detail = {r["id"]: r["status"] for r in answerable}
    assert len(correct) >= need, (
        f"{len(correct)}/{len(answerable)} correct (< {need}): {detail}"
    )
    # citation coverage on every correctly-answered cell
    checked = 0
    for r in correct:
        a: Answer = r["answer"]
        n_cells = sum(len(row) for row in a.rows)
        assert len(a.citations) == n_cells, f"{r['id']}: citation list incomplete"
        for cell in a.citations:
            assert cell.atom_ids, f"{r['id']}: uncited cell {cell.column}[{cell.row}]"
            for atom_id in cell.atom_ids:
                atom = ledger.get_atom(atom_id)
                assert atom is not None, f"{r['id']}: dangling atom {atom_id}"
                assert atom.uri.startswith("atom://")
            checked += 1
    assert checked > 0


def test_gate_freetext_zero_confidently_wrong(freetext_scorecard):
    """No wrong paraphrase answer is returned at confidence >= tau_high — the
    keyless path is allowed to MISS (abstain/clarify) but never to assert a
    wrong answer confidently."""
    for r in freetext_scorecard:
        if r["status"] == "wrong":
            a: Answer = r["answer"]
            assert a.confidence < TAU_HIGH, (
                f"{r['id']} ({r['src']}): WRONG at confidence {a.confidence} "
                f">= tau_high {TAU_HIGH}: rows={a.rows[:3]} expected={r['expected']}"
            )


def test_gate_freetext_unanswerables_abstain(freetext_scorecard):
    """The genuinely unanswerable paraphrases abstain — the abstention contract
    survives paraphrasing (honest coverage does not let them clear the floor)."""
    for r in freetext_scorecard:
        if r["behavior"] == "abstain":
            a: Answer = r["answer"]
            assert a.abstained, f"{r['id']} did not abstain: rows={a.rows[:3]}"
            assert a.abstain_reason


def test_gate_freetext_trick_unit_rejected_by_type_checker(freetext_scorecard):
    """The trick-unit paraphrase (altitude in dollars) is rejected by the OQIR
    TYPE CHECKER, never coerced — the unit/dimension guard is unweakened."""
    rejects = [r for r in freetext_scorecard if r["behavior"] == "reject"]
    assert rejects
    for r in rejects:
        a: Answer = r["answer"]
        assert a.abstained, f"{r['id']} was not rejected: rows={a.rows[:3]}"
        assert "type checker" in a.abstain_reason
        assert "unit" in a.abstain_reason.lower()


def test_freetext_determinism(lodestone):
    """The keyless path is deterministic: asking the same paraphrase twice
    yields the same rows and confidence (no network, no randomness)."""
    q = PARAPHRASES[4][3]  # FT-05
    a1 = lodestone.ask(q)
    a2 = lodestone.ask(q)
    assert a1.rows == a2.rows
    assert a1.confidence == a2.confidence
    assert a1.abstained == a2.abstained
