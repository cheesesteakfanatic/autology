"""Shared fixtures for the WILD corpus suite.

Everything here runs against the COMMITTED ``fixtures/wild`` snapshot — zero
network. The fetcher itself has one slow, skip-if-offline smoke test.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from ontoforge.estates import wild

REPO = Path(__file__).resolve().parents[2]
FIXTURES = REPO / "fixtures" / "wild"


def load_csv(path: Path) -> pd.DataFrame:
    """Wart-preserving load, exactly like generic discovery does."""
    return pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8")


@pytest.fixture(scope="session")
def fixtures_dir() -> Path:
    assert FIXTURES.is_dir(), "fixtures/wild missing — run scripts/fetch_wild_corpus.py"
    return FIXTURES


@pytest.fixture(scope="session")
def manifest(fixtures_dir: Path) -> dict:
    return wild.load_manifest(fixtures_dir)


@pytest.fixture(scope="session")
def datasets(manifest: dict) -> list[dict]:
    return manifest["datasets"]
