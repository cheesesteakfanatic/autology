"""Server fixtures: a REAL project directory (the CLI's on-disk conventions —
config.json, state.json, ledger.sqlite, hearth/, ontology.materialized.json)
holding a ~150-row slice of the aviation estate committed through the product
world builder, then ONE FastAPI app + TestClient over it for the whole session.

Zero network: TestClient drives the ASGI app in-process; the world build is
the same code path `ontoforge materialize` runs (see tests/m12/conftest.py).
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ontoforge.estates import load_estate, load_gold_ontology
from ontoforge.hearth import Hearth
from ontoforge.ledger import SqliteLedger
from ontoforge.lodestone.worldbuild import build_estate_world, extend_gold_ontology
from ontoforge.vista._pipeline import save_ontology

ROW_LIMIT = 150


@pytest.fixture(scope="session")
def project(tmp_path_factory) -> Path:
    proj = tmp_path_factory.mktemp("server-project")

    estate = load_estate()
    estate["tables"] = {
        name: df.head(ROW_LIMIT).copy() for name, df in estate["tables"].items()
    }
    gold = extend_gold_ontology(load_gold_ontology())

    ledger = SqliteLedger(str(proj / "ledger.sqlite"))
    try:
        hearth = Hearth(proj / "hearth", ledger)
        stats = build_estate_world(estate, gold, hearth, ledger)
    finally:
        ledger.close()

    # the exact ontology the world was committed under, like `materialize`
    save_ontology(gold, proj / "ontology.materialized.json")
    (proj / "dashboards").mkdir()
    (proj / "config.json").write_text(
        json.dumps(
            {
                "estate": "aviation",
                "ledger": "ledger.sqlite",
                "hearth_root": "hearth",
                "fixtures_dir": estate["metadata"]["fixtures_dir"],
            },
            indent=1,
        ),
        encoding="utf-8",
    )
    (proj / "state.json").write_text(
        json.dumps(
            {
                "limit": ROW_LIMIT,
                "cdc": {},
                "stages": ["ingest", "profile", "induce", "resolve", "materialize"],
                "materialized": {
                    "ontology": "gold",
                    "ontology_file": "ontology.materialized.json",
                    "entities": stats["entities"],
                    "cells": stats["cells"],
                    "links": stats["links"],
                },
            },
            indent=1,
        ),
        encoding="utf-8",
    )
    return proj


@pytest.fixture(scope="session")
def app(project):
    from ontoforge.server import create_app

    return create_app(project)


@pytest.fixture(scope="session")
def world(app):
    return app.state.world


@pytest.fixture(scope="session")
def client(app):
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def ledger_db(project):
    """A second read connection onto the project ledger (sqlite allows it) —
    lets tests verify what the server REALLY persisted, not what it claims."""
    conn = sqlite3.connect(project / "ledger.sqlite")
    yield conn
    conn.close()
