"""Idempotent retries: an injected mid-run failure on the first attempt is
retried; the output is replaced atomically, so the retried run's result is
identical to an undisturbed run."""

from __future__ import annotations

import pandas as pd

from m7_helpers import make_stack, run_artifacts, src, tdef


def _pipeline(registry) -> None:
    registry.register(tdef("clean", ("raw.t",), "c.t", "SELECT upper(a) AS a, b FROM raw.t"))
    registry.register(
        tdef("agg", ("c.t",), "c.agg", "SELECT a, sum(b) AS total FROM c.t GROUP BY a ORDER BY a")
    )


INPUT = {"raw.t": src(a=["x", "y", "x"], b=[1, 2, 3])}


class FlakyOnce:
    """Raises on the first execution attempt of `target`, then succeeds."""

    def __init__(self, target: str) -> None:
        self.target = target
        self.attempts: list[tuple[str, int]] = []

    def __call__(self, name: str, attempt: int) -> None:
        self.attempts.append((name, attempt))
        if name == self.target and attempt == 0:
            raise OSError("injected transient failure")


def test_retry_recovers_and_matches_undisturbed_run() -> None:
    # reference: undisturbed run
    _, reg_ref, orch_ref = make_stack()
    _pipeline(reg_ref)
    expected = orch_ref.run(INPUT).outputs["c.agg"]

    ledger, registry, orch = make_stack()
    _pipeline(registry)
    flaky = FlakyOnce("agg")
    res = orch.run(INPUT, retries=1, on_execute=flaky)
    assert {r.name: r.record.status for r in res.results} == {
        "clean": "success",
        "agg": "success",
    }
    assert ("agg", 0) in flaky.attempts and ("agg", 1) in flaky.attempts
    pd.testing.assert_frame_equal(res.outputs["c.agg"], expected)
    # persisted record reflects the eventual success, not the failed attempt
    agg_runs = [a for a in run_artifacts(ledger) if a["name"] == "agg"]
    assert [a["status"] for a in agg_runs] == ["success"]


def test_no_retry_budget_means_failure_and_no_partial_output() -> None:
    _, registry, orch = make_stack()
    _pipeline(registry)
    flaky = FlakyOnce("agg")
    res = orch.run(INPUT, retries=0, on_execute=flaky)
    statuses = {r.name: r.record.status for r in res.results}
    assert statuses == {"clean": "success", "agg": "failed"}
    assert "c.agg" not in res.outputs  # atomic: nothing replaced on failure
    assert "injected transient failure" in next(
        r.record.error for r in res.results if r.name == "agg"
    )


def test_failed_then_rerun_is_idempotent() -> None:
    _, registry, orch = make_stack()
    _pipeline(registry)
    orch.run(INPUT, retries=0, on_execute=FlakyOnce("agg"))  # agg fails
    res2 = orch.run(INPUT)  # clean retry of the whole cycle
    assert {r.name: r.record.status for r in res2.results} == {
        "clean": "skipped(memo)",  # upstream materialization reused
        "agg": "success",
    }
    _, reg_ref, orch_ref = make_stack()
    _pipeline(reg_ref)
    pd.testing.assert_frame_equal(
        res2.outputs["c.agg"], orch_ref.run(INPUT).outputs["c.agg"]
    )
