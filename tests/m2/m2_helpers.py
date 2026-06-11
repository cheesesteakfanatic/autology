"""Shared M2 test helpers: synthetic benchmark generators with KNOWN Bayes
posterior, deterministic token-charging fake ModelClients, and request builders.

The generators draw features from two overlapping Gaussians (one per outcome)
so the exact Bayes posterior is available in closed form — the calibration and
conformal assertions are made against ground truth, never against the model's
own outputs.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import Callable, Optional

import numpy as np

from ontoforge.contracts import (
    CalibrationSample,
    DecisionKind,
    DecisionRequest,
    ModelRequest,
    ModelResponse,
)

CANDS = ("no", "yes")

# ----------------------------------------------------------- synthetic worlds


@dataclass(frozen=True)
class GaussianWorld:
    """Binary world: features | outcome ~ N(mu_k, sigma^2 I) in 2-D, overlapping.

    Bayes posterior P(yes | x) is the closed-form logistic of the LDA score —
    exactly computable, so tests can compare calibrated output to ground truth.
    """

    mu: float = 1.0
    sigma: float = 1.2
    p_yes: float = 0.5

    def sample(self, seed: int, n: int) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """Returns (x1, x2, y_bool, bayes_posterior_of_yes)."""
        rng = np.random.default_rng(seed)
        y = rng.random(n) < self.p_yes
        x1 = rng.normal(np.where(y, self.mu, -self.mu), self.sigma, n)
        x2 = rng.normal(np.where(y, 0.5 * self.mu, -0.5 * self.mu), self.sigma, n)
        z = (
            (2.0 * self.mu * x1) / self.sigma**2
            + (self.mu * x2) / self.sigma**2
            + math.log(self.p_yes / (1.0 - self.p_yes))
        )
        return x1, x2, y, 1.0 / (1.0 + np.exp(-z))

    @staticmethod
    def features(a: float, b: float) -> tuple[tuple[str, float], ...]:
        return (("x1", float(a)), ("x2", float(b)))


@dataclass(frozen=True)
class MisspecifiedWorld:
    """Same two-Gaussian world but the spine sees a CUBED feature, so the raw
    logistic scores are systematically miscalibrated — post-hoc recalibration
    (Platt or isotonic, whichever the held-out referee picks) must repair ECE.
    """

    mu: float = 1.0
    sigma: float = 1.0

    def sample(self, seed: int, n: int) -> tuple[np.ndarray, np.ndarray]:
        rng = np.random.default_rng(seed)
        y = rng.random(n) < 0.5
        x = rng.normal(np.where(y, self.mu, -self.mu), self.sigma, n)
        return np.sign(x) * np.abs(x) ** 3, y  # monotone but very non-linear

    @staticmethod
    def features(v: float) -> tuple[tuple[str, float], ...]:
        return (("xc", float(v)),)


def gaussian_samples(
    kind: DecisionKind, seed: int, n: int, world: Optional[GaussianWorld] = None
) -> list[CalibrationSample]:
    w = world or GaussianWorld()
    x1, x2, y, _ = w.sample(seed, n)
    return [
        CalibrationSample(
            kind=kind,
            features=w.features(a, b),
            candidates=CANDS,
            true_outcome="yes" if t else "no",
        )
        for a, b, t in zip(x1, x2, y)
    ]


def misspecified_samples(kind: DecisionKind, seed: int, n: int) -> list[CalibrationSample]:
    w = MisspecifiedWorld()
    xc, y = w.sample(seed, n)
    return [
        CalibrationSample(
            kind=kind, features=w.features(v), candidates=CANDS, true_outcome="yes" if t else "no"
        )
        for v, t in zip(xc, y)
    ]


# ---------------------------------------------------------------- fake client


def heuristic_request(
    kind: DecisionKind, decision_id: str, score: float, impact: float = 1.0
) -> DecisionRequest:
    """A request whose UNCALIBRATED T1 heuristic confidence is exactly `score`:
    the heuristic reads the mean of the feature values as P(candidates[1])."""
    return DecisionRequest(
        kind=kind,
        decision_id=decision_id,
        candidates=CANDS,
        features=(("s", float(score)),),
        context=(("note", f"synthetic case {decision_id}"),),
        impact=impact,
    )


@dataclass
class ScriptedModelClient:
    """Deterministic token-charging fake ModelClient.

    Answers per tier from (choice, confidence) scripts; charges input tokens
    proportional to prompt length (len//4) and a handful of output tokens —
    strictly below the spine's conservative reservation, as any real adapter
    respecting max_tokens would be. Records every call for routing assertions.
    """

    t2: tuple[str, float] = ("yes", 0.99)
    t3: tuple[str, float] = ("yes", 0.99)
    by_decision: dict[str, dict[str, tuple[str, float]]] = field(default_factory=dict)
    malformed_tiers: frozenset[str] = frozenset()
    calls: list[tuple[str, str, str]] = field(default_factory=list)  # (tier, task, decision_id)

    def propose(self, req: ModelRequest) -> ModelResponse:
        lines = req.prompt.splitlines()
        tier = lines[0].split(":", 1)[1].strip()  # "tier: T2" -> "T2"
        payload = json.loads(next(ln for ln in lines if ln.startswith("{")))
        decision_id = payload["decision_id"]
        self.calls.append((tier, req.task, decision_id))
        if tier in self.malformed_tiers:
            text = "I am not sure, sorry."
        else:
            choice, conf = self.by_decision.get(decision_id, {}).get(
                tier, self.t2 if tier == "T2" else self.t3
            )
            text = json.dumps({"choice": choice, "confidence": conf})
        return ModelResponse(
            text=text,
            input_tokens=len(req.prompt) // 4,
            output_tokens=len(text) // 4 + 1,
            model_id=f"fake-{tier.lower()}",
        )

    def calls_for(self, tier: str) -> int:
        return sum(1 for t, _, _ in self.calls if t == tier)


@dataclass
class ExplodingModelClient:
    """A ModelClient that fails the test if it is ever called (no-escalation proofs)."""

    on_call: Callable[[], None] = lambda: (_ for _ in ()).throw(
        AssertionError("ModelClient must not be called")
    )

    def propose(self, req: ModelRequest) -> ModelResponse:  # pragma: no cover
        self.on_call()
        raise AssertionError("unreachable")
