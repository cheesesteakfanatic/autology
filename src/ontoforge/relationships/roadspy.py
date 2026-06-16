"""RoadSpy — the scout payload for the adjudicator (v2.1 §1.2).

CLOSED-CORE IP (OntoForge_Build_Instructions.md §18).

RoadSpy scouts a candidate relationship and packages the *evidence* an LLM (or
human) adjudicator needs to rule on it — "these two columns might match, and
here's exactly how" — into a :class:`~ontoforge.contracts.ScoutPayload`.

INVARIANT — EVIDENCE, NEVER BULK DATA. The payload carries:
  * the column references and a one-line hypothesis,
  * the signals that FIRED and the signals that CONFLICTED (the reasoning trail),
  * a SMALL, capped, sterilized set of sample strings per side plus the shared
    overlap samples (so the adjudicator can eyeball real keys vs. a coincidence),
  * the candidate relationship TYPES in contention.
It does NOT carry full columns, row counts of raw data, or any bulk extract. The
sample cap is enforced here so a misconfigured caller cannot leak bulk values.

The adjudicator reasons over THIS, routing through the keyless ``aimodels`` /
``ensemble`` gate today; no model is invoked in this module. Deterministic: the
samples are sorted-and-capped, so a fixed candidate yields a byte-identical
payload.
"""

from __future__ import annotations

from ontoforge.contracts import (
    RelationshipCandidate,
    RelationshipType,
    ScoutPayload,
)

from .score import SignalSet
from .signals import SAMPLE_CAP, SampledColumn

__all__ = ["build_scout", "SCOUT_SAMPLE_CAP"]

# Scout samples are deliberately tiny — enough to eyeball, never enough to leak.
SCOUT_SAMPLE_CAP = 12


def _sterilize(values: tuple[str, ...]) -> tuple[str, ...]:
    """Sort distinct, cap, and trim long strings so no bulk/PII-ish blob rides along."""
    uniq = sorted({str(v) for v in values[:SAMPLE_CAP]})
    out = [v if len(v) <= 64 else v[:61] + "..." for v in uniq[:SCOUT_SAMPLE_CAP]]
    return tuple(out)


def build_scout(
    candidate: RelationshipCandidate,
    left: SampledColumn,
    right: SampledColumn,
    signals: SignalSet,
    *,
    candidate_types: tuple[RelationshipType, ...] = (),
) -> ScoutPayload:
    """Package a candidate's evidence as a :class:`ScoutPayload` for adjudication.

    ``left``/``right`` supply the (already small) sampled value sets — this
    function re-caps and sterilizes them, so even if a caller over-fills a
    :class:`SampledColumn` the payload stays bounded. ``candidate_types`` lists the
    types in contention (defaults to just the candidate's own type).
    """
    types = candidate_types or (candidate.rel_type,)
    fired = tuple(a for a in signals.artifacts if a.fired)
    conflicted = tuple(a for a in signals.artifacts if a.conflicts)

    left_samples = _sterilize(tuple(left.value_set()))
    right_samples = _sterilize(tuple(right.value_set()))
    shared = _sterilize(tuple(left.value_set() & right.value_set()))

    hypothesis = (
        f"{candidate.left.table}.{candidate.left.column} → "
        f"{candidate.right.table}.{candidate.right.column}: "
        f"{candidate.rel_type.value} (proxy={candidate.confidence:.2f}"
        + (", NEEDS ADJUDICATION" if candidate.needs_adjudication else "")
        + ")"
    )

    return ScoutPayload(
        left=candidate.left,
        right=candidate.right,
        hypothesis=hypothesis,
        signals_fired=fired,
        signals_conflicted=conflicted,
        left_samples=left_samples,
        right_samples=right_samples,
        shared_samples=shared,
        candidate_types=types,
    )
