"""Typed relationship classifier — deterministic rules over the evidence (v2.1 §1.2).

CLOSED-CORE IP (OntoForge_Build_Instructions.md §18).

Replaces the binary join / no-join verdict with the typed taxonomy the ontology
builder consumes. Given the fused :class:`~.score.SignalSet` (and small optional
table-shape hints — themselves derived from profiles, never from bulk rows) the
classifier applies an ORDERED rule cascade and emits a
:class:`~ontoforge.contracts.RelationshipType` plus a ``needs_adjudication`` flag.

Rules (first match wins), each anchored in distribution-aware evidence:

UNRELATED          the explicit false-positive verdict. Names look similar AND/OR
    types are compatible, but the value DISTRIBUTIONS diverge (or there is simply
    no overlap). This fires *before* the positive rules so a look-alike can never
    be mistyped as a join.
FK_JOIN            child values (near-)contained in a near-UNIQUE parent key,
    many:1 shape, distributions aligned. The canonical referential edge.
LOOKUP_DIMENSION   like an FK but the parent is a SMALL unique reference table with
    descriptive (non-key) attributes the child points into — a fact→dimension edge.
M2M_BRIDGE         the (left) column lives in a table that is essentially two FKs
    (a junction/bridge table) and this column is one of those FKs.
DENORMALIZATION    a non-key attribute repeated across tables with the SAME value
    distribution (a copied/denormalized column), not a key reference.
DERIVED_FIELD      one side is a simple deterministic function of the other on the
    sampled rows (equality / scaling / casing) — a computed copy.
UNKNOWN            insufficient evidence to type (sparse samples, no signal cleared).

``needs_adjudication`` is set when signals CONFLICT internally or the proxy lands
in the ambiguous band (delegated to :func:`~.score.score_pair`).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Optional

from ontoforge.contracts import RelationshipType

from .score import SignalSet
from .signals import SampledColumn

__all__ = [
    "PK_BAND_TOLERANCE",
    "PK_DISTINCT_RATIO",
    "ClassifierResult",
    "TableShape",
    "classify_relationship",
    "is_pk_candidate",
]


@dataclass(frozen=True, slots=True)
class TableShape:
    """Cheap table-level shape hints (from profiles only) for the structural rules.

    ``fk_like_columns`` is how many columns of the table look like foreign keys
    (compatible-typed, non-unique, low-ish cardinality). ``total_columns`` is the
    column count. A table with ``fk_like_columns >= 2`` and few other columns is a
    bridge candidate (M2M). ``row_count`` / ``max_distinct`` feed the Tursio PK band
    (a column is a PK candidate when its distinct count is near the row count AND
    near the table's MAX distinct). All values derive from profiles — never bulk
    rows.
    """

    total_columns: int = 0
    fk_like_columns: int = 0
    descriptive_columns: int = 0  # non-key descriptive attrs (lookup/dimension signal)
    is_small: bool = False        # few distinct rows ⇒ reference/dimension-table-like
    row_count: int = 0            # table row count (for the PK band)
    max_distinct: int = 0         # max distinct count across the table's columns (PK band)

    @property
    def is_bridge_like(self) -> bool:
        # mostly two (or more) FKs, little else: a junction table
        return self.fk_like_columns >= 2 and self.total_columns <= self.fk_like_columns + 1


@dataclass(frozen=True, slots=True)
class ClassifierResult:
    rel_type: RelationshipType
    rationale: str


# thresholds (documented; tests pin behavior)
_FK_CONTAIN = 0.9       # child (near-)contained in parent
_FK_KEY_UNIQ = 0.95     # parent near-unique
_LOOKUP_RATIO = 0.25    # parent much smaller than child ⇒ dimension-like many:1
_DIVERGE_CONFLICT = 0.5  # distribution divergence that denies a relationship
_NAME_SIM = 0.6         # "looks similar" name threshold for the FP verdict
_DENORM_DIVERGE = 0.2   # denormalized copy: distributions must nearly match
_DERIVED_EQ = 0.95      # sampled-row equality fraction for a derived/equal field

# Tursio author-tuned PK defaults (RESEARCH_ENGINE_SOTA §4 — adopted as documented
# defaults, kept tunable; x=0.95 is the reported best). A column is a PRIMARY-KEY
# candidate when its distinct count is ≥ PK_DISTINCT_RATIO × row_count AND within
# ±PK_BAND_TOLERANCE of the table's MAX distinct count (so a near-unique column that
# is still far below the widest column is NOT mistaken for the table's key).
PK_DISTINCT_RATIO = 0.95
PK_BAND_TOLERANCE = 0.05


def is_pk_candidate(
    profile,
    shape: Optional[TableShape],
    *,
    distinct_ratio: float = PK_DISTINCT_RATIO,
    band_tolerance: float = PK_BAND_TOLERANCE,
) -> bool:
    """Tursio PK band: is ``profile`` a primary-key candidate for its table?

    Requires distinct ≥ ``distinct_ratio`` × row_count AND distinct within
    ±``band_tolerance`` of the table's max distinct (from ``shape``). When no shape
    is supplied we fall back to the row-count test alone (uniqueness ≥ ratio).
    Pure, deterministic, profile-only.
    """
    rows = getattr(profile, "row_count", 0)
    distinct = getattr(profile, "distinct_estimate", 0)
    if rows <= 0 or distinct <= 0:
        return False
    if distinct < distinct_ratio * rows:
        return False
    if shape is not None and shape.max_distinct > 0:
        lo = (1.0 - band_tolerance) * shape.max_distinct
        if distinct < lo:
            return False
    return True


def _sampled_equality(left: SampledColumn, right: SampledColumn) -> float:
    """Fraction of left's sampled values that appear verbatim in right's sample.

    A crude derived/copy probe over the SAMPLE only (never bulk): a derived field
    that merely re-expresses the parent key (or a cased/copied attribute) shows
    near-total verbatim membership.
    """
    la = left.value_set()
    if not la:
        return 0.0
    ra = right.value_set()
    return len(la & ra) / len(la)


def _detect_transform(left: SampledColumn, right: SampledColumn, *, fire_at: float = 0.9) -> Optional[str]:
    """Detect a SIMPLE deterministic transform left = f(right) on the sample (§1.2).

    Tries a small, fixed family of element-wise string transforms; returns the
    transform name if ≥ ``fire_at`` of right's sampled values map onto a left
    sampled value under it (and it is NOT the identity, which is a copy, not a
    derivation). Pure-python, deterministic, sample-only.
    """
    ra = sorted(right.value_set())
    la = left.value_set()
    if not ra or not la:
        return None

    candidates: dict[str, Callable[[str], str]] = {
        "uppercase": str.upper,
        "lowercase": str.lower,
        "strip": str.strip,
        "first_token": lambda s: s.split()[0] if s.split() else s,
        "prefix_3": lambda s: s[:3],
        "drop_trailing_digits": lambda s: s.rstrip("0123456789"),
    }
    for name, fn in candidates.items():
        hits = 0
        nontrivial = 0
        for v in ra:
            try:
                t = fn(v)
            except Exception:
                continue
            if t != v:
                nontrivial += 1
            if t in la:
                hits += 1
        # require coverage AND that the transform actually changed values (not identity)
        if ra and hits / len(ra) >= fire_at and nontrivial / len(ra) >= 0.5:
            return name
    return None


def classify_relationship(
    left: SampledColumn,
    right: SampledColumn,
    signals: SignalSet,
    *,
    left_table: Optional[TableShape] = None,
    right_table: Optional[TableShape] = None,
) -> ClassifierResult:
    """Type the ordered pair (left = child/referencing, right = parent/referenced)."""
    s = signals
    contain_lr = s.containment_lr.value
    contain_rl = s.containment_rl.value
    best_contain = max(contain_lr, contain_rl)
    overlap = max(best_contain, s.jaccard.value, s.sampled_row.value)
    divergence = s.divergence.value
    key_uniq = s.key_uniqueness.value
    card_ratio = s.cardinality.value
    name_sim = s.name_similarity.value
    type_ok = s.type_compat.value >= 0.6

    # The FK family demands a near-unique key on at least one side; without that
    # there is no referential edge to anchor a high-containment match.
    has_key = key_uniq >= _FK_KEY_UNIQ or left.profile.uniqueness >= _FK_KEY_UNIQ
    non_key_pair = key_uniq < _FK_KEY_UNIQ and left.profile.uniqueness < _FK_KEY_UNIQ

    # ---- Rule -1: DERIVED_FIELD by COMPUTED TRANSFORM (before the FP guard) ----
    # A confirmed element-wise transform left = f(right) IS a relationship even
    # when the raw value SETS look disjoint (e.g. a case/format derivation), so it
    # is recognized before the divergence-based UNRELATED guard would veto it. The
    # detector demands ≥90% non-identity mapping coverage — strong, not incidental.
    if non_key_pair and not has_key:
        transform = _detect_transform(left, right)
        if transform is not None:
            return ClassifierResult(
                RelationshipType.DERIVED_FIELD,
                f"left is a computed '{transform}' transform of right's values "
                f"(simple derivation, neither side a key); derived field",
            )

    # ---- Rule 0: UNRELATED — the false-positive killer (fires first) ----------
    # Looks similar (name and/or type compatible) but the evidence denies it.
    looks_similar = name_sim >= _NAME_SIM or type_ok
    if looks_similar:
        # 0a — strong DISTRIBUTION divergence with no key to anchor a join:
        # the columns may even share a *vocabulary* (high containment) yet disagree
        # on *frequency/shape* and neither side is a key — the classic
        # "shared category labels that mean different things" false positive.
        if divergence >= _DIVERGE_CONFLICT and not has_key:
            return ClassifierResult(
                RelationshipType.UNRELATED,
                f"compatible look-alike columns but value distributions diverge "
                f"(divergence={divergence:.2f} ≥ {_DIVERGE_CONFLICT}) and neither side is a key; "
                f"not a relationship",
            )
        if best_contain < _FK_CONTAIN:
            # 0b — partial overlap + divergence: a coincidental name/type match.
            if divergence >= _DIVERGE_CONFLICT:
                return ClassifierResult(
                    RelationshipType.UNRELATED,
                    f"name/type look similar but value distributions diverge "
                    f"(divergence={divergence:.2f} ≥ {_DIVERGE_CONFLICT}); not a relationship",
                )
            # 0c — negligible value overlap: coincidental, not related.
            if overlap < 0.1:
                return ClassifierResult(
                    RelationshipType.UNRELATED,
                    f"compatible columns with negligible value overlap "
                    f"(overlap={overlap:.2f}); coincidental, not related",
                )

    if not type_ok:
        return ClassifierResult(
            RelationshipType.UNKNOWN,
            f"incompatible types ({s.type_compat.detail}); cannot type a relationship",
        )

    # ---- Rule 1: M2M_BRIDGE — left is one of two FKs in a junction table ------
    if (
        left_table is not None
        and left_table.is_bridge_like
        and best_contain >= _FK_CONTAIN
        and key_uniq >= _FK_KEY_UNIQ
    ):
        return ClassifierResult(
            RelationshipType.M2M_BRIDGE,
            f"left lives in a bridge table ({left_table.fk_like_columns} FKs / "
            f"{left_table.total_columns} cols) and references a unique key — many-to-many edge",
        )

    # ---- Rule 2: FK_JOIN / LOOKUP_DIMENSION — contained child → unique parent --
    if contain_lr >= _FK_CONTAIN and key_uniq >= _FK_KEY_UNIQ and divergence < _DIVERGE_CONFLICT:
        # Tursio PK band: does the parent column read as the table's PRIMARY key
        # (distinct near row-count AND near the table's widest column)? Recorded as
        # corroboration on the rationale; it strengthens the FK reading without
        # being able to veto a high-containment unique-key match.
        parent_is_pk = is_pk_candidate(right.profile, right_table)
        pk_note = "parent clears PK band; " if parent_is_pk else ""
        # dimension if the parent is a small reference table with descriptive attrs
        is_lookup = (
            (right_table is not None and right_table.is_small and right_table.descriptive_columns >= 1)
            or card_ratio <= _LOOKUP_RATIO
        )
        if is_lookup:
            return ClassifierResult(
                RelationshipType.LOOKUP_DIMENSION,
                f"child values contained in a small unique reference key "
                f"({pk_note}containment={contain_lr:.2f}, parent uniqueness={key_uniq:.2f}); "
                f"fact→dimension lookup",
            )
        return ClassifierResult(
            RelationshipType.FK_JOIN,
            f"child values contained in unique parent key "
            f"({pk_note}containment={contain_lr:.2f}, parent uniqueness={key_uniq:.2f}, "
            f"distributions aligned divergence={divergence:.2f}); foreign-key join",
        )

    # reverse direction (right is the child): symmetric FK
    if contain_rl >= _FK_CONTAIN and left.profile.uniqueness >= _FK_KEY_UNIQ and divergence < _DIVERGE_CONFLICT:
        return ClassifierResult(
            RelationshipType.FK_JOIN,
            f"reverse-direction containment into a unique left key "
            f"(containment={contain_rl:.2f}); foreign-key join (parent=left)",
        )

    eq = _sampled_equality(left, right)
    non_key = non_key_pair

    # ---- Rule 4: DENORMALIZATION — repeated non-key attr, matching distribution -
    # A verbatim copy of a non-key attribute: same values AND the SAME distribution
    # (frequencies/shape align) — a denormalization artifact, not a derivation.
    if non_key and overlap >= 0.5 and divergence <= _DENORM_DIVERGE:
        return ClassifierResult(
            RelationshipType.DENORMALIZATION,
            f"non-key attribute repeated across tables with matching distribution "
            f"(overlap={overlap:.2f}, divergence={divergence:.2f}); denormalized copy",
        )

    # ---- Rule 5: DERIVED_FIELD (verbatim projection) — re-projected non-key copy -
    # Verbatim membership on the sample, non-key, but distributions differ enough
    # that it is a re-projection/selection rather than a flat denormalized copy.
    if eq >= _DERIVED_EQ and non_key:
        return ClassifierResult(
            RelationshipType.DERIVED_FIELD,
            f"left reproduces right's values verbatim on the sample "
            f"(equality={eq:.2f}) with a different distribution; derived/projected field",
        )

    # ---- Rule 5: UNKNOWN — partial / mixed evidence, no rule cleared -----------
    return ClassifierResult(
        RelationshipType.UNKNOWN,
        f"partial evidence: containment={best_contain:.2f}, key_uniq={key_uniq:.2f}, "
        f"divergence={divergence:.2f} — no typed rule cleared; route for adjudication",
    )
