"""`ontoforge criticality -p PROJECT [--top N]` (Crew C, §6 integration).

The command loads the active world, builds the criticality graph from the
answering ontology + atlas, replays any recorded usage, and prints the top-N
critical ontology elements in a table. These tests run the REAL pipeline chain
to materialize a small aviation project, then exercise the command. Keyless,
offline, deterministic — the same invariants as the rest of the CLI.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ontoforge.cli import app

LIMIT = 60
runner = CliRunner()

# Aviation-estate questions that ground to real classes (so replay records
# usage). The CLI replays the ledger's 'question' artifacts as 'query' events.
SEED_QUESTIONS = [
    "how many aircraft are there?",
    "list the airports",
    "what airlines operate flights?",
]


def _seed_questions(project: Path) -> None:
    """Persist 'question' artifacts into the ledger exactly as the server's
    ``world.record_question`` does, so the CLI's replay has usage to fold."""
    from ontoforge.contracts import Atom, leaf
    from ontoforge.ledger import SqliteLedger

    cfg = json.loads((project / "config.json").read_text(encoding="utf-8"))
    ledger = SqliteLedger(str(project / cfg["ledger"]))
    try:
        for q in SEED_QUESTIONS:
            qid = hashlib.sha256(q.encode("utf-8")).hexdigest()[:16]
            atom = Atom(uri=f"atom://question/{qid}", value=q)
            ledger.register_atoms([atom])
            prov_ref = ledger.intern(leaf(atom.atom_id))
            ledger.append_artifact(
                artifact_id=f"question:{qid}",
                kind="question",
                payload=json.dumps({"question": q}, sort_keys=True),
                prov_ref=prov_ref,
            )
    finally:
        ledger.close()


@pytest.fixture(scope="module")
def materialized_project(tmp_path_factory) -> Path:
    proj = tmp_path_factory.mktemp("crit-cli") / "proj"
    for argv in (
        ["init", str(proj)],
        ["ingest", "-p", str(proj), "--limit", str(LIMIT)],
        ["profile", "-p", str(proj)],
        ["induce", "-p", str(proj)],
        ["resolve", "-p", str(proj)],
        ["materialize", "-p", str(proj)],
    ):
        result = runner.invoke(app, argv)
        assert result.exit_code == 0, f"{argv}: {result.output}\n{result.exception}"
    _seed_questions(proj)
    return proj


def test_criticality_runs_and_prints_table(materialized_project: Path) -> None:
    result = runner.invoke(app, ["criticality", "-p", str(materialized_project)])
    assert result.exit_code == 0, f"{result.output}\n{result.exception}"
    # with recorded usage replayed, the score-ranked table is printed
    assert "criticality" in result.output
    assert "score" in result.output


def test_criticality_top_flag_is_honored(materialized_project: Path) -> None:
    result = runner.invoke(app, ["criticality", "-p", str(materialized_project), "--top", "3"])
    assert result.exit_code == 0, f"{result.output}\n{result.exception}"
    assert "top 3" in result.output


def test_criticality_no_usage_is_honest(tmp_path_factory) -> None:
    """A materialized project with NO recorded usage prints an honest empty
    message (criticality is usage-driven + lazy) and still exits 0."""
    proj = tmp_path_factory.mktemp("crit-cli-bare") / "proj"
    for argv in (
        ["init", str(proj)],
        ["ingest", "-p", str(proj), "--limit", str(LIMIT)],
        ["profile", "-p", str(proj)],
        ["induce", "-p", str(proj)],
        ["resolve", "-p", str(proj)],
        ["materialize", "-p", str(proj)],
    ):
        result = runner.invoke(app, argv)
        assert result.exit_code == 0, f"{argv}: {result.output}\n{result.exception}"
    result = runner.invoke(app, ["criticality", "-p", str(proj)])
    assert result.exit_code == 0, f"{result.output}\n{result.exception}"
    assert "no critical elements yet" in result.output


def test_criticality_on_uninitialized_project_fails_clean(tmp_path: Path) -> None:
    """No project dir -> a clean non-zero exit, never a traceback."""
    result = runner.invoke(app, ["criticality", "-p", str(tmp_path / "nope")])
    assert result.exit_code != 0
    assert "no project" in result.output
