"""Delta-awareness hook (§5.1, constraint Δ at DAG granularity): changing one
input table re-runs only its downstream cone; work ∝ affected set."""

from __future__ import annotations

import pytest

from ontoforge.transforms import DagError, affected_transforms
from m7_helpers import make_stack, run_artifacts, src, tdef


def _two_cones(registry) -> None:
    # cone(raw.a) = {tA, tJoin};  cone(raw.b) = {tB, tJoin};  tA2 only under raw.a
    registry.register(tdef("tA", ("raw.a",), "c.a", "SELECT k, upper(v) AS v FROM raw.a"))
    registry.register(tdef("tA2", ("c.a",), "c.a2", "SELECT k FROM c.a"))
    registry.register(tdef("tB", ("raw.b",), "c.b", "SELECT k, w FROM raw.b"))
    registry.register(
        tdef(
            "tJoin",
            ("c.a", "c.b"),
            "c.j",
            "SELECT a.k AS k, a.v AS v, b.w AS w FROM c.a AS a JOIN c.b AS b ON a.k = b.k",
        )
    )


def test_affected_transforms_transitive_closure() -> None:
    _, registry, _ = make_stack()
    _two_cones(registry)
    defs = [r.tdef for r in registry.active()]
    assert affected_transforms(defs, {"raw.b"}) == {"tB", "tJoin"}
    assert affected_transforms(defs, {"raw.a"}) == {"tA", "tA2", "tJoin"}
    assert affected_transforms(defs, {"raw.a", "raw.b"}) == {"tA", "tA2", "tB", "tJoin"}
    assert affected_transforms(defs, {"raw.unrelated"}) == set()
    # a changed intermediate table marks only its consumers
    assert affected_transforms(defs, {"c.a"}) == {"tA2", "tJoin"}


def test_delta_run_visits_only_the_cone() -> None:
    ledger, registry, orch = make_stack()
    _two_cones(registry)
    a = src(k=["1", "2"], v=["x", "y"])
    b = src(k=["1", "2"], w=["p", "q"])
    orch.run({"raw.a": a, "raw.b": b})
    n_full = len(run_artifacts(ledger))
    assert n_full == 4

    b2 = src(k=["1", "2"], w=["p", "CHANGED"])
    res = orch.run({"raw.a": a, "raw.b": b2}, changed_tables={"raw.b"})
    # work ∝ affected set: records exist for exactly the cone
    assert {r.name for r in res.results} == {"tB", "tJoin"}
    assert all(r.record.delta_run for r in res.results)
    assert all(r.record.status == "success" for r in res.results)
    assert len(run_artifacts(ledger)) == n_full + 2
    # the join sees the new value, with the unaffected side reused
    assert "CHANGED" in res.outputs["c.j"]["w"].tolist()


def test_delta_run_with_unchanged_data_memo_hits_inside_the_cone() -> None:
    _, registry, orch = make_stack()
    _two_cones(registry)
    a = src(k=["1"], v=["x"])
    b = src(k=["1"], w=["p"])
    orch.run({"raw.a": a, "raw.b": b})
    res = orch.run({"raw.a": a, "raw.b": b}, changed_tables={"raw.b"})
    assert {r.name: r.record.status for r in res.results} == {
        "tB": "skipped(memo)",
        "tJoin": "skipped(memo)",
    }


def test_delta_run_requires_prior_materialization() -> None:
    _, registry, orch = make_stack()
    _two_cones(registry)
    with pytest.raises(DagError, match="prior materialization"):
        orch.run(
            {"raw.a": src(k=["1"], v=["x"]), "raw.b": src(k=["1"], w=["p"])},
            changed_tables={"raw.b"},
        )
