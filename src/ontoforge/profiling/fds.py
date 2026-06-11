"""Functional dependencies, approximate FDs, and candidate keys (whitepaper §3.1, AMD-0003).

AMD-0003 replaces §3.1's HyFD-style hybrid with exact TANE-class partition
refinement for the v0 baseline: stripped partitions per attribute set, partition
products up the lattice, level-wise candidate generation with TANE's C+ rhs
pruning. HyFD remains a registered challenger (§19.1).

Definitions
-----------
stripped partition π*(X)
    Equivalence classes of rows agreeing on X, singleton classes removed.
    X -> A holds exactly iff every class of π*(X) is constant on A
    (zero g3 violations).
g3 confidence
    1 - (min #rows to delete so X -> A holds) / n. Per class of π*(X) the
    non-majority A-rows are violations; stripped singletons never violate.
    An FD with confidence in [approx_threshold, 1) — default 0.98 — is emitted
    as an approximate FD. Supersets of an already-emitted (lhs, rhs) pair are
    suppressed (lhs-minimality, level-wise ascent makes subsets come first).
nulls
    All NULLs are one value (two NULL rows agree on the column). For *keys*
    the SQL entity-integrity rule applies instead: a column containing any
    null is excluded from the key search.
empty lhs
    FD () -> A means "A is (near-)constant". It is genuine minimal output of
    the lattice search (level-1 test with the trivial one-class partition).

Caps per the M3 brief: lhs size <= `max_lhs` (3), candidate keys <= `max_key_size` (2).
Canonical form: FD lhs tuples and multi-column keys are sorted alphabetically.
"""

from __future__ import annotations

from collections import Counter
from itertools import combinations
from typing import Any, Mapping, Optional, Sequence

from ontoforge.contracts import FD

from ._values import columns_of, is_null, value_key

__all__ = [
    "discover_fds",
    "candidate_keys",
    "stripped_partition",
    "partition_product",
    "g3_confidence",
]

_NULL_KEY = "\x00NULL"

Partition = tuple[tuple[int, ...], ...]


def _row_keys(values: Sequence[Any]) -> list[str]:
    """Canonical per-row equality keys; all nulls collapse to one key."""
    return [_NULL_KEY if is_null(v) else value_key(v) for v in values]


def stripped_partition(keys: Sequence[str]) -> Partition:
    """π*: equivalence classes (row-index tuples) with singletons stripped.

    Class order is first-occurrence order — deterministic for a given input.
    """
    groups: dict[str, list[int]] = {}
    for i, k in enumerate(keys):
        groups.setdefault(k, []).append(i)
    return tuple(tuple(g) for g in groups.values() if len(g) > 1)


def partition_product(p1: Partition, p2: Partition, n_rows: int) -> Partition:
    """π*(X) · π*(Y) = π*(X ∪ Y), the standard TANE probe-table product."""
    probe = [-1] * n_rows
    for ci, cls in enumerate(p1):
        for r in cls:
            probe[r] = ci
    out: list[tuple[int, ...]] = []
    for cls in p2:
        buckets: dict[int, list[int]] = {}
        for r in cls:
            ci = probe[r]
            if ci >= 0:
                buckets.setdefault(ci, []).append(r)
        for rows in buckets.values():
            if len(rows) > 1:
                out.append(tuple(rows))
    return tuple(out)


def _violations(lhs_partition: Partition, rhs_keys: Sequence[str]) -> int:
    """g3 numerator: rows that must be deleted for lhs -> rhs to hold."""
    v = 0
    for cls in lhs_partition:
        counts = Counter(rhs_keys[r] for r in cls)
        v += len(cls) - max(counts.values())
    return v


def g3_confidence(lhs_partition: Partition, rhs_keys: Sequence[str], n_rows: int) -> float:
    if n_rows == 0:
        return 1.0
    return 1.0 - _violations(lhs_partition, rhs_keys) / n_rows


# ----------------------------------------------------------------- FD search


def _padded_columns(columns: Mapping[str, list], max_rows: Optional[int]) -> tuple[dict[str, list], int]:
    """Equalize column lengths (missing trailing cells are nulls); optional row cap."""
    n = max((len(v) for v in columns.values()), default=0)
    if max_rows is not None and n > max_rows:
        n = max_rows
    out = {c: (list(v[:n]) + [None] * (n - len(v[:n]))) for c, v in columns.items()}
    return out, n


def _fds_from_columns(
    columns: Mapping[str, list],
    table: str,
    *,
    max_lhs: int = 3,
    approx_threshold: float = 0.98,
    max_rows: Optional[int] = None,
) -> list[FD]:
    cols_map, n = _padded_columns(columns, max_rows)
    cols = list(cols_map)
    if n == 0 or not cols:
        return []

    keys: dict[str, list[str]] = {c: _row_keys(cols_map[c]) for c in cols}
    parts: dict[frozenset[str], Partition] = {frozenset(): ((tuple(range(n)),) if n > 1 else ())}
    for c in cols:
        parts[frozenset((c,))] = stripped_partition(keys[c])

    allcols = frozenset(cols)
    cplus: dict[frozenset[str], set[str]] = {frozenset(): set(cols)}
    exact: list[FD] = []
    approx: list[FD] = []
    covered: list[tuple[frozenset[str], str]] = []  # emitted (lhs, rhs) for AFD minimality

    # level entries as sorted tuples; max node size = max_lhs + 1 (lhs = node minus rhs)
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
                viol = _violations(parts[lhs], keys[a])
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
        # prune nodes whose rhs-candidate set emptied, then prefix-join the next level
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


def discover_fds(
    data: Any,
    table: str,
    *,
    max_lhs: int = 3,
    approx_threshold: float = 0.98,
    max_rows: Optional[int] = None,
) -> list[FD]:
    """§11.2 M3 interface: `discover_fds(table) -> [FD, conf]`.

    `data` is a pyarrow Table, pandas DataFrame, or mapping of column lists.
    Exact FDs carry confidence 1.0; approximate FDs carry g3 confidence in
    [approx_threshold, 1). Output is lhs-minimal and deterministic.
    """
    return _fds_from_columns(
        columns_of(data), table,
        max_lhs=max_lhs, approx_threshold=approx_threshold, max_rows=max_rows,
    )


# ------------------------------------------------------------ candidate keys


def _keys_from_columns(
    columns: Mapping[str, list],
    *,
    max_key_size: int = 2,
    max_rows: Optional[int] = None,
) -> tuple[tuple[str, ...], ...]:
    cols_map, n = _padded_columns(columns, max_rows)
    if n == 0 or not cols_map:
        return ()
    # entity integrity: columns containing nulls cannot participate in a key
    eligible = sorted(c for c, vals in cols_map.items() if not any(is_null(v) for v in vals))
    keys = {c: _row_keys(cols_map[c]) for c in eligible}
    parts = {c: stripped_partition(keys[c]) for c in eligible}

    out: list[tuple[str, ...]] = [(c,) for c in eligible if not parts[c]]
    if max_key_size >= 2:
        rest = [c for c in eligible if parts[c]]  # minimality: skip supersets of unary keys
        for c1, c2 in combinations(rest, 2):
            if not partition_product(parts[c1], parts[c2], n):
                out.append((c1, c2))
    return tuple(out)


def candidate_keys(
    data: Any,
    *,
    max_key_size: int = 2,
    max_rows: Optional[int] = None,
) -> tuple[tuple[str, ...], ...]:
    """Minimal uniqueness column sets, capped at `max_key_size` (brief: 2).

    A set is a key iff its stripped partition is empty (every row distinct).
    Pairs containing a unary key are excluded (minimality); null-bearing
    columns are excluded (entity integrity).
    """
    return _keys_from_columns(columns_of(data), max_key_size=max_key_size, max_rows=max_rows)
