"""CLI end-to-end tests: the REAL pipeline against a tmp project directory.

The chain init -> ingest -> profile -> induce -> resolve -> materialize ->
dashboard -> status runs green on the real aviation fixtures, subsampled via
--limit for speed. CDC proof: a second ingest reports zero deltas. M12/M14
commands degrade gracefully when absent (simulated via import blocking) and
adapt when present (importorskip).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ontoforge.cli import app

LIMIT = 60
runner = CliRunner()


@pytest.fixture(scope="module")
def project(tmp_path_factory) -> Path:
    """One project, the full chain run once (module scope: the chain IS the test
    subject; per-test asserts then inspect each stage's output)."""
    proj = tmp_path_factory.mktemp("cli") / "proj"
    steps = [
        ["init", str(proj)],
        ["ingest", "-p", str(proj), "--limit", str(LIMIT)],
        ["profile", "-p", str(proj)],
        ["induce", "-p", str(proj)],
        ["resolve", "-p", str(proj)],
        ["materialize", "-p", str(proj)],
        ["dashboard", "supplier risk", "-p", str(proj)],
        ["status", "-p", str(proj)],
    ]
    outputs = {}
    for argv in steps:
        result = runner.invoke(app, argv)
        assert result.exit_code == 0, f"{argv}: {result.output}\n{result.exception}"
        outputs[argv[0]] = result.output
    return proj, outputs


def test_init_creates_layout(project):
    proj, outputs = project
    cfg = json.loads((proj / "config.json").read_text())
    assert cfg["estate"] == "aviation"
    assert (proj / "state.json").is_file()
    assert "initialized" in outputs["init"]


def test_ingest_registers_atoms_and_mirrors_raw(project):
    proj, outputs = project
    assert "atoms in ledger" in outputs["ingest"]
    assert (proj / "ledger.sqlite").is_file()
    # RAW mirror: one parquet + manifest per (source, table)
    manifests = list((proj / "raw" / "raw").rglob("manifest.jsonl"))
    assert len(manifests) == 5
    parquets = list((proj / "raw" / "raw").rglob("*.parquet"))
    assert len(parquets) == 5


def test_ingest_rerun_is_zero_deltas(project):
    """CDC proof: pulling unchanged sources produces no deltas, no new atoms."""
    proj, _ = project
    result = runner.invoke(app, ["ingest", "-p", str(proj)])
    assert result.exit_code == 0, result.output
    assert "total deltas this cycle: 0" in result.output
    assert "no changes detected" in result.output


def test_profile_reports_all_tables(project):
    _, outputs = project
    out = outputs["profile"]
    for table in ("faa_master", "faa_acftref", "asrs_repor", "ntsb_events", "maintenanc"):
        assert table in out  # rich may truncate long names
    assert "INDs" in out


def test_induce_saves_ontology_and_prints_tree(project):
    proj, outputs = project
    assert (proj / "ontology.json").is_file()
    data = json.loads((proj / "ontology.json").read_text())
    assert data["format"] == "ontoforge.cli/ontology-v1"
    assert len(data["classes"]) >= 5
    assert "induced ontology" in outputs["induce"]
    assert "vs gold" in outputs["induce"]  # P/R line printed when gold available

    # the documented dialect round-trips
    from ontoforge.vista._pipeline import ontology_from_json

    onto = ontology_from_json(data)
    assert len(onto.classes) == len(data["classes"])


def test_resolve_saves_clusters_with_f1(project):
    proj, outputs = project
    payload = json.loads((proj / "resolved.json").read_text())
    assert set(payload["clusters"]) >= {"aircraft", "operator"}
    assert payload["mention_to_uri"]
    assert "aircraft" in outputs["resolve"]


def test_materialize_commits_into_hearth(project):
    proj, outputs = project
    assert "committed" in outputs["materialize"]
    assert "HEARTH" in outputs["materialize"]
    cells = list((proj / "hearth").rglob("*.parquet"))
    assert cells, "no HEARTH shards written"


def test_dashboard_saves_vega_files(project):
    proj, outputs = project
    files = sorted((proj / "dashboards").glob("dashboard_*_chart_*.vl.json"))
    assert len(files) >= 9  # 3 dashboards x (1 KPI + >=2 breakdowns)
    spec = json.loads(files[0].read_text())
    assert spec["$schema"].endswith("vega-lite/v5.json")
    assert "encoding" in spec and "mark" in spec
    bundles = sorted((proj / "dashboards").glob("dashboard_?.json"))
    assert len(bundles) == 3


def test_status_summarizes_ledger(project):
    _, outputs = project
    out = outputs["status"]
    assert "atoms" in out
    assert "decisions tier" in out
    assert "cost (tokens)" in out
    for stage in ("ingest", "profile", "induce", "resolve", "materialize"):
        assert stage in out


def test_status_before_init_fails_cleanly(tmp_path):
    result = runner.invoke(app, ["status", "-p", str(tmp_path / "nope")])
    assert result.exit_code == 1
    assert "no project" in result.output


# ---------------------------------------------------------- lazy-import seams


def _block_import(monkeypatch, module: str) -> None:
    """Make the CLI's lazy import of `module` fail (simulates M12/M14 absent).

    The CLI imports via importlib.import_module, so patch that seam directly
    (a builtins.__import__ patch would not intercept it)."""
    import importlib

    import ontoforge.cli as cli_mod

    real = importlib.import_module

    def fake_import_module(name, *args, **kwargs):
        if name == module or name.startswith(module + "."):
            raise ImportError(f"blocked: {name}")
        return real(name, *args, **kwargs)

    monkeypatch.setattr(cli_mod.importlib, "import_module", fake_import_module)


def test_ask_degrades_when_lodestone_absent(project, monkeypatch):
    proj, _ = project
    _block_import(monkeypatch, "ontoforge.lodestone")
    result = runner.invoke(app, ["ask", "how many aircraft?", "-p", str(proj)])
    assert result.exit_code == 0
    assert "LODESTONE not yet available" in result.output


def test_snapshot_degrades_when_amber_absent(project, monkeypatch, tmp_path):
    proj, _ = project
    _block_import(monkeypatch, "ontoforge.amber")
    result = runner.invoke(app, ["snapshot", str(tmp_path / "bundle"), "-p", str(proj)])
    assert result.exit_code == 0
    assert "AMBER not yet available" in result.output


def test_ask_works_when_lodestone_present(project):
    """Adapts to M12 landing: skipped until ontoforge.lodestone exposes an
    ask/answer/Lodestone entry point; never crashes either way."""
    lodestone = pytest.importorskip("ontoforge.lodestone")
    if not any(hasattr(lodestone, n) for n in ("ask", "answer", "Lodestone")):
        pytest.skip("ontoforge.lodestone present but entry points not landed yet")
    proj, _ = project
    result = runner.invoke(app, ["ask", "how many aircraft are registered?", "-p", str(proj)])
    assert result.exit_code == 0, result.output
    # a real answer renders a table/abstention/clarification — never a crash
    assert any(
        marker in result.output
        for marker in ("answer", "ABSTAINED", "CLARIFICATION NEEDED", "confidence")
    )


def test_snapshot_works_when_amber_present(project, tmp_path):
    amber = pytest.importorskip("ontoforge.amber")
    if not hasattr(amber, "snapshot"):
        pytest.skip("ontoforge.amber present but snapshot() not landed yet")
    proj, _ = project
    result = runner.invoke(app, ["snapshot", str(tmp_path / "bundle"), "-p", str(proj)])
    assert result.exit_code == 0, result.output
