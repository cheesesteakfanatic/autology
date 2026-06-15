"""M11 regression — a value cell committed under a property the ontology types
as a LINK must NOT crash the RDF export.

The generic-induced engine routinely types a foreign-key column (e.g. a
``model_code`` or ``destination`` code) as an ``is_link`` PropertyDef while the
HEARTH cell under it holds the raw LITERAL identifier, not a resolved entity
URI. ``data_to_rdf`` used to raise ``ExportError`` on the first such cell,
which surfaced as the ``POST /api/export`` 500. The export must SUCCEED:
resolvable values become object triples, unresolved literals are preserved
losslessly as XSD-typed literals on a minted ``of:linkLiteral`` annotation
property with full provenance — never dropped, never raised.
"""

from __future__ import annotations

from pathlib import Path

from rdflib import RDF, XSD, Literal, URIRef

from ontoforge.contracts import (
    ClassDef,
    Datatype,
    Interval,
    Layer,
    Ontology,
    PropertyDef,
    ValueCell,
    leaf,
    make_cell_atom,
)
from ontoforge.export import OF, data_to_rdf, graph_from_turtle, sorted_turtle
from ontoforge.hearth import Hearth
from ontoforge.ledger import SqliteLedger

NS = "onto://test/estate"
PRODUCT = f"{NS}/Product"
MODEL = f"{NS}/ProductModel"


def _build_world(root: Path):
    """A two-class ontology where Product.model_code is typed as a LINK to
    ProductModel, but the committed cells hold raw code literals — plus one
    cell whose value DOES name a real ProductModel entity."""
    onto = Ontology()
    onto.add(
        ClassDef(
            uri=MODEL,
            name="ProductModel",
            properties=(
                PropertyDef(
                    uri=f"{MODEL}/prop/code", name="code", datatype=Datatype.STRING
                ),
            ),
        )
    )
    onto.add(
        ClassDef(
            uri=PRODUCT,
            name="Product",
            properties=(
                PropertyDef(
                    uri=f"{PRODUCT}/prop/sku", name="sku", datatype=Datatype.STRING
                ),
                # the wart: a LINK-typed property whose cells hold literals
                PropertyDef(
                    uri=f"{PRODUCT}/prop/model_code",
                    name="model_code",
                    datatype=Datatype.STRING,
                    is_link=True,
                    range_class=MODEL,
                ),
            ),
        )
    )

    ledger = SqliteLedger(":memory:")
    hearth = Hearth(root / "hearth", ledger, onto)

    def prov(table: str, rkey: str, col: str, val: str) -> str:
        atom = make_cell_atom("src", table, rkey, col, val)
        ledger.register_atoms([atom])
        return ledger.intern(leaf(atom.atom_id))

    def cell(entity: str, p: str, v, pr: str) -> ValueCell:
        return ValueCell(
            entity_uri=entity, prop=p, value=v,
            valid=Interval(0), system=Interval(0), prov_ref=pr,
            confidence=1.0, src_rank=1,
        )

    model_uri = f"ent://model/PLS7"
    hearth.commit(
        Layer.ENTITY, MODEL,
        [cell(model_uri, "code", "PLS7", prov("models", "PLS7", "code", "PLS7"))],
        now=1_700_000_000_000_000,
    )

    # product 1: model_code holds a raw literal that names NO entity (the bug)
    p1 = "ent://product/SKU-1"
    # product 2: model_code holds a literal that IS the model entity URI
    p2 = "ent://product/SKU-2"
    hearth.commit(
        Layer.ENTITY, PRODUCT,
        [
            cell(p1, "sku", "SKU-1", prov("products", "1", "sku", "SKU-1")),
            cell(p1, "model_code", "PLS9",
                 prov("products", "1", "model_code", "PLS9")),
            cell(p2, "sku", "SKU-2", prov("products", "2", "sku", "SKU-2")),
            cell(p2, "model_code", model_uri,
                 prov("products", "2", "model_code", model_uri)),
        ],
        now=1_700_000_000_000_001,
    )
    return ledger, hearth, onto, model_uri


def test_link_typed_literal_does_not_crash_export(tmp_path):
    ledger, hearth, onto, model_uri = _build_world(tmp_path)
    try:
        # the regression: this used to raise ExportError ("value cell committed
        # under link property 'model_code'") — now it must succeed.
        g = data_to_rdf(hearth, onto)
        ttl = sorted_turtle(g)  # also asserts no blank nodes leaked in
    finally:
        ledger.close()

    # the round-trip is byte-stable and re-parses
    reparsed = graph_from_turtle(ttl)
    assert len(reparsed) == len(g)


def test_unresolved_link_literal_preserved_with_provenance(tmp_path):
    ledger, hearth, onto, model_uri = _build_world(tmp_path)
    try:
        g = data_to_rdf(hearth, onto)
    finally:
        ledger.close()

    p1 = URIRef("ent://product/SKU-1")
    link_lit_pred = URIRef(f"{OF}linkLiteral/model_code")
    link_obj_pred = URIRef(f"{PRODUCT}/prop/model_code")

    # the unresolved literal is preserved (not dropped, not an object triple)
    assert (p1, link_lit_pred, Literal("PLS9", datatype=XSD.string)) in g
    assert not list(g.objects(p1, link_obj_pred)), "no bogus object triple"

    # ...flagged as an unresolved link...
    ann = URIRef("ent://product/SKU-1#prov-model_code")
    assert (ann, RDF.type, OF.CellAnnotation) in g
    assert (ann, OF.unresolvedLink, Literal(True)) in g

    # ...and it still carries its interned provenance ref (constraint H survives)
    assert str(g.value(ann, OF.provRef))


def test_resolvable_link_literal_becomes_an_object_triple(tmp_path):
    ledger, hearth, onto, model_uri = _build_world(tmp_path)
    try:
        g = data_to_rdf(hearth, onto)
    finally:
        ledger.close()

    p2 = URIRef("ent://product/SKU-2")
    link_obj_pred = URIRef(f"{PRODUCT}/prop/model_code")
    # SKU-2's model_code names a real entity -> a proper object triple
    assert (p2, link_obj_pred, URIRef(model_uri)) in g
    ann = URIRef("ent://product/SKU-2#prov-model_code")
    assert (ann, OF.object, URIRef(model_uri)) in g
    # and it is NOT mislabeled as an unresolved link
    assert (ann, OF.unresolvedLink, Literal(True)) not in g
