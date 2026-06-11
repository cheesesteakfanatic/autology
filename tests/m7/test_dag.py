"""DAG construction: topological order respected, cycle detection, duplicate
outputs rejected, failure isolation (downstream of a failed node skipped,
independent branches unaffected)."""

from __future__ import annotations

import pytest

from ontoforge.transforms import CycleError, DagError
from m7_helpers import make_stack, run_artifacts, src, tdef


def _diamond(registry) -> None:
    # raw.src -> A -> {B, C} -> D (B and C both feed D)
    registry.register(tdef("A", ("raw.src",), "c.a", "SELECT upper(x) AS x, y FROM raw.src"))
    registry.register(tdef("B", ("c.a",), "c.b", "SELECT x, y FROM c.a WHERE y > 1"))
    registry.register(tdef("C", ("c.a",), "c.c", "SELECT x AS k, y AS w FROM c.a"))
    registry.register(
        tdef(
            "D",
            ("c.b", "c.c"),
            "c.d",
            "SELECT b.x AS x, c.w AS w FROM c.b AS b JOIN c.c AS c ON b.x = c.k",
        )
    )


def test_topological_order_respected() -> None:
    _, registry, orch = make_stack()
    _diamond(registry)
    res = orch.run({"raw.src": src(x=["p", "q"], y=[1, 2])})
    order = [r.name for r in res.results]
    assert order.index("A") < order.index("B") < order.index("D")
    assert order.index("A") < order.index("C") < order.index("D")
    assert all(r.record.status == "success" for r in res.results)
    assert set(res.outputs) == {"c.a", "c.b", "c.c", "c.d"}


def test_cycle_detected() -> None:
    _, registry, orch = make_stack()
    registry.register(tdef("p", ("c.q",), "c.p", "SELECT a FROM c.q"))
    registry.register(tdef("q", ("c.p",), "c.q", "SELECT a FROM c.p"))
    with pytest.raises(CycleError) as ei:
        orch.dag()
    assert set(ei.value.cycle_nodes) == {"p", "q"}


def test_duplicate_output_rejected() -> None:
    _, registry, orch = make_stack()
    registry.register(tdef("one", ("raw.t",), "c.same", "SELECT a FROM raw.t"))
    registry.register(tdef("two", ("raw.u",), "c.same", "SELECT a FROM raw.u"))
    with pytest.raises(DagError, match="two active transforms"):
        orch.dag()


def test_failure_isolation_downstream_skipped_siblings_run() -> None:
    ledger, registry, orch = make_stack()
    _diamond(registry)

    def fail_b(name: str, attempt: int) -> None:
        if name == "B":
            raise RuntimeError("injected B failure")

    res = orch.run({"raw.src": src(x=["p"], y=[5])}, on_execute=fail_b)
    statuses = {r.name: r.record.status for r in res.results}
    assert statuses["A"] == "success"
    assert statuses["B"] == "failed"
    assert "injected B failure" in next(r.record.error for r in res.results if r.name == "B")
    assert statuses["C"] == "success"  # independent branch unaffected
    assert statuses["D"] == "skipped(upstream_failed)"
    assert "c.b" not in res.outputs and "c.d" not in res.outputs
    assert "c.c" in res.outputs
    # records landed in the ledger too
    persisted = {a["name"]: a["status"] for a in run_artifacts(ledger)}
    assert persisted == statuses


def test_missing_source_input_is_an_error() -> None:
    _, registry, orch = make_stack()
    registry.register(tdef("A", ("raw.src",), "c.a", "SELECT x FROM raw.src"))
    with pytest.raises(DagError, match="missing input tables"):
        orch.run({})
