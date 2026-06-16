"""M12 — LODESTONE: Ontology-Grounded Query Planning over OQIR (§6.2).

Public entry point:

    ask(question, ontology, hearth, ledger, spine) -> contracts.Answer

or the stateful `Lodestone` engine (which additionally supports answering the
one clarification question LODESTONE may pose).
"""

from __future__ import annotations

import hashlib
from typing import Optional

from ontoforge.contracts import (
    Answer,
    DecisionKind,
    DecisionRequest,
    ModelClient,
    TierScore,
)
from ontoforge.contracts.ontology import Ontology
from ontoforge.ledger.models import HeuristicAdapter

from .candidates import (
    GENERATE_SCHEMA,
    GENERATE_TASK,
    CandidateSet,
    generate_candidates,
    make_generate_handler,
)
from .citations import assemble_citations
from .clarify import Clarification, resolve_choice, structural_diff
from .execute import EmptyResult, ExecOutcome, execute_candidate
from .flywheel import AskFlywheel, answer_fingerprint, requires_live_composition
from .grounding import ValueIndex, build_value_index, ground
from .model import Candidate
from .typecheck import typecheck, infer, unit_convertible  # re-export

__all__ = [
    "Lodestone",
    "ask",
    "typecheck",
    "infer",
    "unit_convertible",
    "ground",
    "build_value_index",
    "generate_candidates",
    "make_generate_handler",
    "GENERATE_TASK",
    "GENERATE_SCHEMA",
    "AskFlywheel",
    "answer_fingerprint",
    "requires_live_composition",
]

MIN_COVERAGE = 0.6          # strong-grounding floor below which we abstain
MIN_COVERAGE_SOFT = 0.45    # soft-clarify band floor: [SOFT, MIN) asks instead
                            # of abstaining when a strong anchor + a well-typed
                            # candidate exist (whitepaper §6.2 recoverable turn)
CONFORMAL_MARGIN = 0.90     # Γ = candidates within this score ratio of the top
SHARPEN = 4                 # score -> pseudo-probability exponent (uncalibrated)
ABSTAIN_ID = "__abstain__"


class Lodestone:
    """One engine per (ontology, hearth, ledger, spine) world."""

    def __init__(
        self,
        ontology: Ontology,
        hearth,
        ledger,
        spine,
        model_client: Optional[ModelClient] = None,
        *,
        tenant_id: str = "",
        work_store=None,
        flywheel: bool = True,
    ) -> None:
        self.onto = ontology
        self.hearth = hearth
        self.ledger = ledger
        self.spine = spine
        # LLM-readiness seam (keyless-default, byte-identical): the deterministic
        # generate handler is the keyless fallback. resolve_client returns it
        # UNCHANGED with no provider env (so the keyless path is the same object
        # built today and no decorator/router ever runs); with a provider + key it
        # wraps a live adapter behind secure + schema-validate + fallback. An
        # explicit model_client is honored as-is (test/handler injection stays exact).
        if model_client is not None:
            self.client: ModelClient = model_client
        else:
            from ontoforge.aimodels import resolve_client

            self.client = resolve_client(
                GENERATE_TASK,
                fallback=HeuristicAdapter({GENERATE_TASK: make_generate_handler(ontology)}),
            )
        self._value_index: Optional[ValueIndex] = None
        self._pending: Optional[tuple[str, Clarification, float]] = None
        # the Ask flywheel (v2.1 §4): a per-engine CachedWorkStore, created lazily
        # so a world that never asks pays nothing. `flywheel=False` disables the
        # close-the-loop path (always recompute) for benchmarking / debugging.
        self.tenant_id = tenant_id
        self._flywheel_on = flywheel
        self._work_store = work_store
        self._flywheel = None
        self._current_question: Optional[str] = None
        # a clarification-resolved answer is conditional on the human's choice and
        # is NOT a stable property of the bare question — it is never cached, so an
        # ambiguous question re-poses its clarification on every fresh ask.
        self._suppress_write_back = False
        if hasattr(spine, "register_rule"):
            spine.register_rule(DecisionKind.QI, _qi_rule)

    # ------------------------------------------------------------- flywheel

    @property
    def work_store(self):
        """The project CachedWorkStore (lazily created; one per engine/world)."""
        if self._work_store is None:
            from ontoforge.discovery import CachedWorkStore

            self._work_store = CachedWorkStore()
        return self._work_store

    @property
    def flywheel(self):
        if self._flywheel is None:
            from .flywheel import AskFlywheel

            self._flywheel = AskFlywheel(
                self.work_store, self.onto, self.hearth, self.ledger,
                tenant_id=self.tenant_id,
            )
        return self._flywheel

    # ------------------------------------------------------------ plumbing

    @property
    def value_index(self) -> ValueIndex:
        if self._value_index is None:
            self._value_index = build_value_index(self.hearth, self.onto)
        return self._value_index

    def refresh_value_index(self) -> None:
        self._value_index = None

    # ------------------------------------------------------------- asking

    def ask(self, question: str) -> Answer:
        self._pending = None
        self._current_question = question
        self._suppress_write_back = False

        # §4 step 2 — consult the cache FIRST. A still-valid cached answer for a
        # previously composed Ask is served (marked cached) without re-grounding,
        # re-generating candidates, or re-deciding through the spine. A stale entry
        # (its provenance atoms moved) is silently invalidated here and recomputed.
        if self._flywheel_on:
            hit = self.flywheel.consult(question)
            if hit is not None:
                return hit

        g = ground(question, self.onto, self.value_index)

        # below the SOFT floor: genuinely ungroundable -> hard abstain (the
        # abstention contract; CQ-16/CQ-17 land here)
        if g.coverage < MIN_COVERAGE_SOFT:
            missing = ", ".join(g.unconsumed[:8])
            return _abstain(
                f"insufficient grounding (coverage {g.coverage:.2f} < {MIN_COVERAGE}): "
                f"could not ground: {missing}"
            )

        # a STRONG anchor (a class or property the question really named, not a
        # lone value probe) is required to answer OR soft-clarify — keeps an
        # out-of-scope question from clearing the floor on a coincidental probe.
        # The soft-clarify band additionally needs the question's MEASURE to be
        # grounded (a strong prop/agg/textjoin), not merely the entity class:
        # 'warranty period of product P0042' grounds only the class+id and the
        # asked-for property is absent, so it stays on the hard-abstain path.
        has_strong_anchor = any(
            b.strong and b.kind in ("class", "prop") for b in g.bindings
        )
        # the soft-clarify MEASURE anchor must be a property/aggregation the
        # question genuinely named BEYOND the entity class — a prop binding whose
        # span merely echoes a class mention ('product' -> product_id) does not
        # count, so 'warranty period of product P0042' (only class+id grounded,
        # the asked-for property absent) stays on the hard-abstain path.
        class_spans = {
            tok for b in g.bindings
            if b.strong and b.kind == "class" for tok in b.span
        }
        has_measure_anchor = any(
            b.strong and (
                b.kind in ("agg", "textjoin", "number_cond", "date_cond",
                           "having_gt1")
                or (b.kind == "prop" and not set(b.span) <= class_spans)
            )
            for b in g.bindings
        )

        cs: CandidateSet = generate_candidates(question, g, self.onto, self.client)
        if not cs.candidates:
            if cs.type_errors:
                msgs = "; ".join(sorted({e.message for e in cs.type_errors}))
                return _abstain(f"rejected by the OQIR type checker: {msgs}")
            return _abstain("no well-typed interpretation could be constructed")

        # SOFT band [SOFT, MIN): one or two tokens short of the floor with a
        # strong anchor and a well-typed candidate that ACTUALLY HOLDS DATA — ask
        # a single disambiguating question naming the unconsumed tokens instead
        # of abstaining silently. A clarification is never a wrong answer, so
        # 0-confidently-wrong holds. The non-empty-execution requirement keeps a
        # genuinely unanswerable question (CQ-16: airframe hours exist in NO
        # source) on the hard-abstain path: its candidate executes empty.
        if g.coverage < MIN_COVERAGE:
            viable_top = None
            if has_strong_anchor and has_measure_anchor:
                for c in cs.candidates[:4]:
                    out = execute_candidate(c, self.onto, self.hearth)
                    if isinstance(out, ExecOutcome) and out.rows:
                        viable_top = c
                        break
            if viable_top is not None:
                unconsumed = ", ".join(g.unconsumed[:6])
                bound = sorted({
                    (self.onto.get(b.target.split("::")[0]).name
                     if self.onto.get(b.target.split("::")[0]) else b.target)
                    for b in g.bindings
                    if b.strong and b.kind == "class"
                })
                anchor_txt = ", ".join(bound[:3]) or "the named fields"
                clar = Clarification(
                    question=(
                        f"I can answer about {anchor_txt}; what did you mean by "
                        f"'{unconsumed}'?"
                    ),
                    options=("the entities I identified", "rephrase"),
                    candidates=(viable_top, viable_top),
                )
                self._pending = (question, clar, g.coverage)
                a = Answer(confidence=round(0.5 * g.coverage, 4))
                a.clarification = clar.question
                a.clarification_options = clar.options
                return a
            missing = ", ".join(g.unconsumed[:8])
            return _abstain(
                f"insufficient grounding (coverage {g.coverage:.2f} < {MIN_COVERAGE}): "
                f"could not ground: {missing}"
            )

        # ---- DecisionKind.QI through the spine
        decision = self.spine.decide(_qi_request(question, cs.candidates, g.coverage))
        confidence = round(decision.confidence * g.coverage, 4)

        # ---- conformal-style set over candidate scores
        top = cs.candidates[0]
        gamma = [c for c in cs.candidates if c.score >= CONFORMAL_MARGIN * top.score]
        if decision.outcome == ABSTAIN_ID and len(gamma) == 1:
            return _abstain(
                f"interpretation score too weak (grounding coverage {g.coverage:.2f})"
            )

        # ---- execution-guided re-ranking (§6.2): members of Γ whose plan is
        # empty even after repair are not viable readings and leave the set
        if len(gamma) > 1:
            executed: dict[str, ExecOutcome] = {}
            viable: list[Candidate] = []
            for c in gamma:
                out = execute_candidate(c, self.onto, self.hearth)
                if isinstance(out, ExecOutcome):
                    executed[c.cand_id] = out
                    viable.append(c)
            distinct_results = {
                (tuple(executed[c.cand_id].columns),
                 tuple(tuple(str(v) for v in r) for r in executed[c.cand_id].rows))
                for c in viable
            }
            if len(viable) > 1 and len(distinct_results) > 1:
                # candidates whose answers AGREE need no clarification — the
                # minimal-entropy question over an agreeing set is vacuous
                clar = structural_diff(viable, self.onto)
                if clar is not None:
                    self._pending = (question, clar, g.coverage)
                    a = Answer(confidence=confidence)
                    a.clarification = clar.question
                    a.clarification_options = clar.options
                    return a
            if viable:
                # re-rank Γ after execution filtering: the decision is re-posed
                # over the surviving interpretations only (§6.2 repair re-ranks)
                redecision = self.spine.decide(
                    _qi_request(question + " #viable", viable, g.coverage)
                )
                confidence = round(redecision.confidence * g.coverage, 4)
                return self._answer(viable[0], executed[viable[0].cand_id], confidence)
            # whole Γ empty: fall through to the remaining candidates

        # ---- execute with repair; execution-guided re-ranking on failure
        return self._execute_ranked(cs.candidates, confidence)

    def answer_clarification(self, choice) -> Answer:
        """Answer the pending clarification; re-ranks Γ to a singleton."""
        if self._pending is None:
            return _abstain("no clarification is pending")
        question, clar, coverage = self._pending
        cand = resolve_choice(clar, choice)
        if cand is None:
            return _abstain(f"clarification answer {choice!r} matches no offered option")
        self._pending = None
        # a clarification-resolved answer is conditional on the choice — never cache
        self._current_question = question
        self._suppress_write_back = True
        confidence = round(min(0.98, 0.9 + 0.08 * coverage), 4)  # post-clarification singleton
        return self._execute_ranked([cand], confidence)

    # ------------------------------------------------------------ internal

    def _execute_ranked(self, cands: list[Candidate], confidence: float) -> Answer:
        first_failure: Optional[EmptyResult] = None
        for cand in cands:
            out = execute_candidate(cand, self.onto, self.hearth)
            if isinstance(out, EmptyResult):
                first_failure = first_failure or out
                continue
            return self._answer(cand, out, confidence)
        assert first_failure is not None
        return _abstain(
            "no data after execution-guided repair; failed at: " + first_failure.leaf
        )

    def _answer(self, cand: Candidate, out: ExecOutcome, confidence: float) -> Answer:
        a = Answer(
            columns=list(out.columns),
            rows=[list(r) for r in out.rows],
            confidence=confidence,
            oqir=cand.term,
        )
        a.citations = assemble_citations(out.rows, out.cell_provs, out.columns, self.ledger)
        # §4 step 1 — close the loop: a successful Ask that required live
        # composition (a plan over 2+ types or a fresh aggregate) is written back
        # as a versioned, referenceable cached object so the next ask is faster.
        # The write-back is best-effort and never affects the answer returned.
        if (
            self._flywheel_on
            and not self._suppress_write_back
            and self._current_question is not None
        ):
            try:
                self.flywheel.write_back(self._current_question, cand, a)
            except Exception:
                pass
        return a


def _abstain(reason: str) -> Answer:
    return Answer(abstained=True, abstain_reason=reason, confidence=0.0)


def _qi_request(question: str, cands: list[Candidate], coverage: float) -> DecisionRequest:
    qid = hashlib.sha256(question.encode()).hexdigest()[:16]
    abstain_score = max(0.25, 1.05 - coverage)
    features = tuple(
        [(c.cand_id, round(c.score**SHARPEN, 8)) for c in cands]
        + [(ABSTAIN_ID, round(abstain_score**SHARPEN, 8))]
    )
    return DecisionRequest(
        kind=DecisionKind.QI,
        decision_id=f"qi-{qid}",
        candidates=tuple(c.cand_id for c in cands) + (ABSTAIN_ID,),
        features=features,
        context=(("question", question),),
        impact=1.0,
    )


def _qi_rule(req: DecisionRequest):
    """T0 rule for QI: candidate scores arrive as features named per candidate
    (deterministic; the spine normalizes and applies the selective rule)."""
    fmap = dict(req.features)
    if not any(c in fmap for c in req.candidates):
        return None
    return TierScore(scores={c: max(float(fmap.get(c, 0.0)), 0.0) for c in req.candidates})


def ask(
    question: str,
    ontology: Ontology,
    hearth=None,
    ledger=None,
    spine=None,
    *,
    model_client=None,
) -> Answer:
    """Single-shot entry point (§11.2 M12 interface).

    Degrades to an abstention (never an exception) when no HEARTH/ledger/spine
    world is attached — callers like the CLI render the abstention reason."""
    if hearth is None or ledger is None:
        return _abstain(
            "no entity store attached: LODESTONE answers over a HEARTH + ledger world "
            "(materialize the estate first)"
        )
    if spine is None:
        from ontoforge.contracts import SpineProfile
        from ontoforge.spine import DecisionSpine

        spine = DecisionSpine(SpineProfile(), model_client=None)
    return Lodestone(ontology, hearth, ledger, spine, model_client=model_client).ask(question)
