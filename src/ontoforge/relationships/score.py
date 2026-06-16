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
from .weighting import BALANCED, WeightingProfile

__all__ = [
    "AMBIGUOUS_BAND",
    "FK_PROXY_FLOOR",
    "IND_PRUNE_FLOOR",
    "SignalSet",
    "compute_signals",
    "fuse_confidence",
    "ind_candidate_score",
    "score_pair",
]

# The ambiguous band: a candidate whose proxy lands here is escalated for
# adjudication (needs_adjudication) rather than committed or discarded outright.
AMBIGUOUS_BAND: tuple[float, float] = (0.45, 0.65)
# An FK_JOIN verdict needs at least this proxy to be asserted without escalation.
FK_PROXY_FLOOR = 0.7

# Tursio IND candidate prune (RESEARCH_ENGINE_SOTA §4 — adopted default, tunable):
# an inclusion-dependency / join candidate is scored by a 5-component score and
# PRUNED below this. The five components are the vetted IND-relevant metrics:
# containment (the directional IND signal), key-uniqueness (a join target must be
# key-like), cardinality match, type compatibility, and (weak) name similarity.
IND_PRUNE_FLOOR = 0.4

# Distribution divergence above this with otherwise-tempting overlap is the
# explicit "looks similar, isn't related" trip-wire.
_DIVERGENCE_VETO = 0.6

# Infrequent-token Jaccard at/above this, WITH low verbatim overlap, means the
# rare discriminating tokens coincide while the literal values differ — a
# FORMAT-VARIANT join ("St"/"Street") the verbatim signals missed. The "low
# verbatim overlap" gate is essential: when values are ALREADY identical
# (high containment) a high rare-token score is just the identity, and any measured
# divergence is a GENUINE frequency disagreement (the look-alike-but-unrelated
# case) that must keep its full negative weight.
_RARE_RECOVERY_AT = 0.5
_RARE_RECOVERY_MAX_CONTAIN = 0.5


def rare_token_recovery(s: "SignalSet") -> bool:
    """True when the infrequent-token signal is RECOVERING a format-variant join.

    Requires a strong rare-token Jaccard AND low verbatim containment (the rare
    tokens are doing work the literal values could not). A genuine look-alike has
    no rare-token agreement; identical-vocabulary columns have high containment —
    neither qualifies, so this can never resurrect a false positive.
    """
    best_contain = max(s.containment_lr.value, s.containment_rl.value)
    return s.infrequent_token.value >= _RARE_RECOVERY_AT and best_contain <= _RARE_RECOVERY_MAX_CONTAIN


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
    infrequent_token: EvidenceArtifact
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
            self.infrequent_token,
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
        infrequent_token=sig.infrequent_token_signal(left, right),
        divergence=sig.distribution_divergence_signal(left, right),
        cardinality=sig.cardinality_ratio_signal(left, right),
        key_uniqueness=sig.key_uniqueness_signal(left, right),
        entropy=sig.entropy_signal(left, right),
        name_similarity=sig.name_similarity_signal(left, right),
        type_compat=sig.type_compat_signal(left, right),
        sampled_row=sig.sampled_row_signal(left, right),
    )


def fuse_confidence(s: SignalSet, *, profile: WeightingProfile = BALANCED) -> float:
    """Fuse the evidence into the heuristic confidence PROXY ∈ [0, 1].

    Positive backbone: best-direction containment, Jaccard, sampled-row overlap,
    rare-token (infrequent-token) overlap, key-uniqueness (only credited when
    overlap is real). Negative: distribution divergence (strong), type
    incompatibility, low entropy. Name similarity adds a weak nudge gated on at
    least *some* real overlap so a pure name match scores near zero.

    ``profile`` is the PER-ESTATE :class:`~.weighting.WeightingProfile`: it scales
    each signal's weight by its signal GROUP (structural / overlap / semantic) so a
    clean relational estate leans on keys+overlap while a messy lake leans on
    semantic metadata + rare-token overlap. The default :data:`~.weighting.BALANCED`
    profile leaves every weight unchanged. The false-positive killers below
    (divergence, type/entropy conflicts, the hard veto) are NEVER scaled — a
    re-weighting must not be able to silence the guard.
    """
    w = profile.for_field  # field-name → group multiplier

    best_contain = max(s.containment_lr.value, s.containment_rl.value)
    # rare-token overlap counts as "real overlap" too — that is the whole point of
    # the signal (a format-variant join whose verbatim Jaccard collapsed to ~0).
    overlap = max(
        best_contain, s.jaccard.value, s.sampled_row.value, s.infrequent_token.value
    )

    pos = (
        w("containment_lr") * s.containment_lr.weight * s.containment_lr.value
        + 0.5 * w("containment_rl") * s.containment_rl.weight * s.containment_rl.value
        + w("jaccard") * s.jaccard.weight * s.jaccard.value
        + w("sampled_row") * s.sampled_row.weight * s.sampled_row.value
        + w("infrequent_token") * s.infrequent_token.weight * s.infrequent_token.value
    )
    # key-uniqueness only helps if there is real overlap to key against
    if overlap >= 0.3:
        pos += w("key_uniqueness") * s.key_uniqueness.weight * s.key_uniqueness.value
    # name similarity: weak, and only when at least some overlap exists
    if overlap >= 0.2:
        pos += w("name_similarity") * s.name_similarity.weight * s.name_similarity.value

    # Negatives — the discriminators. NOT scaled by the estate profile: re-weighting
    # may shift which POSITIVE evidence carries the day, never weaken the guard.
    neg = 0.0
    # Distribution divergence subtracts in proportion to how much it diverges,
    # amplified when it conflicts; this is what kills look-alike-but-unrelated.
    # EXCEPTION — when the infrequent-token signal strongly fires, the verbatim
    # value sets differ only by FORMAT ("St"/"Street") so their measured divergence
    # is an artifact of the format split, not a genuine frequency/shape
    # disagreement; the rare-token agreement explains it, so its negative weight is
    # damped (never below the conflict-amplified base for a genuine look-alike,
    # which has NO rare-token agreement and is unaffected).
    div = s.divergence.value
    div_factor = (2.0 if s.divergence.conflicts else 1.0)
    if rare_token_recovery(s):
        div_factor *= 0.25  # format-artifact divergence, not a real distribution clash
    neg += s.divergence.weight * div * div_factor
    if s.type_compat.conflicts:
        neg += 0.5  # incompatible types cannot be a real relationship
    if s.entropy.conflicts:
        neg += s.entropy.weight  # low-entropy key penalty

    raw = pos - neg
    # Hard veto: strong divergence with only name/overlap coincidence floors the
    # proxy. EXCEPTION — when the infrequent-token signal strongly fires, the
    # apparent divergence is a FORMAT artifact (the verbatim values differ, e.g.
    # "St" vs "Street", but the rare discriminating tokens COINCIDE), so the veto
    # would wrongly kill a genuine format-variant join. A genuine look-alike has
    # NO rare-token agreement, so this exception cannot resurrect a false positive.
    if s.divergence.value >= _DIVERGENCE_VETO and best_contain < 0.7 and not rare_token_recovery(s):
        raw = min(raw, 0.2)
    return round(min(1.0, max(0.0, raw)), 6)


#: the five IND-candidate score components and their convex weights (sum to 1.0).
#: Containment is the BACKBONE: the four corroborators only count in proportion to
#: how much real containment there is to corroborate (see ``ind_candidate_score``),
#: so a near-key target with matching cardinality/types but ZERO value overlap
#: cannot masquerade as an inclusion dependency.
_IND_COMPONENT_WEIGHTS: dict[str, float] = {
    "containment": 0.40,   # the directional IND signal — the backbone
    "key_uniqueness": 0.25,  # a join target must be key-like
    "cardinality": 0.15,   # granularity match
    "type_compat": 0.15,   # types must be joinable
    "name_similarity": 0.05,  # weak corroboration only
}
# best-direction containment must reach this for the corroborators to count fully;
# below it they are scaled down linearly so a zero-overlap pair scores ~0.
_IND_CONTAIN_BACKBONE = 0.3


def ind_candidate_score(s: SignalSet) -> float:
    """Tursio 5-component IND/join-candidate score ∈ [0, 1] (RESEARCH_ENGINE_SOTA §4).

    A combination of the five vetted IND-relevant metrics — best-direction
    containment, key-uniqueness, cardinality match, type compatibility, and (weak)
    name similarity. CONTAINMENT is the backbone: an inclusion dependency IS value
    containment, so the four corroborators are scaled by a containment-presence
    factor — a near-key target with matching cardinality/types but no value overlap
    is NOT an IND and scores near zero. Used to PRUNE candidates below
    :data:`IND_PRUNE_FLOOR` before they cost any further adjudication.

    This is a CANDIDATE-GENERATION score, NOT the fused confidence proxy: it
    deliberately ignores the distribution-divergence discriminator (that stays the
    false-positive killer in :func:`fuse_confidence` and the classifier), so
    pruning never silences the guard.
    """
    best_contain = max(s.containment_lr.value, s.containment_rl.value)
    # containment-presence factor in [0,1]: full credit once containment clears the
    # backbone, linearly damped to 0 as containment → 0.
    presence = min(1.0, best_contain / _IND_CONTAIN_BACKBONE)
    corroborators = (
        _IND_COMPONENT_WEIGHTS["key_uniqueness"] * s.key_uniqueness.value
        + _IND_COMPONENT_WEIGHTS["cardinality"] * s.cardinality.value
        + _IND_COMPONENT_WEIGHTS["type_compat"] * s.type_compat.value
        + _IND_COMPONENT_WEIGHTS["name_similarity"] * s.name_similarity.value
    )
    score = _IND_COMPONENT_WEIGHTS["containment"] * best_contain + presence * corroborators
    return round(min(1.0, max(0.0, score)), 6)


def score_pair(
    left: SampledColumn,
    right: SampledColumn,
    *,
    rel_type: RelationshipType,
    rationale: str = "",
    signals: SignalSet | None = None,
    profile: WeightingProfile = BALANCED,
) -> RelationshipCandidate:
    """Emit a :class:`RelationshipCandidate` for an ordered pair and a typed verdict.

    ``profile`` is the per-estate weighting profile threaded into the fusion (the
    default leaves weights unchanged). ``needs_adjudication`` is set when the proxy
    lands in :data:`AMBIGUOUS_BAND` OR the evidence conflicts internally (a fired
    positive contradicted by a fired conflict) — the mixed-evidence escalation the
    classifier and discover honor.
    """
    s = signals if signals is not None else compute_signals(left, right)
    conf = fuse_confidence(s, profile=profile)
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
