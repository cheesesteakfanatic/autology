"""Engineer-layer fixtures: a tiny REAL playground world (two joinable tables)
materialized through the live build, plus a fresh EngineerService factory.

Fast and deterministic: synthetic 3–4 row tables, built inline; zero network.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from ontoforge.contracts import SpineProfile
from ontoforge.engineer import EngineerService
from ontoforge.engineer.commands import SchemaView
from ontoforge.hearth import Hearth
from ontoforge.ledger import SqliteLedger
from ontoforge.pipeline.playground import PlaygroundJob
from ontoforge.spine import DecisionSpine
from ontoforge.vista._pipeline import load_ontology


@pytest.fixture(scope="module")
def play_world(tmp_path_factory) -> Path:
    """Build a tiny playground world: Catalog(sku,pname,country) +
    Saleline(line_id,sku,qty) with salelines.sku ⊆ catalog.sku (full
    coverage), so a 'link salelines to catalog on sku' becomes a confirmed
    join."""
    tmp = tmp_path_factory.mktemp("engineer-world")
    src = tmp / "src"
    src.mkdir()
    pd.DataFrame(
        {"sku": ["s1", "s2", "s3"], "pname": ["Widget", "Gadget", "Gizmo"], "country": ["US", "UK", "US"]}
    ).to_csv(src / "catalog.csv", index=False)
    pd.DataFrame(
        {"line_id": ["l1", "l2", "l3"], "sku": ["s1", "s2", "s3"], "qty": ["1", "2", "3"]}
    ).to_csv(src / "salelines.csv", index=False)
    play = tmp / "play"
    job = PlaygroundJob(
        job_id="ew",
        selections=[
            ("c", "catalog", src / "catalog.csv"),
            ("s", "salelines", src / "salelines.csv"),
        ],
        project_dir=play,
    )
    job.run_sync()
    return play


@pytest.fixture()
def make_service(play_world):
    """A factory: a FRESH EngineerService over the built world each call (so
    apply/undo tests start from a clean engine state)."""
    ledger = SqliteLedger(str(play_world / "ledger.sqlite"))
    hearth = Hearth(play_world / "hearth", ledger)

    def _make() -> EngineerService:
        onto = load_ontology(play_world / "ontology.materialized.json")
        spine = DecisionSpine(SpineProfile(), model_client=None, ledger=ledger)
        return EngineerService(onto, hearth=hearth, ledger=ledger, spine=spine)

    yield _make
    ledger.close()


@pytest.fixture()
def schema(play_world) -> SchemaView:
    onto = load_ontology(play_world / "ontology.materialized.json")
    return SchemaView.from_world(onto)
