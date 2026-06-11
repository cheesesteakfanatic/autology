"""M8 conftest: aviation estate + gold ontology fixtures."""

from __future__ import annotations

import pytest

from ontoforge.estates import load_estate, load_gold_ontology


@pytest.fixture(scope="session")
def estate():
    return load_estate()


@pytest.fixture(scope="session")
def gold_ontology():
    return load_gold_ontology()
