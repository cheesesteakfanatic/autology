"""M11 gate 2 — parallel query-equivalence suite.

ONE exported Turtle document (ontology + current-stance data), loaded into two
independent SPARQL engines (rdflib and pyoxigraph). Seven queries — instance
counts, property lookups, a 1-hop join, an rdfs:subClassOf-path subsumption
query, a numeric filter, an edge count, and a reverse join — must return
IDENTICAL result multisets across both stores AND match the answers HEARTH
gives directly (read/scan/traverse/adjacency).
"""

from __future__ import annotations

from collections import Counter

import pytest

from ontoforge.contracts import CURRENT
from ontoforge.export import assert_store_equivalence

NS = "onto://gold/aviation"


def _iri(v: str) -> tuple:
    return ("iri", v)


def _num(v) -> tuple:
    f = float(v)
    return ("num", str(int(f)) if f.is_integer() else repr(f))


def _str(v: str) -> tuple:
    return ("lit", v, "http://www.w3.org/2001/XMLSchema#string")


@pytest.fixture(scope="module")
def queries(world):
    known = world["known_uri"]
    model_uri = world["model_uris"][world["known"]["code"]]
    return {
        "aircraft_count": f"SELECT (COUNT(?x) AS ?n) WHERE {{ ?x a <{NS}/Aircraft> }}",
        "tail_lookup": f"SELECT ?v WHERE {{ <{known}> <{NS}/Aircraft/prop/tail_number> ?v }}",
        "one_hop_model_name": (
            f"SELECT ?mn WHERE {{ <{known}> <{NS}/Aircraft/prop/model> ?m . "
            f"?m <{NS}/AircraftModel/prop/model_name> ?mn }}"
        ),
        "subsumption_agents": (
            "PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> "
            "PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#> "
            f"SELECT (COUNT(?x) AS ?n) WHERE {{ ?x rdf:type/rdfs:subClassOf* <{NS}/Agent> }}"
        ),
        "years_since_1990": (
            f"SELECT ?y WHERE {{ ?x <{NS}/Aircraft/prop/year_mfr> ?y . FILTER(?y >= 1990) }}"
        ),
        "model_edge_count": (
            f"SELECT (COUNT(?o) AS ?n) WHERE {{ ?s <{NS}/Aircraft/prop/model> ?o }}"
        ),
        "reverse_join_tails_of_model": (
            f"SELECT ?t WHERE {{ ?a <{NS}/Aircraft/prop/model> <{model_uri}> . "
            f"?a <{NS}/Aircraft/prop/tail_number> ?t }}"
        ),
    }


@pytest.fixture(scope="module")
def results(full_ttl, queries):
    # raises RoundTripError on any rdflib/pyoxigraph disagreement
    return assert_store_equivalence(full_ttl, queries)


def test_at_least_six_equivalent_queries(results):
    assert len(results) >= 6
    assert all(isinstance(r, Counter) and len(r) > 0 for r in results.values())


def test_class_instance_count_matches_hearth(world, results):
    hearth = world["hearth"]
    n = hearth.scan(f"{NS}/Aircraft", CURRENT).num_rows
    assert results["aircraft_count"] == Counter({(_num(n),): 1})
    assert n == world["n_aircraft"]


def test_property_lookup_matches_hearth(world, results):
    got = world["hearth"].read(world["known_uri"], CURRENT)
    assert results["tail_lookup"] == Counter({(_str(got["tail_number"]),): 1})


def test_one_hop_join_matches_hearth(world, results):
    hearth = world["hearth"]
    [model_uri] = hearth.traverse(world["known_uri"], "model", CURRENT)
    model_name = hearth.read(model_uri, CURRENT)["model_name"]
    assert results["one_hop_model_name"] == Counter({(_str(model_name),): 1})


def test_subsumption_query_matches_hearth(world, results, full_ttl):
    # Operator < Organization < Agent; only Operator has instances, so the
    # rdfs:subClassOf* path must surface exactly the Operator extent.
    n_ops = world["hearth"].scan(f"{NS}/Operator", CURRENT).num_rows
    assert n_ops == world["n_operators"] > 0
    assert results["subsumption_agents"] == Counter({(_num(n_ops),): 1})
    # and a direct-class query does NOT find them (subsumption is load-bearing)
    direct = f"SELECT (COUNT(?x) AS ?n) WHERE {{ ?x a <{NS}/Agent> }}"
    from ontoforge.export import graph_from_turtle, query_rdflib

    assert query_rdflib(graph_from_turtle(full_ttl), direct) == Counter({(_num(0),): 1})


def test_numeric_filter_matches_hearth(world, results):
    table = world["hearth"].scan(f"{NS}/Aircraft", CURRENT)
    years = [y for y in table.column("year_mfr").to_pylist() if y is not None and y >= 1990]
    want = Counter({(_num(y),): n for y, n in Counter(years).items()})
    assert results["years_since_1990"] == want


def test_edge_count_matches_hearth(world, results):
    assert results["model_edge_count"] == Counter({(_num(world["n_links"]),): 1})


def test_reverse_join_matches_hearth(world, results):
    hearth = world["hearth"]
    model_uri = world["model_uris"][world["known"]["code"]]
    subjects = hearth.traverse(model_uri, "model", CURRENT, reverse=True)
    tails = [hearth.read(s, CURRENT)["tail_number"] for s in subjects]
    want = Counter({(_str(t),): n for t, n in Counter(tails).items()})
    assert results["reverse_join_tails_of_model"] == want


def test_data_serialization_deterministic(world, data_ttl):
    from ontoforge.export import data_to_rdf, sorted_turtle

    again = sorted_turtle(data_to_rdf(world["hearth"], world["ontology"]))
    assert again == data_ttl
