"""Pre-optimization reference algorithms (the "before" of the before/after).

These are the verbatim pure-python implementations the engine-speed wave
replaced, kept here so :mod:`ontoforge.profiling.bench` measures a real
before/after and so ``tests/profiling/`` can assert the optimized paths are
BYTE-IDENTICAL to them on random inputs. Nothing in the shipped pipeline imports
this module — it exists purely as the determinism oracle + benchmark baseline.

* ``discover_fds_ref`` — TANE with ``Counter``-per-class violations and the
  dict-bucketize ``partition_product`` (the public reference primitives in
  :mod:`ontoforge.profiling.fds` are reused for stripped_partition /
  partition_product / g3 so this stays a faithful copy of the old hot loop).
* ``discover_inds_ref`` — the original O(cols²) ``frozenset`` intersection with
  NO containment prefilter.
* ``RefMinHash`` — the original k-hash-passes-per-value MinHash.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Mapping, Optional

from ontoforge.contracts import FD, IND, Datatype

from ._values import columns_of, hash64, is_null, value_key
from .fds import (
    Partition,
    _padded_columns,
    _row_keys,
    partition_product,
    stripped_partition,
)
from .inds import (
    _GROUP,  # noqa: F401  (kept for parity with the optimized module's grouping)
    _type_match,
    _W_COVERAGE,
    _W_NAME,
    _W_RHS_UNIQ,
    _W_TYPE,
    name_token_jaccard,
)
from .semantic_types import infer_datatype

_U64 = (1 << 64) - 1


# ------------------------------------------------------------------ FD reference


def _violations_ref(lhs_partition: Partition, rhs_keys) -> int:
    v = 0
    for cls in lhs_partition:
        counts = Counter(rhs_keys[r] for r in cls)
        v += len(cls) - max(counts.values())
    return v


def discover_fds_ref(
    data: Any,
    table: str,
    *,
    max_lhs: int = 3,
    approx_threshold: float = 0.98,
    max_rows: Optional[int] = None,
) -> list[FD]:
    """The pre-P1 TANE search: Counter-per-class violations + dict-bucketize product."""
    columns = columns_of(data)
    cols_map, n = _padded_columns(columns, max_rows)
    cols = list(cols_map)
    if n == 0 or not cols:
        return []

    keys = {c: _row_keys(cols_map[c]) for c in cols}
    parts: dict[frozenset[str], Partition] = {frozenset(): ((tuple(range(n)),) if n > 1 else ())}
    for c in cols:
        parts[frozenset((c,))] = stripped_partition(keys[c])

    allcols = frozenset(cols)
    cplus: dict[frozenset[str], set[str]] = {frozenset(): set(cols)}
    exact: list[FD] = []
    approx: list[FD] = []
    covered: list[tuple[frozenset[str], str]] = []

    level: list[tuple[str, ...]] = [(c,) for c in sorted(cols)]
    size = 1
    while level and size <= max_lhs + 1:
        for t in level:
            x = frozenset(t)
            cp = set(allcols)
            for a in t:
                cp &= cplus.get(x - {a}, allcols)
            cplus[x] = cp
        for t in level:
            x = frozenset(t)
            for a in sorted(x & cplus[x]):
                lhs = x - {a}
                viol = _violations_ref(parts[lhs], keys[a])
                if viol == 0:
                    lhs_t = tuple(sorted(lhs))
                    exact.append(FD(table=table, lhs=lhs_t, rhs=a, confidence=1.0))
                    covered.append((lhs, a))
                    cplus[x].discard(a)
                    for b in allcols - x:
                        cplus[x].discard(b)
                else:
                    conf = 1.0 - viol / n
                    if conf >= approx_threshold and not any(
                        rhs == a and prev <= lhs for prev, rhs in covered
                    ):
                        approx.append(FD(table=table, lhs=tuple(sorted(lhs)), rhs=a,
                                         confidence=round(conf, 4)))
                        covered.append((lhs, a))
        if size == max_lhs + 1:
            break
        survivors = [t for t in level if cplus[frozenset(t)]]
        present = {frozenset(t) for t in survivors}
        nxt: list[tuple[str, ...]] = []
        by_prefix: dict[tuple[str, ...], list[tuple[str, ...]]] = {}
        for t in survivors:
            by_prefix.setdefault(t[:-1], []).append(t)
        for group in by_prefix.values():
            group.sort()
            for i in range(len(group)):
                for j in range(i + 1, len(group)):
                    z = group[i] + (group[j][-1],)
                    zf = frozenset(z)
                    if not all((zf - {a}) in present for a in z):
                        continue
                    parts[zf] = partition_product(
                        parts[frozenset(group[i])], parts[frozenset(group[j])], n
                    )
                    nxt.append(z)
        level = sorted(nxt)
        size += 1

    out = exact + approx
    out.sort(key=lambda f: (round(1.0 - f.confidence, 6), len(f.lhs), f.lhs, f.rhs))
    return out


# ----------------------------------------------------------------- IND reference


class _RefColFacts:
    __slots__ = ("table", "column", "dtype", "hashes", "nonnull")

    def __init__(self, table, column, dtype, hashes, nonnull):
        self.table, self.column, self.dtype = table, column, dtype
        self.hashes, self.nonnull = hashes, nonnull

    @property
    def distinct(self) -> int:
        return len(self.hashes)

    @property
    def uniqueness(self) -> float:
        return self.distinct / self.nonnull if self.nonnull else 0.0


def discover_inds_ref(
    corpus: Mapping[str, Any],
    *,
    min_coverage: float = 0.95,
    min_distinct: int = 2,
) -> list[IND]:
    """The pre-P2 discovery: full frozenset intersection over every pair, no prefilter."""
    facts: list[_RefColFacts] = []
    for tname in corpus:
        for cname, values in columns_of(corpus[tname]).items():
            nn = [v for v in values if not is_null(v)]
            dtype = infer_datatype(values)
            hashes = frozenset(hash64(value_key(v)) for v in nn)
            facts.append(_RefColFacts(tname, cname, dtype, hashes, len(nn)))
    facts = [f for f in facts if f.dtype is not Datatype.BOOLEAN and f.distinct >= min_distinct]
    out: list[IND] = []
    for lhs in facts:
        for rhs in facts:
            if lhs is rhs or (lhs.table == rhs.table and lhs.column == rhs.column):
                continue
            tm = _type_match(lhs.dtype, rhs.dtype)
            if tm == 0.0:
                continue
            coverage = len(lhs.hashes & rhs.hashes) / len(lhs.hashes)
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


# ------------------------------------------------------------- MinHash reference


class RefMinHash:
    """The pre-P3 MinHash: k independent xxhash64 seeds (seed+i for lane i)."""

    def __init__(self, k: int = 64, seed: int = 0) -> None:
        if k < 1:
            raise ValueError("k must be >= 1")
        self._k = k
        self._seed = seed
        self._sig = [_U64] * k
        self._seen: set[int] = set()
        self._empty = True

    def add(self, key: str) -> None:
        fp = hash64(key, seed=self._seed ^ 0x9E3779B97F4A7C15)
        if fp in self._seen:
            return
        self._seen.add(fp)
        self._empty = False
        sig = self._sig
        seed0 = self._seed
        for i in range(self._k):
            h = hash64(key, seed=seed0 + i)
            if h < sig[i]:
                sig[i] = h

    def add_all(self, keys) -> None:
        for k in keys:
            self.add(k)

    def signature(self) -> tuple[int, ...]:
        if self._empty:
            return ()
        return tuple(self._sig)
