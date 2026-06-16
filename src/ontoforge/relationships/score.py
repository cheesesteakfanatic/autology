"""Confidence-PROXY fusion — calibrated weighting over the evidence (v2.1 §1.1).

CLOSED-CORE IP (OntoForge_Build_Instructions.md §18).

This module turns the per-pair :class:`~ontoforge.contracts.EvidenceArtifact`
set into a single heuristic confidence PROXY in [0, 1] and packages it as a
:class:`~ontoforge.contracts.RelationshipCandidate`. The proxy is NOT a calibrated
spine probability — it is the heuristics-first scalar the doc (§1.1) calls for,
the thing that decides "commit / route to adjudication / discard."

Fusion principles (the false-positive killer, made numeric):

* Value OVERLAP (containment + Jaccard + sampled-row) is the positive backbone.
* Value DISTRIBUTION DISAGREEMENT is STRONGLY NEGATIVE: when
  ``DISTRIBUTION_DIVERGENCE`` conflicts, it subtracts hard, so a pair that shares
  values-by-name but diverges in shape/frequency cannot score as related.
* NAME_SIMILARITY is WEAK: it can nudge but never carry a verdict.
* KEY_UNIQUENESS gates the FK family; low ENTROPY and TYPE incompatibility
  subtract.

The score is a clamped convex-ish combination of signed signal contributions; the
weights live on the artifacts (set in :mod:`signals`) so the reasoning trail and
the math stay in lockstep. Deterministic: pure arithmetic over rounded inputs.
"""

from __future__ import annotations

from dataclasses import dataclass

from ontoforge.contracts import (
    ColumnRef,
    EvidenceArtifact,
    RelationshipCandidate,
    RelationshipType,
)

from . import signals as sig
from .signals import SampledColumn

__all__ = [
    "AMBIGUOUS_BAND",
    "FK_PROXY_FLOOR",
    "SignalSet",
    "compute_signals",
    "fuse_confidence",
    "score_pair",
]

# The ambiguous band: a candidate whose proxy lands here is escalated for
# adjudication (needs_adjudication) rather than committed or discarded outright.
AMBIGUOUS_BAND: tuple[float, float] = (0.45, 0.65)
# An FK_JOIN verdict needs at least this proxy to be asserted without escalation.
FK_PROXY_FLOOR = 0.7

# Distribution divergence above this with otherwise-tempting overlap is the
# explicit "looks similar, isn't related" trip-wire.
_DIVERGENCE_VETO = 0.6


@dataclass(frozen=True, slots=True)
class SignalSet:
    """All evidence for one ordered pair, addressable by kind for the classifier.

    ``containment_lr`` is the FK direction (left⊆right); ``containment_rl`` the
    reverse. The flat ``artifacts`` tuple is the full reasoning trail (stable
    order) that rides on the candidate and the scout payload.
    """

    left: ColumnRef
    right: ColumnRef
    containment_lr: EvidenceArtifact
    containment_rl: EvidenceArtifact
    jaccard: EvidenceArtifact
    divergence: EvidenceArtifact
    cardinality: EvidenceArtifact
    key_uniqueness: EvidenceArtifact
    entropy: EvidenceArtifact
    name_similarity: EvidenceArtifact
    type_compat: EvidenceArtifact
    sampled_row: EvidenceArtifact

    @property
    def artifacts(self) -> tuple[EvidenceArtifact, ...]:
        return (
            self.containment_lr,
            self.containment_rl,
            self.jaccard,
            self.divergence,
            self.cardinality,
            self.key_uniqueness,
            self.entropy,
            self.name_similarity,
            self.type_compat,
            self.sampled_row,
        )

    @property
    def fired(self) -> tuple[EvidenceArtifact, ...]:
        return tuple(a for a in self.artifacts if a.fired)

    @property
    def conflicted(self) -> tuple[EvidenceArtifact, ...]:
        return tuple(a for a in self.artifacts if a.conflicts)


def _ref(col: SampledColumn) -> ColumnRef:
    p = col.profile
    return ColumnRef(source_id=p.source_id, table=p.table, column=p.column)


def compute_signals(left: SampledColumn, right: SampledColumn) -> SignalSet:
    """Run every signal over an ordered pair and bundle the evidence.

    ``left`` is the candidate child / referencing side; ``right`` the candidate
    parent / referenced side (the FK direction). Order matters for the
    directional containment and key-uniqueness signals.
    """
    c_lr, c_rl = sig.containment_signals(left, right)
    return SignalSet(
        left=_ref(left),
        right=_ref(right),
        containment_lr=c_lr,
        containment_rl=c_rl,
        jaccard=sig.jaccard_signal(left, right),
        divergence=sig.distribution_divergence_signal(left, right),
        cardinality=sig.cardinality_ratio_signal(left, right),
        key_uniqueness=sig.key_uniqueness_signal(left, right),
        entropy=sig.entropy_signal(left, right),
        name_similarity=sig.name_similarity_signal(left, right),
        type_compat=sig.type_compat_signal(left, right),
        sampled_row=sig.sampled_row_signal(left, right),
    )


def fuse_confidence(s: SignalSet) -> float:
    """Fuse the evidence into the heuristic confidence PROXY ∈ [0, 1].

    Positive backbone: best-direction containment, Jaccard, sampled-row overlap,
    key-uniqueness (only credited when overlap is real). Negative: distribution
    divergence (strong), type incompatibility, low entropy. Name similarity adds a
    weak nudge gated on at least *some* real overlap so a pure name match scores
    near zero.
    """
    best_contain = max(s.containment_lr.value, s.containment_rl.value)
    overlap = max(best_contain, s.jaccard.value, s.sampled_row.value)

    pos = (
        s.containment_lr.weight * s.containment_lr.value
        + 0.5 * s.containment_rl.weight * s.containment_rl.value
        + s.jaccard.weight * s.jaccard.value
        + s.sampled_row.weight * s.sampled_row.value
    )
    # key-uniqueness only helps if there is real overlap to key against
    if overlap >= 0.3:
        pos += s.key_uniqueness.weight * s.key_uniqueness.value
    # name similarity: weak, and only when at least some overlap exists
    if overlap >= 0.2:
        pos += s.name_similarity.weight * s.name_similarity.value

    # Negatives — the discriminators.
    neg = 0.0
    # Distribution divergence subtracts in proportion to how much it diverges,
    # amplified when it conflicts; this is what kills look-alike-but-unrelated.
    div = s.divergence.value
    neg += s.divergence.weight * div * (2.0 if s.divergence.conflicts else 1.0)
    if s.type_compat.conflicts:
        neg += 0.5  # incompatible types cannot be a real relationship
    if s.entropy.conflicts:
        neg += s.entropy.weight  # low-entropy key penalty

    raw = pos - neg
    # Hard veto: strong divergence with only name/overlap coincidence floors the proxy.
    if s.divergence.value >= _DIVERGENCE_VETO and best_contain < 0.7:
        raw = min(raw, 0.2)
    return round(min(1.0, max(0.0, raw)), 6)


def score_pair(
    left: SampledColumn,
    right: SampledColumn,
    *,
    rel_type: RelationshipType,
    rationale: str = "",
    signals: SignalSet | None = None,
) -> RelationshipCandidate:
    """Emit a :class:`RelationshipCandidate` for an ordered pair and a typed verdict.

    ``needs_adjudication`` is set when the proxy lands in :data:`AMBIGUOUS_BAND` OR
    the evidence conflicts internally (a fired positive contradicted by a fired
    conflict) — the mixed-evidence escalation the classifier and discover honor.
    """
    s = signals if signals is not None else compute_signals(left, right)
    conf = fuse_confidence(s)
    lo, hi = AMBIGUOUS_BAND
    in_band = lo <= conf < hi
    # internal conflict: we have real overlap AND a fired conflict signal at once
    has_overlap = max(s.containment_lr.value, s.jaccard.value, s.sampled_row.value) >= 0.4
    has_conflict = len(s.conflicted) > 0
    mixed = has_overlap and has_conflict and rel_type not in (
        RelationshipType.UNRELATED,
        RelationshipType.UNKNOWN,
    )
    needs_adj = in_band or mixed
    return RelationshipCandidate(
        left=s.left,
        right=s.right,
        rel_type=rel_type,
        confidence=conf,
        evidence=s.artifacts,
        rationale=rationale,
        needs_adjudication=needs_adj,
    )
