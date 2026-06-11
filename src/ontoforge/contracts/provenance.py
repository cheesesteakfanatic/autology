"""The provenance semiring N[X]: polynomial terms over atom variables.

Whitepaper §1.3 constraint (H): every stored fact must have non-zero provenance.
§9: one term, many valuations — citations (which atoms), confidence (min/product),
cost, invalidation (which cells does a changed atom touch), and access control.

Algebra
-------
+ (Sum)  : alternative derivations    — "this value is supported by A OR B"
× (Prod) : joint derivations          — "this value needed A AND B"
ZERO     : no derivation (forbidden in committed state)
ONE      : axiomatic / empty product

Smart constructors normalize: flattening, identity elimination, annihilation.
Terms are immutable and hash-stable; `term_hash` is the interning key used by
M0's shape dictionaries (§4.2) and by AMBER manifests.

Normal form note: we do NOT expand products over sums (that can be exponential);
terms are kept as DAG-ish nested polynomials. Valuations are computed by structural
recursion, which is correct for any commutative semiring without expansion.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable, Protocol, Union

import xxhash

# ---------------------------------------------------------------- term types


@dataclass(frozen=True, slots=True)
class Leaf:
    """A variable: one source atom (by atom_id)."""

    atom_id: str


@dataclass(frozen=True, slots=True)
class _Zero:
    pass


@dataclass(frozen=True, slots=True)
class _One:
    pass


@dataclass(frozen=True, slots=True)
class Sum:
    terms: tuple["ProvTerm", ...]


@dataclass(frozen=True, slots=True)
class Prod:
    terms: tuple["ProvTerm", ...]


ProvTerm = Union[Leaf, _Zero, _One, Sum, Prod]

ZERO: ProvTerm = _Zero()
ONE: ProvTerm = _One()


# ------------------------------------------------------- smart constructors


def prov_sum(terms: Iterable[ProvTerm]) -> ProvTerm:
    """n-ary +, normalized: flatten nested sums, drop ZEROs, collapse empties."""
    flat: list[ProvTerm] = []
    for t in terms:
        if isinstance(t, _Zero):
            continue
        if isinstance(t, Sum):
            flat.extend(t.terms)
        else:
            flat.append(t)
    if not flat:
        return ZERO
    if len(flat) == 1:
        return flat[0]
    return Sum(tuple(flat))


def prov_prod(terms: Iterable[ProvTerm]) -> ProvTerm:
    """n-ary ×, normalized: flatten nested products, drop ONEs, annihilate on ZERO."""
    flat: list[ProvTerm] = []
    for t in terms:
        if isinstance(t, _Zero):
            return ZERO
        if isinstance(t, _One):
            continue
        if isinstance(t, Prod):
            flat.extend(t.terms)
        else:
            flat.append(t)
    if not flat:
        return ONE
    if len(flat) == 1:
        return flat[0]
    return Prod(tuple(flat))


def leaf(atom_id: str) -> ProvTerm:
    return Leaf(atom_id)


# ------------------------------------------------------------------ hashing


def term_hash(t: ProvTerm) -> str:
    """Stable content hash of a term (the interning key)."""
    h = xxhash.xxh3_64()
    _hash_into(t, h)
    return f"{h.intdigest():016x}"


def _hash_into(t: ProvTerm, h: "xxhash.xxh3_64") -> None:
    if isinstance(t, Leaf):
        h.update(b"L")
        h.update(t.atom_id.encode())
    elif isinstance(t, _Zero):
        h.update(b"0")
    elif isinstance(t, _One):
        h.update(b"1")
    elif isinstance(t, Sum):
        h.update(b"S(")
        for s in t.terms:
            _hash_into(s, h)
            h.update(b",")
        h.update(b")")
    else:
        h.update(b"P(")
        for s in t.terms:
            _hash_into(s, h)
            h.update(b",")
        h.update(b")")


# --------------------------------------------------------------- valuation


class Semiring(Protocol):
    """A commutative semiring valuation target (whitepaper §9: one term, many valuations)."""

    def zero(self) -> Any: ...
    def one(self) -> Any: ...
    def plus(self, a: Any, b: Any) -> Any: ...
    def times(self, a: Any, b: Any) -> Any: ...
    def leaf(self, atom_id: str) -> Any: ...


def valuate(t: ProvTerm, s: Semiring) -> Any:
    """Evaluate a term under a semiring (the valuation homomorphism)."""
    if isinstance(t, Leaf):
        return s.leaf(t.atom_id)
    if isinstance(t, _Zero):
        return s.zero()
    if isinstance(t, _One):
        return s.one()
    if isinstance(t, Sum):
        acc = s.zero()
        for sub in t.terms:
            acc = s.plus(acc, valuate(sub, s))
        return acc
    acc = s.one()
    for sub in t.terms:
        acc = s.times(acc, valuate(sub, s))
    return acc


def leaves(t: ProvTerm) -> frozenset[str]:
    """All atom_ids appearing in the term — the citation set / invalidation key set."""
    out: set[str] = set()
    stack: list[ProvTerm] = [t]
    while stack:
        cur = stack.pop()
        if isinstance(cur, Leaf):
            out.add(cur.atom_id)
        elif isinstance(cur, (Sum, Prod)):
            stack.extend(cur.terms)
    return frozenset(out)


def map_leaves(t: ProvTerm, f: Callable[[str], ProvTerm]) -> ProvTerm:
    """Substitute leaves (used for shape abstraction/instantiation in M0 interning)."""
    if isinstance(t, Leaf):
        return f(t.atom_id)
    if isinstance(t, Sum):
        return prov_sum(map_leaves(s, f) for s in t.terms)
    if isinstance(t, Prod):
        return prov_prod(map_leaves(s, f) for s in t.terms)
    return t
