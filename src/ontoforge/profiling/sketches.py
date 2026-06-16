"""Sketch suite for φ(p) — dependency-free real implementations (whitepaper §3.1).

Three sketches, all streaming, all deterministic under a fixed seed:

KLLSketch
    KLL-lite quantile sketch: a stack of fixed-capacity compactors. When a level
    buffer fills it is sorted and a random-offset half is promoted with doubled
    weight (odd leftover stays at the level, so total weight is preserved exactly).
    Rank-error bound: each compaction at level h perturbs any rank by at most
    w_h = 2^h, with a random ± sign per compaction; summing variances over the
    O(n / (k·2^h)) compactions per level gives a standard-deviation rank error of
    O(sqrt(H)·n/k) with H = log2(n/k) levels — for k=256, n=10^4 that is well under
    1% of n, far inside the "≤ 2 deciles" acceptance band. Min/max are tracked
    exactly so q=0 / q=1 are exact.

HyperLogLog
    Standard HLL (Flajolet et al.) with p index bits over 64-bit xxhash values,
    alpha_m bias correction, and linear-counting small-range correction. Registers
    fit in 16 bits comfortably (max rank = 64 - p + 1 ≤ 51). Exact fallback: a real
    hash set is kept until `exact_limit` distinct values, after which the sketch
    degrades gracefully to register estimation (the stored 64-bit hashes replay
    into the registers, so nothing seen before the switch is lost).

MinHash
    Universal-hashing permutation trick (P3): each value is hashed ONCE to a base
    64-bit fingerprint h; lane i applies the affine permutation
    ``π_i(h) = ((a_i·h + b_i) mod P)`` over the Mersenne prime ``P = 2^61 - 1``,
    with ``(a_i, b_i)`` a deterministic per-seed multiplier/offset pair (a_i odd,
    in [1,P)). Signature lane i is the minimum of π_i over the value set. The
    family {π_i} is (approximately) min-wise independent, so
    Pr[sig_a[i] == sig_b[i]] ≈ J(A,B) and contracts.minhash_jaccard (fraction of
    equal lanes) stays an (essentially) unbiased Jaccard estimator with std error
    sqrt(J(1-J)/k) ≤ 0.0625 at k=64. This replaces the old k-hash-passes-per-value
    scheme (which paid 64 xxhash64 calls per distinct value); the signature *bytes*
    differ from that scheme but the Jaccard semantics are preserved (no test pins
    literal MinHash signature values; ColumnProfile.sketch_key omits minhash).
"""

from __future__ import annotations

import math
import random
from typing import Iterable

import numpy as np

from ._values import hash64

__all__ = ["KLLSketch", "HyperLogLog", "MinHash"]

_U64 = (1 << 64) - 1
#: Mersenne prime for the universal-hashing permutation family (fits in int64 math).
_MINHASH_PRIME = (1 << 61) - 1
#: rows per batch in MinHash.add_all (bounds the (chunk × k) permutation matrix)
_MINHASH_CHUNK = 8192


# --------------------------------------------------------------------------- KLL


class KLLSketch:
    """Bounded-memory streaming quantile sketch (KLL-lite compactor stack)."""

    def __init__(self, k: int = 256, seed: int = 0) -> None:
        self._k = max(8, int(k))
        self._levels: list[list[float]] = [[]]
        self._rng = random.Random(seed)
        self._n = 0
        self._min = math.inf
        self._max = -math.inf

    @property
    def n(self) -> int:
        return self._n

    def update(self, v: float) -> None:
        v = float(v)
        if math.isnan(v):
            return
        self._n += 1
        if v < self._min:
            self._min = v
        if v > self._max:
            self._max = v
        self._levels[0].append(v)
        if len(self._levels[0]) >= self._k:
            self._compress()

    def extend(self, values: Iterable[float]) -> None:
        for v in values:
            self.update(v)

    def _compress(self) -> None:
        lvl = 0
        while lvl < len(self._levels) and len(self._levels[lvl]) >= self._k:
            buf = sorted(self._levels[lvl])
            leftover: list[float] = []
            if len(buf) % 2 == 1:
                leftover = [buf.pop()]  # keep odd item at this level: weight preserved
            offset = self._rng.randint(0, 1)
            promoted = buf[offset::2]
            if lvl + 1 == len(self._levels):
                self._levels.append([])
            self._levels[lvl + 1].extend(promoted)
            self._levels[lvl] = leftover
            lvl += 1

    def _weighted_items(self) -> list[tuple[float, int]]:
        items: list[tuple[float, int]] = []
        for lvl, buf in enumerate(self._levels):
            w = 1 << lvl
            items.extend((v, w) for v in buf)
        items.sort(key=lambda t: t[0])
        return items

    def quantile(self, q: float) -> float:
        if self._n == 0:
            raise ValueError("empty sketch")
        q = min(1.0, max(0.0, q))
        if q <= 0.0:
            return self._min
        if q >= 1.0:
            return self._max
        items = self._weighted_items()
        total = sum(w for _, w in items)
        target = q * total
        cum = 0.0
        for v, w in items:
            cum += w
            if cum >= target:
                return min(max(v, self._min), self._max)
        return self._max

    def deciles(self) -> tuple[float, ...]:
        """Eleven values: q = 0.0, 0.1, ..., 1.0 (min and max exact)."""
        if self._n == 0:
            return ()
        return tuple(self.quantile(i / 10) for i in range(11))


# --------------------------------------------------------------------------- HLL


class HyperLogLog:
    """HyperLogLog distinct-count sketch with exact-set fallback for small n."""

    def __init__(self, p: int = 14, exact_limit: int = 4096, seed: int = 0) -> None:
        if not 4 <= p <= 18:
            raise ValueError("p must be in [4, 18]")
        self._p = p
        self._m = 1 << p
        self._seed = seed
        self._regs: list[int] | None = None
        self._exact: set[int] | None = set()
        self._exact_limit = max(0, exact_limit)
        if self._m >= 128:
            self._alpha = 0.7213 / (1.0 + 1.079 / self._m)
        elif self._m == 64:
            self._alpha = 0.709
        elif self._m == 32:
            self._alpha = 0.697
        else:
            self._alpha = 0.673

    def add(self, key: str) -> None:
        h = hash64(key, seed=self._seed)
        if self._exact is not None:
            self._exact.add(h)
            if len(self._exact) > self._exact_limit:
                self._regs = [0] * self._m
                for hv in self._exact:
                    self._update_reg(hv)
                self._exact = None
            return
        assert self._regs is not None
        self._update_reg(h)

    def add_all(self, keys: Iterable[str]) -> None:
        for k in keys:
            self.add(k)

    def _update_reg(self, h: int) -> None:
        assert self._regs is not None
        idx = h >> (64 - self._p)
        w = (h << self._p) & _U64
        if w == 0:
            rank = 64 - self._p + 1
        else:
            rank = 64 - w.bit_length() + 1
        if rank > self._regs[idx]:
            self._regs[idx] = rank

    @property
    def is_exact(self) -> bool:
        return self._exact is not None

    def estimate(self) -> int:
        if self._exact is not None:
            return len(self._exact)
        assert self._regs is not None
        m = self._m
        inv_sum = 0.0
        zeros = 0
        for r in self._regs:
            inv_sum += 2.0 ** (-r)
            if r == 0:
                zeros += 1
        raw = self._alpha * m * m / inv_sum
        if raw <= 2.5 * m and zeros:
            return int(round(m * math.log(m / zeros)))  # linear counting
        return int(round(raw))


# ----------------------------------------------------------------------- MinHash


def _minhash_coeffs(k: int, seed: int) -> tuple[np.ndarray, np.ndarray]:
    """Deterministic per-seed (a, b) affine-permutation coefficients over P=2^61-1.

    a_i ∈ [1, P) is forced odd (gcd with the odd prime is 1), b_i ∈ [0, P). Drawn
    from a seeded RNG so two MinHash(seed=s) instances are byte-identical. a_i is
    kept < 2^31 so the vectorized modmul below stays inside int64.
    """
    rng = np.random.default_rng(seed & _U64)
    a = (rng.integers(1, 1 << 31, size=k, dtype=np.int64) | 1)
    b = rng.integers(0, _MINHASH_PRIME, size=k, dtype=np.int64)
    return a, b


def _shift31_mod(u: np.ndarray) -> np.ndarray:
    """``(u << 31) mod (2^61-1)`` for u < 2^61, staying inside int64.

    With P = 2^61-1, 2^61 ≡ 1 (mod P). Split u = u_hi·2^30 + u_lo (u_hi < 2^31,
    u_lo < 2^30); then u·2^31 = u_hi·2^61 + u_lo·2^31 ≡ u_hi + u_lo·2^31, and
    u_lo·2^31 < 2^61 fits in int64.
    """
    P = _MINHASH_PRIME
    u_hi = u >> 30
    u_lo = u & ((1 << 30) - 1)
    return (u_hi + (u_lo << 31)) % P


def _modmul_perm(fp: np.ndarray, a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Vectorized ``(a*fp + b) mod (2^61-1)`` staying inside int64.

    fp < 2^61, a < 2^31 (see :func:`_minhash_coeffs`). Split fp = hi·2^31 + lo
    (hi < 2^30, lo < 2^31): a·fp = (a·hi)·2^31 + a·lo. a·hi < 2^61 and a·lo < 2^62
    both fit int64; the ·2^31 is folded through :func:`_shift31_mod`.
    Shapes broadcast: fp is (...,1) or (...,), a/b are (k,).
    """
    P = _MINHASH_PRIME
    hi = fp >> 31
    lo = fp & ((1 << 31) - 1)
    t = _shift31_mod((a * hi) % P)        # ((a*hi) mod P) << 31  (mod P)
    return (t + (a * lo) % P + b) % P


class MinHash:
    """k-MinHash signature over a value set (universal-hashing permutation trick).

    Each value is hashed once to a base fingerprint; lane i applies the affine
    permutation ``(a_i·h + b_i) mod (2^61-1)`` and keeps the running minimum.
    """

    def __init__(self, k: int = 64, seed: int = 0) -> None:
        if k < 1:
            raise ValueError("k must be >= 1")
        self._k = k
        self._seed = seed
        self._a, self._b = _minhash_coeffs(k, seed)
        self._sig = np.full(k, _MINHASH_PRIME, dtype=np.int64)  # P is an unreachable sentinel max
        self._seen: set[int] = set()  # dedupe inserts: multiset == set for MinHash
        self._empty = True

    def _base_fp(self, key: str) -> int:
        """One xxhash64 → fingerprint in [0, P) (the permutation domain)."""
        return hash64(key, seed=self._seed ^ 0x9E3779B97F4A7C15) % _MINHASH_PRIME

    def add(self, key: str) -> None:
        fp = self._base_fp(key)
        if fp in self._seen:
            return
        self._seen.add(fp)
        self._empty = False
        # lane i: (a_i*fp + b_i) mod P, vectorized over k lanes; keep per-lane min.
        perm = _modmul_perm(np.int64(fp), self._a, self._b)
        np.minimum(self._sig, perm, out=self._sig)

    def add_all(self, keys: Iterable[str]) -> None:
        """Batched fast path: dedupe fingerprints, then one vectorized lane min.

        Computes ``perm`` as a (distinct × k) int64 matrix via the overflow-safe
        Mersenne modmul, reduced to per-lane minima — O(distinct·k) numpy ops
        instead of k hash passes per value. Identical result to repeated
        :meth:`add` (min is order/multiplicity invariant). Chunked to bound memory.
        """
        fresh: list[int] = []
        seen = self._seen
        for key in keys:
            fp = self._base_fp(key)
            if fp not in seen:
                seen.add(fp)
                fresh.append(fp)
        if not fresh:
            return
        self._empty = False
        a, b = self._a[None, :], self._b[None, :]
        for start in range(0, len(fresh), _MINHASH_CHUNK):
            chunk = np.array(fresh[start : start + _MINHASH_CHUNK], dtype=np.int64)
            perm = _modmul_perm(chunk[:, None], a, b)   # (chunk, k)
            lane_min = perm.min(axis=0)
            np.minimum(self._sig, lane_min, out=self._sig)

    def signature(self) -> tuple[int, ...]:
        """Empty set -> empty tuple (contracts.minhash_jaccard then returns 0.0)."""
        if self._empty:
            return ()
        return tuple(int(x) for x in self._sig)

    @classmethod
    def signature_from_hashes(
        cls, hashes: "np.ndarray", *, k: int = 64, seed: int = 0
    ) -> tuple[int, ...]:
        """k-MinHash signature of a value set given its precomputed 64-bit hashes.

        Lets a caller that already hashed each distinct value (e.g. IND discovery)
        reuse those hashes as the base fingerprints — folded into the permutation
        domain ``mod P`` exactly as :meth:`add` would, then run the same vectorized
        lane minimum. Equivalent to feeding the same value set through
        :meth:`add_all` (same (a, b) family per seed). Used only for the cheap IND
        containment prefilter, never persisted, so the base-hash provenance differs
        from the string path without affecting any pinned signature.
        """
        if hashes.shape[0] == 0:
            return ()
        a, b = _minhash_coeffs(k, seed)
        fps = (hashes.astype(np.uint64) % np.uint64(_MINHASH_PRIME)).astype(np.int64)
        sig = np.full(k, _MINHASH_PRIME, dtype=np.int64)
        for start in range(0, fps.shape[0], _MINHASH_CHUNK):
            chunk = fps[start : start + _MINHASH_CHUNK]
            perm = _modmul_perm(chunk[:, None], a[None, :], b[None, :])
            np.minimum(sig, perm.min(axis=0), out=sig)
        return tuple(int(x) for x in sig)
