"""Gold-artifact loaders for estate fixtures (whitepaper §17.4 Tier-2).

`load_gold_ontology` materializes ``gold/mini_ontology.json`` — the hand-built
gold mini-ontology that doubles as the FROZEN ontology for the §11.3 de-risking
vertical slice — into a :class:`ontoforge.contracts.Ontology`.

Constraint (H): every ClassDef carries a non-empty ``prov_ref`` pointing into
the gold artifact that produced it; the loader refuses classes without one.

JSON dialect (authored by ``scripts/build_aviation_fixtures.py``):

.. code-block:: json

    {
      "estate": "aviation",
      "version": 1,
      "namespace": "onto://gold/aviation",
      "provenance": "gold://aviation/mini_ontology.json",
      "classes": [
        {"name": "...", "parents": ["..."], "is_event": false,
         "definition": "...",
         "properties": [{"name": "...", "datatype": "float",
                         "dimension": {"m": 1}, "unit": "ft", ...}],
         "shapes": [{"prop": "...", "min_count": 1, ...}]}
      ]
    }

Class references (parents, link ranges) are by *name*; the loader resolves them
to URIs ``{namespace}/{name}`` and validates that every reference exists.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

import xxhash

from ontoforge.contracts import (
    ClassDef,
    Datatype,
    Ontology,
    PropertyDef,
    ShapeConstraint,
    property_uri,
)
from ontoforge.contracts.units import Dimension, dim


def _dimension(spec: Optional[dict[str, int]]) -> Optional[Dimension]:
    if spec is None:
        return None
    return dim(**spec)


def _intent_hash(name: str, prop_names: list[str]) -> str:
    """Stable identity anchor: hash of the class's defining attribute set
    (mirrors §3.4.4 intent-hash-stable URIs for the induced path)."""
    h = xxhash.xxh3_64()
    h.update(name.encode())
    for p in sorted(prop_names):
        h.update(b"\x1f")
        h.update(p.encode())
    return f"{h.intdigest():016x}"


def load_gold_ontology(path: str | Path) -> Ontology:
    """Load and validate a gold mini-ontology JSON file into contracts.Ontology.

    Raises ``ValueError`` on dangling parent/range references, duplicate class
    names, shapes citing unknown properties, or missing provenance.
    """
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    ns = data["namespace"]
    prov_base = data.get("provenance", "")
    if not prov_base:
        raise ValueError("gold ontology missing top-level provenance ref (constraint H)")

    names = [c["name"] for c in data["classes"]]
    if len(set(names)) != len(names):
        raise ValueError("duplicate class names in gold ontology")
    uri_of = {n: f"{ns}/{n}" for n in names}

    onto = Ontology(version=int(data.get("version", 1)))
    for c in data["classes"]:
        c_uri = uri_of[c["name"]]
        for parent in c.get("parents", []):
            if parent not in uri_of:
                raise ValueError(f"class {c['name']}: unknown parent {parent!r}")
        props: list[PropertyDef] = []
        for p in c.get("properties", []):
            range_name = p.get("range")
            if p.get("is_link", False):
                if range_name not in uri_of:
                    raise ValueError(
                        f"class {c['name']}: link property {p['name']!r} has "
                        f"unknown range {range_name!r}"
                    )
                range_uri: Optional[str] = uri_of[range_name]
            else:
                if range_name is not None:
                    raise ValueError(
                        f"class {c['name']}: non-link property {p['name']!r} has a range"
                    )
                range_uri = None
            props.append(
                PropertyDef(
                    uri=property_uri(c_uri, p["name"]),
                    name=p["name"],
                    datatype=Datatype(p.get("datatype", "string")),
                    is_link=bool(p.get("is_link", False)),
                    range_class=range_uri,
                    dimension=_dimension(p.get("dimension")),
                    unit=p.get("unit"),
                    cardinality=p.get("cardinality", "one"),
                    functional=bool(p.get("functional", False)),
                    synonyms=tuple(p.get("synonyms", [])),
                    definition=p.get("definition", ""),
                )
            )
        prop_names = [p.name for p in props]
        shapes: list[ShapeConstraint] = []
        for s in c.get("shapes", []):
            if s["prop"] not in prop_names:
                raise ValueError(
                    f"class {c['name']}: shape cites unknown property {s['prop']!r}"
                )
            shapes.append(
                ShapeConstraint(
                    prop=s["prop"],
                    min_count=int(s.get("min_count", 0)),
                    max_count=s.get("max_count"),
                    datatype=Datatype(s["datatype"]) if s.get("datatype") else None,
                    pattern=s.get("pattern"),
                    in_values=tuple(s["in_values"]) if s.get("in_values") else None,
                    min_value=s.get("min_value"),
                    max_value=s.get("max_value"),
                    unit=s.get("unit"),
                )
            )
        onto.add(
            ClassDef(
                uri=c_uri,
                name=c["name"],
                parents=tuple(uri_of[p] for p in c.get("parents", [])),
                properties=tuple(props),
                shapes=tuple(shapes),
                definition=c.get("definition", ""),
                intent_hash=_intent_hash(c["name"], prop_names),
                is_event=bool(c.get("is_event", False)),
                confidence=1.0,
                prov_ref=f"{prov_base}#{c['name']}",
            )
        )

    # post-validate: every parent/range URI resolves inside the ontology
    for cls in onto.iter_classes():
        for parent in cls.parents:
            if onto.get(parent) is None:
                raise ValueError(f"dangling parent {parent} on {cls.name}")
        for p in cls.properties:
            if p.is_link and onto.get(p.range_class or "") is None:
                raise ValueError(f"dangling range {p.range_class} on {cls.name}.{p.name}")
    return onto


def load_competency_questions(path: str | Path) -> dict[str, Any]:
    """Parse the competency-question gold artifact (yamlite YAML subset)."""
    from . import yamlite

    return yamlite.loads(Path(path).read_text(encoding="utf-8"))
