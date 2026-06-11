"""M10 conftest: gold ontology, session-built base HEARTH store (copied per
test via copytree — Hearth state is fully reconstructable from Parquet), and
the precomputed base-version battery answers."""

from __future__ import annotations

import shutil

import pytest

from ontoforge.estates import load_gold_ontology
from ontoforge.hearth import Hearth
from ontoforge.ledger import SqliteLedger
from ontoforge.temper import execute, lift

from m10_helpers import BATTERY, build_base_store


@pytest.fixture(scope="session")
def gold():
    return load_gold_ontology()


@pytest.fixture(scope="session")
def session_ledger():
    return SqliteLedger()


@pytest.fixture(scope="session")
def base_store_dir(tmp_path_factory, session_ledger):
    root = tmp_path_factory.mktemp("m10-base") / "hearth"
    build_base_store(root, session_ledger)
    return root


@pytest.fixture(scope="session")
def base_answers(base_store_dir, session_ledger, gold):
    h = Hearth(base_store_dir, session_ledger)
    return {q: execute(lift(q, gold), h, gold) for q in BATTERY}


@pytest.fixture
def clone_store(base_store_dir, session_ledger, tmp_path):
    """A fresh Hearth on a private copy of the base store."""

    def _clone() -> Hearth:
        dest = tmp_path / f"hearth-{len(list(tmp_path.iterdir()))}"
        shutil.copytree(base_store_dir, dest)
        return Hearth(dest, session_ledger)

    return _clone


@pytest.fixture
def ledger():
    return SqliteLedger()
