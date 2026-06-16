"""Reasoning-path experts — typed-relationship voting via DISTINCT reasoning paths.

v2.1 build instructions §1.3 ("reasoning-path voting, not temperature noise").

The whitepaper insight the existing fire/hold :class:`~ontoforge.ensemble.gate.Gate`
does NOT capture: an ensemble that merely re-samples one model at different
*temperatures* produces correlated errors — it is loud, not diverse. The
research-correct axis of diversity is the *reasoning itself*. So we run three
experts that reach a typed verdict by genuinely different inference:

  * :class:`SchemaPath` — reasons from STRUCTURE. Key uniqueness, cardinality
    ratio, column/table naming, key-position cues. "Is the right side a key, and
    does the left point into it?" Knows nothing about the values themselves.
  * :class:`ValuePath` — reasons from DATA. Value containment, MinHash overlap,
    distribution divergence (JSD/quantile), entropy, sampled-row corroboration.
    "Do the values actually line up, and do the distributions match?" Ignores
    names entirely.
  * :class:`BusinessLogicPath` — reasons from MEANING. Semantic types, units,
    naming *domain* (not lexical overlap — domain role), table roles (fact vs.
    dimension vs. bridge). "Does this make sense as a domain relationship, and is
    it a lookup, a bridge, or a derived copy?"

Each path consumes the SAME :class:`~ontoforge.contracts.RelationshipCandidate`
(+ its :class:`~ontoforge.contracts.EvidenceArtifact` trail + an optional
:class:`~ontoforge.contracts.JoinValidation`) but weighs an orthogonal slice of
it, so a plurality vote over the three is a meaningful typed decision — not three
echoes of one heuristic.

Each path is ALSO a seam: when keys arrive, a path's :meth:`vote` body is the
exact place a live LLM call (via ``aimodels`` router, a DIFFERENT prompt per path
— schema-prompt / value-prompt / business-prompt) plugs in. The signature and the
:class:`~ontoforge.contracts.PathVote` it returns are unchanged, so the
:class:`~ontoforge.ensemble.relgate.RelationshipGate` math never moves. KEYLESS
and DETERMINISTIC today: identical candidate + validation ⇒ identical vote.

CLOSED-CORE IP — proprietary per OntoForge_Build_Instructions.md §18. Not part of
the open-shell surface.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol, runtime_checkable

from ..contracts import (
    EvidenceArtifact,
    JoinValidation,
    PathVote,
    ReasoningPath,
    RelationshipCandidate,
    RelationshipType,
    SignalKind,
)

__all__ = [
    "BusinessLogicPath",
    "PathExpert",
    "ReasoningPathExpert",
    "SchemaPath",
    "ValuePath",
    "default_paths",
]


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, x))


def _signal_map(cand: RelationshipCandidate) -> dict[SignalKind, EvidenceArtifact]:
    """Index the candidate's evidence trail by signal kind (last one wins if dup)."""
    return {ev.kind: ev for ev in cand.evidence}


def _val(sig: dict[SignalKind, EvidenceArtifact], kind: SignalKind, default: float = 0.0) -> float:
    ev = sig.get(kind)
    return ev.value if ev is not None else default


def _has(sig: dict[SignalKind, EvidenceArtifact], kind: SignalKind) -> bool:
    return kind in sig


# --------------------------------------------------------------------------
# protocol
# --------------------------------------------------------------------------


@runtime_checkable
class PathExpert(Protocol):
    """A reasoning-path expert: typed vote from a candidate + optional validation."""

    path: ReasoningPath

    def vote(
        self,
        cand: RelationshipCandidate,
        validation: Optional[JoinValidation] = None,
    ) -> PathVote: ...


# alias for readers who prefer the longer name in the contracts vocabulary
ReasoningPathExpert = PathExpert


# --------------------------------------------------------------------------
# SchemaPath — reasons from STRUCTURE (keys, cardinality, names, position)
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SchemaPath:
    """Schema-centric reasoning. The shape of the keys decides the type.

    Decision logic (structure only — never looks at value distributions):

    * The right side must be KEY-LIKE (high ``KEY_UNIQUENESS``) for a parent-side
      relationship to exist at all. If it is not, the structure says this is at
      best a bridge column or unrelated.
    * ``CARDINALITY_RATIO`` distinct(lhs):distinct(rhs) discriminates the type:
        - ratio ≫ 1 with a unique right side ⇒ many-children-to-one-parent ⇒
          ``FK_JOIN`` (or ``LOOKUP_DIMENSION`` when the right is small/reference-like).
        - ratio ≈ 1 with both sides unique ⇒ one-to-one ⇒ ``DENORMALIZATION``
          (a copied attribute) is the structural reading.
        - neither side unique ⇒ many-to-many shape ⇒ ``M2M_BRIDGE``.
    * Naming is a tie-breaker only (an ``_id`` suffix nudges FK over denorm),
      never the primary signal — structure dominates name here.
    """

    path: ReasoningPath = ReasoningPath.SCHEMA
    key_unique_floor: float = 0.9   # rhs uniqueness to count as a key
    small_dim_cardinality: float = 64.0  # rhs distinct count below this ⇒ dimension-like

    def vote(
        self,
        cand: RelationshipCandidate,
        validation: Optional[JoinValidation] = None,
    ) -> PathVote:
        sig = _signal_map(cand)
        rhs_unique = _val(sig, SignalKind.KEY_UNIQUENESS, default=0.0)
        ratio = _val(sig, SignalKind.CARDINALITY_RATIO, default=1.0)
        lhs_is_id = cand.left.column.lower().endswith(("_id", "id", "_key", "key", "_fk", "code"))

        rhs_is_key = rhs_unique >= self.key_unique_floor
        # rhs distinct count proxy: the right side of a unique key with ratio R has
        # ~ rows_left / R distinct values; when we lack counts, the validation gives one.
        rhs_distinct = None
        if validation is not None and validation.rows_right > 0:
            rhs_distinct = float(validation.rows_right)

        if not rhs_is_key:
            # right side is not a key. Structurally this is a bridge shape (both
            # non-unique) or simply not a parent relationship.
            lhs_unique = _val(sig, SignalKind.KEY_UNIQUENESS, default=0.0)  # symmetric fallback
            if ratio <= 1.5 and lhs_unique < self.key_unique_floor:
                return PathVote(
                    self.path, RelationshipType.M2M_BRIDGE,
                    _clamp(0.45 + 0.2 * (1.0 - rhs_unique)),
                    f"rhs not key-like (uniq={rhs_unique:.2f}); both sides non-unique ⇒ bridge shape",
                )
            return PathVote(
                self.path, RelationshipType.UNRELATED,
                _clamp(0.4 + 0.3 * (1.0 - rhs_unique)),
                f"rhs not key-like (uniq={rhs_unique:.2f}); no parent-key structure",
            )

        # rhs IS a key. Discriminate by cardinality ratio.
        if ratio >= 2.0:
            # many left rows per right key. Small reference key ⇒ dimension/lookup.
            if rhs_distinct is not None and rhs_distinct <= self.small_dim_cardinality:
                return PathVote(
                    self.path, RelationshipType.LOOKUP_DIMENSION,
                    _clamp(0.55 + 0.3 * min(1.0, rhs_unique)),
                    f"unique small rhs key (≈{rhs_distinct:.0f} rows), ratio {ratio:.1f}:1 ⇒ dimension",
                )
            conf = _clamp(0.6 + 0.35 * min(1.0, rhs_unique) + (0.05 if lhs_is_id else 0.0))
            return PathVote(
                self.path, RelationshipType.FK_JOIN, conf,
                f"unique rhs key, fan-in {ratio:.1f}:1, lhs id-shaped={lhs_is_id} ⇒ fk_join",
            )

        # ratio ≈ 1 and rhs unique ⇒ one-to-one. Structurally a denormalized copy,
        # unless the lhs is clearly an id (then a 1:1 fk is plausible).
        if lhs_is_id:
            return PathVote(
                self.path, RelationshipType.FK_JOIN,
                _clamp(0.5 + 0.25 * min(1.0, rhs_unique)),
                "unique rhs key, 1:1 ratio, id-shaped lhs ⇒ fk_join (1:1)",
            )
        return PathVote(
            self.path, RelationshipType.DENORMALIZATION,
            _clamp(0.5 + 0.25 * min(1.0, rhs_unique)),
            "unique rhs key, 1:1 ratio, non-id lhs ⇒ denormalized copy",
        )


# --------------------------------------------------------------------------
# ValuePath — reasons from DATA (containment, divergence, overlap, samples)
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ValuePath:
    """Value-centric reasoning. The data itself decides the type — names ignored.

    Decision logic (distribution-aware; this is the path that KILLS
    looks-similar-isn't-related):

    * ``DISTRIBUTION_DIVERGENCE`` (JSD/quantile) is the veto signal: two columns
      whose value sets overlap but whose DISTRIBUTIONS diverge sharply are
      ``UNRELATED`` despite similarity — high overlap with high divergence is the
      classic false-positive (e.g. two unrelated integer id spaces that happen to
      collide). This path catches it where SchemaPath cannot.
    * ``VALUE_CONTAINMENT`` values(lhs) ⊆ values(rhs) is the join evidence: near-
      total containment with low divergence ⇒ the left points into the right ⇒
      ``FK_JOIN`` / ``LOOKUP_DIMENSION``.
    * ``VALUE_JACCARD`` symmetric overlap with neither containment direction
      dominating ⇒ shared-vocabulary ⇒ ``M2M_BRIDGE`` reading.
    * ``ENTROPY`` low on a contained right side ⇒ few repeated codes ⇒ dimension.
    * Sampled rows corroborate (a row-level seam for the LLM later).
    """

    path: ReasoningPath = ReasoningPath.VALUE
    contain_floor: float = 0.85
    divergence_veto: float = 0.6   # JSD-ish above this with overlap ⇒ unrelated
    low_entropy: float = 0.35

    def vote(
        self,
        cand: RelationshipCandidate,
        validation: Optional[JoinValidation] = None,
    ) -> PathVote:
        sig = _signal_map(cand)
        contain = _val(sig, SignalKind.VALUE_CONTAINMENT, default=0.0)
        jaccard = _val(sig, SignalKind.VALUE_JACCARD, default=0.0)
        diverge = _val(sig, SignalKind.DISTRIBUTION_DIVERGENCE, default=0.0)
        entropy = _val(sig, SignalKind.ENTROPY, default=1.0)
        overlap = max(contain, jaccard)

        # 1) the distribution-aware false-positive guard: values overlap but the
        #    distributions diverge ⇒ unrelated-despite-similarity.
        if overlap >= 0.5 and diverge >= self.divergence_veto:
            return PathVote(
                self.path, RelationshipType.UNRELATED,
                _clamp(0.5 + 0.4 * diverge),
                f"overlap {overlap:.2f} but distributions diverge (JSD {diverge:.2f}) "
                f"⇒ unrelated despite similarity",
            )

        # 2) strong containment with aligned distributions ⇒ the left points in.
        if contain >= self.contain_floor and diverge < self.divergence_veto:
            # low entropy on the right ⇒ few repeated reference codes ⇒ dimension.
            if entropy <= self.low_entropy:
                return PathVote(
                    self.path, RelationshipType.LOOKUP_DIMENSION,
                    _clamp(0.55 + 0.4 * contain - 0.2 * diverge),
                    f"containment {contain:.2f}, low entropy {entropy:.2f} ⇒ reference lookup",
                )
            return PathVote(
                self.path, RelationshipType.FK_JOIN,
                _clamp(0.55 + 0.4 * contain - 0.25 * diverge),
                f"containment {contain:.2f}, aligned distributions (JSD {diverge:.2f}) ⇒ fk_join",
            )

        # 3) symmetric overlap, neither contained ⇒ shared vocabulary ⇒ bridge.
        if jaccard >= 0.4 and contain < self.contain_floor:
            return PathVote(
                self.path, RelationshipType.M2M_BRIDGE,
                _clamp(0.4 + 0.4 * jaccard),
                f"symmetric overlap (jaccard {jaccard:.2f}), no containment ⇒ bridge",
            )

        # 4) very little overlap ⇒ unrelated.
        if overlap < 0.2:
            return PathVote(
                self.path, RelationshipType.UNRELATED,
                _clamp(0.5 + 0.4 * (1.0 - overlap)),
                f"value overlap {overlap:.2f} too low to relate",
            )

        # 5) partial overlap, no clear shape ⇒ unknown (let other paths decide).
        return PathVote(
            self.path, RelationshipType.UNKNOWN,
            _clamp(0.3 + 0.2 * overlap),
            f"partial overlap {overlap:.2f}, ambiguous value evidence",
        )


# --------------------------------------------------------------------------
# BusinessLogicPath — reasons from MEANING (semtypes, units, domain, roles)
# --------------------------------------------------------------------------


# domain-role lexicons: NOT lexical overlap with the other column — these are
# semantic role cues read off a single column/table name.
_DERIVED_CUES = ("total", "amount", "sum", "avg", "count", "rate", "ratio",
                 "pct", "percent", "score", "net", "gross", "balance")
_DIMENSION_TABLES = ("dim", "dimension", "ref", "reference", "lookup", "type",
                     "category", "status", "country", "region", "currency", "calendar")
_BRIDGE_TABLES = ("bridge", "junction", "link", "map", "mapping", "xref",
                  "membership", "assignment", "_x_", "rel")
_FACT_TABLES = ("fact", "txn", "transaction", "event", "order", "sale", "ledger",
                "log", "line", "item")


def _name_hits(name: str, cues: tuple[str, ...]) -> bool:
    n = name.lower()
    return any(c in n for c in cues)


@dataclass(frozen=True, slots=True)
class BusinessLogicPath:
    """Business-logic-centric reasoning. Domain MEANING decides the type.

    Decision logic (semantic, not lexical — orthogonal to SchemaPath's name
    tie-break and to ValuePath's distributions):

    * ``TYPE_COMPAT`` semantic-type / unit incompatibility ⇒ ``UNRELATED``: you
      cannot relate a currency to a date no matter how the numbers line up. A
      unit MISMATCH on otherwise-numeric columns is the derived-field tell.
    * Table ROLES from naming domain: a ``*_bridge`` / junction table ⇒
      ``M2M_BRIDGE``; a fact→``dim_*`` pairing ⇒ ``LOOKUP_DIMENSION``; a column
      whose name reads as a COMPUTED measure (``total``, ``amount``, ``rate``…)
      against a base column ⇒ ``DERIVED_FIELD``.
    * Falls back to ``FK_JOIN`` when the domain reads as a plain id reference, and
      ``UNKNOWN`` when no domain cue fires (so it does not fabricate meaning).
    """

    path: ReasoningPath = ReasoningPath.BUSINESS_LOGIC

    def vote(
        self,
        cand: RelationshipCandidate,
        validation: Optional[JoinValidation] = None,
    ) -> PathVote:
        sig = _signal_map(cand)
        type_compat = _val(sig, SignalKind.TYPE_COMPAT, default=1.0)
        type_ev = sig.get(SignalKind.TYPE_COMPAT)

        l_tbl, l_col = cand.left.table.lower(), cand.left.column.lower()
        r_tbl, r_col = cand.right.table.lower(), cand.right.column.lower()

        # 1) semantic-type / unit incompatibility ⇒ unrelated (a domain veto).
        #    A conflicting TYPE_COMPAT artifact is the strongest meaning-level "no".
        if type_ev is not None and type_ev.conflicts and type_compat < 0.3:
            return PathVote(
                self.path, RelationshipType.UNRELATED,
                _clamp(0.55 + 0.4 * (1.0 - type_compat)),
                f"semantic types incompatible ({type_ev.detail or 'unit/type conflict'}) "
                f"⇒ cannot relate by meaning",
            )

        # 2) bridge/junction table role ⇒ many-to-many.
        if _name_hits(l_tbl, _BRIDGE_TABLES) or _name_hits(r_tbl, _BRIDGE_TABLES):
            return PathVote(
                self.path, RelationshipType.M2M_BRIDGE, 0.7,
                f"bridge/junction table role in '{l_tbl}'/'{r_tbl}' ⇒ many-to-many",
            )

        # 3) dimension / reference table role ⇒ lookup.
        r_is_dim = _name_hits(r_tbl, _DIMENSION_TABLES)
        l_is_fact = _name_hits(l_tbl, _FACT_TABLES)
        if r_is_dim or (l_is_fact and r_col.endswith(("_id", "id", "code", "key"))):
            return PathVote(
                self.path, RelationshipType.LOOKUP_DIMENSION,
                _clamp(0.6 + (0.1 if l_is_fact else 0.0)),
                f"fact '{l_tbl}' → dimension/reference '{r_tbl}' ⇒ lookup",
            )

        # 4) computed-measure naming ⇒ derived field (especially on unit mismatch).
        unit_mismatch = (
            type_ev is not None and type_ev.fired
            and "unit" in (type_ev.detail or "").lower()
        )
        if _name_hits(l_col, _DERIVED_CUES) and (unit_mismatch or _name_hits(r_col, _DERIVED_CUES)):
            return PathVote(
                self.path, RelationshipType.DERIVED_FIELD,
                _clamp(0.5 + (0.2 if unit_mismatch else 0.05)),
                f"'{l_col}' reads as a computed measure ⇒ derived field",
            )

        # 5) plain id reference domain ⇒ fk.
        if r_col.endswith(("_id", "id", "_key", "key", "_fk")) and type_compat >= 0.5:
            return PathVote(
                self.path, RelationshipType.FK_JOIN, 0.55,
                f"id-reference domain ('{l_col}'→'{r_col}'), types compatible ⇒ fk_join",
            )

        # 6) no domain cue fired — do not fabricate meaning.
        return PathVote(
            self.path, RelationshipType.UNKNOWN, 0.3,
            "no decisive domain/semantic cue ⇒ defer to other paths",
        )


def default_paths() -> list[PathExpert]:
    """The three distinct reasoning paths (§1.3): schema · value · business-logic."""
    return [SchemaPath(), ValuePath(), BusinessLogicPath()]
