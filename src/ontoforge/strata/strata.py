"""The STRATA orchestrator — §11.2 M4 interface.

    induce(candidates, K) -> lattice      Strata.induce(...)
    admit(concept) -> spine decision      Strata.admit(concept)
    insert_delta(Δcandidates)             Strata.insert_delta([...])
    emit_ontology() -> (C, ≤, P, ax, Σ)   Strata.emit_ontology()

Pipeline: candidates -> hub spine review -> formal context -> iceberg concept
lattice -> spine-gated admission -> contracts.Ontology. All state needed for
incremental deltas is kept on the instance; admission is re-run after each
delta with decision memoization, so spine cost concentrates on the affected
order filter and any flipped outcome surfaces as a ChangeProposal.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

from ontoforge.contracts import (
    FD,
    IND,
    DecisionResult,
    ModelClient,
    Ontology,
    SpineProfile,
    TableProfile,
)
from ontoforge.spine import DecisionSpine

from .admission import (
    AdmissionEngine,
    AdmissionResult,
    NameMemo,
    build_strata_client,
    register_admit_rules,
    review_hub_candidates,
)
from .candidates import TypeCandidate, generate_candidates
from .context import FormalContext, build_context, build_property_clusters, candidate_attributes
from .emit import emit_ontology
from .incremental import ChangeProposal, diff_admissions, insert_object
from .lattice import Concept, ConceptLattice, build_lattice

__all__ = ["Strata", "StrataResult"]


@dataclass
class StrataResult:
    candidates: list[TypeCandidate]
    hub_reviews: dict[str, DecisionResult]
    context: FormalContext
    lattice: ConceptLattice
    admission: AdmissionResult
    ontology: Ontology
    proposals: list[ChangeProposal] = field(default_factory=list)


class Strata:
    """One induction scope: holds the evidence substrate plus mutable lattice
    and admission state across deltas."""

    def __init__(
        self,
        spine: Optional[Any] = None,
        model_client: Optional[ModelClient] = None,
        ledger: Any = None,
        sigma: int = 1,
    ) -> None:
        # LLM-readiness seam (keyless-default, byte-identical):
        # resolve_client returns the deterministic fallback UNCHANGED when no
        # provider env is set, so the keyless path is the same object the
        # call-site built today; with a provider + key it wraps a live adapter in
        # the secure + validating + fallback chain. An EXPLICIT model_client is
        # honored as-is (never re-wrapped) so test/handler injection stays exact.
        if model_client is not None:
            self.client: ModelClient = model_client
        else:
            from ontoforge.aimodels import resolve_client

            self.client = resolve_client(
                "strata.name_concept", fallback=build_strata_client()
            )
        self.spine = spine if spine is not None else DecisionSpine(
            SpineProfile(), self.client, ledger
        )
        register_admit_rules(self.spine)
        self.ledger = ledger
        self.sigma = sigma
        self.memo = NameMemo(ledger)

        # state populated by induce()
        self.profiles: list[TableProfile] = []
        self.inds: list[IND] = []
        self.fds: list[FD] = []
        self.context: Optional[FormalContext] = None
        self.lattice: Optional[ConceptLattice] = None
        self.engine: Optional[AdmissionEngine] = None
        self.admission: Optional[AdmissionResult] = None
        self.hub_reviews: dict[str, DecisionResult] = {}

    # -- §11.2: induce -----------------------------------------------------

    def induce(
        self,
        profiles: Sequence[TableProfile],
        inds: Sequence[IND] = (),
        fds: Optional[Sequence[FD]] = None,
        candidates: Optional[Sequence[TypeCandidate]] = None,
    ) -> StrataResult:
        """Full induction over the evidence substrate. ``candidates`` may be
        supplied (e.g. a subset, for staged/incremental loads); by default
        all §3.3 generators run."""
        self.profiles = list(profiles)
        self.inds = list(inds)
        self.fds = list(fds) if fds is not None else [fd for tp in profiles for fd in tp.fds]
        if candidates is None:
            candidates = generate_candidates(self.profiles, self.inds, self.fds)

        by_table = {tp.table: tp for tp in self.profiles}
        clusters = build_property_clusters(self.profiles, self.inds)
        surviving, self.hub_reviews = review_hub_candidates(
            self.spine, list(candidates), clusters, by_table, self.ledger
        )
        self.context = build_context(surviving, self.profiles, self.inds, clusters)
        bypass = [c.cid for c in surviving if c.bypass_sigma]
        self.lattice = build_lattice(self.context, sigma=self.sigma, bypass_objects=bypass)
        self.engine = AdmissionEngine(self.spine, self.ledger, by_table)
        self.admission = self.engine.process(self.lattice, self.context)
        ontology = self.emit_ontology()
        return StrataResult(
            candidates=list(surviving),
            hub_reviews=dict(self.hub_reviews),
            context=self.context,
            lattice=self.lattice,
            admission=self.admission,
            ontology=ontology,
        )

    # -- §11.2: admit ---------------------------------------------------------

    def admit(self, concept: Concept) -> DecisionResult:
        """Route one concept through the spine (parity interface)."""
        if self.context is None or self.engine is None:
            raise RuntimeError("induce() first")
        return self.engine.admit(concept, self.context)

    # -- §11.2: insert_delta ----------------------------------------------------

    def insert_delta(self, new_candidates: Sequence[TypeCandidate]) -> tuple[list[ChangeProposal], set[str]]:
        """AddIntent-style insertion of candidate deltas.

        The evidence substrate (profiles/INDs and hence the synonym map) is
        fixed at induce() time; deltas insert *candidates* into the lattice.
        Returns (change proposals, touched-or-created intent hashes).
        """
        if self.context is None or self.lattice is None or self.engine is None or self.admission is None:
            raise RuntimeError("induce() first")
        by_table = {tp.table: tp for tp in self.profiles}
        clusters = self.context.clusters
        assert clusters is not None

        surviving, reviews = review_hub_candidates(
            self.spine, list(new_candidates), clusters, by_table, self.ledger
        )
        self.hub_reviews.update(reviews)

        affected: set[str] = set()
        for cand in surviving:
            attrs = candidate_attributes(cand, clusters, by_table)
            self.context.add_object(cand.cid, attrs, cand)
            touched, created = insert_object(
                self.lattice, self.context, cand.cid, bypass=cand.bypass_sigma
            )
            affected |= touched | created

        previous = self.admission
        self.admission = self.engine.process(self.lattice, self.context)
        proposals = diff_admissions(
            previous,
            self.admission,
            ledger=self.ledger,
            ctx=self.context,
            profiles_by_table=by_table,
            lattice=self.lattice,
        )
        return proposals, affected

    # -- §11.2: emit_ontology ------------------------------------------------------

    def emit_ontology(self, version: int = 1) -> Ontology:
        if self.context is None or self.lattice is None or self.admission is None:
            raise RuntimeError("induce() first")
        return emit_ontology(
            self.context,
            self.lattice,
            self.admission,
            self.profiles,
            self.inds,
            client=self.client,
            ledger=self.ledger,
            memo=self.memo,
        )
