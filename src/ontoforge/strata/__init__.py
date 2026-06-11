"""M4 — STRATA: Stratified Type-Lattice Induction (whitepaper §3.3–§3.5, §11.2 M4).

candidates -> formal context -> iceberg concept lattice -> spine-gated
admission -> contracts.Ontology, with AddIntent-style incremental maintenance.

§11.2 M4 interface mapping:
    induce(candidates, K) -> lattice      Strata.induce(...)  (.lattice on the result)
    admit(concept) -> spine decision      Strata.admit(concept)
    insert_delta(Δcandidates)             Strata.insert_delta([...])
    emit_ontology() -> (C, ≤, P, ax, Σ)   Strata.emit_ontology()
"""

from .admission import (
    ADMIT_CANDIDATES,
    AdmissionEngine,
    AdmissionResult,
    AdmittedConcept,
    NameMemo,
    admit_adjudication_handler,
    build_strata_client,
    name_concept_handler,
    register_admit_rules,
    review_hub_candidates,
)
from .candidates import (
    TypeCandidate,
    g_decomp_candidates,
    g_join_candidates,
    g_table_candidates,
    generate_candidates,
)
from .context import (
    ATTRIBUTE_WEIGHTS,
    FormalContext,
    PropertyClusters,
    attribute_weight,
    build_context,
    build_property_clusters,
    candidate_attributes,
    intent_hash_of,
    is_timestampish,
)
from .emit import emit_ontology
from .incremental import ChangeProposal, diff_admissions, insert_object
from .lattice import Concept, ConceptLattice, build_lattice, stability
from .strata import Strata, StrataResult

__all__ = [
    "ADMIT_CANDIDATES",
    "ATTRIBUTE_WEIGHTS",
    "AdmissionEngine",
    "AdmissionResult",
    "AdmittedConcept",
    "ChangeProposal",
    "Concept",
    "ConceptLattice",
    "FormalContext",
    "NameMemo",
    "PropertyClusters",
    "Strata",
    "StrataResult",
    "TypeCandidate",
    "admit_adjudication_handler",
    "attribute_weight",
    "build_context",
    "build_lattice",
    "build_property_clusters",
    "build_strata_client",
    "candidate_attributes",
    "diff_admissions",
    "emit_ontology",
    "g_decomp_candidates",
    "g_join_candidates",
    "g_table_candidates",
    "generate_candidates",
    "insert_object",
    "intent_hash_of",
    "is_timestampish",
    "name_concept_handler",
    "register_admit_rules",
    "review_hub_candidates",
    "stability",
]
