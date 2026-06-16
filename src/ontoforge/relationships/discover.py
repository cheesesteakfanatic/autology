"""Relationship discovery — rank every viable column pair (v2.1 §1.1, §1.2).

CLOSED-CORE IP (OntoForge_Build_Instructions.md §18).

:func:`discover_relationships` is the engine entry point. Given a set of
:class:`~ontoforge.contracts.TableProfile`s (the φ sketches), an OPTIONAL set of
already-validated :class:`~ontoforge.contracts.IND`s (to prioritize/seed pairs),
and a sample provider (column → small sampled values, NEVER bulk rows), it:

  1. enumerates ordered, type-compatible column pairs across distinct tables;
  2. computes the full signal set per pair (:mod:`signals` / :mod:`score`);
  3. types the pair (:mod:`classify`) with cheap table-shape hints;
  4. fuses the confidence PROXY and emits a
     :class:`~ontoforge.contracts.RelationshipCandidate` (one per viable pair);
  5. returns them ranked by descending proxy confidence (deterministic tie-break
     on the column addresses).

Pairs whose proxy falls below ``min_confidence`` and whose verdict is UNRELATED /
UNKNOWN are dropped (they carry no signal); the explicit UNRELATED verdict is
KEPT when it still cleared overlap-worthy attention (so the false-positive
decision is auditable). Determinism: sorted table/column iteration, fixed
thresholds, rounded proxies — a fixed input yields identical candidates and
identical evidence.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Optional

from ontoforge.contracts import (
    ColumnProfile,
    Datatype,
    IND,
    RelationshipCandidate,
    RelationshipType,
    TableProfile,
)

from .classify import TableShape, classify_relationship
from .score import IND_PRUNE_FLOOR, compute_signals, ind_candidate_score, score_pair
from .signals import SampledColumn
from .weighting import WeightingProfile, weighting_for_estate

__all__ = ["PairProfiles", "SampleProvider", "discover_relationships"]

# A sample provider maps (source_id, table, column) -> small sampled value set.
SampleProvider = Callable[[str, str, str], Sequence[str]]

_GROUP: dict[Datatype, str] = {
    Datatype.INTEGER: "numeric",
    Datatype.FLOAT: "numeric",
    Datatype.STRING: "string",
    Datatype.TEXT: "string",
    Datatype.DATE: "date",
    Datatype.DATETIME: "datetime",
}
# columns this small (distinct) make a table look like a reference/dimension table
_SMALL_TABLE_DISTINCT = 64

# the JOIN-shaped (inclusion-dependency-anchored) relationship types, subject to the
# Tursio IND-candidate prune; non-join types are governed by the confidence floor.
_JOIN_TYPES = frozenset({
    RelationshipType.FK_JOIN,
    RelationshipType.LOOKUP_DIMENSION,
    RelationshipType.M2M_BRIDGE,
})


@dataclass(frozen=True, slots=True)
class PairProfiles:
    """A scored ordered pair returned alongside its candidate (for downstream tools)."""

    candidate: RelationshipCandidate
    left: SampledColumn
    right: SampledColumn


def _compatible(a: Datatype, b: Datatype) -> bool:
    ga, gb = _GROUP.get(a), _GROUP.get(b)
    return ga is not None and ga == gb


def _is_fk_like(col: ColumnProfile) -> bool:
    """A column looks FK-like if it is an IDENTIFIER-shaped reference, not a measure.

    FK references are integer or string ids that repeat (non-unique) and are not
    constant. A FLOAT column, or any column carrying a physical unit/dimension, is
    a MEASURE (amount, weight, price) — never a foreign key — and is excluded so a
    fact table with measures is not mistaken for a bridge/junction table.
    """
    return (
        col.inferred_type in (Datatype.INTEGER, Datatype.STRING)
        and col.unit is None
        and col.dimension is None
        and col.distinct_estimate >= 2
        and col.uniqueness < 0.98
    )


def _table_shape(tp: TableProfile) -> TableShape:
    cols = list(tp.columns.values())
    fk_like = sum(1 for c in cols if _is_fk_like(c))
    descriptive = sum(
        1
        for c in cols
        if c.inferred_type in (Datatype.STRING, Datatype.TEXT) and c.uniqueness < 0.98
    )
    # A surrogate-key column (a single near-unique id) marks a FACT/entity table,
    # NOT a bridge: a junction table's grain is the FK pair, not its own id. Its
    # presence suppresses the bridge interpretation.
    has_surrogate_key = any(c.uniqueness >= 0.98 for c in cols)
    is_small = 0 < tp.row_count <= _SMALL_TABLE_DISTINCT or all(
        c.distinct_estimate <= _SMALL_TABLE_DISTINCT for c in cols
    ) if cols else False
    max_distinct = max((c.distinct_estimate for c in cols), default=0)
    return TableShape(
        total_columns=len(cols),
        fk_like_columns=0 if has_surrogate_key else fk_like,
        descriptive_columns=descriptive,
        is_small=bool(is_small),
        row_count=tp.row_count,
        max_distinct=max_distinct,
    )


def _sampled(col: ColumnProfile, provider: Optional[SampleProvider]) -> SampledColumn:
    vals: tuple[str, ...] = ()
    if provider is not None:
        raw = provider(col.source_id, col.table, col.column)
        if raw:
            vals = tuple(str(v) for v in raw)
    if not vals:
        # fall back to φ's own stratified sample (always present, always small)
        vals = col.sample_values
    return SampledColumn(profile=col, values=vals)


def discover_relationships(
    table_profiles: Sequence[TableProfile],
    *,
    inds: Optional[Sequence[IND]] = None,
    samples: Optional[SampleProvider] = None,
    min_confidence: float = 0.5,
    keep_unrelated: bool = True,
    profile: Optional[WeightingProfile] = None,
    ind_prune_floor: float = IND_PRUNE_FLOOR,
) -> list[RelationshipCandidate]:
    """Discover and rank typed relationship candidates across tables.

    Parameters
    ----------
    table_profiles : the φ sketches for every table.
    inds : optional validated inclusion dependencies — when supplied, only pairs
        that appear as an IND (either direction) are considered, which both prunes
        the O(n²) pair space and respects the M3 join-candidate evidence. When
        omitted, all type-compatible cross-table pairs are considered.
    samples : optional (source, table, column) -> small sampled values provider;
        falls back to each :class:`ColumnProfile`'s own ``sample_values``. NEVER
        bulk rows.
    min_confidence : drop typed candidates whose proxy is below this.
    keep_unrelated : keep the explicit UNRELATED verdict (auditable false-positive
        decision) even below ``min_confidence`` when the pair was IND-seeded or
        name/type tempting.
    profile : the PER-ESTATE :class:`~.weighting.WeightingProfile` for the signal
        fusion. ``None`` (the default) AUTO-DETECTS the estate kind (clean-relational
        vs messy-lake) from ``table_profiles`` and re-weights accordingly; pass
        :data:`~.weighting.BALANCED` to force the unbiased global formula.

    Returns a list of :class:`RelationshipCandidate`, ranked by descending proxy
    confidence then by column address (stable).
    """
    est_profile = profile if profile is not None else weighting_for_estate(table_profiles)
    by_addr: dict[tuple[str, str], ColumnProfile] = {}
    shapes: dict[tuple[str, str], TableShape] = {}
    for tp in table_profiles:
        shapes[(tp.source_id, tp.table)] = _table_shape(tp)
        for col in tp.columns.values():
            by_addr[(col.table, col.column)] = col

    pairs = _candidate_pairs(table_profiles, inds)

    out: list[RelationshipCandidate] = []
    seen: set[tuple[str, str, str, str]] = set()
    for (lt, lc), (rt, rc) in pairs:
        left_cp = by_addr.get((lt, lc))
        right_cp = by_addr.get((rt, rc))
        if left_cp is None or right_cp is None:
            continue
        if left_cp.table == right_cp.table and left_cp.column == right_cp.column:
            continue
        if not _compatible(left_cp.inferred_type, right_cp.inferred_type):
            continue
        key = (left_cp.table, left_cp.column, right_cp.table, right_cp.column)
        if key in seen:
            continue
        seen.add(key)

        left = _sampled(left_cp, samples)
        right = _sampled(right_cp, samples)
        sigs = compute_signals(left, right)
        result = classify_relationship(
            left,
            right,
            sigs,
            left_table=shapes.get((left_cp.source_id, left_cp.table)),
            right_table=shapes.get((right_cp.source_id, right_cp.table)),
        )
        cand = score_pair(
            left,
            right,
            rel_type=result.rel_type,
            rationale=result.rationale,
            signals=sigs,
            profile=est_profile,
        )

        if cand.rel_type is RelationshipType.UNRELATED:
            if keep_unrelated:
                out.append(cand)
            continue
        # Tursio IND prune (RESEARCH_ENGINE_SOTA §4): a JOIN-shaped candidate whose
        # 5-component IND/candidate score is below the floor is too weak to carry an
        # inclusion-dependency edge — prune it at candidate generation, unless it is
        # explicitly flagged for adjudication. Non-join types (DENORM / DERIVED /
        # UNKNOWN) are governed by the confidence floor below, not the IND score.
        if cand.rel_type in _JOIN_TYPES and not cand.needs_adjudication:
            if ind_candidate_score(sigs) < ind_prune_floor:
                continue
        if cand.rel_type is RelationshipType.UNKNOWN and cand.confidence < min_confidence:
            continue
        if cand.confidence < min_confidence and not cand.needs_adjudication:
            continue
        out.append(cand)

    out.sort(
        key=lambda c: (
            -c.confidence,
            c.left.table,
            c.left.column,
            c.right.table,
            c.right.column,
        )
    )
    return out


def _candidate_pairs(
    table_profiles: Sequence[TableProfile],
    inds: Optional[Sequence[IND]],
) -> list[tuple[tuple[str, str], tuple[str, str]]]:
    """Ordered (left, right) column-address pairs to evaluate (deterministic order)."""
    if inds:
        seen: set[tuple[tuple[str, str], tuple[str, str]]] = set()
        ordered: list[tuple[tuple[str, str], tuple[str, str]]] = []
        for ind in inds:
            pair = ((ind.lhs_table, ind.lhs_column), (ind.rhs_table, ind.rhs_column))
            if pair not in seen:
                seen.add(pair)
                ordered.append(pair)
        ordered.sort()
        return ordered

    cols: list[tuple[str, str]] = []
    for tp in sorted(table_profiles, key=lambda t: (t.source_id, t.table)):
        for cname in sorted(tp.columns):
            cols.append((tp.table, cname))
    out: list[tuple[tuple[str, str], tuple[str, str]]] = []
    for i, a in enumerate(cols):
        for j, b in enumerate(cols):
            if i == j or a[0] == b[0]:  # skip self and same-table pairs
                continue
            out.append((a, b))
    return out
