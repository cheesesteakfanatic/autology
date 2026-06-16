"""Synthetic column-pair builders with KNOWN truth for the relationship engine tests.

Every column is profiled through the REAL M3 ``profile_column`` so the
``ColumnProfile`` carries genuine φ sketches (MinHash, HLL, KLL deciles, format
signature, samples) — the engine is exercised against the actual profiler output,
not hand-stubbed numbers. Determinism is inherited from the fixed profiling seed.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from typing import Sequence

from ontoforge.contracts import ColumnProfile, TableProfile
from ontoforge.profiling import profile_column
from ontoforge.relationships.signals import SampledColumn


def make_col(
    name: str,
    values: Sequence,
    *,
    source_id: str = "src",
    table: str = "t",
    with_counts: bool = False,
) -> SampledColumn:
    """Profile ``values`` and wrap as a :class:`SampledColumn`.

    ``with_counts=True`` supplies sample-level value frequencies so the
    categorical distribution-divergence (JSD) and entropy signals see real
    frequency mass (otherwise they treat the sample as a uniform distinct set).
    """
    vals = list(values)
    cp, _ = profile_column(source_id, table, name, vals)
    if with_counts:
        counts = tuple(Counter(str(v) for v in vals).items())
        return SampledColumn(profile=cp, value_counts=counts)
    return SampledColumn(profile=cp, values=tuple(str(v) for v in vals))


def make_profile(
    source_id: str,
    table: str,
    columns: dict[str, Sequence],
) -> TableProfile:
    """Build a real :class:`TableProfile` from a {column: values} mapping."""
    profs: dict[str, ColumnProfile] = {}
    row_count = max((len(v) for v in columns.values()), default=0)
    for cname, vals in columns.items():
        cp, _ = profile_column(source_id, table, cname, list(vals))
        profs[cname] = cp
    return TableProfile(source_id=source_id, table=table, row_count=row_count, columns=profs)


def dict_sample_provider(
    tables: dict[tuple[str, str], dict[str, Sequence]],
) -> Callable[[str, str, str], tuple[str, ...]]:
    """A sample provider over an in-memory {(source,table): {col: values}} corpus.

    Returns the FULL distinct sample (these synthetic columns are tiny); the
    engine caps internally. Falls back to () for unknown columns so discover uses
    the φ sample.
    """

    def provider(source_id: str, table: str, column: str) -> tuple[str, ...]:
        cols = tables.get((source_id, table))
        if not cols or column not in cols:
            return ()
        return tuple(str(v) for v in cols[column])

    return provider
