"""The Ask flywheel — close the loop (whitepaper v2.1 §4). CLOSED-CORE IP.

A *novel cross-source Ask* that engineers a new answer writes the result back as
a referenceable cached object, so the next identical ask is served from cache
instead of recomposed. This module is the thin policy layer that sits between
:class:`~ontoforge.lodestone.Lodestone` and the project's
:class:`~ontoforge.discovery.cached_work.CachedWorkStore`:

* :func:`requires_live_composition` — is this Ask worth caching? Only a
  *non-trivial OQIR plan over 2+ types* (a Traverse / TextJoin link hop) OR a
  *fresh aggregate* qualifies. A bare single-class lookup is cheap to recompute
  and is NOT cached (it would only bloat the store).

* :func:`answer_fingerprint` — the validity fingerprint over the provenance
  atoms an :class:`~ontoforge.contracts.Answer` cites. Two answers over the same
  underlying source cells share a fingerprint; once any cited cell is edited /
  recommitted (a new content-addressed atom is minted) the fingerprint moves.

* :class:`AskFlywheel` — ``consult`` (serve a still-valid cached answer, marked
  cached; never a stale one) and ``write_back`` (record the result as a versioned
  :data:`WorkKind.ASK` object with description + provenance + fingerprint).

VALIDITY IS A HARD GATE. ``consult`` re-derives the live fingerprint by RE-RUNNING
the cached plan's single candidate (cheap: no grounding, no candidate generation,
no spine decision, no conformal/clarify reasoning — the expensive stages the cache
exists to skip) and compares it to the stored one. A mismatch invalidates the
entry and forces a full recompute, so the flywheel can never serve a
confidently-wrong stale answer. Keyless / deterministic / zero-network.
"""

from __future__ import annotations

from typing import Optional

from ontoforge.contracts import Answer
from ontoforge.contracts.ontology import Ontology
from ontoforge.contracts.oqir import (
    Aggregate,
    AsOf,
    OQIRTerm,
    Select,
    TextJoin,
    TopK,
    Traverse,
)
from ontoforge.discovery import CachedWorkStore, WorkObject, fingerprint_atoms

from .execute import ExecOutcome, execute_candidate
from .model import Candidate

__all__ = [
    "AskFlywheel",
    "answer_fingerprint",
    "requires_live_composition",
]


def _distinct_classes(term: OQIRTerm, acc: set[str]) -> None:
    """Collect every class_uri the plan touches (the join arity)."""
    if isinstance(term, Select):
        acc.add(term.class_uri)
    elif isinstance(term, Traverse):
        acc.add(term.link)  # link name distinguishes the hop's target side
        _distinct_classes(term.source, acc)
    elif isinstance(term, (TextJoin, Aggregate, TopK)):
        _distinct_classes(term.source, acc)
    elif isinstance(term, AsOf):
        _distinct_classes(term.term, acc)


def _has_link_hop(term: OQIRTerm) -> bool:
    """A Traverse or TextJoin anywhere in the plan = a cross-source composition."""
    if isinstance(term, (Traverse, TextJoin)):
        return True
    if isinstance(term, (Aggregate, TopK)):
        return _has_link_hop(term.source)
    if isinstance(term, AsOf):
        return _has_link_hop(term.term)
    return False


def _has_aggregate(term: OQIRTerm) -> bool:
    if isinstance(term, Aggregate):
        return True
    if isinstance(term, TopK):
        return _has_aggregate(term.source)
    if isinstance(term, AsOf):
        return _has_aggregate(term.term)
    return False


def requires_live_composition(term: OQIRTerm) -> bool:
    """True iff this plan is worth caching: a non-trivial OQIR plan over 2+ types
    (a link hop) OR a fresh aggregate. A bare ``select(class, conditions)`` is a
    cheap single-type lookup and is NOT cached."""
    if _has_link_hop(term):
        return True
    if _has_aggregate(term):
        return True
    classes: set[str] = set()
    _distinct_classes(term, classes)
    return len(classes) >= 2


def answer_fingerprint(answer: Answer) -> str:
    """Validity fingerprint of an Answer over the provenance atoms it cites."""
    atoms: set[str] = set()
    for cell in answer.citations:
        atoms.update(cell.atom_ids)
    return fingerprint_atoms(atoms)


def _outcome_fingerprint(out: ExecOutcome, ledger) -> str:
    """Fingerprint computed straight from a re-executed plan's cell provenance —
    the cheap live check (avoids rebuilding a full Answer)."""
    from .citations import assemble_citations

    cells = assemble_citations(out.rows, out.cell_provs, out.columns, ledger)
    atoms: set[str] = set()
    for c in cells:
        atoms.update(c.atom_ids)
    return fingerprint_atoms(atoms)


class AskFlywheel:
    """Per-world bridge between LODESTONE and a project CachedWorkStore (§4)."""

    def __init__(
        self,
        store: CachedWorkStore,
        onto: Ontology,
        hearth,
        ledger,
        *,
        tenant_id: str = "",
    ) -> None:
        self.store = store
        self.onto = onto
        self.hearth = hearth
        self.ledger = ledger
        self.tenant_id = tenant_id

    # --------------------------------------------------------------- consult

    def consult(self, question: str) -> Optional[Answer]:
        """Step 2: consult the cache FIRST. Return a fresh, still-VALID cached
        answer (marked ``cached``) or ``None`` (miss / stale -> recompute).

        Validity is re-checked live: the cached plan's single candidate is
        re-executed and its provenance fingerprint compared to the stored one. A
        stale entry (provenance moved) returns ``None`` so the caller recomputes;
        a missing-plan entry (cannot revalidate) is treated as a miss."""
        obj = self.store.latest_ask(question, tenant_id=self.tenant_id)
        if obj is None:
            return None
        cand = obj.payload.get("_candidate")
        if not isinstance(cand, Candidate):
            return None  # cannot revalidate without the plan -> safe miss
        out = execute_candidate(cand, self.onto, self.hearth)
        if not isinstance(out, ExecOutcome):
            return None  # the plan no longer holds data -> stale, recompute
        live_fp = _outcome_fingerprint(out, self.ledger)
        if live_fp != str(obj.payload.get("fingerprint", "")):
            return None  # provenance changed underneath -> invalidate + recompute
        return _hydrate(obj, cand)

    # ------------------------------------------------------------ write-back

    def write_back(self, question: str, candidate: Candidate, answer: Answer) -> Optional[WorkObject]:
        """Step 1: on a successful composed Ask, write the result back as a
        versioned, referenceable cache object. No-op (returns ``None``) for
        abstentions, clarifications, empty answers, or cheap single-type lookups."""
        if answer.abstained or answer.clarification or not answer.rows:
            return None
        if not requires_live_composition(candidate.term):
            return None
        # the fingerprint is (re)derived inside cache_answer from these atom ids;
        # answer_fingerprint(answer) over the same set yields the identical value.
        atoms: set[str] = set()
        for cell in answer.citations:
            atoms.update(cell.atom_ids)
        obj = self.store.cache_answer(
            question,
            columns=answer.columns,
            rows=answer.rows,
            citations=[
                {"row": c.row, "column": c.column, "value": c.value,
                 "atom_ids": list(c.atom_ids)}
                for c in answer.citations
            ],
            atom_ids=atoms,
            oqir=repr(candidate.term),
            confidence=answer.confidence,
            provenance=_provenance_ref(atoms),
            tenant_id=self.tenant_id,
        )
        # stash the live plan so a later consult can cheaply revalidate. The store
        # is process-local (the world owns one), so carrying the Candidate object
        # is sound and avoids re-grounding on revalidation.
        obj.payload["_candidate"] = candidate
        return obj


def _provenance_ref(atoms: set[str]) -> str:
    """A compact, deterministic provenance reference for the cached object."""
    n = len(atoms)
    return f"atoms:{n}" if n else "atoms:0"


def _hydrate(obj: WorkObject, cand: Candidate) -> Answer:
    """Reconstitute the stored answer as a contracts.Answer, marked cached."""
    from ontoforge.contracts.oqir import CitedCell

    p = obj.payload
    a = Answer(
        columns=list(p.get("columns", []) or []),
        rows=[list(r) for r in (p.get("rows", []) or [])],
        confidence=float(p.get("confidence", 0.0)),
        oqir=cand.term,
    )
    a.citations = [
        CitedCell(
            row=int(c["row"]),
            column=str(c["column"]),
            value=c.get("value"),
            atom_ids=tuple(c.get("atom_ids", ()) or ()),
        )
        for c in (p.get("citations", []) or [])
    ]
    # mark the served answer as a cache hit (referenceable downstream by id + desc)
    a.cached = True
    a.cache_object_id = obj.object_id
    a.cache_description = obj.description
    return a
