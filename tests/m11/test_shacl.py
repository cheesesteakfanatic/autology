"""M11 gate 3 — SHACL conformance via pySHACL.

The committed estate data, exported to RDF, must conform to the gold-ontology
shapes (no fixture wart in the committed slice legitimately violates them —
the builder normalizes the documented FAA padding warts on the way in, which
is exactly the conformance contract of the ENTITY layer, §4.3). A seeded
violating entity must be caught with the precise expected constraint
components.
"""

from __future__ import annotations

import pytest
from rdflib import RDF, XSD, Graph, Literal, URIRef

from ontoforge.export import (
    data_to_rdf,
    graph_from_turtle,
    ontology_to_rdf,
    shacl_validate,
    shapes_to_rdf,
    sorted_turtle,
)

NS = "onto://gold/aviation"


@pytest.fixture(scope="module")
def graphs(world, data_ttl):
    return {
        "data": graph_from_turtle(data_ttl),
        "shapes": shapes_to_rdf(world["ontology"]),
        "ont": ontology_to_rdf(world["ontology"]),
    }


def test_estate_data_conforms_to_gold_shapes(graphs):
    report = shacl_validate(graphs["data"], graphs["shapes"], ont_graph=graphs["ont"])
    assert report.conforms, report.text


def test_shapes_graph_is_deterministic(world):
    assert sorted_turtle(shapes_to_rdf(world["ontology"])) == sorted_turtle(
        shapes_to_rdf(world["ontology"])
    )


def test_seeded_violations_are_caught_precisely(world, graphs, data_ttl):
    bad = graph_from_turtle(data_ttl)
    rogue = URIRef("ent://aircraft/rogue-0")
    bad.add((rogue, RDF.type, URIRef(f"{NS}/Aircraft")))
    # missing serial_number (sh:minCount), bad tail pattern, impossible year
    bad.add(
        (rogue, URIRef(f"{NS}/Aircraft/prop/tail_number"), Literal("0BAD", datatype=XSD.string))
    )
    bad.add((rogue, URIRef(f"{NS}/Aircraft/prop/year_mfr"), Literal(1850)))
    report = shacl_validate(bad, graphs["shapes"], ont_graph=graphs["ont"])
    assert not report.conforms
    assert report.violated("rogue-0", "MinCountConstraintComponent")
    assert report.violated("rogue-0", "PatternConstraintComponent")
    assert report.violated("rogue-0", "MinInclusiveConstraintComponent")
    # and no pre-existing entity is implicated
    assert all("rogue-0" in v["focus"] for v in report.violations), report.violations


def test_subclass_targeting_reaches_operator_instances(world, graphs):
    """Agent declares name minCount 1; Operator instances are Agent instances
    via rdfs:subClassOf — strip a name and the Agent shape must fire."""
    g = Graph()
    for t in graphs["data"]:
        g.add(t)
    rogue = URIRef("ent://operator/rogue-noname")
    g.add((rogue, RDF.type, URIRef(f"{NS}/Operator")))
    report = shacl_validate(g, graphs["shapes"], ont_graph=graphs["ont"])
    assert not report.conforms
    assert report.violated("rogue-noname", "MinCountConstraintComponent")


def test_data_graph_is_stance_aware(world):
    """Exporting under as_of(t_mid) shows the historical registrant; the
    current graph shows the successor (the §4.4 stance carried into RDF)."""
    from ontoforge.contracts import Stance

    onto, hearth, known = world["ontology"], world["hearth"], world["known_uri"]
    prop = URIRef(f"{NS}/Aircraft/prop/registrant_name")
    g_now = data_to_rdf(hearth, onto)
    g_then = data_to_rdf(hearth, onto, Stance("as_of", valid_at=world["known"]["t_mid"]))
    now_val = g_now.value(URIRef(known), prop)
    then_val = g_then.value(URIRef(known), prop)
    assert str(then_val) == world["known"]["registrant"]
    assert str(now_val) == world["known"]["successor"]
