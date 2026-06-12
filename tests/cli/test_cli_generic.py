"""CLI end-to-end tests for a GENERIC project: `ontoforge init --source <dir>`
on a plain directory of CSVs (the deterministic retail corpus), then the full
chain ingest -> profile -> induce -> resolve -> materialize -> ask ->
dashboard -> status — everything running over the ontology OntoForge INDUCED
(generic estates have no gold ontology to fall back on).
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ontoforge.cli import app

runner = CliRunner()


def _build_retail_frames():
    """The tests/pipeline retail corpus builder, loaded by path (test packages
    have no __init__.py, so cross-package imports go through importlib)."""
    src = Path(__file__).resolve().parent.parent / "pipeline" / "conftest.py"
    spec = importlib.util.spec_from_file_location("retail_corpus_conftest", src)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod.build_retail_frames()


@pytest.fixture(scope="module")
def gproject(tmp_path_factory):
    """One generic project, the full chain run once (module scope: the chain IS
    the test subject; per-test asserts inspect each stage's output)."""
    frames = _build_retail_frames()
    src = tmp_path_factory.mktemp("retail-src")
    for name, df in frames.items():
        public = df[[c for c in df.columns if not c.startswith("_")]]
        public.to_csv(src / f"{name}.csv", index=False)

    proj = tmp_path_factory.mktemp("cli-generic") / "proj"
    steps = [
        ["init", str(proj), "--source", str(src)],
        ["ingest", "-p", str(proj)],
        ["profile", "-p", str(proj)],
        ["induce", "-p", str(proj)],
        ["resolve", "-p", str(proj)],
        ["materialize", "-p", str(proj)],
        ["ask", "How many orders are there?", "-p", str(proj)],
        ["dashboard", "supplier overview", "-p", str(proj)],
        ["status", "-p", str(proj)],
    ]
    outputs = {}
    for argv in steps:
        result = runner.invoke(app, argv)
        assert result.exit_code == 0, f"{argv}: {result.output}\n{result.exception}"
        outputs[argv[0]] = result.output
    return proj, outputs, frames


def test_init_generic_estate(gproject):
    proj, outputs, _ = gproject
    cfg = json.loads((proj / "config.json").read_text())
    assert cfg["estate"] == "generic"
    assert Path(cfg["source_dir"]).is_dir()
    assert "estate: generic" in outputs["init"]


def test_init_rejects_missing_source_dir(tmp_path):
    result = runner.invoke(app, ["init", str(tmp_path / "p"), "--source", str(tmp_path / "nope")])
    assert result.exit_code == 1
    assert "not a directory" in result.output


def test_ingest_discovers_and_mirrors_every_csv(gproject):
    proj, outputs, frames = gproject
    assert "atoms in ledger" in outputs["ingest"]
    manifests = list((proj / "raw" / "raw").rglob("manifest.jsonl"))
    assert len(manifests) == len(frames)
    # CDC steady state on rerun
    result = runner.invoke(app, ["ingest", "-p", str(proj)])
    assert result.exit_code == 0, result.output
    assert "total deltas this cycle: 0" in result.output


def test_profile_covers_all_discovered_tables(gproject):
    _, outputs, frames = gproject
    for table in frames:
        assert table in outputs["profile"]
    assert "INDs" in outputs["profile"]


def test_induce_yields_generic_ontology_without_gold(gproject):
    proj, outputs, _ = gproject
    data = json.loads((proj / "ontology.json").read_text())
    assert data["format"] == "ontoforge.cli/ontology-v1"
    names = {c["name"] for c in data["classes"]}
    assert len(data["classes"]) >= 4
    assert {"Customer", "Order", "Product", "Supplier"} <= names
    # generic estates have no gold artifacts to score against
    assert "vs gold" not in outputs["induce"]


def test_resolve_runs_generic_er_over_identity_domains(gproject):
    proj, outputs, _ = gproject
    payload = json.loads((proj / "resolved.json").read_text())
    assert payload["methods"].get("Supplier") == "er-cascade"
    assert payload["clusters"]["Supplier"]
    assert payload["mention_to_uri"]
    assert "er-cascade" in outputs["resolve"]


def test_materialize_commits_world_under_induced_ontology(gproject):
    proj, outputs, frames = gproject
    out = outputs["materialize"]
    assert "induced" in out
    assert "committed" in out and "HEARTH" in out
    state = json.loads((proj / "state.json").read_text())
    mat = state["materialized"]
    assert mat["ontology"] == "induced"
    assert (proj / mat["ontology_file"]).is_file()
    n_rows = sum(len(df) for df in frames.values())
    assert mat["entities"] >= n_rows  # one entity per row + latent types
    assert mat["cells"] > mat["entities"]
    assert mat["links"] > 0
    assert list((proj / "hearth").rglob("*.parquet")), "no HEARTH shards written"


def test_ask_answers_with_citations_over_induced_world(gproject):
    _, outputs, frames = gproject
    out = outputs["ask"]
    assert "ABSTAINED" not in out
    assert str(len(frames["orders"])) in out
    assert "citations" in out


def test_ask_traverses_er_resolved_link(gproject):
    """Supplier rating of a product: only reachable through the ER-built
    Product -> Supplier link (the spelling variants break any IND)."""
    proj, _, frames = gproject
    products, suppliers = frames["products"], frames["suppliers"]
    true_name = products.loc[products["product_id"] == "P0042", "_supplier_true"].iloc[0]
    truth = suppliers.loc[suppliers["supplier_name"] == true_name, "rating"].iloc[0]
    result = runner.invoke(
        app, ["ask", "What is the rating of the supplier of product P0042?", "-p", str(proj)]
    )
    assert result.exit_code == 0, result.output
    assert "ABSTAINED" not in result.output
    assert str(truth) in result.output


def test_dashboard_proposes_over_induced_ontology(gproject):
    proj, outputs, _ = gproject
    assert "induced ontology" in outputs["dashboard"]
    files = sorted((proj / "dashboards").glob("dashboard_*_chart_*.vl.json"))
    assert files, "no Vega-Lite charts saved"
    spec = json.loads(files[0].read_text())
    assert "encoding" in spec and "mark" in spec


def test_status_reports_generic_chain(gproject):
    _, outputs, _ = gproject
    out = outputs["status"]
    assert "estate: generic" in out
    for stage in ("ingest", "profile", "induce", "resolve", "materialize"):
        assert stage in out
