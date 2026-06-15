"""M11 — Ontology/Graph export: contracts.Ontology + HEARTH state -> RDF
(whitepaper v1 G7, §11.2 M11; v2 §7 item (1)/(3) — the AMBER ontology and
graph payloads are produced here).

Design rules
------------
* **No blank nodes, ever.** Every node that would normally be a bnode (SHACL
  property shapes, RDF list spine for ``sh:in``) gets a deterministic minted
  IRI derived from the class URI. This is what makes byte-stable sorted
  serialization possible (see :func:`sorted_turtle`).
* **Lossless annotations.** OWL has no slot for unit/dimension/cardinality/
  intent-hash/etc., so they ride as custom annotation properties in the
  ``of:`` namespace; :func:`rdf_to_ontology` reads them back, giving the M11
  round-trip-isomorphism gate something exact to chew on. The documented loss
  set is exactly: tuple ORDER of parents/properties/shapes/synonyms (sets are
  preserved, order is normalized).
* **Cell provenance as annotation resources** (RDF-star deferral): rdflib's
  Turtle round-trip of quoted triples is not yet byte-stable across stores at
  the versions pinned here, so each asserted data triple gets a deterministic
  companion ``of:CellAnnotation`` resource carrying ``of:provRef`` (the
  interned ledger term hash) and ``of:confidence``. RDF-star embedding is a
  drop-in upgrade behind the same function.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Optional

from rdflib import RDF, RDFS, XSD, Graph, Literal, Namespace, URIRef
from rdflib.collection import Collection
from rdflib.namespace import OWL, SH
from rdflib.term import BNode

from ontoforge.contracts import (
    CURRENT,
    ClassDef,
    Datatype,
    Layer,
    Ontology,
    PropertyDef,
    ShapeConstraint,
    Stance,
    property_uri,
)
from ontoforge.contracts.units import Dimension
from ontoforge.hearth import link_visible, survivorship_key

#: OntoForge annotation namespace (custom annotation properties; see module doc).
OF = Namespace("https://ontoforge.dev/ns#")

#: The ontology header node (carries owl:versionInfo so the TEMPER version
#: counter survives the RDF round trip).
ONTOLOGY_NODE = URIRef("https://ontoforge.dev/ontology")

_XSD_OF_DATATYPE = {
    Datatype.STRING: XSD.string,
    Datatype.TEXT: XSD.string,
    Datatype.INTEGER: XSD.integer,
    Datatype.FLOAT: XSD.double,
    Datatype.BOOLEAN: XSD.boolean,
    Datatype.DATE: XSD.date,
    Datatype.DATETIME: XSD.dateTime,
}


class ExportError(ValueError):
    """An ontology/data state that cannot be exported faithfully."""


def _resolve_prop(ontology: Ontology, class_uri: str, prop: str) -> Optional[PropertyDef]:
    """Property lookup with inheritance: own properties first, then ancestors
    in sorted order (deterministic under multiple inheritance). Inherited
    properties keep the DECLARING class's property URI, so SHACL paths and
    SPARQL joins line up across the subsumption hierarchy."""
    cls = ontology.get(class_uri)
    if cls is None:
        return None
    pdef = cls.prop(prop)
    if pdef is not None:
        return pdef
    for ancestor in sorted(ontology.ancestors(class_uri)):
        anc = ontology.get(ancestor)
        if anc is not None:
            pdef = anc.prop(prop)
            if pdef is not None:
                return pdef
    return None


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]+", "_", text)


# ---------------------------------------------------------------------------
# Ontology -> OWL
# ---------------------------------------------------------------------------


def ontology_to_rdf(ontology: Ontology) -> Graph:
    """O^(t) as OWL 2: classes with rdfs:subClassOf, labels/definitions,
    datatype/object properties with rdfs:domain/range, and the lossless
    ``of:`` annotation layer (unit, dimension, cardinality, functional,
    synonyms, intent hash, event flag, confidence, provenance ref)."""
    g = Graph()
    _bind(g)
    g.add((ONTOLOGY_NODE, RDF.type, OWL.Ontology))
    g.add((ONTOLOGY_NODE, OWL.versionInfo, Literal(int(ontology.version))))
    for uri in sorted(ontology.classes):
        c = ontology.classes[uri]
        cu = URIRef(uri)
        g.add((cu, RDF.type, OWL.Class))
        g.add((cu, RDFS.label, Literal(c.name)))
        if c.definition:
            g.add((cu, RDFS.comment, Literal(c.definition)))
        for parent in sorted(c.parents):
            g.add((cu, RDFS.subClassOf, URIRef(parent)))
        for d in sorted(c.disjoint_with):
            g.add((cu, OWL.disjointWith, URIRef(d)))
        if c.intent_hash:
            g.add((cu, OF.intentHash, Literal(c.intent_hash)))
        g.add((cu, OF.isEvent, Literal(bool(c.is_event))))
        g.add((cu, OF.confidence, Literal(repr(float(c.confidence)), datatype=XSD.double)))
        if c.prov_ref:
            g.add((cu, OF.provRef, Literal(c.prov_ref)))
        for p in c.properties:
            _add_property(g, cu, p)
    return g


def _add_property(g: Graph, domain: URIRef, p: PropertyDef) -> None:
    pu = URIRef(p.uri)
    g.add((pu, RDF.type, OWL.ObjectProperty if p.is_link else OWL.DatatypeProperty))
    g.add((pu, RDFS.label, Literal(p.name)))
    g.add((pu, RDFS.domain, domain))
    if p.is_link:
        if not p.range_class:
            raise ExportError(f"link property {p.uri} has no range class")
        g.add((pu, RDFS.range, URIRef(p.range_class)))
    else:
        g.add((pu, RDFS.range, _XSD_OF_DATATYPE[p.datatype]))
    # lossless annotation layer
    g.add((pu, OF.datatype, Literal(p.datatype.value)))
    g.add((pu, OF.cardinality, Literal(p.cardinality)))
    g.add((pu, OF.functional, Literal(bool(p.functional))))
    if p.unit:
        g.add((pu, OF.unit, Literal(p.unit)))
    if p.dimension is not None:
        g.add((pu, OF.dimension, Literal(json.dumps(list(p.dimension.exps)))))
    for s in sorted(p.synonyms):
        g.add((pu, OF.synonym, Literal(s)))
    if p.definition:
        g.add((pu, RDFS.comment, Literal(p.definition)))


# ---------------------------------------------------------------------------
# ShapeConstraints -> SHACL
# ---------------------------------------------------------------------------


def shapes_to_rdf(ontology: Ontology) -> Graph:
    """Σ as a SHACL graph: one sh:NodeShape per class that declares shapes,
    one minted-IRI sh:PropertyShape per ShapeConstraint (zero-padded index in
    the IRI keeps declaration order recoverable), RDF lists for sh:in built
    from minted IRIs (no blank nodes; see module doc)."""
    g = Graph()
    _bind(g)
    for uri in sorted(ontology.classes):
        c = ontology.classes[uri]
        if not c.shapes:
            continue
        ns = URIRef(f"{uri}#shape")
        g.add((ns, RDF.type, SH.NodeShape))
        g.add((ns, SH.targetClass, URIRef(uri)))
        for i, s in enumerate(c.shapes):
            psu = URIRef(f"{uri}#shape-{i:02d}-{_slug(s.prop)}")
            g.add((ns, SH.property, psu))
            g.add((psu, RDF.type, SH.PropertyShape))
            pdef = c.prop(s.prop)
            path = pdef.uri if pdef is not None else property_uri(uri, s.prop)
            g.add((psu, SH.path, URIRef(path)))
            if s.min_count > 0:
                g.add((psu, SH.minCount, Literal(int(s.min_count))))
            if s.max_count is not None:
                g.add((psu, SH.maxCount, Literal(int(s.max_count))))
            if s.datatype is not None:
                g.add((psu, SH.datatype, _XSD_OF_DATATYPE[s.datatype]))
                g.add((psu, OF.datatype, Literal(s.datatype.value)))
            if s.pattern is not None:
                g.add((psu, SH.pattern, Literal(s.pattern)))
            if s.in_values is not None:
                head = URIRef(f"{psu}-in-0")
                nodes = [URIRef(f"{psu}-in-{j}") for j in range(len(s.in_values))]
                for j, v in enumerate(s.in_values):
                    # explicit xsd:string: rdflib/pySHACL term equality treats
                    # plain and xsd:string literals as distinct, and the data
                    # graph emits explicit xsd:string (RDF 1.1 identification)
                    g.add((nodes[j], RDF.first, Literal(v, datatype=XSD.string)))
                    nxt = nodes[j + 1] if j + 1 < len(nodes) else RDF.nil
                    g.add((nodes[j], RDF.rest, nxt))
                g.add((psu, SH["in"], head))
            if s.min_value is not None:
                g.add((psu, SH.minInclusive, _numeric_literal(s.min_value)))
            if s.max_value is not None:
                g.add((psu, SH.maxInclusive, _numeric_literal(s.max_value)))
            if s.unit is not None:
                g.add((psu, OF.unit, Literal(s.unit)))
    return g


def ontology_graph(ontology: Ontology) -> Graph:
    """OWL + SHACL in one graph — the AMBER ``ontology/ontology.ttl`` payload."""
    g = ontology_to_rdf(ontology)
    for triple in shapes_to_rdf(ontology):
        g.add(triple)
    return g


def _numeric_literal(v: float) -> Literal:
    f = float(v)
    if f.is_integer():
        return Literal(int(f))
    return Literal(repr(f), datatype=XSD.double)


# ---------------------------------------------------------------------------
# HEARTH data -> RDF
# ---------------------------------------------------------------------------


def _entity_uris(hearth: Any, stance: Stance, layer: Layer) -> set[str]:
    """Every stance-visible entity URI in the store (the link-resolution
    target set): a value committed under a link property is a real object
    triple only if its value names one of these."""
    out: set[str] = set()
    for shard in hearth.value_shard_items():
        if shard.layer is not layer:
            continue
        for c in shard.cells:
            if c.visible_under(stance):
                out.add(c.entity_uri)
    return out


def _is_resolvable_entity(value: Any, entity_uris: set[str]) -> bool:
    """A link cell value resolves to an object triple iff it is a string that
    is a URI (has a scheme) AND names a known stance-visible entity."""
    if not isinstance(value, str) or "://" not in value:
        return False
    return value in entity_uris


def data_to_rdf(
    hearth: Any,
    ontology: Ontology,
    stance: Stance = CURRENT,
    *,
    layer: Layer = Layer.ENTITY,
) -> Graph:
    """Stance-visible entities as typed resources: ``entity a class``,
    datatype properties as XSD-typed literals, links as object triples, and a
    deterministic ``of:CellAnnotation`` resource per asserted triple carrying
    the cell's interned provenance ref + confidence (RDF-star deferral, see
    module doc). Survivorship is applied per (entity, prop) with the exact
    HEARTH ordering (one rule, third call site).

    A value cell committed under a property the ontology types as a LINK is
    not necessarily a bug in the data — a generic-induced ``is_link`` property
    can hold a foreign-key *literal* (a code or name) that never resolved to an
    entity. When the value names a known entity it is asserted as the object
    triple it is; otherwise it is preserved LOSSLESSLY as an XSD-typed literal
    on a minted ``of:`` annotation property (``of:linkLiteral/<prop>``) and
    flagged with ``of:unresolvedLink true`` rather than silently dropped or
    raising — the export must succeed and round-trip with full provenance."""
    g = Graph()
    _bind(g)
    entity_uris = _entity_uris(hearth, stance, layer)
    for shard in hearth.value_shard_items():
        if shard.layer is not layer:
            continue
        cu = URIRef(shard.class_uri)
        per_key: dict[tuple[str, str], list[tuple[int, Any]]] = {}
        for seq, c in enumerate(shard.cells):
            if c.visible_under(stance):
                per_key.setdefault((c.entity_uri, c.prop), []).append((seq, c))
        for entity_uri in sorted({e for e, _ in per_key}):
            g.add((URIRef(entity_uri), RDF.type, cu))
        for (entity_uri, prop) in sorted(per_key):
            seq, win = min(per_key[(entity_uri, prop)], key=lambda sc: survivorship_key(*sc))
            pdef = _resolve_prop(ontology, shard.class_uri, prop)
            su = URIRef(entity_uri)
            if pdef is not None and pdef.is_link:
                if _is_resolvable_entity(win.value, entity_uris):
                    # the value IS an entity URI: assert the object triple
                    pu = URIRef(pdef.uri)
                    ou = URIRef(str(win.value))
                    g.add((su, pu, ou))
                    ann = URIRef(f"{entity_uri}#prov-{_slug(prop)}")
                    _annotate(g, ann, su, pu, win.prov_ref, win.confidence)
                    g.add((ann, OF.object, ou))
                    continue
                # link-typed property holding a non-entity literal: preserve it
                # losslessly as a typed literal on a minted annotation property
                pu = URIRef(f"{OF}linkLiteral/{_slug(prop)}")
                g.add((su, pu, _value_literal(win.value, None)))
                ann = URIRef(f"{entity_uri}#prov-{_slug(prop)}")
                _annotate(g, ann, su, pu, win.prov_ref, win.confidence)
                g.add((ann, OF.unresolvedLink, Literal(True)))
                continue
            pu = URIRef(pdef.uri) if pdef is not None else URIRef(property_uri(shard.class_uri, prop))
            g.add((su, pu, _value_literal(win.value, pdef)))
            ann = URIRef(f"{entity_uri}#prov-{_slug(prop)}")
            _annotate(g, ann, su, pu, win.prov_ref, win.confidence)
    for lshard in hearth.links.link_shard_items():
        pdef = _resolve_prop(ontology, lshard.class_uri, lshard.predicate)
        pu = (
            URIRef(pdef.uri)
            if pdef is not None
            else URIRef(property_uri(lshard.class_uri, lshard.predicate))
        )
        for seq, link in enumerate(lshard.cells):
            if not link_visible(link, stance):
                continue
            s, o = URIRef(link.subject_uri), URIRef(link.object_uri)
            g.add((s, pu, o))
            digest = hashlib.sha256(
                f"{link.subject_uri}\x1f{lshard.predicate}\x1f{link.object_uri}".encode()
            ).hexdigest()[:16]
            ann = URIRef(f"{link.subject_uri}#prov-{_slug(lshard.predicate)}-{digest}")
            _annotate(g, ann, s, pu, link.prov_ref, link.confidence)
            g.add((ann, OF.object, o))
    return g


def _annotate(
    g: Graph, ann: URIRef, subject: URIRef, predicate: URIRef, prov_ref: str, confidence: float
) -> None:
    g.add((ann, RDF.type, OF.CellAnnotation))
    g.add((ann, OF.subject, subject))
    g.add((ann, OF.predicate, predicate))
    g.add((ann, OF.provRef, Literal(prov_ref)))
    g.add((ann, OF.confidence, Literal(repr(float(confidence)), datatype=XSD.double)))


def _value_literal(value: Any, pdef: Optional[PropertyDef]) -> Literal:
    """XSD-typed literal per the declared datatype; undeclared properties fall
    back to the Python type (canonical-JSON string for compounds)."""
    if pdef is not None:
        dt = pdef.datatype
        if dt is Datatype.INTEGER:
            return Literal(int(value))
        if dt is Datatype.FLOAT:
            return Literal(repr(float(value)), datatype=XSD.double)
        if dt is Datatype.BOOLEAN:
            return Literal(bool(value))
        if dt is Datatype.DATE:
            return Literal(str(value), datatype=XSD.date)
        if dt is Datatype.DATETIME:
            return Literal(str(value), datatype=XSD.dateTime)
        return Literal(str(value), datatype=XSD.string)
    if isinstance(value, bool):
        return Literal(value)
    if isinstance(value, int):
        return Literal(value)
    if isinstance(value, float):
        return Literal(repr(value), datatype=XSD.double)
    if isinstance(value, str):
        return Literal(value, datatype=XSD.string)
    return Literal(
        json.dumps(value, sort_keys=True, separators=(",", ":")), datatype=XSD.string
    )


# ---------------------------------------------------------------------------
# Deterministic serialization
# ---------------------------------------------------------------------------


def sorted_turtle(g: Graph) -> str:
    """Byte-deterministic serialization: one N-Triples line per triple, sorted.
    (N-Triples is a syntactic subset of Turtle, so the output parses as both.)
    Refuses graphs containing blank nodes — by construction we never emit any,
    and bnodes are exactly what breaks cross-run byte stability."""
    for triple in g:
        for term in triple:
            if isinstance(term, BNode):
                raise ExportError(f"blank node in export graph: {triple}")
    lines = sorted(set(g.serialize(format="nt11").splitlines()))
    return "\n".join(line for line in lines if line.strip()) + "\n"


def _bind(g: Graph) -> None:
    g.bind("of", OF)
    g.bind("sh", SH)
    g.bind("owl", OWL)


# ---------------------------------------------------------------------------
# RDF -> Ontology (the round-trip inverse)
# ---------------------------------------------------------------------------

_OF_DATATYPE = {d.value: d for d in Datatype}


def rdf_to_ontology(g: Graph) -> Ontology:
    """Inverse of :func:`ontology_graph`: reconstruct a contracts.Ontology from
    the OWL + annotation layer + SHACL shapes. Order of parents/properties/
    shapes/synonyms comes back normalized (sorted / IRI-index order) — the
    documented round-trip loss."""
    onto = Ontology()
    version = g.value(ONTOLOGY_NODE, OWL.versionInfo)
    if version is not None:
        onto.version = int(version)
    shapes_by_class = _read_shapes(g)
    for cu in sorted(g.subjects(RDF.type, OWL.Class), key=str):
        uri = str(cu)
        props = tuple(
            _read_property(g, pu, link=(pu, RDF.type, OWL.ObjectProperty) in g)
            for pu in sorted(g.subjects(RDFS.domain, cu), key=str)
        )
        definition = ""
        for comment in g.objects(cu, RDFS.comment):
            definition = str(comment)
        onto.add(
            ClassDef(
                uri=uri,
                name=str(g.value(cu, RDFS.label) or uri),
                parents=tuple(sorted(str(p) for p in g.objects(cu, RDFS.subClassOf))),
                properties=props,
                shapes=tuple(shapes_by_class.get(uri, ())),
                definition=definition,
                intent_hash=str(g.value(cu, OF.intentHash) or ""),
                is_event=bool(g.value(cu, OF.isEvent) and g.value(cu, OF.isEvent).toPython()),
                confidence=float(g.value(cu, OF.confidence) or 1.0),
                prov_ref=str(g.value(cu, OF.provRef) or ""),
                disjoint_with=tuple(sorted(str(d) for d in g.objects(cu, OWL.disjointWith))),
            )
        )
    return onto


def _read_property(g: Graph, pu: URIRef, *, link: bool) -> PropertyDef:
    dim_lit = g.value(pu, OF.dimension)
    definition = str(g.value(pu, RDFS.comment) or "")
    return PropertyDef(
        uri=str(pu),
        name=str(g.value(pu, RDFS.label) or ""),
        datatype=_OF_DATATYPE[str(g.value(pu, OF.datatype) or "string")],
        is_link=link,
        range_class=str(g.value(pu, RDFS.range)) if link else None,
        dimension=Dimension(tuple(json.loads(str(dim_lit)))) if dim_lit is not None else None,
        unit=str(g.value(pu, OF.unit)) if g.value(pu, OF.unit) is not None else None,
        cardinality=str(g.value(pu, OF.cardinality) or "one"),
        functional=bool(g.value(pu, OF.functional) and g.value(pu, OF.functional).toPython()),
        synonyms=tuple(sorted(str(s) for s in g.objects(pu, OF.synonym))),
        definition=definition,
    )


def _read_shapes(g: Graph) -> dict[str, list[ShapeConstraint]]:
    out: dict[str, list[ShapeConstraint]] = {}
    for ns in sorted(g.subjects(RDF.type, SH.NodeShape), key=str):
        target = g.value(ns, SH.targetClass)
        if target is None:
            continue
        constraints: list[ShapeConstraint] = []
        # minted IRIs embed a zero-padded index => sorting restores order
        for psu in sorted(g.objects(ns, SH.property), key=str):
            path = str(g.value(psu, SH.path))
            prop = path.rsplit("/prop/", 1)[1] if "/prop/" in path else path
            in_head = g.value(psu, SH["in"])
            in_values = (
                tuple(str(v) for v in Collection(g, in_head)) if in_head is not None else None
            )
            of_dt = g.value(psu, OF.datatype)
            mn, mx = g.value(psu, SH.minInclusive), g.value(psu, SH.maxInclusive)
            max_count = g.value(psu, SH.maxCount)
            constraints.append(
                ShapeConstraint(
                    prop=prop,
                    min_count=int(g.value(psu, SH.minCount) or 0),
                    max_count=int(max_count) if max_count is not None else None,
                    datatype=_OF_DATATYPE[str(of_dt)] if of_dt is not None else None,
                    pattern=str(g.value(psu, SH.pattern))
                    if g.value(psu, SH.pattern) is not None
                    else None,
                    in_values=in_values,
                    min_value=float(mn) if mn is not None else None,
                    max_value=float(mx) if mx is not None else None,
                    unit=str(g.value(psu, OF.unit)) if g.value(psu, OF.unit) is not None else None,
                )
            )
        out[str(target)] = constraints
    return out
