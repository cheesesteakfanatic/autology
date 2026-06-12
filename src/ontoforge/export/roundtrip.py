"""M11 — round-trip harness: one Turtle document, two independent stores
(rdflib and pyoxigraph), equivalent SPARQL answers (whitepaper v1 G7 "parallel
query-equivalence suite"), plus pySHACL conformance.

The equivalence oracle is a RESULT MULTISET: each solution row is normalized
to a tuple of canonical term keys (IRI value, or literal lexical form with
numeric canonicalization and the RDF 1.1 plain-literal == xsd:string
identification), counted with a Counter. Two engines agree iff the Counters
are equal — order-free, duplicate-exact.

Scope note (deviation from the full §11.2 M11 matrix): the v0 store matrix is
rdflib + pyoxigraph (both in the approved dependency set). Jena/Neo4j/Kùzu
require runtimes outside the AMD-0001 environment; the harness API takes any
(load, query) pair, so adding stores is additive.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

import pyoxigraph
from rdflib import RDF, Graph
from rdflib.namespace import SH
from rdflib.term import BNode, Literal, URIRef

__all__ = [
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

_NUMERIC_XSD = {
    "http://www.w3.org/2001/XMLSchema#integer",
    "http://www.w3.org/2001/XMLSchema#int",
    "http://www.w3.org/2001/XMLSchema#long",
    "http://www.w3.org/2001/XMLSchema#decimal",
    "http://www.w3.org/2001/XMLSchema#double",
    "http://www.w3.org/2001/XMLSchema#float",
}
_XSD_STRING = "http://www.w3.org/2001/XMLSchema#string"


class RoundTripError(AssertionError):
    """Two stores disagreed on a query over the same serialized graph."""


def graph_from_turtle(ttl: str) -> Graph:
    g = Graph()
    g.parse(data=ttl, format="turtle")
    return g


def oxigraph_from_turtle(ttl: str) -> "pyoxigraph.Store":
    store = pyoxigraph.Store()
    store.load(ttl.encode("utf-8"), format=pyoxigraph.RdfFormat.TURTLE)
    return store


# ---------------------------------------------------------------------------
# Term normalization (shared canonical key space for both engines)
# ---------------------------------------------------------------------------


def normalize_term(kind: str, value: str, datatype: Optional[str], language: Optional[str]) -> tuple:
    """Engine-independent canonical key for one RDF term.

    kind: 'iri' | 'lit' | 'bnode'. Plain literals are identified with
    xsd:string (RDF 1.1); numeric literals canonicalize their lexical form so
    '01'^^xsd:integer == '1'^^xsd:integer and '2.50'^^xsd:double == '2.5'.
    """
    if kind == "iri":
        return ("iri", value)
    if kind == "bnode":
        # bnode identity is store-local; only its presence is comparable
        return ("bnode",)
    if language:
        return ("lit", value, f"@{language}")
    dt = datatype or _XSD_STRING
    if dt in _NUMERIC_XSD:
        num = float(value)
        lex = str(int(num)) if num.is_integer() else repr(num)
        return ("num", lex)
    return ("lit", value, dt)


def _norm_rdflib(term: Any) -> tuple:
    if term is None:
        return ("unbound",)
    if isinstance(term, URIRef):
        return normalize_term("iri", str(term), None, None)
    if isinstance(term, BNode):
        return normalize_term("bnode", str(term), None, None)
    lit: Literal = term
    return normalize_term(
        "lit", str(lit), str(lit.datatype) if lit.datatype else None, lit.language
    )


def _norm_oxigraph(term: Any) -> tuple:
    if term is None:
        return ("unbound",)
    if isinstance(term, pyoxigraph.NamedNode):
        return normalize_term("iri", term.value, None, None)
    if isinstance(term, pyoxigraph.BlankNode):
        return normalize_term("bnode", term.value, None, None)
    lit: pyoxigraph.Literal = term
    return normalize_term(
        "lit", lit.value, lit.datatype.value if lit.datatype else None, lit.language
    )


# ---------------------------------------------------------------------------
# Query execution -> result multisets
# ---------------------------------------------------------------------------


def query_rdflib(g: Graph, sparql: str) -> Counter:
    result = g.query(sparql)
    names = [str(v) for v in result.vars]  # type: ignore[union-attr]
    out: Counter = Counter()
    for row in result:
        out[tuple(_norm_rdflib(row[i]) for i in range(len(names)))] += 1
    return out


def query_oxigraph(store: "pyoxigraph.Store", sparql: str) -> Counter:
    solutions = store.query(sparql)
    names = [v.value for v in solutions.variables]
    out: Counter = Counter()
    for sol in solutions:
        out[tuple(_norm_oxigraph(sol[name]) for name in names)] += 1
    return out


def assert_store_equivalence(ttl: str, queries: Mapping[str, str]) -> dict[str, Counter]:
    """Load ONE Turtle document into both engines, run every query in both,
    and require identical result multisets. Returns {name: multiset} (from the
    rdflib side) for further comparison against direct-HEARTH answers."""
    g = graph_from_turtle(ttl)
    store = oxigraph_from_turtle(ttl)
    results: dict[str, Counter] = {}
    for name, sparql in queries.items():
        a = query_rdflib(g, sparql)
        b = query_oxigraph(store, sparql)
        if a != b:
            only_a = a - b
            only_b = b - a
            raise RoundTripError(
                f"query {name!r}: rdflib and pyoxigraph disagree\n"
                f"  rdflib-only:    {dict(only_a)}\n"
                f"  pyoxigraph-only: {dict(only_b)}"
            )
        results[name] = a
    return results


# ---------------------------------------------------------------------------
# SHACL validation
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ShaclReport:
    conforms: bool
    text: str
    violations: list[dict[str, str]] = field(default_factory=list)

    def violated(self, focus_suffix: str, component_suffix: str) -> bool:
        """Is there a violation whose focus node ends with `focus_suffix` and
        whose constraint component name ends with `component_suffix`?"""
        return any(
            v["focus"].endswith(focus_suffix) and v["component"].endswith(component_suffix)
            for v in self.violations
        )


def shacl_validate(
    data_graph: Graph, shapes_graph: Graph, ont_graph: Optional[Graph] = None
) -> ShaclReport:
    """pySHACL conformance of a data graph against the exported SHACL shapes.
    `ont_graph` supplies rdfs:subClassOf triples so sh:targetClass reaches
    subclass instances (SHACL-instance semantics) without OWL inference."""
    from pyshacl import validate as _pyshacl_validate

    conforms, results_graph, text = _pyshacl_validate(
        data_graph,
        shacl_graph=shapes_graph,
        ont_graph=ont_graph,
        inference="none",
        abort_on_first=False,
        allow_warnings=False,
    )
    violations: list[dict[str, str]] = []
    for r in results_graph.subjects(RDF.type, SH.ValidationResult):
        violations.append(
            {
                "focus": str(results_graph.value(r, SH.focusNode) or ""),
                "path": str(results_graph.value(r, SH.resultPath) or ""),
                "component": str(results_graph.value(r, SH.sourceConstraintComponent) or ""),
                "message": str(results_graph.value(r, SH.resultMessage) or ""),
            }
        )
    violations.sort(key=lambda v: (v["focus"], v["path"], v["component"]))
    return ShaclReport(conforms=bool(conforms), text=text, violations=violations)
