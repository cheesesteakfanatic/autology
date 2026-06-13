"""Scale guard for cross-table value-overlap analysis (wild-corpus pre-pass).

:func:`ontoforge.profiling.discover_inds` (frozen) intersects the full distinct
value sets of ALL type-compatible column pairs — exact and fine at fixture
scale, O(columns²) set intersections at wild-internet scale (200+ tables /
2000+ columns). This module is the pipeline-side replacement for that call
site (profiling/ itself is untouched): the same contracts.IND objects come out,
but candidate pairs are pruned first through a banded value-hash index.

Pipeline
--------
1. ``column_facts`` hashes every column's distinct value set ONCE (the same
   canonical ``value_key``/``hash64`` chain discover_inds uses), keeping a
   small bottom-hash -> display-value map for shared-value evidence.
2. Candidate generation buckets columns by type group and indexes each
   column's hashes — full sets for columns with <= ``BAND_CAP`` (3000)
   distinct values, the uniform hash band ``h <= tau`` for larger ones (tau is
   sized so the largest column contributes ~BAND_CAP hashes). Tallying a
   column's (banded) probe hashes through the index yields, for every other
   column, an exact coverage when both sides are fully indexed and an unbiased
   banded estimate otherwise; pairs are kept when the estimate clears the
   floor minus a binomial slack (``_SLACK/sqrt(n)``), so true positives are
   never pruned at small scale and are overwhelmingly retained at large scale.
3. Surviving pairs are verified EXACTLY (full frozenset intersection) and
   scored with the same convex combination discover_inds documents (weights
   imported from the frozen module — one source of truth).

Equivalence contract: when every column has <= BAND_CAP distinct values the
band is the full hash space, estimates are exact, and
``discover_inds_scaled(corpus) == discover_inds(corpus)`` element for element
(tests/pipeline/test_atlas_scale.py pins this property over random corpora).

``pair_affinities`` runs the same machinery at a lower coverage floor and
returns the full affinity evidence (coverage, overlap, shared-value samples,
name similarity, rhs uniqueness) the connection atlas tiers from.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from ontoforge.contracts import IND, Datatype

# read-only reuse of the frozen M3 internals: the EXACT hashing chain, type
# compatibility, name tokenization and score weights discover_inds uses.
from ontoforge.profiling._values import columns_of, display_str, hash64, is_null, value_key
from ontoforge.profiling.inds import (
    _GROUP,
    _type_match,
    _W_COVERAGE,
    _W_NAME,
    _W_RHS_UNIQ,
    _W_TYPE,
    name_token_jaccard,
)
from ontoforge.profiling.semantic_types import infer_datatype

__all__ = [
    "BAND_CAP",
    "ColumnFacts",
    "PairAffinity",
    "column_facts",
    "discover_inds_scaled",
    "pair_affinities",
]

_MAX_HASH = 2**64 - 1
#: per-column distinct-hash budget for the candidate band (the mission cap)
BAND_CAP = 3000
#: bottom-hash -> display-value sample kept per column (shared-value evidence)
SAMPLE_CAP = 512
#: banded estimates with fewer probes than this cannot prune reliably; the
#: column is paired unconditionally against the banded (huge) columns instead
MIN_EST = 16
#: binomial slack multiplier: keep a pair when est >= floor - _SLACK/sqrt(n)
_SLACK = 4.0


@dataclass(slots=True)
class ColumnFacts:
    """One column's value-set facts, computed once per corpus."""

    table: str
    column: str
    dtype: Datatype
    hashes: frozenset[int]
    nonnull: int
    samples: dict[int, str] = field(default_factory=dict)  # bottom hashes -> display

    @property
    def distinct(self) -> int:
        return len(self.hashes)

    @property
    def uniqueness(self) -> float:
        return self.distinct / self.nonnull if self.nonnull else 0.0


@dataclass(frozen=True, slots=True)
class PairAffinity:
    """Exact value-overlap affinity of one ordered cross-column pair."""

    lhs_table: str
    lhs_column: str
    rhs_table: str
    rhs_column: str
    coverage: float          # |lhs ∩ rhs| / |lhs| over distinct values
    overlap: int             # |lhs ∩ rhs| distinct shared values
    lhs_distinct: int
    rhs_distinct: int
    name_similarity: float   # snake/camel token Jaccard
    type_match: float        # 1.0 same type, 0.6 same group
    rhs_uniqueness: float
    score: float             # discover_inds' convex combination, same weights
    shared_samples: tuple[str, ...] = ()   # <= 5 example shared values


def column_facts(corpus: Mapping[str, Any]) -> list[ColumnFacts]:
    """One hashing pass over every (table, column) — the discover_inds chain."""
    facts: list[ColumnFacts] = []
    for tname in corpus:
        for cname, values in columns_of(corpus[tname]).items():
            nn = [v for v in values if not is_null(v)]
            dtype = infer_datatype(values)
            by_hash: dict[int, Any] = {}
            for v in nn:
                h = hash64(value_key(v))
                if h not in by_hash:
                    by_hash[h] = v
            picks = sorted(by_hash)[:SAMPLE_CAP]
            facts.append(
                ColumnFacts(
                    table=tname,
                    column=cname,
                    dtype=dtype,
                    hashes=frozenset(by_hash),
                    nonnull=len(nn),
                    samples={h: display_str(by_hash[h]) for h in picks},
                )
            )
    return facts


def _eligible(facts: list[ColumnFacts], min_distinct: int) -> list[int]:
    return [
        i
        for i, f in enumerate(facts)
        if f.dtype is not Datatype.BOOLEAN and f.dtype in _GROUP and f.distinct >= min_distinct
    ]


def _plausible_pairs(
    facts: list[ColumnFacts], idxs: list[int], floor: float
) -> set[tuple[int, int]]:
    """Ordered (lhs, rhs) index pairs whose coverage plausibly reaches `floor`.

    Exact (zero false negatives AND zero false positives at the floor) when no
    column exceeds BAND_CAP distinct values; at scale, banded columns use an
    unbiased estimate with `_SLACK/sqrt(n)` slack."""
    groups: dict[str, list[int]] = {}
    for i in idxs:
        groups.setdefault(_GROUP[facts[i].dtype], []).append(i)

    out: set[tuple[int, int]] = set()
    for members in groups.values():
        if len(members) < 2:
            continue
        max_distinct = max(facts[i].distinct for i in members)
        banded = max_distinct > BAND_CAP
        tau = int(_MAX_HASH * (BAND_CAP / max_distinct)) if banded else _MAX_HASH

        smalls = [i for i in members if facts[i].distinct <= BAND_CAP]
        huges = [i for i in members if facts[i].distinct > BAND_CAP]
        band_of: dict[int, frozenset[int]] = {}
        if banded:
            for i in members:
                band_of[i] = frozenset(h for h in facts[i].hashes if h <= tau)

        index_small: dict[int, list[int]] = {}
        for j in smalls:
            for h in facts[j].hashes:
                index_small.setdefault(h, []).append(j)
        index_huge: dict[int, list[int]] = {}
        for j in huges:
            for h in band_of[j]:
                index_huge.setdefault(h, []).append(j)

        def tally(probes: Iterable[int], index: dict[int, list[int]]) -> Counter:
            counts: Counter = Counter()
            for h in probes:
                hit = index.get(h)
                if hit is not None:
                    counts.update(hit)
            return counts

        for i in members:
            f = facts[i]
            # --- vs fully-indexed (small) columns -------------------------
            if f.distinct <= BAND_CAP:
                probes: Iterable[int] = f.hashes
                n, slack = f.distinct, 0.0
            else:
                probes = band_of[i]
                n, slack = len(band_of[i]), 0.0
                slack = _SLACK / math.sqrt(n) if n else 1.0
            if n:
                need = max(0.0, floor - slack)
                for j, c in tally(probes, index_small).items():
                    if j != i and c / n >= need and c > 0:
                        out.add((i, j))
            # --- vs banded (huge) columns ---------------------------------
            if huges:
                bp = band_of[i]
                nb = len(bp)
                if nb < MIN_EST:
                    # too thin to estimate: verify exactly against every huge
                    out.update((i, j) for j in huges if j != i)
                else:
                    need = max(0.0, floor - _SLACK / math.sqrt(nb))
                    for j, c in tally(bp, index_huge).items():
                        if j != i and c / nb >= need and c > 0:
                            out.add((i, j))
    return out


def _shared_samples(fl: ColumnFacts, fr: ColumnFacts, inter: frozenset[int] | set[int], k: int = 5) -> tuple[str, ...]:
    picks: list[str] = []
    for h in sorted(inter):
        s = fl.samples.get(h)
        if s is None:
            s = fr.samples.get(h)
        if s is not None:
            picks.append(s)
        if len(picks) >= k:
            break
    return tuple(picks)


def _affinity(fl: ColumnFacts, fr: ColumnFacts) -> PairAffinity:
    inter = fl.hashes & fr.hashes
    coverage = len(inter) / fl.distinct if fl.distinct else 0.0
    tm = _type_match(fl.dtype, fr.dtype)
    name_sim = name_token_jaccard(fl.column, fr.column)
    score = (
        _W_COVERAGE * coverage
        + _W_NAME * name_sim
        + _W_TYPE * tm
        + _W_RHS_UNIQ * fr.uniqueness
    )
    return PairAffinity(
        lhs_table=fl.table,
        lhs_column=fl.column,
        rhs_table=fr.table,
        rhs_column=fr.column,
        coverage=coverage,
        overlap=len(inter),
        lhs_distinct=fl.distinct,
        rhs_distinct=fr.distinct,
        name_similarity=round(name_sim, 4),
        type_match=tm,
        rhs_uniqueness=round(fr.uniqueness, 4),
        score=round(score, 4),
        shared_samples=_shared_samples(fl, fr, inter),
    )


def pair_affinities(
    facts: list[ColumnFacts],
    *,
    floor: float,
    min_distinct: int = 2,
) -> list[PairAffinity]:
    """Every ordered type-compatible column pair with coverage >= floor,
    exactly verified, sorted by descending score (then names)."""
    idxs = _eligible(facts, min_distinct)
    out: list[PairAffinity] = []
    for i, j in _plausible_pairs(facts, idxs, floor):
        fl, fr = facts[i], facts[j]
        if fl.table == fr.table and fl.column == fr.column:
            continue
        aff = _affinity(fl, fr)
        if aff.coverage >= floor:
            out.append(aff)
    out.sort(key=lambda a: (-a.score, a.lhs_table, a.lhs_column, a.rhs_table, a.rhs_column))
    return out


def discover_inds_scaled(
    corpus: Mapping[str, Any],
    *,
    min_coverage: float = 0.95,
    min_distinct: int = 2,
) -> list[IND]:
    """Drop-in for :func:`ontoforge.profiling.discover_inds` with the banded
    candidate pre-pass. Same contracts.IND objects, same scores, same order;
    exactly equivalent whenever every column has <= BAND_CAP distinct values
    (property-tested), and within the documented banded slack beyond that."""
    facts = column_facts(corpus)
    idxs = _eligible(facts, min_distinct)
    out: list[IND] = []
    for i, j in _plausible_pairs(facts, idxs, min_coverage):
        fl, fr = facts[i], facts[j]
        if fl.table == fr.table and fl.column == fr.column:
            continue
        tm = _type_match(fl.dtype, fr.dtype)
        if tm == 0.0:
            continue
        coverage = len(fl.hashes & fr.hashes) / len(fl.hashes)
        if coverage < min_coverage:
            continue
        score = (
            _W_COVERAGE * coverage
            + _W_NAME * name_token_jaccard(fl.column, fr.column)
            + _W_TYPE * tm
            + _W_RHS_UNIQ * fr.uniqueness
        )
        out.append(
            IND(
                lhs_table=fl.table, lhs_column=fl.column,
                rhs_table=fr.table, rhs_column=fr.column,
                coverage=round(coverage, 4), score=round(score, 4),
            )
        )
    out.sort(key=lambda i: (-i.score, i.lhs_table, i.lhs_column, i.rhs_table, i.rhs_column))
    return out
