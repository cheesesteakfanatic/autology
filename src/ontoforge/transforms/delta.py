"""M7 delta-awareness hook (whitepaper §5.1 / constraint Δ at DAG granularity).

Given the set of changed *input tables*, compute the affected downstream
transform set — the transitive closure over the dependency DAG — so a delta
cycle runs exactly that cone and nothing else (work ∝ affected set).

Row-level incremental computation (Z-set deltas, DBSP) is deferred per
AMD-0001; this hook fixes the *granularity contract* (table-level cones) that
the row-level engine will later refine. See the module README.
"""

from __future__ import annotations

from typing import Iterable, Sequence

from ontoforge.contracts.transforms import TransformDef

__all__ = ["affected_transforms"]


def affected_transforms(
    defs: Sequence[TransformDef], changed_tables: Iterable[str]
) -> set[str]:
    """Names of every transform whose output is (transitively) downstream of
    any changed table. Deterministic; O(nodes + edges) worklist closure."""
    affected: set[str] = set()
    dirty_tables = set(changed_tables)
    # worklist over tables: a transform is affected if any input is dirty;
    # its output then becomes dirty.
    progress = True
    while progress:
        progress = False
        for d in defs:
            if d.name in affected:
                continue
            if any(t in dirty_tables for t in d.inputs):
                affected.add(d.name)
                if d.output not in dirty_tables:
                    dirty_tables.add(d.output)
                progress = True
    return affected
