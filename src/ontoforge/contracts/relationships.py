"""Typed relationship-inference contracts (v2.1 build instructions §1.1–§1.5, M-REL).

The v2.1 mandate replaces "binary join / no-join" with a TYPED relationship
taxonomy fed by a distribution-aware confidence PROXY. These are the shared,
versioned interfaces the relationship engine builds against:

  * §1.1 — a confidence-proxy scoring engine emits ``EvidenceArtifact``s (which
    signals fired, which conflicted) — the thing that kills "looks-similar-
    isn't-related."
  * §1.2 — ``RelationshipType`` is the typed taxonomy (FK-join · lookup/dimension ·
    many-to-many bridge · denormalization · derived field · unrelated-despite-
    similarity); ``ScoutPayload`` is the "RoadSpy" evidence packaged for an LLM
    adjudicator — EVIDENCE, NEVER raw bulk data.
  * §1.3 — distinct reasoning PATHS (schema / value / business-logic) cast
    ``PathVote``s on the relationship TYPE; ``RelationshipVerdict`` carries the
    plurality type with median-of-path confidence and consensus/route flags.
  * §1.4 — ``JoinValidation`` is the backward-validation result (synthesize the
    join, execute it, measure match/orphan/fan-out/null-key against real data).
  * §1.5 — ``TenantPrior`` is the per-tenant (NEVER cross-tenant) learned prior.

CLOSED-CORE IP — these engine contracts are proprietary per
OntoForge_Build_Instructions.md §18; they are NOT part of the open contract
surface beyond what downstream modules need to integrate.

The engine ships KEYLESS: every adjudication step routes through the existing
aimodels router / ensemble gate but runs on deterministic adapters today. These
types are pure data — no heavy deps, no network, no model invocation here.

Module agents: import from here; NEVER edit this package. Interface changes are
spec amendments (docs/DEVIATIONS.md) made by the architect.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


# ------------------------------------------------------------------- taxonomy


class RelationshipType(str, Enum):
    """The typed relationship taxonomy (§1.2) — not a binary join verdict."""

    FK_JOIN = "fk_join"                      # foreign-key join (child → parent key)
    LOOKUP_DIMENSION = "lookup_dimension"    # fact → dimension / reference lookup
    M2M_BRIDGE = "m2m_bridge"                # many-to-many via a bridge/junction table
    DENORMALIZATION = "denormalization"      # denormalized copy of an upstream attribute
    DERIVED_FIELD = "derived_field"          # computed/derived from the other column
    UNRELATED = "unrelated"                  # unrelated-despite-similarity verdict
    UNKNOWN = "unknown"                      # insufficient evidence to type


class SignalKind(str, Enum):
    """The evidence signals the confidence-proxy engine fuses (§1.1)."""

    VALUE_CONTAINMENT = "value_containment"            # values(lhs) ⊆ values(rhs)
    VALUE_JACCARD = "value_jaccard"                    # MinHash Jaccard of value sets
    DISTRIBUTION_DIVERGENCE = "distribution_divergence"  # JSD/KL / quantile divergence
    CARDINALITY_RATIO = "cardinality_ratio"            # distinct(lhs) : distinct(rhs)
    KEY_UNIQUENESS = "key_uniqueness"                  # rhs side near-unique (key-like)
    ENTROPY = "entropy"                                # value-distribution entropy
    NAME_SIMILARITY = "name_similarity"                # column/table name affinity
    TYPE_COMPAT = "type_compat"                        # datatype/semantic-type compatibility
    SAMPLED_ROW = "sampled_row"                        # sampled-row corroboration
    FANOUT = "fanout"                                  # observed join fan-out signal


# ------------------------------------------------------------------- references


@dataclass(frozen=True, slots=True)
class ColumnRef:
    """A fully-qualified column address across sources/tables."""

    source_id: str
    table: str
    column: str


# ------------------------------------------------------------------- evidence


@dataclass(frozen=True, slots=True)
class EvidenceArtifact:
    """One reasoning artifact: a signal's value, its weight, and whether it fired/conflicted.

    The fused set of these IS the distribution-aware confidence proxy's reasoning
    trail (§1.1) — it records which signals fired and which conflicted, so a
    high-similarity-but-unrelated pair can be explained, not just scored.
    """

    kind: SignalKind
    value: float           # raw signal value (signal-specific scale)
    weight: float          # contribution weight in the fused proxy
    fired: bool            # signal cleared its activation threshold
    conflicts: bool        # signal contradicts the leading hypothesis
    detail: str = ""       # human-readable note (e.g. "JSD=0.71 distributions diverge")


# ------------------------------------------------------------------- candidate


@dataclass(frozen=True, slots=True)
class RelationshipCandidate:
    """A typed relationship hypothesis with its heuristic confidence PROXY.

    ``confidence`` is the heuristic proxy (NOT a calibrated spine probability);
    ``needs_adjudication`` flags the ambiguous-band escalation — the candidate
    falls in the band where evidence is mixed and an adjudicator (or human) must
    decide. The evidence tuple carries the full reasoning trail.
    """

    left: ColumnRef
    right: ColumnRef
    rel_type: RelationshipType
    confidence: float
    evidence: tuple[EvidenceArtifact, ...] = ()
    rationale: str = ""
    needs_adjudication: bool = False


# ------------------------------------------------------------------- scout payload


@dataclass(frozen=True, slots=True)
class ScoutPayload:
    """The "RoadSpy" evidence package handed to an LLM adjudicator (§1.2).

    Carries EVIDENCE, NEVER raw bulk data: the hypothesis, the signals that fired
    and conflicted, and only small sterilized SAMPLE strings (left/right/shared)
    plus the candidate types in contention. The adjudicator reasons over this,
    not over the underlying tables.
    """

    left: ColumnRef
    right: ColumnRef
    hypothesis: str
    signals_fired: tuple[EvidenceArtifact, ...] = ()
    signals_conflicted: tuple[EvidenceArtifact, ...] = ()
    left_samples: tuple[str, ...] = ()
    right_samples: tuple[str, ...] = ()
    shared_samples: tuple[str, ...] = ()
    candidate_types: tuple[RelationshipType, ...] = ()


# ------------------------------------------------------------------- backward validation


@dataclass(frozen=True, slots=True)
class JoinValidation:
    """Backward-validation result (§1.4): synthesize the join, execute it, measure real data.

    The strongest correctness guarantee — the join is actually run and scored on
    match rate, orphan/dangling rate, fan-out, and null-key rate before any type
    is committed.
    """

    match_rate: float          # fraction of left rows that matched
    orphan_rate: float         # fraction of left rows with no match (dangling)
    fanout_avg: float          # average matches per matched left row
    fanout_max: float          # worst-case fan-out
    null_key_rate: float       # fraction of join-key values that are null
    rows_left: int
    rows_right: int
    verdict: RelationshipType
    ok: bool = False           # validation supports the verdict
    detail: str = ""


# ------------------------------------------------------------------- reasoning-path voting


class ReasoningPath(str, Enum):
    """Distinct reasoning PATHS that vote on the relationship type (§1.3)."""

    SCHEMA = "schema"                  # schema-centric (keys, names, types, FDs/INDs)
    VALUE = "value"                    # value-centric (overlap, distribution, sampled rows)
    BUSINESS_LOGIC = "business_logic"  # business-logic-centric (semantics, conventions)


@dataclass(frozen=True, slots=True)
class PathVote:
    """One reasoning path's typed vote and its own confidence."""

    path: ReasoningPath
    rel_type: RelationshipType
    confidence: float
    rationale: str = ""


@dataclass(frozen=True, slots=True)
class RelationshipVerdict:
    """The committed (or routed) relationship outcome (§1.3).

    ``rel_type`` is the plurality type across paths; ``confidence`` is the
    median-of-path confidence; ``consensus`` marks paths agreeing above the
    commit threshold. Commit only above consensus, else route to Build/human
    (``routed_to_human``). ``validation`` attaches the backward-validation result;
    ``prov_ref`` links the ledger provenance record.
    """

    left: ColumnRef
    right: ColumnRef
    rel_type: RelationshipType
    confidence: float
    consensus: bool
    votes: tuple[PathVote, ...] = ()
    validation: Optional[JoinValidation] = None
    committed: bool = False
    routed_to_human: bool = False
    prov_ref: str = ""


# ------------------------------------------------------------------- tenant priors


@dataclass(frozen=True, slots=True)
class TenantPrior:
    """A per-tenant learned prior (§1.5) — ISOLATED, NEVER cross-tenant.

    Captures naming conventions, accepted/rejected join history, semantic-type
    maps, etc. ``weight`` is the learned strength; ``observations`` is how many
    review verdicts back it.
    """

    tenant_id: str
    kind: str               # 'name_convention' | 'join_history' | 'semtype_map' | ...
    key: str
    value: str
    weight: float = 0.0
    observations: int = 0
