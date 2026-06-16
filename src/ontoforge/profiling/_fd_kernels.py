"""Integer-coded numpy kernels for TANE stripped-partition refinement (P1).

The pure-python reference lives in :mod:`ontoforge.profiling.fds`
(:func:`stripped_partition`, :func:`partition_product`, :func:`g3_confidence`)
and is mirrored in :mod:`ontoforge.profiling._bench_ref`. This module is the
FAST PATH: every per-row equality key is factorized to an ``int32`` code ONCE,
then the level-wise lattice search runs over those codes with fully vectorized
``argsort`` segment scans + composite-key counting — no per-class Python loop,
no ``Counter``, no dict bucketize.

Equivalence contract (``tests/profiling/test_perf_fds.py`` asserts it on random
tables; the existing ``tests/m3/test_fds.py`` pins fixture behavior):

* :func:`violations_coded` (the g3 numerator) is BYTE-IDENTICAL to the reference
  ``_violations`` — per stripped class it sums ``len(cls) - max(per-value count)``.
* :func:`is_empty` matches the reference "every class a singleton" test, so the
  FD-holds and candidate-key decisions are identical.
* a stripped partition (:class:`CodedPartition`) is membership-identical to the
  reference partition. :func:`stripped_partition_coded` additionally preserves the
  reference's first-occurrence class order + ascending rows; the *product* kernel
  preserves class MEMBERSHIP (the only thing :func:`violations_coded`,
  :func:`is_empty`, and deeper products depend on) — both yield byte-identical FDs.

Numba is OPTIONAL: when importable, the inner-loop violations fallback compiles;
otherwise the pure-numpy implementation (the default below) runs and is already
fully vectorized. A numba import/compile failure only changes speed, never
results — preserving the keyless / no-required-dependency guarantee.
"""

from __future__ import annotations

from typing import Sequence

import numpy as np

try:  # optional acceleration; the pure-numpy path below is byte-identical
    import numba  # type: ignore  # noqa: F401

    _HAVE_NUMBA = True
except Exception:  # pragma: no cover - exercised only when numba is absent
    _HAVE_NUMBA = False


__all__ = [
    "CodedPartition",
    "codes_of",
    "full_partition",
    "stripped_partition_coded",
    "partition_product_coded",
    "violations_coded",
    "is_empty",
    "to_tuple",
    "HAVE_NUMBA",
]

HAVE_NUMBA = _HAVE_NUMBA


class CodedPartition:
    """A stripped partition over row indices, stored as flat int arrays.

    ``rows`` concatenates the row indices of every (non-singleton) class;
    ``offsets`` has ``n_classes+1`` entries with class ``c`` =
    ``rows[offsets[c]:offsets[c+1]]``. An empty partition is ``rows=[]``,
    ``offsets=[0]``. ``class_of`` (lazily built) maps row index → class id, or -1.
    """

    __slots__ = ("rows", "offsets")

    def __init__(self, rows: np.ndarray, offsets: np.ndarray) -> None:
        self.rows = rows
        self.offsets = offsets

    @property
    def n_classes(self) -> int:
        return len(self.offsets) - 1


def codes_of(keys: Sequence[str]) -> np.ndarray:
    """Factorize per-row equality keys to dense ``int32`` codes (first-occurrence).

    Uses ``pandas.factorize`` (C-level on object arrays) when available — codes are
    already assigned in first-occurrence order there — falling back to a numpy
    ``unique`` remap. All keys are canonical strings (nulls collapsed upstream).
    """
    n = len(keys)
    if n == 0:
        return np.empty(0, dtype=np.int32)
    try:
        import pandas as pd  # noqa: PLC0415 - optional fast path

        codes, _ = pd.factorize(np.asarray(keys, dtype=object), use_na_sentinel=False)
        return codes.astype(np.int32, copy=False)
    except Exception:  # pragma: no cover - pandas always present in this project
        arr = np.asarray(keys, dtype=object)
        _, first_idx, inverse = np.unique(arr, return_index=True, return_inverse=True)
        inverse = inverse.reshape(-1)
        order = np.argsort(first_idx, kind="stable")
        remap = np.empty(order.shape[0], dtype=np.int32)
        remap[order] = np.arange(order.shape[0], dtype=np.int32)
        return remap[inverse].astype(np.int32, copy=False)


def full_partition(n_rows: int) -> CodedPartition:
    """π*(∅): one class of all rows when n>1, else empty (matches the reference)."""
    if n_rows <= 1:
        return CodedPartition(np.empty(0, dtype=np.int64), np.zeros(1, dtype=np.int64))
    return CodedPartition(
        np.arange(n_rows, dtype=np.int64),
        np.array([0, n_rows], dtype=np.int64),
    )


def _classes_from_sorted_keys(
    sort_keys: np.ndarray, rows_sorted: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """Given a sort key (class label) per element and the matching rows, both already
    sorted by (key, row), return stripped (rows, offsets): runs of length >= 2.
    """
    n = sort_keys.shape[0]
    if n == 0:
        return np.empty(0, dtype=np.int64), np.zeros(1, dtype=np.int64)
    change = np.ones(n, dtype=bool)
    np.not_equal(sort_keys[1:], sort_keys[:-1], out=change[1:])
    starts = np.flatnonzero(change)
    lengths = np.diff(np.append(starts, n))
    keep = lengths >= 2
    starts, lengths = starts[keep], lengths[keep]
    if starts.shape[0] == 0:
        return np.empty(0, dtype=np.int64), np.zeros(1, dtype=np.int64)
    offsets = np.empty(starts.shape[0] + 1, dtype=np.int64)
    offsets[0] = 0
    np.cumsum(lengths, out=offsets[1:])
    # gather the surviving runs in one vectorized take
    seg_starts = np.repeat(starts, lengths)
    within = np.arange(seg_starts.shape[0], dtype=np.int64) - np.repeat(offsets[:-1], lengths)
    rows = rows_sorted[seg_starts + within].astype(np.int64, copy=False)
    return rows, offsets


def stripped_partition_coded(codes: np.ndarray) -> CodedPartition:
    """π*: equivalence classes (row-index runs) with singletons stripped.

    Class order == first-occurrence code order; rows ascend within a class
    (stable argsort on codes keeps rows ascending within ties).
    """
    codes = np.ascontiguousarray(codes, dtype=np.int64)
    n = codes.shape[0]
    if n == 0:
        return CodedPartition(np.empty(0, dtype=np.int64), np.zeros(1, dtype=np.int64))
    order = np.argsort(codes, kind="stable")
    rows, offsets = _classes_from_sorted_keys(codes[order], order)
    return CodedPartition(rows, offsets)


def partition_product_coded(p1: CodedPartition, p2: CodedPartition, n_rows: int) -> CodedPartition:
    """π*(X)·π*(Y): refine p2's classes by p1's class id (TANE probe product).

    Fully vectorized: rows present in BOTH partitions get a composite label
    ``p2_class * (max_p1_class+1) + p1_class``; runs of size >= 2 over the
    composite-sorted rows are the product classes. The class MEMBERSHIP is
    identical to the reference :func:`partition_product` (class *order* may differ,
    which is immaterial — :func:`violations_coded`, :func:`is_empty`, and deeper
    products all depend on membership only). Rows ascend within each class.
    """
    nc1, nc2 = p1.n_classes, p2.n_classes
    if nc1 == 0 or nc2 == 0:
        return CodedPartition(np.empty(0, dtype=np.int64), np.zeros(1, dtype=np.int64))
    c1_of = np.full(n_rows, -1, dtype=np.int64)
    c1_of[p1.rows] = np.repeat(np.arange(nc1, dtype=np.int64), np.diff(p1.offsets))
    rows2 = p2.rows
    c2_of = np.repeat(np.arange(nc2, dtype=np.int64), np.diff(p2.offsets))
    pc = c1_of[rows2]
    mask = pc >= 0
    if not mask.any():
        return CodedPartition(np.empty(0, dtype=np.int64), np.zeros(1, dtype=np.int64))
    rows2 = rows2[mask]
    composite = c2_of[mask] * np.int64(nc1) + pc[mask]
    order = np.lexsort((rows2, composite))  # primary composite, secondary row asc
    rows, offsets = _classes_from_sorted_keys(composite[order], rows2[order])
    return CodedPartition(rows, offsets)


if _HAVE_NUMBA:  # pragma: no cover - only when numba is installed
    from numba import njit as _njit

    @_njit(cache=True, nogil=True)
    def _violations_numba(rows, offsets, rhs_codes, max_code):
        total = 0
        counts = np.zeros(max_code, dtype=np.int64)
        touched = np.empty(rows.shape[0], dtype=np.int64)
        for c in range(offsets.shape[0] - 1):
            start = offsets[c]
            end = offsets[c + 1]
            best = 0
            nt = 0
            for k in range(start, end):
                code = rhs_codes[rows[k]]
                counts[code] += 1
                if counts[code] == 1:
                    touched[nt] = code
                    nt += 1
                if counts[code] > best:
                    best = counts[code]
            total += (end - start) - best
            for t in range(nt):
                counts[touched[t]] = 0
        return total


def _violations_numpy(part: CodedPartition, rhs_codes: np.ndarray, max_code: int) -> int:
    """Fully vectorized g3 numerator: per class, len - max(per-value count).

    Build a composite key ``class_id * max_code + rhs_code`` over the partition's
    rows (in class order); count identical composites; the per-class maximum count
    is a segment-max over classes; sum ``class_size - max_count``.
    """
    rows = part.rows
    nc = part.n_classes
    sizes = np.diff(part.offsets)
    class_id = np.repeat(np.arange(nc, dtype=np.int64), sizes)
    rc = rhs_codes[rows].astype(np.int64, copy=False)
    composite = class_id * np.int64(max_code) + rc
    composite.sort()
    # counts of each (class, value) composite
    change = np.ones(composite.shape[0], dtype=bool)
    np.not_equal(composite[1:], composite[:-1], out=change[1:])
    starts = np.flatnonzero(change)
    counts = np.diff(np.append(starts, composite.shape[0]))
    # which class each composite-run belongs to
    run_class = composite[starts] // np.int64(max_code)
    # per-class max count via reduceat over runs grouped by class (runs are sorted
    # by composite ⇒ by class, then value, so class groups are contiguous)
    cls_change = np.ones(run_class.shape[0], dtype=bool)
    np.not_equal(run_class[1:], run_class[:-1], out=cls_change[1:])
    cls_starts = np.flatnonzero(cls_change)
    max_per_class = np.maximum.reduceat(counts, cls_starts)
    return int(sizes.sum() - max_per_class.sum())


def violations_coded(part: CodedPartition, rhs_codes: np.ndarray, max_code: int) -> int:
    """g3 numerator over a coded partition (rows to delete for lhs -> rhs to hold)."""
    if part.offsets.shape[0] <= 1 or max_code == 0:
        return 0
    rhs_codes = np.ascontiguousarray(rhs_codes, dtype=np.int64)
    if _HAVE_NUMBA:  # pragma: no cover - numba path
        rows = np.ascontiguousarray(part.rows, dtype=np.int64)
        offsets = np.ascontiguousarray(part.offsets, dtype=np.int64)
        return int(_violations_numba(rows, offsets, rhs_codes, int(max_code)))
    return _violations_numpy(part, rhs_codes, int(max_code))


def is_empty(part: CodedPartition) -> bool:
    """True iff every class is a singleton (key / FD-holds test)."""
    return part.offsets.shape[0] <= 1


def to_tuple(part: CodedPartition) -> tuple[tuple[int, ...], ...]:
    """Recover a ``Partition`` tuple form (parity helper; order matches the kernel)."""
    rows = part.rows
    off = part.offsets
    return tuple(tuple(int(x) for x in rows[off[c] : off[c + 1]]) for c in range(part.n_classes))
