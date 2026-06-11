"""TX acceptance (§5.2 step 4): every verified candidate is a DecisionKind.TX
decision through the Decision Spine.

Features: holdout pass rate, sample coverage, program complexity (MDL prior —
shorter programs preferred). A deterministic T0 rule encodes the acceptance
policy; ambiguous syntheses (holdout pass rate in the review band) land in the
escalation band, and with no model client the spine defers to human — recorded
as a ledger 'review' artifact (the readable-DSL review queue).

Accepted candidates become contracts.TransformDef artifacts with readable SQL
(pretty-printed via sqlglot) and synthesized_by = 'anvil:T0' | 'anvil:T1'.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Optional

import sqlglot
import xxhash

from ontoforge.contracts import (
    Atom,
    ClassDef,
    DecisionKind,
    DecisionRequest,
    DecisionResult,
    Layer,
    SpineProfile,
    TierScore,
    TransformDef,
    VerificationReport,
    leaf,
)
from ontoforge.spine import DecisionSpine

from .program import CandidateProgram

__all__ = ["AcceptanceOutcome", "Acceptor", "tx_rule", "pretty_sql"]

# Review band: shapes unsatisfied but holdout pass rate >= REVIEW_FLOOR -> human queue.
REVIEW_FLOOR = 0.70


def pretty_sql(sql: str) -> str:
    """Readability invariant: every accepted program ships pretty-printed SQL."""
    return sqlglot.transpile(sql, read="duckdb", write="duckdb", pretty=True)[0]


def tx_rule(req: DecisionRequest) -> Optional[TierScore]:
    """Deterministic T0 acceptance policy over the verification evidence."""
    f = dict(req.features)
    prov = f.get("provenance_equivalent", 0.0)
    shapes = f.get("shapes_satisfied", 0.0)
    rate = f.get("holdout_pass_rate", 0.0)
    mdl = f.get("mdl_prior", 0.5)          # 1/(1+complexity): shorter -> closer to 1
    if prov < 1.0:
        return TierScore(scores={"no": 0.98, "yes": 0.02})
    if shapes >= 1.0:
        yes = min(0.99, 0.94 + 0.05 * mdl)
        return TierScore(scores={"no": 1.0 - yes, "yes": yes})
    if rate >= REVIEW_FLOOR:
        # ambiguous: land inside the escalation band (tau_low < 0.6 < tau_high)
        return TierScore(scores={"no": 0.4, "yes": 0.6})
    return TierScore(scores={"no": 0.95, "yes": 0.05})


@dataclass(slots=True)
class AcceptanceOutcome:
    status: str                      # "accepted" | "review" | "rejected"
    decision: DecisionResult
    transform: Optional[TransformDef] = None


class Acceptor:
    def __init__(self, spine: Optional[DecisionSpine] = None, ledger=None) -> None:
        self.ledger = ledger
        self.spine = spine if spine is not None else DecisionSpine(SpineProfile(), ledger=ledger)
        self.spine.register_rule(DecisionKind.TX, tx_rule)

    # ------------------------------------------------------------------ api

    def decide(
        self,
        program: CandidateProgram,
        report: VerificationReport,
        target_class: ClassDef,
        *,
        coverage: float,
        source_id: str = "anvil",
    ) -> AcceptanceOutcome:
        sql = program.sql(tagged=False)
        sql_hash = xxhash.xxh3_64(sql.encode()).hexdigest()
        req = DecisionRequest(
            kind=DecisionKind.TX,
            decision_id=f"tx:{program.source_table}->{target_class.name}:{sql_hash}",
            candidates=("no", "yes"),
            features=(
                ("holdout_pass_rate", float(report.holdout_pass_rate)),
                ("shapes_satisfied", 1.0 if report.shapes_satisfied else 0.0),
                ("provenance_equivalent", 1.0 if report.provenance_equivalent else 0.0),
                ("sample_coverage", float(coverage)),
                # MDL prior in [0, 1]: shorter programs preferred; bounded so the
                # spine's pre-calibration heuristic stays sane
                ("mdl_prior", 1.0 / (1.0 + float(report.program_complexity))),
            ),
            context=(("sql", sql), ("notes", tuple(report.notes))),
        )
        decision = self.spine.decide(req)

        if decision.outcome == "yes" and decision.auto_decided:
            tdef = self._transform_def(program, target_class, sql)
            self._ledger_artifact("transform", tdef.fingerprint, self._payload(tdef, report), program, target_class)
            return AcceptanceOutcome("accepted", decision, tdef)
        if decision.deferred_to_human or decision.quarantined:
            self._ledger_artifact(
                "review",
                f"anvil-review:{sql_hash}",
                json.dumps(
                    {
                        "source_table": program.source_table,
                        "target_class": target_class.name,
                        "sql": pretty_sql(sql),
                        "holdout_pass_rate": report.holdout_pass_rate,
                        "notes": list(report.notes),
                    },
                    sort_keys=True,
                ),
                program,
                target_class,
            )
            return AcceptanceOutcome("review", decision)
        return AcceptanceOutcome("rejected", decision)

    # -------------------------------------------------------------- helpers

    def _transform_def(
        self, program: CandidateProgram, target_class: ClassDef, sql: str
    ) -> TransformDef:
        inputs = [f"raw.{program.source_table}"]
        if program.join is not None:
            inputs.append(f"raw.{program.join.table}")
        return TransformDef(
            name=f"anvil_{program.source_table}_to_{target_class.name.lower()}",
            inputs=tuple(sorted(inputs)),
            output=f"conformed.{target_class.name.lower()}",
            sql=pretty_sql(sql),
            output_layer=Layer.CONFORMED,
            expectations=tuple(target_class.shapes),
            description="; ".join(
                [f"{fx.kind}({fx.column}): {fx.note}" for fx in program.fixes] + program.notes
            ),
            synthesized_by=program.tier,
        )

    @staticmethod
    def _payload(tdef: TransformDef, report: VerificationReport) -> str:
        return json.dumps(
            {
                "name": tdef.name,
                "inputs": list(tdef.inputs),
                "output": tdef.output,
                "sql": tdef.sql,
                "synthesized_by": tdef.synthesized_by,
                "fingerprint": tdef.fingerprint,
                "verification": {
                    "holdout_rows": report.holdout_rows,
                    "holdout_pass_rate": report.holdout_pass_rate,
                    "shapes_satisfied": report.shapes_satisfied,
                    "provenance_equivalent": report.provenance_equivalent,
                    "program_complexity": report.program_complexity,
                },
            },
            sort_keys=True,
        )

    def _ledger_artifact(
        self, kind: str, artifact_id: str, payload: str, program: CandidateProgram, target_class: ClassDef
    ) -> None:
        if self.ledger is None:
            return
        atom = Atom(
            uri=f"atom://anvil/{program.source_table}/{target_class.name}#synthesis",
            value=payload,
        )
        self.ledger.register_atoms([atom])
        prov_ref = self.ledger.intern(leaf(atom.atom_id))
        self.ledger.append_artifact(artifact_id, kind, payload, prov_ref)
