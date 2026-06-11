"""M7 orchestrator (whitepaper §5.1): dependency DAG over registered
transforms, topological execution through DuckDB over pandas inputs,
virtual-environment memoization, failure isolation, idempotent retries,
delta-scoped runs, and RunRecords appended to the ledger (kind "run").

Determinism: RunRecord instants come from an internal monotone counter, not
the wall clock — runs are bit-reproducible (the store-stamped-time pattern
from M6, applied to run history).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Callable, Mapping, Optional

import duckdb
import pandas as pd
from sqlglot import exp

from ontoforge.contracts.ledger import Ledger
from ontoforge.contracts.transforms import RunRecord

from .delta import affected_transforms
from .dsl import DIALECT, validate_sql
from .fingerprints import fingerprint_dataframe, memo_key
from .registry import RegisteredTransform, TransformRegistry

__all__ = ["CycleError", "DagError", "NodeResult", "RunResult", "Orchestrator"]


class DagError(ValueError):
    """The set of active transforms does not form a valid DAG."""


class CycleError(DagError):
    def __init__(self, cycle_nodes: list[str]) -> None:
        super().__init__(f"transform graph has a cycle through: {sorted(cycle_nodes)}")
        self.cycle_nodes = sorted(cycle_nodes)


@dataclass(frozen=True, slots=True)
class NodeResult:
    name: str
    fingerprint: str
    record: RunRecord
    executed: bool  # True only when the body actually ran in DuckDB


@dataclass(slots=True)
class RunResult:
    results: list[NodeResult] = field(default_factory=list)
    outputs: dict[str, pd.DataFrame] = field(default_factory=dict)

    def executed_names(self) -> list[str]:
        return [r.name for r in self.results if r.executed]

    def status(self, name: str) -> str:
        for r in self.results:
            if r.name == name:
                return r.record.status
        raise KeyError(name)


def _rewrite_tables(tree: exp.Select, available: Mapping[str, str]) -> exp.Select:
    """Map layer-qualified table names (e.g. "raw.faa_master") onto the
    sanitized names registered with DuckDB, preserving the alias surface:
    a table without an explicit alias keeps its bare name as alias so
    `faa_master.col` qualifiers keep working."""
    tree = tree.copy()
    for t in list(tree.find_all(exp.Table)):
        parts = [p.name for p in (t.args.get("catalog"), t.args.get("db"), t.args.get("this")) if p]
        full = ".".join(parts)
        if full not in available:
            raise DagError(f"table {full!r} referenced in SQL is not available as an input")
        alias = t.alias or parts[-1]
        t.replace(
            exp.Table(
                this=exp.to_identifier(available[full]),
                alias=exp.TableAlias(this=exp.to_identifier(alias)),
            )
        )
    return tree


def _sanitize(table: str) -> str:
    return "df_" + table.replace(".", "__")


class Orchestrator:
    """Plans and runs the transform DAG with §5.1 virtual-environment
    semantics: an output materialization is keyed by
    (transform fingerprint, input data fingerprints); unchanged keys are
    reused as 'skipped(memo)' RunRecords instead of re-executing."""

    def __init__(self, registry: TransformRegistry, ledger: Ledger) -> None:
        self.registry = registry
        self.ledger = ledger
        self._memo: dict[str, pd.DataFrame] = {}      # memo_key -> materialization
        self._outputs: dict[str, pd.DataFrame] = {}   # table -> last materialization
        self._clock = 0
        self._run_seq = 0

    # ------------------------------------------------------------------ DAG

    def dag(self) -> dict[str, set[str]]:
        """name -> set of upstream transform names (cycle-checked)."""
        regs = self.registry.active()
        by_output: dict[str, str] = {}
        for r in regs:
            if r.tdef.output in by_output:
                raise DagError(
                    f"two active transforms produce {r.tdef.output!r}: "
                    f"{by_output[r.tdef.output]!r} and {r.tdef.name!r}"
                )
            by_output[r.tdef.output] = r.tdef.name
        deps = {
            r.tdef.name: {by_output[t] for t in r.tdef.inputs if t in by_output}
            for r in regs
        }
        self._check_acyclic(deps)
        return deps

    @staticmethod
    def _check_acyclic(deps: Mapping[str, set[str]]) -> None:
        remaining = {n: set(d) for n, d in deps.items()}
        while remaining:
            ready = [n for n, d in remaining.items() if not d]
            if not ready:
                raise CycleError(list(remaining))
            for n in ready:
                del remaining[n]
            for d in remaining.values():
                d.difference_update(ready)

    def topo_order(self) -> list[RegisteredTransform]:
        """Deterministic topological order (registration order breaks ties)."""
        regs = self.registry.active()
        deps = self.dag()
        order: list[RegisteredTransform] = []
        done: set[str] = set()
        pending = list(regs)
        while pending:
            progressed = False
            for r in list(pending):
                if deps[r.tdef.name] <= done:
                    order.append(r)
                    done.add(r.tdef.name)
                    pending.remove(r)
                    progressed = True
            if not progressed:  # pragma: no cover - dag() already raised
                raise CycleError([r.tdef.name for r in pending])
        return order

    # ----------------------------------------------------------------- plan

    def plan(
        self,
        inputs: Mapping[str, pd.DataFrame],
        *,
        changed_tables: Optional[set[str]] = None,
    ) -> list[tuple[RegisteredTransform, str]]:
        """Topologically ordered (transform, action) pairs.

        action ∈ {"execute", "memo", "outside-delta"}. "memo" is provable
        without executing anything (all upstream fingerprints already known);
        run() additionally discovers memo hits dynamically.
        """
        order = self.topo_order()
        affected = (
            affected_transforms([r.tdef for r in order], changed_tables)
            if changed_tables is not None
            else None
        )
        fps: dict[str, str] = {t: fingerprint_dataframe(df) for t, df in inputs.items()}
        plan: list[tuple[RegisteredTransform, str]] = []
        for r in order:
            if affected is not None and r.tdef.name not in affected:
                plan.append((r, "outside-delta"))
                continue
            if all(t in fps for t in r.tdef.inputs):
                key = memo_key(r.fingerprint, {t: fps[t] for t in r.tdef.inputs})
                if key in self._memo:
                    plan.append((r, "memo"))
                    fps[r.tdef.output] = fingerprint_dataframe(self._memo[key])
                    continue
            plan.append((r, "execute"))
        return plan

    # ------------------------------------------------------------------ run

    def run(
        self,
        inputs: Mapping[str, pd.DataFrame],
        *,
        changed_tables: Optional[set[str]] = None,
        retries: int = 0,
        on_execute: Optional[Callable[[str, int], None]] = None,
    ) -> RunResult:
        """Execute the DAG over the given source tables.

        changed_tables: delta mode — only the affected cone (transitive
            consumers of the changed tables) is visited; everything else
            reuses its previous materialization without emitting a record
            (work ∝ affected set). Requires a prior full run.
        retries: extra attempts per node; the output table is replaced only
            after a fully successful execution (idempotent commit).
        on_execute(name, attempt): test/observability hook invoked before
            each execution attempt; an exception it raises counts as a
            failure of that attempt (failure injection).
        """
        self._run_seq += 1
        order = self.topo_order()
        delta_mode = changed_tables is not None
        affected = (
            affected_transforms([r.tdef for r in order], changed_tables or set())
            if delta_mode
            else None
        )
        tables: dict[str, pd.DataFrame] = dict(inputs)
        fps: dict[str, str] = {t: fingerprint_dataframe(df) for t, df in tables.items()}
        result = RunResult()
        failed_outputs: set[str] = set()

        for idx, r in enumerate(order):
            t = r.tdef
            if affected is not None and t.name not in affected:
                # outside the delta cone: reuse the previous materialization
                if t.output not in self._outputs:
                    raise DagError(
                        f"delta run requires a prior materialization of {t.output!r} "
                        f"(transform {t.name!r}); run a full cycle first"
                    )
                tables[t.output] = self._outputs[t.output]
                fps[t.output] = fingerprint_dataframe(tables[t.output])
                continue

            started = self._tick()
            if any(inp in failed_outputs for inp in t.inputs):
                rec = RunRecord(
                    transform_fingerprint=r.fingerprint,
                    started_at=started,
                    finished_at=self._tick(),
                    rows_in=0,
                    rows_out=0,
                    status="skipped(upstream_failed)",
                    error=f"upstream of {t.name!r} failed",
                    delta_run=delta_mode,
                )
                failed_outputs.add(t.output)
                self._record(result, r, rec, idx, executed=False)
                continue

            missing = [inp for inp in t.inputs if inp not in tables]
            if missing:
                raise DagError(f"transform {t.name!r} is missing input tables {missing}")
            in_dfs = {inp: tables[inp] for inp in t.inputs}
            rows_in = sum(len(df) for df in in_dfs.values())
            key = memo_key(r.fingerprint, {inp: fps[inp] for inp in t.inputs})

            if key in self._memo:
                out_df = self._memo[key]
                rec = RunRecord(
                    transform_fingerprint=r.fingerprint,
                    started_at=started,
                    finished_at=self._tick(),
                    rows_in=rows_in,
                    rows_out=len(out_df),
                    status="skipped(memo)",
                    delta_run=delta_mode,
                )
                self._install(tables, fps, result, t.output, out_df, r, rec, idx, executed=False)
                continue

            out_df, error = self._execute_with_retries(r, in_dfs, retries, on_execute)
            if out_df is None:
                rec = RunRecord(
                    transform_fingerprint=r.fingerprint,
                    started_at=started,
                    finished_at=self._tick(),
                    rows_in=rows_in,
                    rows_out=0,
                    status="failed",
                    error=error,
                    delta_run=delta_mode,
                )
                failed_outputs.add(t.output)
                self._record(result, r, rec, idx, executed=True)
                continue
            rec = RunRecord(
                transform_fingerprint=r.fingerprint,
                started_at=started,
                finished_at=self._tick(),
                rows_in=rows_in,
                rows_out=len(out_df),
                status="success",
                delta_run=delta_mode,
            )
            self._memo[key] = out_df
            self._install(tables, fps, result, t.output, out_df, r, rec, idx, executed=True)

        return result

    # ------------------------------------------------------------- internals

    def _tick(self) -> int:
        self._clock += 1
        return self._clock

    def _install(
        self,
        tables: dict[str, pd.DataFrame],
        fps: dict[str, str],
        result: RunResult,
        output: str,
        df: pd.DataFrame,
        reg: RegisteredTransform,
        rec: RunRecord,
        idx: int,
        *,
        executed: bool,
    ) -> None:
        # atomic replace: the previous materialization is swapped only here,
        # after a fully successful execution or a memo hit.
        tables[output] = df
        fps[output] = fingerprint_dataframe(df)
        self._outputs[output] = df
        result.outputs[output] = df
        self._record(result, reg, rec, idx, executed=executed)

    def _record(
        self, result: RunResult, reg: RegisteredTransform, rec: RunRecord, idx: int, *, executed: bool
    ) -> None:
        result.results.append(
            NodeResult(name=reg.tdef.name, fingerprint=reg.fingerprint, record=rec, executed=executed and rec.status == "success")
        )
        payload = json.dumps(
            {
                "name": reg.tdef.name,
                "transform_fingerprint": rec.transform_fingerprint,
                "started_at": rec.started_at,
                "finished_at": rec.finished_at,
                "rows_in": rec.rows_in,
                "rows_out": rec.rows_out,
                "status": rec.status,
                "error": rec.error,
                "delta_run": rec.delta_run,
            },
            sort_keys=True,
        )
        self.ledger.append_artifact(
            f"run:{self._run_seq}:{idx}:{reg.fingerprint}", "run", payload, reg.prov_ref
        )

    def _execute_with_retries(
        self,
        reg: RegisteredTransform,
        in_dfs: Mapping[str, pd.DataFrame],
        retries: int,
        on_execute: Optional[Callable[[str, int], None]],
    ) -> tuple[Optional[pd.DataFrame], str]:
        last_error = ""
        for attempt in range(retries + 1):
            try:
                if on_execute is not None:
                    on_execute(reg.tdef.name, attempt)
                return self._execute(reg, in_dfs), ""
            except Exception as e:  # noqa: BLE001 - failure isolation boundary
                last_error = f"{type(e).__name__}: {e}"
        return None, last_error

    def _execute(
        self, reg: RegisteredTransform, in_dfs: Mapping[str, pd.DataFrame]
    ) -> pd.DataFrame:
        tree = validate_sql(reg.tdef.sql)
        available = {t: _sanitize(t) for t in in_dfs}
        rewritten = _rewrite_tables(tree, available)
        con = duckdb.connect(":memory:")
        try:
            for table, df in in_dfs.items():
                con.register(_sanitize(table), df)
            return con.execute(rewritten.sql(dialect=DIALECT)).df()
        finally:
            con.close()
