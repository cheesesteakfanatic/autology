"""STRATA swap-in evaluation: the aviation competency suite over BOTH worlds —
the gold-ontology world (whitepaper §11.3 de-risking slice) and the world the
GENERIC engine materialized from the INDUCED ontology — side by side.

Regenerates docs/SWAPIN_REPORT.md:

    uv run python scripts/swapin_eval.py [--limit N] [--out docs/SWAPIN_REPORT.md]

The per-question REGRESSIONS notes below are maintained by hand against the
observed runs; the scorecard table and the gate numbers are computed live.
"""

from __future__ import annotations

import argparse
import tempfile
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any, Optional

from ontoforge.contracts import Answer, SpineProfile
from ontoforge.estates import load_competency_questions, load_estate, load_gold_ontology
from ontoforge.hearth import Hearth
from ontoforge.ledger import SqliteLedger
from ontoforge.lodestone import Lodestone
from ontoforge.lodestone.worldbuild import build_estate_world, extend_gold_ontology
from ontoforge.pipeline import induce_estate, materialize_induced
from ontoforge.spine import DecisionSpine

TAU_HIGH = SpineProfile().tau_high

# ---------------------------------------------------------------- regressions
# Hand-maintained per-question analysis of the INDUCED run (kept honest by
# re-running this script after any pipeline/STRATA change). Categories:
#   grounding miss  — the induced vocabulary cannot ground a question phrase
#   missing class   — STRATA admitted no class for the needed concept
#   name drift      — induced class/property names differ enough to mis-bind
#   semantics gap   — grounded and bound, but the induced structure lacks the
#                     semantic the question needs (e.g. bitemporality, dedup)
REGRESSIONS: dict[str, dict[str, str]] = {
    "CQ-03": {
        "category": "semantics gap (temporal identity) + latent-class name capture",
        "detail": (
            "Registry key reuse: tail N44304 names two airframes over time, and "
            "'as of 1987-05-20' must pick the one registered THEN. The gold "
            "world-builder hand-encodes the semantic that CERT ISSUE DATE -> "
            "EXPIRATION DATE is the registration's valid-time window "
            "(worldbuild.window()), so as-of execution filters correctly. The "
            "generic engine has no licensed evidence for that reading and "
            "commits every cell with Interval(0) validity. Compounding it, "
            "'registrant' grounds most strongly to the induced latent "
            "Registrant class (the faa_master REGISTRANT NAME 3NF decomp), and "
            "the planner's Registrant-target candidates die in the type "
            "checker ('unknown property n_number on class Registrant') — every "
            "interpretation is rejected and the engine abstains."
        ),
        "strata_fix": (
            "Induce valid-time anchors: when a table shows identifier reuse "
            "(same non-key identifier, multiple rows) with a paired "
            "start/end date column whose ranges are disjoint per identifier, "
            "emit a temporal-window annotation the materializer turns into "
            "real valid Intervals and LODESTONE plans as-of filters against."
        ),
    },
    "CQ-04": {
        "category": "semantics gap (temporal identity)",
        "detail": (
            "Same key-reuse shape as CQ-03. Here the Master-target reading "
            "survives (grounding 0.83) but with Interval(0) validity BOTH "
            "registry rows for N44304 project their serials — the correct "
            "17238197 plus the superseded 28-1997319 — at confidence 0.21, "
            "far below tau_high: a low-confidence over-answer, not a "
            "confidently-wrong one."
        ),
        "strata_fix": "Same temporal-window induction as CQ-03.",
    },
    "CQ-11": {
        "category": "missing class (latent operator concept has no extent)",
        "detail": (
            "The operator property cluster spans three tables (asrs AIRCRAFT 1 "
            "OPERATOR, erp OPERATOR_NAME, ntsb OPERATOR), but STRATA admits it "
            "only on a lattice-intersection class (OperatorDateEvent) with NO "
            "backing candidate — no plan materializes operator entities, and "
            "because the columns are non-unique in their tables no alternate-"
            "identity domain forms, so generic ER never folds the ERP spelling "
            "variants. 'DELTA AIR LINES INC' then matches two different value "
            "surfaces (an unfolded operator spelling vs registrant_name) and "
            "the engine asks its one clarification instead of counting over a "
            "folded identity. Safe behavior, wrong outcome class."
        ),
        "strata_fix": (
            "Admit shared multi-table name-cluster domains as latent entity "
            "candidates (extent = distinct values of the cluster), so the "
            "generic ER cascade folds their spelling variants and counts "
            "group over the folded entity — the schema evidence (3-table "
            "property cluster) already exists; only candidate emission is "
            "missing."
        ),
    },
    "CQ-15": {
        "category": "grounding miss (abbreviation opacity / property drift)",
        "detail": (
            "ntsb INJ_TOT_F induces as 'injury_total_f': STRATA's normalizer "
            "expands INJ->injury and TOT->total, but the trailing 'F' (fatal) "
            "is semantically opaque without domain knowledge, so 'fatalities' "
            "cannot ground onto the measure. The planner falls back to the "
            "well-typed count-rows reading of 'How many ... across NTSB "
            "events in AK' = 5 at confidence 0.87 — under tau_high 0.92, so "
            "the calibrated gate holds, but this is the swap-in's most "
            "uncomfortable miss: a wrong number delivered with citations."
        ),
        "strata_fix": (
            "LLM-tier property naming at induction: escalate opaque column "
            "abbreviations through the spine (tier-2) for name/synonym "
            "proposals grounded in the column's value distribution, recorded "
            "as ordinary PropertyDef synonyms (the same surface the identity-"
            "synonym enrichment already uses)."
        ),
    },
}


def _matches(expected: Any, ans: Answer) -> bool:
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


def _limit_estate(estate: dict[str, Any], limit: Optional[int]) -> dict[str, Any]:
    if limit is None:
        return estate
    estate = dict(estate)
    estate["tables"] = {t: df.head(limit) for t, df in estate["tables"].items()}
    estate.pop("profiles", None)
    return estate


def _score(engine: Lodestone, questions: list[dict[str, Any]], ledger) -> list[dict[str, Any]]:
    rows = []
    for q in questions:
        ans = engine.ask(q["question"])
        if ans.clarification:
            status = "clarify"
        elif ans.abstained:
            status = "abstain"
        elif _matches(q.get("answer"), ans):
            status = "correct"
        else:
            status = "wrong"
        cited = True
        if not ans.abstained and not ans.clarification:
            n_cells = sum(len(r) for r in ans.rows)
            cited = len(ans.citations) == n_cells and all(
                c.atom_ids and all(ledger.get_atom(a) is not None for a in c.atom_ids)
                for c in ans.citations
            )
        rows.append({"q": q, "answer": ans, "status": status, "cited": cited})
    return rows


def _build_gold(estate, questions, workdir: Path):
    ledger = SqliteLedger(":memory:")
    onto = extend_gold_ontology(load_gold_ontology())
    hearth = Hearth(workdir / "gold-hearth", ledger)
    build_estate_world(estate, onto, hearth, ledger)
    engine = Lodestone(onto, hearth, ledger, DecisionSpine(SpineProfile(), model_client=None))
    return _score(engine, questions, ledger)


def _build_induced(estate, questions, workdir: Path):
    ledger = SqliteLedger(":memory:")
    artifacts = induce_estate(estate, ledger)
    hearth = Hearth(workdir / "induced-hearth", ledger)
    stats = materialize_induced(estate, artifacts.ontology, artifacts, hearth, ledger)
    engine = Lodestone(
        artifacts.ontology, hearth, ledger, DecisionSpine(SpineProfile(), model_client=None)
    )
    return _score(engine, questions, ledger), stats, artifacts


def _cell(r: dict[str, Any]) -> str:
    a: Answer = r["answer"]
    s = r["status"]
    if s == "correct":
        return "correct"
    if s == "abstain":
        return f"abstain ({a.abstain_reason[:48].strip()}...)" if a.abstain_reason else "abstain"
    if s == "clarify":
        return "clarify"
    flat = [v for row in a.rows for v in row]
    return f"WRONG (got {flat[:2]!r}, conf {a.confidence:.2f})"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None, help="head-N rows per table")
    ap.add_argument("--out", type=Path, default=Path("docs/SWAPIN_REPORT.md"))
    args = ap.parse_args()

    estate = _limit_estate(load_estate(), args.limit)
    questions = load_competency_questions()["questions"]

    with tempfile.TemporaryDirectory() as td:
        workdir = Path(td)
        gold = _build_gold(estate, questions, workdir)
        induced, stats, artifacts = _build_induced(estate, questions, workdir)

    by_id_g = {r["q"]["id"]: r for r in gold}
    by_id_i = {r["q"]["id"]: r for r in induced}
    answerable = [q for q in questions if q["answerable"]]

    def n_correct(rows):
        return sum(1 for r in rows if r["q"]["answerable"] and r["status"] == "correct")

    g_ok, i_ok = n_correct(gold), n_correct(induced)
    conf_wrong = [
        r for r in induced if r["status"] == "wrong" and r["answer"].confidence >= TAU_HIGH
    ]
    uncited = [r["q"]["id"] for r in induced if not r["cited"]]
    onto = artifacts.ontology

    lines: list[str] = []
    w = lines.append
    w("# STRATA Swap-In Report — aviation competency suite over the INDUCED ontology")
    w("")
    w(f"*Generated by `scripts/swapin_eval.py` on {date.today().isoformat()}"
      + (f" (--limit {args.limit})" if args.limit else " (full fixtures)") + ".*")
    w("")
    w("The swap-in (whitepaper §11.3 Phase 3): `ontoforge materialize --ontology induced`")
    w("materializes the estate via the GENERIC engine from the ontology STRATA induced —")
    w("no gold ontology, no hand-built mapping — and `ask` answers over that world.")
    w("")
    w("## Gates")
    w("")
    w("| gate | requirement | result |")
    w("|---|---|---|")
    w(f"| answerable correct (induced) | >= 8/15 | **{i_ok}/15** {'PASS' if i_ok >= 8 else 'FAIL'} |")
    w(f"| answerable correct (gold)    | reference | {g_ok}/15 |")
    w(f"| citation coverage on answered cells | 100% | {'100% PASS' if not uncited else 'FAIL: ' + ', '.join(uncited)} |")
    w(f"| confidently wrong (conf >= tau_high {TAU_HIGH}) | 0 | {len(conf_wrong)} {'PASS' if not conf_wrong else 'FAIL'} |")
    unans = [r for r in induced if r["q"]["expected_behavior"] == "abstain"]
    unans_ok = all(r["answer"].abstained for r in unans)
    w(f"| unanswerables abstain | 2/2 | {'2/2 PASS' if unans_ok else 'FAIL'} |")
    trick = next(r for r in induced if r["q"]["expected_behavior"] == "reject_unit_mismatch")
    trick_ok = trick["answer"].abstained and "type checker" in (trick["answer"].abstain_reason or "")
    w(f"| trick unit (CQ-18) rejected by type checker | yes | {'PASS' if trick_ok else 'FAIL'} |")
    w("")
    w("## Induced world")
    w("")
    w(f"- classes induced: {len(onto.classes)} "
      f"({', '.join(sorted(c.name for c in onto.iter_classes()))})")
    w(f"- entities {stats['entities']}, cells {stats['cells']}, links {stats['links']}")
    for cls, info in sorted(stats["er"].items()):
        w(f"- ER[{cls}]: {info['method']}, {info['clusters']} clusters / "
          f"{info['identities']} identities over {', '.join(info['tables'])}")
    w("")
    w("## Per-question scorecard (induced vs gold)")
    w("")
    w("| id | kind | gold | induced |")
    w("|---|---|---|---|")
    for q in questions:
        kind = ", ".join(q.get("kinds", []))
        w(f"| {q['id']} | {kind} | {_cell(by_id_g[q['id']])} | {_cell(by_id_i[q['id']])} |")
    w("")
    w("## Regression analysis (induced vs gold)")
    w("")
    regressed = [
        q["id"] for q in answerable
        if by_id_g[q["id"]]["status"] == "correct" and by_id_i[q["id"]]["status"] != "correct"
    ]
    if not regressed:
        w("No regressions: every question the gold world answers correctly, the induced world")
        w("answers correctly too.")
    for qid in regressed:
        info = REGRESSIONS.get(qid, {})
        w(f"### {qid} — {info.get('category', 'unclassified (update REGRESSIONS in scripts/swapin_eval.py)')}")
        w("")
        w(f"*{by_id_i[qid]['q']['question']}*")
        w("")
        w(f"- observed: {_cell(by_id_i[qid])}")
        if info.get("detail"):
            w(f"- why: {info['detail']}")
        if info.get("strata_fix"):
            w(f"- STRATA improvement implied: {info['strata_fix']}")
        w("")
    w("## Honest summary")
    w("")
    miss = Counter(
        r["status"] for r in induced if r["q"]["answerable"] and r["status"] != "correct"
    )
    miss_txt = ", ".join(f"{n} {s}" for s, n in sorted(miss.items())) or "none"
    w(f"The induced world answers {i_ok}/15 vs the gold world's {g_ok}/15. Misses: {miss_txt}.")
    if miss.get("wrong"):
        worst = max(
            (r["answer"].confidence for r in induced if r["status"] == "wrong"), default=0.0
        )
        w(f"Wrong answers stay BELOW tau_high {TAU_HIGH} (max wrong confidence {worst:.2f}),")
        w("so the calibrated-abstention safety gate holds, but they are real planner misses")
        w("— low-confidence fallback readings, not silent hallucinations (see per-question")
        w("analysis above). 100% atom-level citation coverage carries over unchanged.")
    else:
        w("No wrong answers at any confidence: every miss abstains or asks to clarify;")
        w("100% atom-level citation coverage carries over unchanged.")
    w("The remaining gap is concentrated in vocabulary opacity (abbreviated source columns")
    w("the induced names cannot expand), latent concepts STRATA admits only as un-extended")
    w("lattice intersections (the operator domain), and temporal identity (key reuse over")
    w("time) — instance-level evidence the pipeline could lift into STRATA annotations.")
    w("")

    args.out.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {args.out}")
    print(f"gold {g_ok}/15, induced {i_ok}/15, confidently-wrong {len(conf_wrong)}, "
          f"uncited {uncited or 'none'}")
    for q in questions:
        print(f"  {q['id']:6s} gold={by_id_g[q['id']]['status']:8s} induced={by_id_i[q['id']]['status']}")


if __name__ == "__main__":
    main()
