"""M11 gate 1 — round-trip isomorphism modulo documented loss.

Export the (TEMPER-evolved) gold ontology to OWL+SHACL Turtle, re-parse with a
fresh rdflib Graph, reconstruct a contracts.Ontology, and require the class /
property / shape / edge sets EQUAL after order normalization (the only
documented loss is tuple ordering of parents/properties/shapes/synonyms).
Also pins byte-determinism of the sorted-Turtle serialization.
"""

from __future__ import annotations

import dataclasses

from rdflib import RDF, Graph
from rdflib.namespace import OWL

from ontoforge.contracts import ClassDef, Ontology, PropertyDef
from ontoforge.export import (
    ontology_graph,
    ontology_to_rdf,
    rdf_to_ontology,
    shapes_to_rdf,
    sorted_turtle,
)


def _norm_prop(p: PropertyDef) -> tuple:
    d = dataclasses.asdict(p)
    d["synonyms"] = tuple(sorted(p.synonyms))
    d["dimension"] = tuple(p.dimension.exps) if p.dimension is not None else None
    return tuple(sorted(d.items()))


def _norm_class(c: ClassDef) -> tuple:
    return (
        c.uri,
        c.name,
        tuple(sorted(c.parents)),
        tuple(sorted(_norm_prop(p) for p in c.properties)),
        tuple(sorted(dataclasses.astuple(s) for s in c.shapes)),
        c.definition,
        c.intent_hash,
        c.is_event,
        round(c.confidence, 12),
        c.prov_ref,
        tuple(sorted(c.disjoint_with)),
    )


def _norm(onto: Ontology) -> dict[str, tuple]:
    return {uri: _norm_class(c) for uri, c in onto.classes.items()}


def test_roundtrip_isomorphism(world):
    onto: Ontology = world["ontology"]
    ttl = sorted_turtle(ontology_graph(onto))
    g = Graph()
    g.parse(data=ttl, format="turtle")
    back = rdf_to_ontology(g)
    assert back.version == onto.version
    assert set(back.classes) == set(onto.classes)
    for uri in onto.classes:
        assert _norm_class(back.classes[uri]) == _norm_class(onto.classes[uri]), uri


def test_shape_constraint_sets_survive(world):
    onto: Ontology = world["ontology"]
    g = Graph()
    g.parse(data=sorted_turtle(ontology_graph(onto)), format="turtle")
    back = rdf_to_ontology(g)
    # declaration ORDER of shapes is preserved exactly (zero-padded shape IRIs)
    for uri, c in onto.classes.items():
        assert back.classes[uri].shapes == c.shapes, uri


def test_subsumption_edges_survive(world):
    onto: Ontology = world["ontology"]
    g = Graph()
    g.parse(data=sorted_turtle(ontology_graph(onto)), format="turtle")
    back = rdf_to_ontology(g)
    for uri in onto.classes:
        assert back.ancestors(uri) == onto.ancestors(uri)


def test_serialization_is_deterministic(world):
    onto: Ontology = world["ontology"]
    a = sorted_turtle(ontology_graph(onto))
    b = sorted_turtle(ontology_graph(onto))
    assert a == b
    # a structurally fresh clone serializes identically too
    c = sorted_turtle(ontology_graph(onto.clone()))
    assert a == c


def test_no_blank_nodes_anywhere(world):
    from rdflib.term import BNode

    for g in (ontology_to_rdf(world["ontology"]), shapes_to_rdf(world["ontology"])):
        for triple in g:
            assert not any(isinstance(t, BNode) for t in triple), triple


def test_owl_vocabulary_complete(world):
    onto: Ontology = world["ontology"]
    g = ontology_to_rdf(onto)
    classes = set(g.subjects(RDF.type, OWL.Class))
    assert len(classes) == len(onto.classes)
    n_obj = len(set(g.subjects(RDF.type, OWL.ObjectProperty)))
    n_dt = len(set(g.subjects(RDF.type, OWL.DatatypeProperty)))
    want_obj = sum(1 for c in onto.iter_classes() for p in c.properties if p.is_link)
    want_dt = sum(1 for c in onto.iter_classes() for p in c.properties if not p.is_link)
    assert (n_obj, n_dt) == (want_obj, want_dt)
