"""Incremental lattice maintenance (whitepaper §3.4.2 item 4, AddIntent-style).

New candidates arrive as deltas; we maintain B_sigma(K) by *object insertion*
(the object-wise dual of AddIntent): adding object g with attribute set
attrs(g)

- updates exactly the concepts whose intent is contained in attrs(g) — that
  set is the order filter of gamma(g), the "affected filter" the whitepaper's
  amortized-cost claim refers to; existing concepts never disappear and their
  intents (hence intent hashes and class URIs) never change;
- creates new concepts only at intersections intent(c) ∩ attrs(g) and
  attrs(h) ∩ attrs(g). With sigma <= 2 this candidate set is provably
  complete (a concept newly crossing the iceberg threshold has extent
  S ∪ {g} with |S| <= 1, so its intent is attrs(h) ∩ attrs(g) for a single
  h); larger sigma would need (sigma-1)-subsets and is documented as a
  limitation in the module README.

Admission decisions for touched concepts are RE-ROUTED through the spine —
never silently applied. Any concept whose applied outcome differs from the
previously recorded state yields a :class:`ChangeProposal` (the future TEMPER
operation, §3.6); proposals are recorded in the ledger as artifacts of kind
``strata.change_proposal``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Optional

from ontoforge.contracts import leaf, prov_sum

from .admission import AdmissionResult, register_evidence_atoms
from .context import FormalContext, intent_hash_of
from .lattice import Concept, ConceptLattice, stability

__all__ = ["ChangeProposal", "insert_object", "diff_admissions"]


@dataclass(frozen=True, slots=True)
class ChangeProposal:
    """A flipped/new admission outcome, proposed (not silently applied) as a
    future TEMPER operation (§3.6)."""

    kind: str             # "add-class" | "retract-class" | "merge-class" | "discard-class"
    intent_hash: str
    previous: str         # previous outcome ("" when the concept is new)
    proposed: str         # new outcome
    rationale: str

    def payload(self) -> str:
        return json.dumps(
            {
                "kind": self.kind,
                "intent_hash": self.intent_hash,
                "previous": self.previous,
                "proposed": self.proposed,
                "rationale": self.rationale,
            },
            sort_keys=True,
        )


def insert_object(
    lattice: ConceptLattice,
    ctx: FormalContext,
    cid: str,
    *,
    bypass: bool = False,
) -> tuple[set[str], set[str]]:
    """Insert object ``cid`` (already added to ``ctx``) into the lattice.

    Returns ``(touched, created)`` intent-hash sets. ``touched`` is exactly
    the order filter of gamma(cid) among pre-existing concepts (their extents
    grew); ``created`` are newly materialized concepts. Cover links are
    recomputed afterwards (cheap at iceberg scale; the *enumeration* work is
    bounded by the affected filter, which is what the test asserts).
    """
    if cid not in ctx.objects:
        raise ValueError(f"object {cid!r} must be added to the context first")
    attrs = ctx.objects[cid]
    sigma = lattice.sigma

    touched: set[str] = set()
    for concept in lattice.concepts.values():
        if concept.intent <= attrs:
            concept.extent = concept.extent | {cid}
            concept.support = len(concept.extent)
            touched.add(concept.intent_hash)

    # candidate intents for new concepts
    candidate_intents: set[frozenset[str]] = {attrs}
    for concept in lattice.concepts.values():
        candidate_intents.add(concept.intent & attrs)
    for other, other_attrs in ctx.objects.items():
        if other != cid:
            candidate_intents.add(other_attrs & attrs)

    created: set[str] = set()
    for cand_intent in candidate_intents:
        extent = ctx.prime_attrs(cand_intent)
        intent = ctx.prime_objects(extent)
        ih = intent_hash_of(intent)
        if ih in lattice.concepts:
            continue
        if len(extent) < sigma and not (bypass and cid in extent):
            continue  # the sigma bypass only covers the inserted hub's own filter
        lattice.concepts[ih] = Concept(
            extent=extent,
            intent=intent,
            intent_hash=ih,
            support=len(extent),
            stability=0.0,
            bypass=bypass and cid in extent,
        )
        created.add(ih)

    # stability depends on the extent: recompute only where the extent changed
    for ih in touched | created:
        c = lattice.concepts[ih]
        c.stability = stability(ctx, c.extent, c.intent)

    lattice.recompute_covers()
    return touched, created


def diff_admissions(
    previous: AdmissionResult,
    current: AdmissionResult,
    *,
    ledger: Any = None,
    ctx: Optional[FormalContext] = None,
    profiles_by_table: Optional[dict[str, Any]] = None,
    lattice: Optional[ConceptLattice] = None,
) -> list[ChangeProposal]:
    """Compare applied admission states and emit ChangeProposals for every
    difference; proposals are recorded in the ledger when one is wired."""
    proposals: list[ChangeProposal] = []
    # union over EVERY recorded outcome, including spine-less structural-root
    # discards (in .discarded but not .decisions): no flip is ever silent.
    hashes = (
        set(previous.decisions) | set(previous.discarded)
        | set(current.decisions) | set(current.discarded)
    )
    for ih in sorted(hashes):
        prev = previous.outcome_of(ih)
        prev = "" if prev == "unknown" else prev
        cur = current.outcome_of(ih)
        cur = "" if cur == "unknown" else cur
        if prev == cur:
            continue
        if cur == "admit":
            kind = "add-class"
        elif prev == "admit":
            kind = "retract-class"
        elif cur == "merge":
            kind = "merge-class"
        else:
            kind = "discard-class"
        if ih in current.decisions:
            rationale = current.decisions[ih].rationale
        elif ih in current.discarded:
            rationale = current.discarded[ih]
        else:
            rationale = "concept absent from new lattice state"
        proposals.append(
            ChangeProposal(
                kind=kind, intent_hash=ih, previous=prev, proposed=cur, rationale=rationale
            )
        )

    if ledger is not None and proposals and ctx is not None and lattice is not None:
        for prop in proposals:
            concept = lattice.concepts.get(prop.intent_hash)
            cands = (
                [ctx.candidates[g] for g in concept.extent if g in ctx.candidates]
                if concept is not None
                else []
            )
            atom_ids = register_evidence_atoms(ledger, cands, profiles_by_table or {})
            if not atom_ids:
                continue
            prov_ref = ledger.intern(prov_sum([leaf(a) for a in atom_ids]))
            ledger.append_artifact(
                f"strata:proposal:{prop.intent_hash}:{prop.previous or 'none'}->{prop.proposed or 'none'}",
                "strata.change_proposal",
                prop.payload(),
                prov_ref,
            )
    return proposals
