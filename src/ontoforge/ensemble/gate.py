"""The DE decision gate — weighted-majority voting over deterministic experts.

The headline mechanism (docs/AI_NATIVE_AND_UI_PLAN.md §D): decide whether a
data-engineering action (join / merge / retype) should FIRE. Research-correct,
keyless today, and unchanged when live models are swapped in as experts.

The five load-bearing ideas, in the order they apply inside :meth:`Gate.decide`:

1. **Execution-grounded verification FIRST (the veto).** A ``verifier`` callback
   (e.g. the engineer's join-coverage floor) can VETO regardless of votes — the
   data refusing a join overrides any unanimous 'fire'. The experts only PROPOSE;
   the verifier + vote GATE. This IS the confidently-wrong guard for the gate.

2. **PER-EXPERT Weighted-Majority Aggregation (WMA).** Each expert has a weight
   (start 1.0). Each expert's confidence-scaled weight is added to the bucket of
   the side it voted ('fire' / 'hold'); the side with the larger summed weight
   wins. Per-expert weighting (not per-temperature) is the research-correct axis.

3. **TURN — a label-free aggregation temperature.** ``aggregation_temperature``
   picks a near-optimal softness for combining votes by an entropy turning-point
   over the *current* vote spread — no labels, fits the keyless constraint. It
   sharpens a confident, agreeing ensemble and softens a split one; it tunes
   confidence, it is NOT the primary decision axis (cross-expert voting is).

4. **Soft-Self-Consistency for SPARSE actions.** For a single specific action we
   score the 'fire' candidate continuously by min/mean/product of the per-expert
   confidences (not exact-match majority), and gate the final fire on a CALIBRATED
   probability ``threshold``. High-temperature single-model self-consistency is
   deliberately avoided (it adds hallucination, not useful diversity).

5. **Self-improving weights (Littlestone–Warmuth).** :meth:`Gate.update_weights`
   applies a multiplicative penalty ``epsilon = sqrt(ln N / T)`` to every expert
   that voted against a later human-confirmed outcome — the provable-regret rule,
   wired to the review queue's Confirm/Reject so the system self-improves as the
   lazy user clicks.

Determinism: with a fixed set of experts and weights, :meth:`decide` is a pure
function of the :class:`~.experts.ActionContext` — identical inputs yield an
identical :class:`GateDecision`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Callable, Optional, Sequence

from .experts import ActionContext, Expert, Vote

__all__ = [
    "DEFAULT_THRESHOLD",
    "Gate",
    "GateDecision",
    "soft_self_consistency",
    "turn_temperature",
]

#: calibrated probability threshold the soft-self-consistency 'fire' score must
#: clear (alongside the weighted majority) for a SPARSE action to fire.
DEFAULT_THRESHOLD = 0.5

#: a verifier returns (ok, reason); ok=False VETOES regardless of votes.
Verifier = Callable[[ActionContext], "tuple[bool, str]"]


# --------------------------------------------------------------------------
# TURN — label-free aggregation temperature
# --------------------------------------------------------------------------


def turn_temperature(confidences: Sequence[float], lo: float = 0.3, hi: float = 1.5) -> float:
    """Pick a label-free aggregation temperature from the spread of the experts'
    confidences (a TURN-style entropy turning-point).

    Intuition: a tight, confident ensemble wants a LOW temperature (sharpen — trust
    it); a dispersed, uncertain ensemble wants a HIGH temperature (soften — hedge).
    We map normalized spread (std/mean-ish) into [lo, hi]. Deterministic; no labels.
    """
    cs = [c for c in confidences if c is not None]
    if len(cs) < 2:
        return 1.0
    mean = sum(cs) / len(cs)
    if mean <= 0:
        return hi
    var = sum((c - mean) ** 2 for c in cs) / len(cs)
    spread = math.sqrt(var) / (mean + 1e-9)  # coefficient of variation
    t = lo + (hi - lo) * min(1.0, spread)
    return round(t, 6)


def soft_self_consistency(confidences: Sequence[float], mode: str = "mean") -> float:
    """Soft-Self-Consistency continuous score for a SPARSE action: combine the
    per-sample (here per-expert agreeing) confidences by ``min`` / ``mean`` /
    ``product`` instead of exact-match majority. Returns a score in [0,1]."""
    cs = [max(0.0, min(1.0, c)) for c in confidences]
    if not cs:
        return 0.0
    if mode == "min":
        return min(cs)
    if mode == "product":
        p = 1.0
        for c in cs:
            p *= c
        return p
    return sum(cs) / len(cs)  # mean (default)


# --------------------------------------------------------------------------
# The gate
# --------------------------------------------------------------------------


@dataclass(slots=True)
class GateDecision:
    """The gate's verdict and full provenance — recorded into the ledger so a
    gated action can answer 'why did this fire?'."""

    fire: bool
    confidence: float
    tally: dict[str, float]              # summed weight per side {'fire':..,'hold':..}
    weights: dict[str, float]            # per-expert weight at decision time
    votes: list[dict[str, Any]]          # per-expert {name, decision, confidence, rationale}
    vetoed: bool = False
    veto_reason: str = ""
    threshold: float = DEFAULT_THRESHOLD
    soft_score: float = 0.0
    temperature: float = 1.0

    def to_provenance(self) -> dict[str, Any]:
        """JSON-serializable provenance payload for the ledger."""
        return {
            "fire": self.fire,
            "confidence": round(self.confidence, 6),
            "tally": {k: round(v, 6) for k, v in self.tally.items()},
            "weights": {k: round(v, 6) for k, v in self.weights.items()},
            "votes": self.votes,
            "vetoed": self.vetoed,
            "veto_reason": self.veto_reason,
            "threshold": self.threshold,
            "soft_score": round(self.soft_score, 6),
            "temperature": self.temperature,
        }


class Gate:
    """Weighted-majority DE-action gate with a Littlestone–Warmuth self-improving
    weight update. Construct with the experts; ``decide`` over an action context
    (with an optional verifier veto); ``update_weights`` on a human verdict."""

    def __init__(
        self,
        experts: Sequence[Expert],
        *,
        threshold: float = DEFAULT_THRESHOLD,
        horizon: int = 64,
        weights: Optional[dict[str, float]] = None,
        soft_mode: str = "mean",
    ) -> None:
        if not experts:
            raise ValueError("Gate needs at least one expert")
        self.experts: list[Expert] = list(experts)
        names = [e.name for e in self.experts]
        if len(set(names)) != len(names):
            raise ValueError(f"expert names must be unique, got {names}")
        self.threshold = threshold
        self.soft_mode = soft_mode
        # Littlestone–Warmuth penalty rate epsilon = sqrt(ln N / T). N experts,
        # T = horizon (expected number of feedback rounds). Clamped to (0,1).
        n = len(self.experts)
        self._epsilon = min(0.99, max(1e-3, math.sqrt(math.log(max(n, 2)) / max(horizon, 1))))
        self.weights: dict[str, float] = (
            dict(weights) if weights is not None else {e.name: 1.0 for e in self.experts}
        )

    @property
    def epsilon(self) -> float:
        return self._epsilon

    # ----------------------------------------------------------------- vote

    def _collect_votes(self, ctx: ActionContext) -> list[Vote]:
        out: list[Vote] = []
        for e in self.experts:
            v = e.vote(ctx)
            # ensure the vote carries the expert name even if the expert omitted it
            out.append(v if v.name else Vote(v.decision, v.confidence, v.rationale, e.name))
        return out

    def decide(
        self,
        ctx: ActionContext,
        verifier: Optional[Verifier] = None,
    ) -> GateDecision:
        """Gate an action. Verifier veto FIRST (execution-grounded), then weighted
        majority + soft-self-consistency threshold. Deterministic in (experts,
        weights, ctx)."""
        votes = self._collect_votes(ctx)
        vote_rows = [
            {"name": v.name, "decision": v.decision, "confidence": round(v.confidence, 6),
             "rationale": v.rationale}
            for v in votes
        ]

        # 1) execution-grounded VETO — overrides votes entirely.
        if verifier is not None:
            ok, reason = verifier(ctx)
            if not ok:
                return GateDecision(
                    fire=False, confidence=1.0,
                    tally=self._tally(votes), weights=dict(self.weights),
                    votes=vote_rows, vetoed=True, veto_reason=reason,
                    threshold=self.threshold, soft_score=0.0, temperature=1.0,
                )

        # 2) TURN aggregation temperature (label-free).
        temp = turn_temperature([v.confidence for v in votes])

        # 3) weighted-majority tally with temperature-shaped confidence weighting.
        tally = self._tally(votes, temperature=temp)
        fire_w, hold_w = tally["fire"], tally["hold"]
        total_w = fire_w + hold_w
        majority_fire = fire_w > hold_w

        # 4) Soft-Self-Consistency: continuous 'fire' score for the SPARSE action,
        #    over the experts that voted fire (their confidences). Gate on threshold.
        fire_confs = [v.confidence for v in votes if v.decision == "fire"]
        soft = soft_self_consistency(fire_confs, mode=self.soft_mode)
        margin = (fire_w / total_w) if total_w > 0 else 0.0

        fire = bool(majority_fire and soft >= self.threshold)
        # confidence: blend the weighted margin with the soft score (both in [0,1])
        confidence = round(0.5 * margin + 0.5 * (soft if fire else (1.0 - soft)), 6)

        return GateDecision(
            fire=fire,
            confidence=confidence,
            tally=tally,
            weights=dict(self.weights),
            votes=vote_rows,
            vetoed=False,
            threshold=self.threshold,
            soft_score=round(soft, 6),
            temperature=temp,
        )

    def _tally(self, votes: Sequence[Vote], temperature: float = 1.0) -> dict[str, float]:
        """Summed (weight * temperature-shaped confidence) per side."""
        out = {"fire": 0.0, "hold": 0.0}
        for v in votes:
            w = self.weights.get(v.name, 1.0)
            # temperature shapes how much a confidence counts: T<1 sharpens
            # (confident votes dominate), T>1 flattens (votes count more equally).
            shaped = v.confidence ** (1.0 / max(temperature, 1e-6))
            out[v.decision] += w * shaped
        return out

    # ------------------------------------------------------- weight update

    def update_weights(self, ctx: ActionContext, confirmed_fire: bool) -> dict[str, float]:
        """Littlestone–Warmuth update on a human Confirm/Reject verdict.

        ``confirmed_fire`` is the ground truth the reviewer established (True =
        the action was correct to fire, False = it should have held). Every expert
        whose vote DISAGREED with that truth is penalized multiplicatively by
        ``(1 - epsilon)``; agreeing experts keep their weight. Weights are then
        renormalized so their mean stays 1.0 (keeps the tally scale stable while
        preserving relative trust). Returns the new weight map."""
        truth = "fire" if confirmed_fire else "hold"
        for v in self._collect_votes(ctx):
            if v.decision != truth:
                self.weights[v.name] = self.weights.get(v.name, 1.0) * (1.0 - self._epsilon)
        self._renormalize()
        return dict(self.weights)

    def _renormalize(self) -> None:
        n = len(self.weights)
        s = sum(self.weights.values())
        if s <= 0:
            self.weights = {k: 1.0 for k in self.weights}
            return
        scale = n / s
        self.weights = {k: w * scale for k, w in self.weights.items()}
