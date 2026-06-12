"""M11 — Ontology/Graph Export & RDF round-trip (whitepaper v1 G7, §11.2 M11)."""

from .rdf import (
    OF,
    ONTOLOGY_NODE,
    ExportError,
    data_to_rdf,
    ontology_graph,
    ontology_to_rdf,
    rdf_to_ontology,
    shapes_to_rdf,
    sorted_turtle,
)
from .roundtrip import (
    RoundTripError,
    ShaclReport,
    assert_store_equivalence,
    graph_from_turtle,
    normalize_term,
    oxigraph_from_turtle,
    query_oxigraph,
    query_rdflib,
    shacl_validate,
)

__all__ = [
    "OF",
    "ONTOLOGY_NODE",
    "ExportError",
    "ontology_to_rdf",
    "shapes_to_rdf",
    "ontology_graph",
    "data_to_rdf",
    "rdf_to_ontology",
    "sorted_turtle",
    "RoundTripError",
    "ShaclReport",
    "graph_from_turtle",
    "oxigraph_from_turtle",
    "query_rdflib",
    "query_oxigraph",
    "assert_store_equivalence",
    "shacl_validate",
    "normalize_term",
]
