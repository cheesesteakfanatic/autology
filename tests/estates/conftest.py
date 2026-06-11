"""Shared fixtures for the aviation hero-estate tests (whitepaper §17.2.1/§17.4)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
FIXTURES = REPO / "fixtures" / "aviation"
GENERATOR = REPO / "scripts" / "build_aviation_fixtures.py"


def load_generator():
    spec = importlib.util.spec_from_file_location("build_aviation_fixtures", GENERATOR)
    mod = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("build_aviation_fixtures", mod)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="session")
def generator():
    return load_generator()


@pytest.fixture(scope="session")
def estate():
    from ontoforge.estates import aviation

    return aviation.load_estate(FIXTURES)


@pytest.fixture(scope="session")
def row_index(estate):
    """table -> row_key -> row(dict) using the estate's key-column convention."""
    from ontoforge.estates import aviation

    idx: dict[str, dict[str, dict]] = {}
    for tname, df in estate["tables"].items():
        keys = aviation.TABLES[tname]["key_columns"]
        table_idx: dict[str, dict] = {}
        for rec in df.to_dict(orient="records"):
            rk = "|".join(str(rec[c]).strip() for c in keys)
            table_idx[rk] = rec
        idx[tname] = table_idx
    return idx
