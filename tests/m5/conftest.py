"""Shared fixtures for the M5 suite.

The full batch resolution is expensive (~10 s), so it is computed once per
session and shared; tests that need a SECOND independent run (determinism,
incremental staging) build their own cascade instances.

No network, no fixture regeneration; everything derives from the committed
fixtures/aviation corpus and fixed seeds (CascadeConfig.seed, eval.SPLIT_SEED).
"""

from __future__ import annotations

import pytest

from ontoforge.er import ERCascade, extract_mentions, load_gold
from ontoforge.estates.aviation import load_estate

KINDS = ("aircraft", "operator")


@pytest.fixture(scope="session")
def estate():
    return load_estate()


@pytest.fixture(scope="session")
def mentions(estate):
    return extract_mentions(estate)


@pytest.fixture(scope="session")
def gold():
    return load_gold()


@pytest.fixture(scope="session")
def train_labels(gold):
    return {k: gold.split_labels(k, "train") for k in KINDS}


@pytest.fixture(scope="session")
def test_labels(gold):
    return {k: gold.split_labels(k, "test") for k in KINDS}


@pytest.fixture(scope="session")
def batch(mentions, train_labels):
    """One shared batch resolution: (cascade, result)."""
    cascade = ERCascade()
    result = cascade.run(mentions, train_labels)
    return cascade, result
