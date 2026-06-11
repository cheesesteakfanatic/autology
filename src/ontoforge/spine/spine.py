"""DecisionSpine — the tier router (whitepaper §8, §11.2 M2; MVP plan §2, §5.3).

Tier chain per decide():

  T0  registered deterministic rule functions per kind (exact keys, formats);
  T1  per-kind calibrated logistic scores over req.features + split conformal
      prediction set (singleton at level alpha => auto-decide, §3.4);
  T2  distilled-specialist ModelClient call (task spine.adjudicate.<kind>);
  T3  frontier ModelClient call, consulted only when T2's calibrated
      confidence lands in the ambiguous band (economy) or always (CRUCIBLE);
  HUMAN  tiers exhausted -> deferred_to_human=True.

Selective rule (MVP plan §2 escalation contract): confidence >= tau_high ->
auto-accept; binary score <= tau_low -> auto-reject; the band in between
escalates. Impact > 1 widens the band (high-impact decisions escalate more
readily, contracts.decisions.DecisionRequest.impact).

Budget governor (§8 economy): every T2/T3 call is admitted against the
remaining token budget using a conservative reservation (prompt-size estimate
+ max_tokens). When the reservation would overrun the budget the call is NOT
made and the decision returns quarantined=True — fail-closed, never a silent
auto-decision. CRUCIBLE sets the budget shadow price to ~0: budget is ignored
(still metered), the escalation band is widened to (0.02, 0.98), conformal
non-singletons always escalate, and T2+T3 are both consulted with agreement
boosting confidence (disagreement -> human queue, §8 adversarial verification).
"""

from __future__ import annotations

from typing import Callable, Optional, Sequence

from ontoforge.contracts import (
    CalibrationSample,
    DecisionKind,
    DecisionRequest,
    DecisionResult,
    ModelClient,
    SpineProfile,
    Tier,
    TierScore,
)

from .adjudicator import ADJUDICATE_MAX_TOKENS, Adjudication, adjudicate, build_prompt
from .calibration import EPS, CalibrationReport, KindCalibrator, heuristic_probabilities

# CRUCIBLE band: escalate on any non-trivial ambiguity (whitepaper §8).
CRUCIBLE_TAU_HIGH = 0.98
CRUCIBLE_TAU_LOW = 0.02
# Band widening per unit of impact above 1.0.
IMPACT_WIDENING = 0.02
# Conservative chars->tokens reservation factor for budget admission.
CHARS_PER_TOKEN = 4

RuleFn = Callable[[DecisionRequest], Optional[TierScore]]


class DecisionSpine:
    """Implements the contracts.decisions.Spine protocol."""

    def __init__(
        self,
        profile: SpineProfile,
        model_client: Optional[ModelClient] = None,
        ledger=None,
    ) -> None:
        self._profile = profile
        self._client = model_client
        self._ledger = ledger
        self._rules: dict[DecisionKind, list[RuleFn]] = {}
        self._calibrators: dict[DecisionKind, KindCalibrator] = {}
        self._spent = 0

    # ------------------------------------------------------------ protocol

    def set_profile(self, profile: SpineProfile) -> None:
        self._profile = profile

    def spent_tokens(self) -> int:
        return self._spent

    def recalibrate(self, kind: DecisionKind, samples: Sequence[CalibrationSample]) -> None:
        """Ingest ground-truth feedback (AL loop / human review / gold labels)
        and refit base model + Platt/isotonic recalibrators + conformal scores."""
        cal = self._calibrators.get(kind)
        if cal is None:
            cal = KindCalibrator(kind)
            self._calibrators[kind] = cal
        cal.fit(samples)

    def decide(self, req: DecisionRequest) -> DecisionResult:
        if len(req.candidates) < 2:
            raise ValueError("DecisionRequest needs >= 2 candidates")
        result = self._decide(req)
        if self._ledger is not None:
            self._ledger.append_decision(result, req.prov_atoms)
            self._ledger.record_cost(f"spine.decide.{req.kind.value}", result.cost_tokens)
        return result

    # -------------------------------------------------------------- extras

    def register_rule(self, kind: DecisionKind, fn: RuleFn) -> None:
        """Register a deterministic T0 rule: callable(req) -> TierScore | None."""
        self._rules.setdefault(kind, []).append(fn)

    def ece(self, kind: DecisionKind) -> Optional[float]:
        """Held-out ECE of the selected recalibrator for this kind (None if uncalibrated)."""
        cal = self._calibrators.get(kind)
        return cal.ece if cal is not None else None

    def calibrator(self, kind: DecisionKind) -> Optional[KindCalibrator]:
        """Read access to the fitted per-kind calibrator (AL inspection / tests)."""
        return self._calibrators.get(kind)

    def calibration_report(self) -> dict[str, CalibrationReport]:
        """Per-kind calibration report: method chosen, both candidate ECEs, splits."""
        return {k.value: c.report for k, c in self._calibrators.items()}

    # ------------------------------------------------------------- routing

    def _effective_taus(self, impact: float) -> tuple[float, float]:
        th, tl = self._profile.tau_high, self._profile.tau_low
        if self._profile.name == "crucible":
            th, tl = max(th, CRUCIBLE_TAU_HIGH), min(tl, CRUCIBLE_TAU_LOW)
        if impact > 1.0:
            w = IMPACT_WIDENING * (impact - 1.0)
            th = min(1.0 - 1e-4, th + w)
            tl = max(1e-4, tl - w)
        return th, tl

    @staticmethod
    def _select(
        req: DecisionRequest, probs: dict[str, float], tau_high: float, tau_low: float
    ) -> Optional[tuple[str, float]]:
        """Two-threshold selective rule. Returns (outcome, confidence) when the
        decision clears a threshold, None when it lands in the escalation band."""
        cands = req.candidates
        if len(cands) == 2:
            pos = cands[1]  # contract convention: binary = ("no", "yes")
            p_pos = probs[pos]
            if p_pos >= tau_high:
                return pos, p_pos
            if p_pos <= tau_low:
                return cands[0], 1.0 - p_pos
            return None
        out = max(cands, key=lambda c: probs[c])
        conf = probs[out]
        if conf >= tau_high:
            return out, conf
        return None

    @staticmethod
    def _argmax(req: DecisionRequest, probs: dict[str, float]) -> tuple[str, float]:
        out = max(req.candidates, key=lambda c: probs[c])
        return out, probs[out]

    @staticmethod
    def _choice_probs(req: DecisionRequest, choice: str, confidence: float) -> dict[str, float]:
        """Turn a model's {choice, confidence} into a distribution over candidates."""
        c = min(max(confidence, EPS), 1.0 - EPS)
        k = len(req.candidates)
        rest = (1.0 - c) / (k - 1)
        return {cand: (c if cand == choice else rest) for cand in req.candidates}

    def _model_probs(self, req: DecisionRequest, adj: Adjudication) -> dict[str, float]:
        if adj.choice is None:
            u = 1.0 / len(req.candidates)
            return {c: u for c in req.candidates}
        return self._choice_probs(req, adj.choice, adj.confidence)

    # ------------------------------------------------------------ economics

    def _estimate_call_tokens(self, prompt: str, max_tokens: int = ADJUDICATE_MAX_TOKENS) -> int:
        """Conservative reservation: estimated prompt tokens + the full output cap.
        Fail-closed: we quarantine on the estimate, never on an overrun after the fact."""
        return len(prompt) // CHARS_PER_TOKEN + max_tokens

    def _budget_blocks(self, prompt: str) -> bool:
        if self._profile.name == "crucible":
            return False  # shadow price ~0 (§8): budget ignored, spend still metered
        return self._spent + self._estimate_call_tokens(prompt) > self._profile.budget_tokens

    def _charge(self, tokens: int) -> None:
        self._spent += tokens

    # ---------------------------------------------------------------- core

    def _decide(self, req: DecisionRequest) -> DecisionResult:
        th, tl = self._effective_taus(req.impact)
        crucible = self._profile.name == "crucible"
        alpha = self._profile.alpha
        full_set = tuple(req.candidates)

        # ---------------------------------------------------------- T0 rules
        t0 = self._run_t0(req)
        if t0 is not None:
            probs0, t0_cost = t0
            sel = self._select(req, probs0, th, tl)
            if sel is not None:
                out, conf = sel
                return DecisionResult(
                    decision_id=req.decision_id,
                    outcome=out,
                    confidence=conf,
                    conformal_set=full_set,
                    tier=Tier.T0,
                    cost_tokens=t0_cost,
                    rationale=f"t0 deterministic rule; conf={conf:.4f}; taus=({tl:.3f},{th:.3f})",
                )

        # ------------------------------------------------ T1 calibrated model
        cal = self._calibrators.get(req.kind)
        probs1 = cal.probabilities(req.features, req.candidates) if cal is not None else None
        calibrated = probs1 is not None
        if probs1 is None:
            probs1 = heuristic_probabilities(req)
        t1_tag = f"t1:{cal.method}" if calibrated and cal is not None else "t1:uncalibrated-heuristic"
        cset = cal.conformal_set(req.features, req.candidates, alpha) if calibrated and cal else None
        report_set = cset if cset is not None else full_set
        singleton = cset is not None and len(cset) == 1

        sel = self._select(req, probs1, th, tl)
        if crucible:
            # Escalate on any non-trivial ambiguity: a fitted conformal predictor
            # must agree (singleton set) before a threshold pass auto-decides.
            decided = sel is not None and (cset is None or singleton)
        else:
            decided = sel is not None
        if decided and sel is not None:
            out, conf = sel
            return DecisionResult(
                decision_id=req.decision_id,
                outcome=out,
                confidence=conf,
                conformal_set=report_set,
                tier=Tier.T1,
                rationale=f"{t1_tag}; selective rule; conf={conf:.4f}; taus=({tl:.3f},{th:.3f})",
            )
        if not crucible and singleton:
            # Conformal gating (§3.4): singleton prediction set at level alpha
            # => auto-decide even inside the threshold band.
            out = cset[0]
            return DecisionResult(
                decision_id=req.decision_id,
                outcome=out,
                confidence=probs1[out],
                conformal_set=cset,
                tier=Tier.T1,
                rationale=f"{t1_tag}; conformal singleton at alpha={alpha}; auto-decide",
            )

        # -------------------------------------------------------- escalation
        if self._client is None:
            out, conf = self._argmax(req, probs1)
            return DecisionResult(
                decision_id=req.decision_id,
                outcome=out,
                confidence=conf,
                conformal_set=report_set,
                tier=Tier.HUMAN,
                deferred_to_human=True,
                rationale=f"{t1_tag}; ambiguous and no model client; deferred",
            )
        if crucible:
            return self._escalate_crucible(req, probs1, report_set, th, tl)
        return self._escalate_economy(req, probs1, report_set, th, tl)

    # ------------------------------------------------------------------- T0

    def _run_t0(self, req: DecisionRequest) -> Optional[tuple[dict[str, float], int]]:
        cost = 0
        for rule in self._rules.get(req.kind, []):
            ts = rule(req)
            if ts is None or ts.abstain or not ts.scores:
                continue
            cost += int(ts.cost_tokens)
            raw = {c: max(float(ts.scores.get(c, 0.0)), 0.0) for c in req.candidates}
            tot = sum(raw.values())
            if tot <= 0:
                continue
            return {c: v / tot for c, v in raw.items()}, cost
        return None

    # -------------------------------------------------------- economy chain

    def _escalate_economy(
        self,
        req: DecisionRequest,
        probs1: dict[str, float],
        report_set: tuple[str, ...],
        th: float,
        tl: float,
    ) -> DecisionResult:
        assert self._client is not None
        cost = 0

        prompt2 = build_prompt(req, "T2")
        if self._budget_blocks(prompt2):
            out, conf = self._argmax(req, probs1)
            return DecisionResult(
                decision_id=req.decision_id,
                outcome=out,
                confidence=conf,
                conformal_set=report_set,
                tier=Tier.T1,
                cost_tokens=0,
                quarantined=True,
                rationale=(
                    f"budget exhausted before T2 (spent={self._spent}, "
                    f"budget={self._profile.budget_tokens}); fail-closed quarantine"
                ),
            )
        adj2 = adjudicate(self._client, req, "T2")
        self._charge(adj2.tokens)
        cost += adj2.tokens
        probs2 = self._model_probs(req, adj2)
        sel2 = self._select(req, probs2, th, tl)
        if sel2 is not None:
            out, conf = sel2
            return DecisionResult(
                decision_id=req.decision_id,
                outcome=out,
                confidence=conf,
                conformal_set=report_set,
                tier=Tier.T2,
                cost_tokens=cost,
                rationale=f"t2 adjudication ({adj2.model_id}); conf={conf:.4f}",
            )

        # T2 confidence in the ambiguous band -> T3
        prompt3 = build_prompt(req, "T3")
        if self._budget_blocks(prompt3):
            out, conf = self._argmax(req, probs2)
            return DecisionResult(
                decision_id=req.decision_id,
                outcome=out,
                confidence=conf,
                conformal_set=report_set,
                tier=Tier.T2,
                cost_tokens=cost,
                quarantined=True,
                rationale=(
                    f"budget exhausted before T3 (spent={self._spent}, "
                    f"budget={self._profile.budget_tokens}); fail-closed quarantine"
                ),
            )
        adj3 = adjudicate(self._client, req, "T3")
        self._charge(adj3.tokens)
        cost += adj3.tokens
        probs3 = self._model_probs(req, adj3)
        sel3 = self._select(req, probs3, th, tl)
        if sel3 is not None:
            out, conf = sel3
            return DecisionResult(
                decision_id=req.decision_id,
                outcome=out,
                confidence=conf,
                conformal_set=report_set,
                tier=Tier.T3,
                cost_tokens=cost,
                rationale=f"t3 adjudication ({adj3.model_id}); conf={conf:.4f}",
            )
        out, conf = self._argmax(req, probs3)
        return DecisionResult(
            decision_id=req.decision_id,
            outcome=out,
            confidence=conf,
            conformal_set=report_set,
            tier=Tier.HUMAN,
            cost_tokens=cost,
            deferred_to_human=True,
            rationale="tiers exhausted (T0..T3 all ambiguous); deferred to human",
        )

    # ------------------------------------------------------- crucible chain

    def _escalate_crucible(
        self,
        req: DecisionRequest,
        probs1: dict[str, float],
        report_set: tuple[str, ...],
        th: float,
        tl: float,
    ) -> DecisionResult:
        """CRUCIBLE (§8): T2 and T3 are BOTH consulted — T2 as an independent
        agreement signal, T3 as the verifier. Agreement boosts confidence
        (independent-error OR-combination); disagreement goes to the human
        queue (adversarial verification). Budget is ignored (shadow price 0)."""
        assert self._client is not None
        adj2 = adjudicate(self._client, req, "T2")
        self._charge(adj2.tokens)
        adj3 = adjudicate(self._client, req, "T3")
        self._charge(adj3.tokens)
        cost = adj2.tokens + adj3.tokens

        if adj2.choice is not None and adj2.choice == adj3.choice:
            boosted = 1.0 - (1.0 - adj2.confidence) * (1.0 - adj3.confidence)
            probs_e = self._choice_probs(req, adj2.choice, boosted)
            sel = self._select(req, probs_e, th, tl)
            if sel is not None:
                out, conf = sel
                return DecisionResult(
                    decision_id=req.decision_id,
                    outcome=out,
                    confidence=conf,
                    conformal_set=report_set,
                    tier=Tier.T3,
                    cost_tokens=cost,
                    rationale=(
                        f"crucible t2/t3 agreement on '{adj2.choice}' "
                        f"({adj2.confidence:.3f},{adj3.confidence:.3f})->boost {boosted:.4f}"
                    ),
                )
            out, conf = self._argmax(req, probs_e)
            return DecisionResult(
                decision_id=req.decision_id,
                outcome=out,
                confidence=conf,
                conformal_set=report_set,
                tier=Tier.HUMAN,
                cost_tokens=cost,
                deferred_to_human=True,
                rationale="crucible: t2/t3 agree but boosted confidence still ambiguous; deferred",
            )

        # Disagreement (or T2 parse failure) -> human queue, T3 answer as best guess.
        out = adj3.choice or adj2.choice or self._argmax(req, probs1)[0]
        conf = min(max(adj3.confidence * (1.0 - adj2.confidence), EPS), 1.0 - EPS)
        return DecisionResult(
            decision_id=req.decision_id,
            outcome=out,
            confidence=conf,
            conformal_set=report_set,
            tier=Tier.HUMAN,
            cost_tokens=cost,
            deferred_to_human=True,
            rationale=(
                f"crucible: t2/t3 disagreement ({adj2.choice!r} vs {adj3.choice!r}); "
                "adversarial-verification human queue"
            ),
        )
