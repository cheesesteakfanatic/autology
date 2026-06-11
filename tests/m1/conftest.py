"""M1 conftest: expose the FakeLedger fixture (helpers live in m1_helpers.py)."""

from __future__ import annotations

import pytest

from m1_helpers import FakeLedger


@pytest.fixture
def fake_ledger() -> FakeLedger:
    return FakeLedger()
