"""M11 fixtures: one shared estate world + its exported RDF documents."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from world import build_world  # noqa: E402


@pytest.fixture(scope="package")
def world(tmp_path_factory):
    w = build_world(tmp_path_factory.mktemp("m11_world"))
    yield w
    w["ledger"].close()


@pytest.fixture(scope="package")
def ontology_ttl(world) -> str:
    from ontoforge.export import ontology_graph, sorted_turtle

    return sorted_turtle(ontology_graph(world["ontology"]))


@pytest.fixture(scope="package")
def data_ttl(world) -> str:
    from ontoforge.export import data_to_rdf, sorted_turtle

    return sorted_turtle(data_to_rdf(world["hearth"], world["ontology"]))


@pytest.fixture(scope="package")
def full_ttl(ontology_ttl, data_ttl) -> str:
    """Ontology + data in one document (subsumption queries need both)."""
    return ontology_ttl + data_ttl
