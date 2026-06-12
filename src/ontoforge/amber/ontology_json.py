"""M14 — native JSON (de)serialization of contracts.Ontology.

The AMBER bundle ships the ontology twice (§7 item (1)): OWL+SHACL Turtle for
the open-standards stack, and this native JSON for exact, order-preserving
reconstruction on import (tuple order of parents/properties/shapes/synonyms is
preserved here, which the RDF round trip deliberately normalizes away).

The encoding is canonical (sorted keys, compact separators) so the bundle file
is byte-deterministic for a given ontology.
"""

from __future__ import annotations

import json
from typing import Any

from ontoforge.contracts import ClassDef, Datatype, Ontology, PropertyDef, ShapeConstraint
from ontoforge.contracts.units import Dimension


def _prop_to_obj(p: PropertyDef) -> dict[str, Any]:
    return {
        "uri": p.uri,
        "name": p.name,
        "datatype": p.datatype.value,
        "is_link": p.is_link,
        "range_class": p.range_class,
        "dimension": list(p.dimension.exps) if p.dimension is not None else None,
        "unit": p.unit,
        "cardinality": p.cardinality,
        "functional": p.functional,
        "synonyms": list(p.synonyms),
        "definition": p.definition,
    }


def _prop_from_obj(d: dict[str, Any]) -> PropertyDef:
    return PropertyDef(
        uri=d["uri"],
        name=d["name"],
        datatype=Datatype(d["datatype"]),
        is_link=d["is_link"],
        range_class=d["range_class"],
        dimension=Dimension(tuple(d["dimension"])) if d["dimension"] is not None else None,
        unit=d["unit"],
        cardinality=d["cardinality"],
        functional=d["functional"],
        synonyms=tuple(d["synonyms"]),
        definition=d["definition"],
    )


def _shape_to_obj(s: ShapeConstraint) -> dict[str, Any]:
    return {
        "prop": s.prop,
        "min_count": s.min_count,
        "max_count": s.max_count,
        "datatype": s.datatype.value if s.datatype is not None else None,
        "pattern": s.pattern,
        "in_values": list(s.in_values) if s.in_values is not None else None,
        "min_value": s.min_value,
        "max_value": s.max_value,
        "unit": s.unit,
    }


def _shape_from_obj(d: dict[str, Any]) -> ShapeConstraint:
    return ShapeConstraint(
        prop=d["prop"],
        min_count=d["min_count"],
        max_count=d["max_count"],
        datatype=Datatype(d["datatype"]) if d["datatype"] is not None else None,
        pattern=d["pattern"],
        in_values=tuple(d["in_values"]) if d["in_values"] is not None else None,
        min_value=d["min_value"],
        max_value=d["max_value"],
        unit=d["unit"],
    )


def ontology_to_obj(onto: Ontology) -> dict[str, Any]:
    return {
        "format": "ontoforge-ontology",
        "version": onto.version,
        "classes": [
            {
                "uri": c.uri,
                "name": c.name,
                "parents": list(c.parents),
                "properties": [_prop_to_obj(p) for p in c.properties],
                "shapes": [_shape_to_obj(s) for s in c.shapes],
                "definition": c.definition,
                "intent_hash": c.intent_hash,
                "is_event": c.is_event,
                "confidence": c.confidence,
                "prov_ref": c.prov_ref,
                "disjoint_with": list(c.disjoint_with),
            }
            for _, c in sorted(onto.classes.items())
        ],
    }


def ontology_from_obj(d: dict[str, Any]) -> Ontology:
    onto = Ontology(version=d["version"])
    for c in d["classes"]:
        onto.add(
            ClassDef(
                uri=c["uri"],
                name=c["name"],
                parents=tuple(c["parents"]),
                properties=tuple(_prop_from_obj(p) for p in c["properties"]),
                shapes=tuple(_shape_from_obj(s) for s in c["shapes"]),
                definition=c["definition"],
                intent_hash=c["intent_hash"],
                is_event=c["is_event"],
                confidence=c["confidence"],
                prov_ref=c["prov_ref"],
                disjoint_with=tuple(c["disjoint_with"]),
            )
        )
    return onto


def dumps(onto: Ontology) -> str:
    return json.dumps(ontology_to_obj(onto), sort_keys=True, separators=(",", ":")) + "\n"


def loads(text: str) -> Ontology:
    return ontology_from_obj(json.loads(text))
