"""M11 — data graph provenance annotations: every asserted data triple carries
an of:CellAnnotation whose of:provRef resolves in the REAL ledger to non-zero
citations over registered atoms (constraint H carried into the RDF export)."""

from __future__ import annotations

from rdflib import RDF, Graph, URIRef

from ontoforge.export import OF, graph_from_turtle

NS = "onto://gold/aviation"


def _annotations(g: Graph):
    for ann in g.subjects(RDF.type, OF.CellAnnotation):
        yield ann


def test_every_value_triple_is_annotated(world, data_ttl):
    g = graph_from_turtle(data_ttl)
    asserted = {
        (s, p)
        for s, p, _ in g
        if str(p).startswith(NS) and "/prop/" in str(p)
    }
    annotated = {
        (g.value(a, OF.subject), g.value(a, OF.predicate)) for a in _annotations(g)
    }
    assert asserted == annotated


def test_prov_refs_resolve_to_registered_atoms(world, data_ttl):
    g = graph_from_turtle(data_ttl)
    ledger = world["ledger"]
    n = 0
    for ann in _annotations(g):
        ref = str(g.value(ann, OF.provRef))
        assert ledger.valuate_ref(ref, "derivable") is True
        citations = ledger.valuate_ref(ref, "citations")
        assert citations
        for atom_id in citations:
            atom = ledger.get_atom(atom_id)
            assert atom is not None and atom.uri.startswith("atom://")
        n += 1
    assert n > 100  # the whole estate slice is annotated, not a token sample


def test_entities_are_typed_resources(world, data_ttl):
    g = graph_from_turtle(data_ttl)
    aircraft = set(g.subjects(RDF.type, URIRef(f"{NS}/Aircraft")))
    assert len(aircraft) == world["n_aircraft"]
    models = set(g.subjects(RDF.type, URIRef(f"{NS}/AircraftModel")))
    assert len(models) == world["n_models"]
