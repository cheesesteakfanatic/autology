"""M12 fixtures: the aviation estate committed into a REAL HEARTH under the
frozen gold mini-ontology (§11.3 de-risking slice), via the PRODUCT world
builder (ontoforge.lodestone.worldbuild) — the same code path the CLI
`materialize` command runs. LODESTONE itself never sees the CSVs.
"""

from __future__ import annotations

import pytest

from ontoforge.contracts import SpineProfile
from ontoforge.estates import load_competency_questions, load_estate, load_gold_ontology
from ontoforge.hearth import Hearth
from ontoforge.ledger import SqliteLedger
from ontoforge.lodestone import Lodestone
from ontoforge.lodestone.worldbuild import build_estate_world, extend_gold_ontology
from ontoforge.spine import DecisionSpine


@pytest.fixture(scope="session")
def estate():
    return load_estate()


@pytest.fixture(scope="session")
def gold_onto():
    return extend_gold_ontology(load_gold_ontology())


@pytest.fixture(scope="session")
def ledger():
    led = SqliteLedger(":memory:")
    yield led
    led.close()


@pytest.fixture(scope="session")
def hearth_world(tmp_path_factory, estate, gold_onto, ledger):
    hearth = Hearth(tmp_path_factory.mktemp("m12-hearth") / "store", ledger)
    build_estate_world(estate, gold_onto, hearth, ledger)
    return hearth


@pytest.fixture(scope="session")
def spine():
    return DecisionSpine(SpineProfile(), model_client=None)


@pytest.fixture(scope="session")
def lodestone(gold_onto, hearth_world, ledger, spine):
    return Lodestone(gold_onto, hearth_world, ledger, spine)


@pytest.fixture(scope="session")
def competency():
    return load_competency_questions()
