"""M6 conftest: ledger + Hearth fixtures (helpers in m6_helpers.py)."""

from __future__ import annotations

import pytest

from ontoforge.hearth import Hearth
from ontoforge.ledger import SqliteLedger


@pytest.fixture
def ledger() -> SqliteLedger:
    return SqliteLedger()


@pytest.fixture
def hearth(tmp_path, ledger) -> Hearth:
    return Hearth(tmp_path / "hearth", ledger)


@pytest.fixture
def gold_hearth(tmp_path, ledger) -> Hearth:
    from ontoforge.estates import load_gold_ontology

    return Hearth(tmp_path / "hearth", ledger, load_gold_ontology())
