"""Ontology artifact types (whitepaper §1.2): O = (C, ≤_C, P, ax) + SHACL-style shapes Σ.

These are the *induced* artifacts STRATA emits and everything downstream consumes:
HEARTH shard layout (M6), ANVIL's synthesis target (M8), WARDEN expectations (M9),
TEMPER operands (M10), RDF export (M11), LODESTONE grounding space (M12).

Class URIs are intent-hash-stable (§3.4.4): the URI derives from the concept's
intent (its defining attribute set), not from discovery order, so re-induction on
permuted input yields identical URIs (M4 acceptance test).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Iterator, Optional

from .units import Dimension


class Datatype(str, Enum):
    STRING = "string"
    TEXT = "text"          # long-form; eligible for textJoin
    INTEGER = "integer"
    FLOAT = "float"
    BOOLEAN = "boolean"
    DATE = "date"
    DATETIME = "datetime"


@dataclass(frozen=True, slots=True)
class PropertyDef:
    uri: str
    name: str
    datatype: Datatype = Datatype.STRING
    is_link: bool = False                  # object property?
    range_class: Optional[str] = None      # class URI when is_link
    dimension: Optional[Dimension] = None  # physical dimension (None = not a measure)
    unit: Optional[str] = None             # canonical unit symbol
    cardinality: str = "one"               # "one" | "many"
    functional: bool = False               # FD-backed: key determines this value
    synonyms: tuple[str, ...] = ()
    definition: str = ""


@dataclass(frozen=True, slots=True)
class ShapeConstraint:
    """One SHACL-class constraint on (class, property). WARDEN compiles these to
    runtime expectations; pySHACL validates the exported graph against them."""

    prop: str                              # property name
    min_count: int = 0
    max_count: Optional[int] = None
    datatype: Optional[Datatype] = None
    pattern: Optional[str] = None          # regex on lexical form
    in_values: Optional[tuple[str, ...]] = None
    min_value: Optional[float] = None
    max_value: Optional[float] = None
    unit: Optional[str] = None


@dataclass(frozen=True, slots=True)
class ClassDef:
    uri: str
    name: str
    parents: tuple[str, ...] = ()          # class URIs; multiple inheritance allowed (FCA)
    properties: tuple[PropertyDef, ...] = ()
    shapes: tuple[ShapeConstraint, ...] = ()
    definition: str = ""
    intent_hash: str = ""                  # STRATA concept identity anchor
    is_event: bool = False                 # §3.5 event-like classes
    confidence: float = 1.0
    prov_ref: str = ""
    disjoint_with: tuple[str, ...] = ()

    def prop(self, name: str) -> Optional[PropertyDef]:
        for p in self.properties:
            if p.name == name:
                return p
        return None


@dataclass(slots=True)
class Ontology:
    """O^(t): classes with subsumption partial order, plus a version counter that
    TEMPER bumps on every applied operator (the morphism ledger references versions)."""

    classes: dict[str, ClassDef] = field(default_factory=dict)
    version: int = 0

    def add(self, c: ClassDef) -> None:
        self.classes[c.uri] = c

    def get(self, uri: str) -> Optional[ClassDef]:
        return self.classes.get(uri)

    def by_name(self, name: str) -> Optional[ClassDef]:
        low = name.lower()
        for c in self.classes.values():
            if c.name.lower() == low:
                return c
        return None

    def ancestors(self, uri: str) -> set[str]:
        out: set[str] = set()
        stack = list(self.classes[uri].parents) if uri in self.classes else []
        while stack:
            p = stack.pop()
            if p in out or p not in self.classes:
                continue
            out.add(p)
            stack.extend(self.classes[p].parents)
        return out

    def descendants(self, uri: str) -> set[str]:
        out: set[str] = set()
        for c_uri, c in self.classes.items():
            if uri in self.ancestors(c_uri):
                out.add(c_uri)
        return out

    def subsumes(self, ancestor: str, descendant: str) -> bool:
        return ancestor == descendant or ancestor in self.ancestors(descendant)

    def iter_classes(self) -> Iterator[ClassDef]:
        yield from self.classes.values()

    def link_properties(self) -> Iterator[tuple[ClassDef, PropertyDef]]:
        for c in self.classes.values():
            for p in c.properties:
                if p.is_link:
                    yield c, p

    def replace_class(self, c: ClassDef) -> None:
        self.classes[c.uri] = c

    def clone(self) -> "Ontology":
        return Ontology(classes=dict(self.classes), version=self.version)


def class_uri_from_intent(intent_hash: str) -> str:
    return f"onto://class/{intent_hash}"


def property_uri(class_uri_or_ns: str, prop_name: str) -> str:
    return f"{class_uri_or_ns}/prop/{prop_name}"
