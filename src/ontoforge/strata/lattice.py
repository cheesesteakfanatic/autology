"""Iceberg concept-lattice construction (whitepaper §3.4.2 item 1).

Close-by-One (CbO) enumeration of formal concepts restricted to extent support
>= sigma (the iceberg restriction — TITANIC/CHARM-class pruning is sound here
because extents shrink monotonically down the CbO search tree). Each concept
carries:

- extent / intent (intent re-expanded against the ORIGINAL context, so concept
  identity is independent of attribute clarification/reduction),
- support, the §3.4.3 stability index (exact subset enumeration for extents
  <= 12 objects, seeded-sampling approximation above),
- parent/child covering links of the lattice order
  (A1,B1) <= (A2,B2)  iff  A1 ⊆ A2  (equivalently B2 ⊆ B1).

Hub candidates (G-join) bypass sigma: their object concepts are force-included
and flagged, so rare-but-real reference types survive the iceberg cut and get
explicit spine review instead (§3.4 failure-mode (b) mitigation).
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from itertools import combinations
from typing import Iterable, Optional, Sequence

from .context import FormalContext, intent_hash_of

__all__ = ["Concept", "ConceptLattice", "build_lattice", "stability"]

#: extent size above which stability switches to the sampling approximation
STABILITY_EXACT_LIMIT = 12
#: number of sampled subsets for the approximation (seeded per concept)
STABILITY_SAMPLES = 2048


@dataclass
class Concept:
    """One formal concept (A, B) of the iceberg lattice B_sigma(K)."""

    extent: frozenset[str]
    intent: frozenset[str]            # FULL intent in the original context
    intent_hash: str
    support: int
    stability: float = 0.0
    parents: tuple[str, ...] = ()     # intent hashes of covering superconcepts
    children: tuple[str, ...] = ()    # intent hashes of covered subconcepts
    bypass: bool = False              # kept below sigma via the hub bypass

    def leq(self, other: "Concept") -> bool:
        """Lattice order: (A1,B1) <= (A2,B2) iff A1 ⊆ A2."""
        return self.extent <= other.extent


@dataclass
class ConceptLattice:
    sigma: int
    concepts: dict[str, Concept] = field(default_factory=dict)   # intent_hash -> Concept

    def __len__(self) -> int:
        return len(self.concepts)

    def __contains__(self, intent_hash: str) -> bool:
        return intent_hash in self.concepts

    def get(self, intent_hash: str) -> Optional[Concept]:
        return self.concepts.get(intent_hash)

    def by_intent(self, intent: Iterable[str]) -> Optional[Concept]:
        return self.concepts.get(intent_hash_of(intent))

    def object_concept(self, ctx: FormalContext, cid: str) -> Optional[Concept]:
        """gamma(g) = ({g}'', {g}') — the most specific concept containing g."""
        return self.by_intent(ctx.objects[cid])

    def ancestors(self, intent_hash: str) -> set[str]:
        out: set[str] = set()
        stack = list(self.concepts[intent_hash].parents)
        while stack:
            h = stack.pop()
            if h in out:
                continue
            out.add(h)
            stack.extend(self.concepts[h].parents)
        return out

    def roots(self) -> list[Concept]:
        return [c for c in self.concepts.values() if not c.parents]

    def top_down(self) -> list[Concept]:
        """Deterministic admission order: decreasing support, then smaller
        intents first, then intent hash."""
        return sorted(
            self.concepts.values(),
            key=lambda c: (-c.support, len(c.intent), c.intent_hash),
        )

    def recompute_covers(self) -> None:
        """(Re)derive the covering relation from extent inclusion. O(n^2)
        candidate edges + intermediate filtering; n is iceberg-bounded."""
        items = list(self.concepts.values())
        for c in items:
            uppers = [p for p in items if p is not c and c.extent < p.extent]
            covers = [
                p for p in uppers
                if not any(z is not p and c.extent < z.extent < p.extent for z in uppers)
            ]
            c.parents = tuple(sorted(p.intent_hash for p in covers))
        children: dict[str, list[str]] = {h: [] for h in self.concepts}
        for c in items:
            for p in c.parents:
                children[p].append(c.intent_hash)
        for h, kids in children.items():
            self.concepts[h].children = tuple(sorted(kids))


# ---------------------------------------------------------------------------
# stability (the standard FCA concept-stability measure, §3.4.3)
# ---------------------------------------------------------------------------


def stability(ctx: FormalContext, extent: frozenset[str], intent: frozenset[str]) -> float:
    """Intensional stability: fraction of extent subsets S with S' = B.

    Exact enumeration for |A| <= STABILITY_EXACT_LIMIT; otherwise the standard
    Monte-Carlo approximation with an RNG seeded from the intent hash, so the
    estimate is deterministic AND independent of candidate input order."""
    objs = sorted(extent)
    n = len(objs)
    if n == 0:
        return 0.0
    if n <= STABILITY_EXACT_LIMIT:
        hits = 0
        for r in range(n + 1):
            for combo in combinations(objs, r):
                if ctx.prime_objects(combo) == intent:
                    hits += 1
        return hits / (1 << n)
    rng = random.Random(int(intent_hash_of(intent), 16) & 0x7FFFFFFF)
    hits = 0
    for _ in range(STABILITY_SAMPLES):
        subset = [g for g in objs if rng.random() < 0.5]
        if ctx.prime_objects(subset) == intent:
            hits += 1
    return hits / STABILITY_SAMPLES


# ---------------------------------------------------------------------------
# CbO with iceberg pruning
# ---------------------------------------------------------------------------


def _enumerate_closed_extents(
    ctx: FormalContext, sigma: int
) -> dict[frozenset[str], frozenset[str]]:
    """All closed (extent, reduced-intent) pairs with |extent| >= sigma via
    Close-by-One over the clarified+reduced attribute set."""
    reps = ctx.reduced_attributes()
    ext = {a: ctx.attr_extent(a) for a in reps}
    out: dict[frozenset[str], frozenset[str]] = {}

    def rep_closure(extent: frozenset[str]) -> frozenset[str]:
        return frozenset(a for a in reps if extent <= ext[a])

    def process(extent: frozenset[str], rintent: frozenset[str], j: int) -> None:
        out[extent] = rintent
        for k in range(j, len(reps)):
            a = reps[k]
            if a in rintent:
                continue
            extent2 = extent & ext[a]
            if len(extent2) < sigma:
                continue  # iceberg pruning: extents only shrink below here
            rintent2 = rep_closure(extent2)
            # canonicity: no attribute earlier than a may newly appear
            if any(b in rintent2 and b not in rintent for b in reps[:k]):
                continue
            process(extent2, rintent2, k + 1)

    top_extent = ctx.all_objects
    if len(top_extent) >= sigma:
        process(top_extent, rep_closure(top_extent), 0)
    return out


def build_lattice(
    ctx: FormalContext,
    sigma: int = 1,
    bypass_objects: Sequence[str] = (),
) -> ConceptLattice:
    """Build the iceberg lattice B_sigma(K).

    ``bypass_objects`` are object ids (G-join hub candidates) whose object
    concepts are force-included even when their support falls below sigma.
    """
    if sigma < 1:
        raise ValueError("sigma must be >= 1")
    lat = ConceptLattice(sigma=sigma)
    closed = _enumerate_closed_extents(ctx, sigma)

    extents = set(closed)
    bypass_extents: set[frozenset[str]] = set()
    for cid in bypass_objects:
        if cid not in ctx.objects:
            continue
        a = ctx.closure_objects([cid])
        if a not in extents:
            bypass_extents.add(a)
            extents.add(a)

    for extent in extents:
        intent = ctx.prime_objects(extent)  # FULL original intent
        ih = intent_hash_of(intent)
        lat.concepts[ih] = Concept(
            extent=extent,
            intent=intent,
            intent_hash=ih,
            support=len(extent),
            stability=stability(ctx, extent, intent),
            bypass=extent in bypass_extents,
        )
    lat.recompute_covers()
    return lat
