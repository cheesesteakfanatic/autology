"""Virtual environments (§5.1): materializations are keyed by
(transform fingerprint, input data fingerprints). Changing ONE transform in a
4-node chain re-runs exactly it and its descendants; unchanged upstream is
reused via memo — asserted via execution counters in the persisted RunRecords."""

from __future__ import annotations

from m7_helpers import make_stack, run_artifacts, src, tdef

INPUTS = {"raw.src": None}  # filled per test


def _chain(registry, *, t2_sql: str = "SELECT x, y * 2 AS y FROM c.t1", t2_version: int = 1):
    # 4-node DAG: t1 -> t2 -> t4, t1 -> t3 (t3 is a sibling of t2's branch)
    registry.register(tdef("t1", ("raw.src",), "c.t1", "SELECT upper(x) AS x, y FROM raw.src"))
    registry.register(tdef("t2", ("c.t1",), "c.t2", t2_sql, version=t2_version))
    registry.register(tdef("t3", ("c.t1",), "c.t3", "SELECT x AS k FROM c.t1"))
    registry.register(tdef("t4", ("c.t2",), "c.t4", "SELECT x, y + 1 AS y1 FROM c.t2"))


def test_identical_rerun_is_fully_memoized() -> None:
    ledger, registry, orch = make_stack()
    _chain(registry)
    inputs = {"raw.src": src(x=["a", "b"], y=[1, 2])}
    r1 = orch.run(inputs)
    assert [r.record.status for r in r1.results] == ["success"] * 4
    r2 = orch.run(inputs)
    assert [r.record.status for r in r2.results] == ["skipped(memo)"] * 4
    assert r2.executed_names() == []
    # memoized outputs are byte-identical materializations
    assert r2.outputs["c.t4"].equals(r1.outputs["c.t4"])
    runs = run_artifacts(ledger)
    assert sum(1 for a in runs if a["status"] == "success") == 4
    assert sum(1 for a in runs if a["status"] == "skipped(memo)") == 4


def test_changing_one_transform_reruns_exactly_it_and_descendants() -> None:
    ledger, registry, orch = make_stack()
    _chain(registry)
    inputs = {"raw.src": src(x=["a", "b"], y=[1, 2])}
    orch.run(inputs)

    # change ONLY t2 (new body => new content fingerprint, same name)
    registry.register(
        tdef("t2", ("c.t1",), "c.t2", "SELECT x, y * 3 AS y FROM c.t1", version=2)
    )
    res = orch.run(inputs)
    statuses = {r.name: r.record.status for r in res.results}
    assert statuses == {
        "t1": "skipped(memo)",      # unchanged upstream reused by fingerprint
        "t2": "success",            # the changed transform
        "t3": "skipped(memo)",      # sibling branch untouched
        "t4": "success",            # descendant of the change
    }
    assert set(res.executed_names()) == {"t2", "t4"}
    # execution counters from the ledger: t1/t3 executed once total, t2 v1+v2
    runs = run_artifacts(ledger)
    by_fp_success: dict[str, int] = {}
    for a in runs:
        if a["status"] == "success":
            by_fp_success[a["transform_fingerprint"]] = (
                by_fp_success.get(a["transform_fingerprint"], 0) + 1
            )
    t1_fp = registry.by_name("t1").fingerprint
    t3_fp = registry.by_name("t3").fingerprint
    assert by_fp_success[t1_fp] == 1
    assert by_fp_success[t3_fp] == 1
    assert res.outputs["c.t4"]["y1"].tolist() == [4, 7]  # y*3 + 1


def test_changed_input_data_busts_the_memo() -> None:
    _, registry, orch = make_stack()
    _chain(registry)
    orch.run({"raw.src": src(x=["a"], y=[1])})
    res = orch.run({"raw.src": src(x=["a"], y=[99])})
    assert [r.record.status for r in res.results] == ["success"] * 4


def test_row_order_change_does_not_bust_the_memo() -> None:
    _, registry, orch = make_stack()
    _chain(registry)
    orch.run({"raw.src": src(x=["a", "b"], y=[1, 2])})
    res = orch.run({"raw.src": src(x=["b", "a"], y=[2, 1])})
    assert [r.record.status for r in res.results] == ["skipped(memo)"] * 4


def test_plan_reports_memo_actions_without_executing() -> None:
    _, registry, orch = make_stack()
    _chain(registry)
    inputs = {"raw.src": src(x=["a"], y=[1])}
    plan0 = orch.plan(inputs)
    assert [a for _, a in plan0] == ["execute"] * 4
    orch.run(inputs)
    plan1 = orch.plan(inputs)
    assert [a for _, a in plan1] == ["memo"] * 4
    registry.register(
        tdef("t2", ("c.t1",), "c.t2", "SELECT x, y * 9 AS y FROM c.t1", version=2)
    )
    plan2 = {r.tdef.name: a for r, a in orch.plan(inputs)}
    assert plan2 == {"t1": "memo", "t2": "execute", "t3": "memo", "t4": "execute"}
