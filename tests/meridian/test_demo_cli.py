"""`ontoforge demo` argument handling (the full demo pipeline is exercised by
the gate suite over the same corpus; here we keep the CLI surface honest and
fast)."""

from __future__ import annotations

import json

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


def _tiny_generic_estate(src):
    """Two joinable tables (orders.customer_id -> customers.id): the smallest
    estate that induces a cross-dataset CONFIRMED link, hence an atlas island."""
    src.mkdir(parents=True, exist_ok=True)
    (src / "customers.csv").write_text(
        "id,name\n" + "\n".join(f"C{i},Cust {i}" for i in range(40)) + "\n",
        encoding="utf-8",
    )
    (src / "orders.csv").write_text(
        "order_id,customer_id,amount\n"
        + "\n".join(f"O{i},C{i % 40},{i * 3}" for i in range(120))
        + "\n",
        encoding="utf-8",
    )


def test_demo_builds_atlas_json_with_at_least_one_component(tmp_path):
    """The demo's final step writes <project>/atlas.json so GET /api/atlas
    lights up the Constellation instead of 404ing. We exercise the EXACT
    helper the demo command calls (``_build_demo_atlas``) over a tiny generic
    project run through the real pipeline stages — fast, no ~2min full demo."""
    from ontoforge.cli import (
        _build_demo_atlas,
        induce,
        ingest,
        init,
        materialize,
        profile,
        resolve,
    )

    src = tmp_path / "src"
    _tiny_generic_estate(src)
    proj = tmp_path / "proj"
    init(proj, fixtures=None, source=src)
    ingest(project=proj, limit=None)
    profile(project=proj)
    induce(project=proj)
    resolve(project=proj)
    materialize(project=proj, ontology=None)

    atlas_path = proj / "atlas.json"
    assert not atlas_path.exists(), "materialize must NOT write the atlas itself"

    _build_demo_atlas(proj)

    assert atlas_path.is_file(), "the demo's atlas step writes atlas.json"
    atlas = json.loads(atlas_path.read_text(encoding="utf-8"))
    assert set(atlas) == {"components", "links", "stats"}
    assert atlas["stats"]["components"] >= 1
    assert len(atlas["components"]) >= 1
    # the orders->customers FK is recovered as a CONFIRMED cross-dataset link
    assert atlas["stats"]["confirmed"] >= 1
    assert any(lk["tier"] == "confirmed" for lk in atlas["links"])


def test_meridian_demo_has_one_more_stage_than_the_others():
    """The atlas step adds a banner: meridian is 8 stages (corpus-gen first),
    aviation/wild are 7. A regression in the banner math is a UX bug."""
    import inspect

    from ontoforge import cli

    body = inspect.getsource(cli._run_demo)
    assert "stages = 8 if estate == \"meridian\" else 7" in body
