"""Per-estate weighting profile — context-dependent signal fusion (RESEARCH_ENGINE_SOTA §5).

CLOSED-CORE IP (OntoForge_Build_Instructions.md §18).

The verified research rule (3-0): there is NO single global fusion formula that is
right for every estate. SIZE / OVERLAP / UNIQUENESS signals dominate when the data
is a **clean relational DB** (well-keyed, surrogate ids everywhere, lots of true
FK structure); METADATA + VALUE-SEMANTICS signals (names, types, rare-token
overlap, distribution shape) dominate when the data is a **messy data lake**
(loose, name-poor, few real keys, free-text-ish columns). So we DETECT the estate
type from the profiles alone and re-weight the fusion accordingly — never one
global formula.

This module is pure detection + a multiplier table. It NEVER reads bulk rows: the
estate fingerprint is computed from ``TableProfile`` / ``ColumnProfile`` sketches
(uniqueness, datatype mix, distinct counts) that the profiler already produced.

How the fusion uses it (:mod:`score`): each signal belongs to a SIGNAL GROUP
(``structural`` / ``overlap`` / ``semantic``); the profile carries one multiplier
per group; ``fuse_confidence`` scales each artifact's weight by its group's
multiplier before summing. The DEFAULT profile is balanced (all multipliers 1.0)
so behavior is unchanged unless an estate fingerprint is supplied.

Deterministic: a fixed set of profiles yields a fixed estate kind and fixed
multipliers (pure arithmetic over rounded inputs).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum

from ontoforge.contracts import Datatype, TableProfile

__all__ = [
    "EstateKind",
    "SignalGroup",
    "WeightingProfile",
    "BALANCED",
    "RELATIONAL",
    "LAKE",
    "EstateFingerprint",
    "fingerprint_estate",
    "classify_estate",
    "weighting_for_estate",
    "profile_for",
    "SIGNAL_GROUP",
]


class EstateKind(str, Enum):
    """The two estate archetypes the research separates, plus the safe default."""

    BALANCED = "balanced"      # default — no strong evidence either way
    RELATIONAL = "relational"  # clean, well-keyed, surrogate ids, true FK structure
    LAKE = "lake"              # messy data lake — loose, name-poor, few real keys


class SignalGroup(str, Enum):
    """Which family a signal belongs to, for group-level re-weighting."""

    STRUCTURAL = "structural"  # size / uniqueness / cardinality — keyness of the schema
    OVERLAP = "overlap"        # value containment / Jaccard / sampled-row — set agreement
    SEMANTIC = "semantic"      # names / types / rare-token / distribution — meaning & metadata


# Each fused signal kind → its group. ``score.fuse_confidence`` consults this to
# decide which multiplier applies. (DISTRIBUTION_DIVERGENCE is the false-positive
# killer and is deliberately NOT down-weighted by any profile — see below.)
SIGNAL_GROUP: dict[str, SignalGroup] = {
    "containment_lr": SignalGroup.OVERLAP,
    "containment_rl": SignalGroup.OVERLAP,
    "jaccard": SignalGroup.OVERLAP,
    "sampled_row": SignalGroup.OVERLAP,
    "key_uniqueness": SignalGroup.STRUCTURAL,
    "cardinality": SignalGroup.STRUCTURAL,
    "infrequent_token": SignalGroup.SEMANTIC,
    "name_similarity": SignalGroup.SEMANTIC,
    "type_compat": SignalGroup.SEMANTIC,
    # entropy & divergence are discriminators handled explicitly in fusion, not scaled.
}


@dataclass(frozen=True, slots=True)
class WeightingProfile:
    """Per-group weight multipliers applied to the signal fusion.

    A multiplier > 1.0 amplifies that group's contribution, < 1.0 dampens it. The
    false-positive killers (distribution divergence, type/entropy conflicts) are
    NEVER scaled — re-weighting must never be able to silence the guard that keeps
    a look-alike from being typed as a join.
    """

    kind: EstateKind = EstateKind.BALANCED
    structural: float = 1.0
    overlap: float = 1.0
    semantic: float = 1.0

    def multiplier(self, group: SignalGroup) -> float:
        if group is SignalGroup.STRUCTURAL:
            return self.structural
        if group is SignalGroup.OVERLAP:
            return self.overlap
        return self.semantic

    def for_field(self, field_name: str) -> float:
        """Multiplier for a named SignalSet field (1.0 if the field is ungrouped)."""
        grp = SIGNAL_GROUP.get(field_name)
        return self.multiplier(grp) if grp is not None else 1.0


# The three documented profiles. RELATIONAL leans on structure+overlap (real keys
# and value agreement carry the signal); LAKE leans on semantic metadata (names,
# rare-token overlap, type/distribution cues) because keys and clean overlap are
# scarce. BALANCED is the unbiased default. Multipliers stay in a tight band so the
# guard math (which is NOT scaled) still dominates the false-positive decision.
BALANCED = WeightingProfile(EstateKind.BALANCED, structural=1.0, overlap=1.0, semantic=1.0)
RELATIONAL = WeightingProfile(EstateKind.RELATIONAL, structural=1.30, overlap=1.20, semantic=0.80)
LAKE = WeightingProfile(EstateKind.LAKE, structural=0.80, overlap=0.90, semantic=1.40)

_PROFILES: dict[EstateKind, WeightingProfile] = {
    EstateKind.BALANCED: BALANCED,
    EstateKind.RELATIONAL: RELATIONAL,
    EstateKind.LAKE: LAKE,
}


def profile_for(kind: EstateKind) -> WeightingProfile:
    """The documented :class:`WeightingProfile` for an :class:`EstateKind`."""
    return _PROFILES[kind]


@dataclass(frozen=True, slots=True)
class EstateFingerprint:
    """A small, profile-derived fingerprint of an estate's shape.

    Every field is computed from ``ColumnProfile`` sketches — never bulk rows.

    * ``keyed_table_fraction`` — fraction of tables that have at least one
      near-unique (key-like) column. Clean relational schemas key almost every
      table; lakes rarely do.
    * ``string_column_fraction`` — fraction of columns that are STRING/TEXT.
      Lakes are name-poor and text-heavy; relational schemas are id/measure heavy.
    * ``avg_uniqueness`` — mean column uniqueness. High ⇒ lots of distinct
      identifier-shaped columns ⇒ relational.
    """

    n_tables: int
    n_columns: int
    keyed_table_fraction: float
    string_column_fraction: float
    avg_uniqueness: float


# tunables (documented; tests pin behavior)
_KEY_UNIQ = 0.95          # uniqueness for a column to count as "key-like"
_RELATIONAL_KEYED = 0.6   # ≥ this fraction of tables keyed ⇒ relational evidence
_RELATIONAL_STRINGY = 0.5  # string-column fraction BELOW this ⇒ relational evidence
_LAKE_KEYED = 0.3         # ≤ this fraction of tables keyed ⇒ lake evidence
_LAKE_STRINGY = 0.6       # string-column fraction ABOVE this ⇒ lake evidence


def fingerprint_estate(table_profiles: Sequence[TableProfile]) -> EstateFingerprint:
    """Compute the estate fingerprint from table/column profiles (no bulk rows)."""
    n_tables = len(table_profiles)
    n_cols = 0
    keyed_tables = 0
    string_cols = 0
    uniq_sum = 0.0
    for tp in table_profiles:
        cols = list(tp.columns.values())
        n_cols += len(cols)
        if any(c.uniqueness >= _KEY_UNIQ for c in cols):
            keyed_tables += 1
        for c in cols:
            if c.inferred_type in (Datatype.STRING, Datatype.TEXT):
                string_cols += 1
            uniq_sum += c.uniqueness
    keyed_frac = keyed_tables / n_tables if n_tables else 0.0
    string_frac = string_cols / n_cols if n_cols else 0.0
    avg_uniq = uniq_sum / n_cols if n_cols else 0.0
    return EstateFingerprint(
        n_tables=n_tables,
        n_columns=n_cols,
        keyed_table_fraction=round(keyed_frac, 6),
        string_column_fraction=round(string_frac, 6),
        avg_uniqueness=round(avg_uniq, 6),
    )


def classify_estate(fp: EstateFingerprint) -> EstateKind:
    """Classify the estate from its fingerprint (deterministic thresholds).

    RELATIONAL when the schema is well-keyed AND id/measure-heavy (not stringy);
    LAKE when it is poorly-keyed AND string-heavy; BALANCED otherwise (and always
    on a too-small estate where the evidence is thin).
    """
    if fp.n_tables < 2 or fp.n_columns == 0:
        return EstateKind.BALANCED
    relational_score = 0
    lake_score = 0
    if fp.keyed_table_fraction >= _RELATIONAL_KEYED:
        relational_score += 1
    if fp.string_column_fraction < _RELATIONAL_STRINGY:
        relational_score += 1
    if fp.avg_uniqueness >= 0.5:
        relational_score += 1
    if fp.keyed_table_fraction <= _LAKE_KEYED:
        lake_score += 1
    if fp.string_column_fraction > _LAKE_STRINGY:
        lake_score += 1
    if fp.avg_uniqueness < 0.35:
        lake_score += 1
    if relational_score >= 2 and relational_score > lake_score:
        return EstateKind.RELATIONAL
    if lake_score >= 2 and lake_score > relational_score:
        return EstateKind.LAKE
    return EstateKind.BALANCED


def weighting_for_estate(table_profiles: Sequence[TableProfile]) -> WeightingProfile:
    """End-to-end: profiles → fingerprint → estate kind → weighting profile."""
    return profile_for(classify_estate(fingerprint_estate(table_profiles)))
