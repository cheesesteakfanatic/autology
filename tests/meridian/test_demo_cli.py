"""`ontoforge demo` argument handling (the full demo pipeline is exercised by
the gate suite over the same corpus; here we keep the CLI surface honest and
fast)."""

from __future__ import annotations

from typer.testing import CliRunner

from ontoforge.cli import app

runner = CliRunner()


def test_demo_rejects_unknown_estate(tmp_path):
    result = runner.invoke(app, ["demo", "klingon", str(tmp_path / "p")])
    assert result.exit_code == 1
    assert "unknown demo estate" in result.output


def test_demo_listed_in_help():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "demo" in result.output


def test_demo_memo_is_inactive_outside_demo():
    import ontoforge.cli as cli

    assert cli._DEMO_MEMO is None
