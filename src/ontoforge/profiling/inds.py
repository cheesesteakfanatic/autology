"""Unary inclusion-dependency discovery and join-candidate scoring (whitepaper §3.1).

§3.1 specifies BINDER-style divide-and-conquer over a value index; at v0 fixture
scale a direct value-set-hash intersection over all (table, column) pairs is
exact and simpler (BINDER's partitioning matters when value sets exceed memory —
registered as a scale challenger per §19.1). The n-ary apriori refinement over
validated unary INDs (v1 G3) is deferred with the rest of that scope.

Pipeline
--------
1. Hash every non-null value with the shared canonical `value_key` and xxhash64.
   Integral floats collapse to integer keys, so a BIGINT FK is found inside a
   DOUBLE PK (the cross-engine wart called out in `_values`). 64-bit collisions
   are negligible at fixture scale (~1e-9 at 10^5 distinct values).
2. Keep ordered pairs of type-compatible columns with
   coverage = |values(lhs) ∩ values(rhs)| / |values(lhs)| >= min_coverage (0.95).
   Compatibility groups: {INTEGER, FLOAT}, {STRING, TEXT}, {DATE}, {DATETIME}.
   BOOLEAN and near-constant columns (distinct < min_distinct) are excluded —
   they produce vacuous inclusions, not join evidence.
3. Score join candidates with a fixed convex combination (§3.1: "INDs +
   name/type/cardinality scoring yield join candidates"):

       score = 0.40·coverage + 0.20·name-token Jaccard
             + 0.15·type-match + 0.25·rhs-uniqueness

   rhs-uniqueness because a join target should be key-like; name tokens come
   from snake/camel splitting with light plural stemming so `o_custkey` and
   `c_custkey` share the token `custkey`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping

import numpy as np

from ontoforge.contracts import IND, Datatype

from ._values import columns_of, hash64, is_null, value_key
from .semantic_types import infer_datatype
from .units_infer import split_name_tokens

__all__ = ["discover_inds", "name_token_jaccard"]

#: k MinHash lanes for the (high-column-count) containment prefilter signature.
_PREFILTER_K = 32
#: only skip a zero-collision pair when the probability a true positive would have
#: produced zero matching lanes, (1-J_min)^k, is below this — so the MinHash branch
#: has no false negatives across the property fuzz (tests/profiling/test_perf_inds.py).
_PREFILTER_FALSE_SKIP_EPS = 1e-9
#: MEASURE-FIRST (bench.py): the EXACT cardinality containment bound
#: (cov ≤ min(1, |rhs|/|lhs|)) is free, exact, and prunes at EVERY scale — it gates
#: the intersect unconditionally. The per-column MinHash sketch only amortizes once
#: the O(cols²) exact-intersect fan-out dwarfs the O(cols) sketch build, i.e. past
#: ~hundreds of type-compatible columns; below this it is skipped entirely.
_PREFILTER_MIN_COLS = 120

# weights of the composite join-candidate score (documented above; tests pin behavior)
_W_COVERAGE = 0.40
_W_NAME = 0.20
_W_TYPE = 0.15
_W_RHS_UNIQ = 0.25

_GROUP: dict[Datatype, str] = {
    Datatype.INTEGER: "numeric",
    Datatype.FLOAT: "numeric",
    Datatype.STRING: "string",
    Datatype.TEXT: "string",
    Datatype.DATE: "date",
    Datatype.DATETIME: "datetime",
}


def _type_match(a: Datatype, b: Datatype) -> float:
    if a not in _GROUP or b not in _GROUP:
        return 0.0
    if a == b:
        return 1.0
    return 0.6 if _GROUP[a] == _GROUP[b] else 0.0


def _name_tokens(name: str) -> frozenset[str]:
    toks = set()
    for t in split_name_tokens(name):
        toks.add(t[:-1] if len(t) > 3 and t.endswith("s") else t)
    return frozenset(toks)


def name_token_jaccard(a: str, b: str) -> float:
    """Jaccard over snake/camel name tokens with light plural stemming."""
    ta, tb = _name_tokens(a), _name_tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


@dataclass(frozen=True, slots=True)
class _ColumnFacts:
    table: str
    column: str
    dtype: Datatype
    hashes: frozenset[int]
    nonnull: int
    sorted_hashes: np.ndarray   # P2: uint64, ascending — for vectorized intersect
    minhash: tuple[int, ...]    # P2: containment-prefilter signature (k lanes)

    @property
    def distinct(self) -> int:
        return len(self.hashes)

    @property
    def uniqueness(self) -> float:
        return self.distinct / self.nonnull if self.nonnull else 0.0


def _num_columns(table: Any) -> int:
    """Cheap column count for a pyarrow Table / pandas DataFrame / mapping (no
    per-cell normalization — used only to size the prefilter decision)."""
    if hasattr(table, "num_columns"):
        return int(table.num_columns)
    cols = getattr(table, "columns", None)
    if cols is not None and not isinstance(table, Mapping):
        return len(cols)
    if isinstance(table, Mapping):
        return len(table)
    return len(columns_of(table))


def _column_facts(corpus: Mapping[str, Any], *, with_minhash: bool) -> list[_ColumnFacts]:
    """One hashing pass per (table, column). ``with_minhash`` builds the cheap
    containment-prefilter signature (only worth it past ``_PREFILTER_MIN_COLS``)."""
    from .sketches import MinHash  # local import: keep the sketch dep off module load

    facts: list[_ColumnFacts] = []
    for tname in corpus:
        for cname, values in columns_of(corpus[tname]).items():
            nn = [v for v in values if not is_null(v)]
            dtype = infer_datatype(values)
            hashes = frozenset(hash64(value_key(v)) for v in nn)
            sorted_hashes = (
                np.fromiter(hashes, dtype=np.uint64, count=len(hashes))
                if hashes else np.empty(0, np.uint64)
            )
            sorted_hashes.sort()
            # reuse the column's already-computed value hashes as MinHash base
            # fingerprints (no second hashing pass).
            sig = (
                MinHash.signature_from_hashes(sorted_hashes, k=_PREFILTER_K)
                if with_minhash else ()
            )
            facts.append(_ColumnFacts(tname, cname, dtype, hashes, len(nn),
                                      sorted_hashes, sig))
    return facts


def _exact_coverage(lhs: _ColumnFacts, rhs: _ColumnFacts) -> float:
    """|lhs ∩ rhs| / |lhs| over distinct value hashes (P2: sorted-int64 intersect)."""
    inter = np.intersect1d(lhs.sorted_hashes, rhs.sorted_hashes, assume_unique=True)
    return inter.shape[0] / lhs.distinct if lhs.distinct else 0.0


def _passes_prefilter(lhs: _ColumnFacts, rhs: _ColumnFacts, min_coverage: float) -> bool:
    """Cheap containment prefilter: True iff lhs→rhs CAN still reach min_coverage.

    Two stacked tests, both with ZERO false negatives (a surviving exact intersect
    decides the rest):

    1. EXACT cardinality bound (always, free): cov = |A∩B|/|A| ≤ min(1, |B|/|A|).
       If |B| < min_coverage·|A| the pair provably cannot clear the floor — skip.
       This alone is a sound containment lower-bound prefilter at every scale.

    2. MinHash disjointness bound (only when signatures are present, i.e. at high
       column count): if a true coverage ≥ floor held, the value sets would share
       ≥ floor·|A| ≥ 2 distinct values, giving Jaccard J ≥ J_min and an EXPECTED
       matching-lane count k·J_min. We skip only when ZERO lanes match AND the
       Chernoff bound Pr[0 matches | J ≥ J_min] = (1-J_min)^k is below 1e-9 — so
       a true positive is essentially never pruned here, and never on the fuzz.
    """
    dA, dB = lhs.distinct, rhs.distinct
    if dA == 0:
        return True
    if dB < min_coverage * dA:               # exact cardinality bound — cannot reach floor
        return False
    if not lhs.minhash or not rhs.minhash:   # no sketch (low column count): cardinality only
        return True
    matches = sum(1 for x, y in zip(lhs.minhash, rhs.minhash) if x == y)
    if matches > 0:
        return True                          # any lane collision → keep (be conservative)
    # zero matching lanes. A true coverage ≥ floor forces |A∩B| ≥ ceil(floor·dA) ≥ 1,
    # hence J ≥ inter/(dA+dB-inter); use the smallest such J to bound the false-skip risk.
    inter_min = max(1, math.ceil(min_coverage * dA))
    j_min = inter_min / (dA + dB - inter_min)
    false_skip_prob = (1.0 - j_min) ** _PREFILTER_K
    return false_skip_prob > _PREFILTER_FALSE_SKIP_EPS  # keep unless a missed TP is impossible


def discover_inds(
    corpus: Mapping[str, Any],
    *,
    min_coverage: float = 0.95,
    min_distinct: int = 2,
) -> list[IND]:
    """§11.2 M3 interface: `discover_inds(corpus) -> [IND, score]`.

    `corpus` maps table name -> pyarrow Table / pandas DataFrame / column mapping.
    Returns contracts.IND sorted by descending score (then names, deterministic).

    P2: each ordered pair is gated by a cheap containment prefilter
    (:func:`_passes_prefilter` — exact cardinality bound always, plus a
    provably-safe MinHash disjointness skip at high column count) BEFORE the exact
    sorted-int64 ``np.intersect1d`` coverage. Pairs that provably cannot clear
    ``min_coverage`` are skipped. The prefilter has no false negatives, so the
    emitted IND set is byte-identical to the unfiltered reference (property-tested).
    """
    # Build the containment-prefilter sketches only when the column count makes the
    # O(cols²) exact-intersect fan-out the bottleneck (MEASURE-FIRST: below the
    # threshold the per-column sketch build costs more than the pairs it skips).
    n_cols = sum(_num_columns(corpus[t]) for t in corpus)
    with_minhash = n_cols >= _PREFILTER_MIN_COLS
    facts = [
        f for f in _column_facts(corpus, with_minhash=with_minhash)
        if f.dtype is not Datatype.BOOLEAN and f.distinct >= min_distinct
    ]
    out: list[IND] = []
    for lhs in facts:
        for rhs in facts:
            if lhs is rhs or (lhs.table == rhs.table and lhs.column == rhs.column):
                continue
            tm = _type_match(lhs.dtype, rhs.dtype)
            if tm == 0.0:
                continue
            if not _passes_prefilter(lhs, rhs, min_coverage):
                continue  # P2 prefilter: cannot reach the floor — skip exact intersect
            coverage = _exact_coverage(lhs, rhs)
            if coverage < min_coverage:
                continue
            score = (
                _W_COVERAGE * coverage
                + _W_NAME * name_token_jaccard(lhs.column, rhs.column)
                + _W_TYPE * tm
                + _W_RHS_UNIQ * rhs.uniqueness
            )
            out.append(IND(
                lhs_table=lhs.table, lhs_column=lhs.column,
                rhs_table=rhs.table, rhs_column=rhs.column,
                coverage=round(coverage, 4), score=round(score, 4),
            ))
    out.sort(key=lambda i: (-i.score, i.lhs_table, i.lhs_column, i.rhs_table, i.rhs_column))
    return out
