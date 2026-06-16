"""Observation loop for the LIVING prompt library (plan §3, AI-native layer).

A prompt is no longer a static artifact: every time the router *proposes* against
a task we can record an :class:`Observation` of which prompt **version** fired,
which model/tier served it, and what the model decided. Aggregated, this lets the
library promote the empirically best version per task (see ``library.py``).

HARD invariants (suite-enforced):

* **Deterministic.** No wall-clock anywhere: each :class:`Observation` carries an
  explicit, monotonically increasing integer ``seq`` assigned by the log, so two
  identical runs produce byte-identical records.
* **Stable fingerprint.** ``input_fingerprint`` is the first 16 hex chars of the
  SHA-256 of the rendered prompt string; equal prompts => equal fingerprints, so
  observations can be grouped/deduplicated without storing the prompt itself.
* **Keyless / offline.** Pure stdlib (``hashlib``); nothing here touches a key or
  the network.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

__all__ = [
    "FIRE",
    "Observation",
    "ObservationLog",
    "fingerprint_prompt",
]

#: the decision string that counts as a positive ("fire") in ``fire_rate``.
FIRE = "fire"


def fingerprint_prompt(prompt: str) -> str:
    """First 16 hex chars of sha256(prompt) — stable across runs and processes.

    Equal prompt strings always yield the same fingerprint; this is the join key
    that lets us group observations by *what was actually asked* without keeping
    the (potentially large, PII-bearing) prompt around.
    """
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True, slots=True)
class Observation:
    """One recorded proposal outcome.

    ``task``/``version`` identify the prompt template that served the call;
    ``model_id``/``tier`` the spec that answered; ``decision``/``confidence`` the
    structured outcome (defensively extracted by ``aimodels.make_observer``);
    ``input_fingerprint`` the stable hash of the rendered prompt; ``seq`` the
    log-assigned ordinal (NO wall-clock — determinism).
    """

    task: str
    version: str
    input_fingerprint: str
    model_id: str
    tier: str
    decision: str
    confidence: float
    seq: int


class ObservationLog:
    """An append-only, deterministic log of :class:`Observation` records.

    ``append`` auto-assigns the next integer ``seq`` (starting at 0) so callers
    never supply a timestamp. ``summarize(task)`` rolls the records for a task up
    into per-version stats used by :meth:`PromptLibrary.select_by_observations`.
    """

    def __init__(self) -> None:
        self.records: list[Observation] = []
        self._next_seq: int = 0

    def append(
        self,
        task: str,
        version: str,
        input_fingerprint: str,
        model_id: str,
        tier: str,
        decision: str,
        confidence: float,
    ) -> Observation:
        """Record one observation, auto-assigning the next monotonic ``seq``."""
        obs = Observation(
            task=task,
            version=version,
            input_fingerprint=input_fingerprint,
            model_id=model_id,
            tier=tier,
            decision=decision,
            confidence=float(confidence),
            seq=self._next_seq,
        )
        self._next_seq += 1
        self.records.append(obs)
        return obs

    def summarize(self, task: str) -> dict[str, dict[str, float]]:
        """Per-version stats for ``task``.

        Returns ``{version: {"count": int, "mean_confidence": float,
        "fire_rate": float}}`` where ``fire_rate`` is the share of decisions equal
        to :data:`FIRE`. Versions with no records for the task are absent.
        Deterministic: depends only on the recorded values.
        """
        counts: dict[str, int] = {}
        conf_sums: dict[str, float] = {}
        fire_counts: dict[str, int] = {}
        for obs in self.records:
            if obs.task != task:
                continue
            v = obs.version
            counts[v] = counts.get(v, 0) + 1
            conf_sums[v] = conf_sums.get(v, 0.0) + obs.confidence
            if obs.decision == FIRE:
                fire_counts[v] = fire_counts.get(v, 0) + 1
        summary: dict[str, dict[str, float]] = {}
        for v, n in counts.items():
            summary[v] = {
                "count": n,
                "mean_confidence": conf_sums[v] / n,
                "fire_rate": fire_counts.get(v, 0) / n,
            }
        return summary
