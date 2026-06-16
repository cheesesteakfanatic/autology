"""tests/cdc conftest: in-memory FakeLedger fixture (register_atoms only).

Mirrors tests/m1: the connectors depend on M0 only through the structural
``register_atoms`` slice, so tests use a tiny fake rather than importing the real
SqliteLedger. Zero network, zero external services. The FakeLedger class lives in
``cdc_helpers`` (a uniquely-named module) so direct imports in test bodies are not
shadowed by another suite's ``conftest`` under pytest's prepend import mode.
"""

from __future__ import annotations

import pytest

from cdc_helpers import FakeLedger


@pytest.fixture
def fake_ledger() -> FakeLedger:
    return FakeLedger()
