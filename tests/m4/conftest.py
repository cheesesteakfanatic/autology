"""M4 STRATA test fixtures.

The aviation estate is profiled ONCE per session (M3 profiling dominates
wall-clock); inductions themselves are cheap and re-run freely per test.
Everything here is deterministic: fixed fixture corpus, seeded profilers,
heuristic (keyless) model tiers, no network.
"""

from __future__ import annotations

import pytest

from ontoforge.estates import load_estate, load_gold_ontology
from ontoforge.profiling import discover_inds, profile_table
from ontoforge.strata import Strata, StrataResult


@pytest.fixture(scope="session")
def estate() -> dict:
    return load_estate()


@pytest.fixture(scope="session")
def profiles(estate) -> list:
    return [
        profile_table(df, estate["metadata"]["tables"][name]["source_id"], name)
        for name, df in estate["tables"].items()
    ]


@pytest.fixture(scope="session")
def inds(estate) -> list:
    return discover_inds(estate["tables"])


@pytest.fixture(scope="session")
def induction(profiles, inds) -> tuple[Strata, StrataResult]:
    strata = Strata()
    result = strata.induce(profiles, inds)
    return strata, result


@pytest.fixture(scope="session")
def gold():
    return load_gold_ontology()
