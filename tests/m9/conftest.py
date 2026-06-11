"""M9 conftest: estate / gold-ontology / ledger fixtures.

(The fast per-column profiling helper lives in m9_corruptions.quick_profile,
imported by the suites that stream profiles.)
"""

from __future__ import annotations

import pytest

from ontoforge.contracts import Ontology
from ontoforge.estates import load_estate, load_gold_ontology


@pytest.fixture(scope="session")
def estate() -> dict:
    return load_estate()


@pytest.fixture(scope="session")
def gold_ontology() -> Ontology:
    return load_gold_ontology()


@pytest.fixture
def ledger():
    from ontoforge.ledger import SqliteLedger

    return SqliteLedger()
