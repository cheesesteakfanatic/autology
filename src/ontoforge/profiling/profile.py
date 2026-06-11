"""profile_table — the M3 orchestrator producing contracts.TableProfile (§3.1, §11.2).

One pass over a pyarrow Table / pandas DataFrame / column mapping produces the
profile sketch φ(p) per column (type, nulls, HLL distinct, KLL deciles, k=64
MinHash, format signature, samples, token stats, unit/dimension, semantic type)
plus table-level FDs, candidate keys, and the append-mostly event signal (§3.5).

Unit mapping onto the frozen ColumnProfile contract
---------------------------------------------------
ColumnProfile has only `unit`/`dimension` — no mixed/conflict/confidence fields —
so the mapping is conservative:

- mixed-unit columns (the §3.2 silent-corruption class): unit=None ALWAYS; the
  dimension is kept when all observed units share one. Never silently merged.
- name/value conflict: unit=None (an asserted-but-conflicted unit with no
  confidence channel would be a silent lie); dimension from the value evidence.
- otherwise the unit is asserted when inference confidence >= 0.5.

`profile_table_detailed` returns the full per-column UnitInference (mixed /
conflict / observed_units / source) for consumers that need the §3.2 escalation
signal — reported as a contract gap in the module README.

Append-mostly hook (§3.5)
-------------------------
`detect_append_mostly(prev, cur)` compares successive TableProfiles: rows must
grow, and per shared column the MinHash Jaccard must be consistent with the old
value set being contained in the new one (expected J ≈ d_prev/d_cur under pure
append; in-place updates replace values and drag J well below that). The
detector is a hook: pass any `(prev, cur) -> bool` via `append_detector`.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any, Callable, Optional

from ontoforge.contracts import ColumnProfile, Datatype, TableProfile, minhash_jaccard

from ._values import columns_of, display_str, is_null, row_count_of, sample_evenly, value_key
from .fds import _fds_from_columns, _keys_from_columns
from .format_signature import format_signature
from .semantic_types import SemanticClassifier, infer_datatype, infer_semantic_type
from .sketches import HyperLogLog, KLLSketch, MinHash
from .units_infer import UnitInference, infer_unit

__all__ = [
    "profile_table",
    "profile_table_detailed",
    "profile_column",
    "detect_append_mostly",
    "profile",
]

_SEED = 0  # fixed seeds everywhere: profiling must be deterministic (§18.4)
_NUMERIC = (Datatype.INTEGER, Datatype.FLOAT)
_UNIT_ELIGIBLE = (Datatype.INTEGER, Datatype.FLOAT, Datatype.STRING)
_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z']+")


def _numeric_values(values: list) -> list[float]:
    out: list[float] = []
    for v in values:
        if is_null(v) or isinstance(v, bool):
            continue
        if isinstance(v, (int, float)):
            out.append(float(v))
            continue
        try:
            out.append(float(str(v).strip()))
        except ValueError:
            continue
    return out


def _token_stats(strings: list[str], top: int = 10, max_strings: int = 2000) -> tuple[tuple[str, float], ...]:
    counts: Counter[str] = Counter()
    total = 0
    for s in strings[:max_strings]:
        for t in _TOKEN_RE.findall(s.lower()):
            counts[t] += 1
            total += 1
    if not total:
        return ()
    items = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:top]
    return tuple((t, round(c / total, 6)) for t, c in items)


def profile_column(
    source_id: str,
    table: str,
    column: str,
    values: list,
    *,
    classifier: Optional[SemanticClassifier] = None,
    seed: int = _SEED,
) -> tuple[ColumnProfile, UnitInference]:
    """Profile one column; returns (ColumnProfile, full UnitInference)."""
    row_count = len(values)
    nn = [v for v in values if not is_null(v)]
    null_count = row_count - len(nn)
    dtype = infer_datatype(values)

    hll = HyperLogLog(seed=seed)
    mh = MinHash(k=64, seed=seed)
    for v in nn:
        k = value_key(v)
        hll.add(k)
        mh.add(k)

    quantiles: tuple[float, ...] = ()
    if dtype in _NUMERIC:
        nums = _numeric_values(nn)
        if nums:
            kll = KLLSketch(seed=seed)
            kll.extend(nums)
            quantiles = kll.deciles()

    strings = [display_str(v) for v in nn]
    fmt = format_signature(strings) if dtype is not Datatype.TEXT else ""
    samples = tuple(sample_evenly([s for s in strings if s], 10))
    tokens = _token_stats(strings) if dtype is Datatype.TEXT else ()

    inference = infer_unit(column, values) if dtype in _UNIT_ELIGIBLE else UnitInference()
    assert_unit = (
        inference.unit is not None
        and not inference.mixed
        and not inference.conflict
        and inference.confidence >= 0.5
    )
    sem_label, sem_conf = infer_semantic_type(values, column, dtype, classifier)

    cp = ColumnProfile(
        source_id=source_id,
        table=table,
        column=column,
        inferred_type=dtype,
        row_count=row_count,
        null_count=null_count,
        distinct_estimate=hll.estimate(),
        quantiles=quantiles,
        minhash=mh.signature(),
        format_signature=fmt,
        sample_values=samples,
        token_stats=tokens,
        unit=inference.unit if assert_unit else None,
        dimension=inference.dimension,
        semantic_type=sem_label,
        semantic_confidence=sem_conf,
    )
    return cp, inference


def detect_append_mostly(
    prev: TableProfile,
    cur: TableProfile,
    *,
    jaccard_floor: float = 0.7,
    column_fraction: float = 0.8,
) -> bool:
    """§3.5 event signal: inserts >> updates, judged from successive profiles.

    Pure append keeps every old value, so per column the true Jaccard between
    old and new value sets is d_prev/d_cur. A column is append-consistent when
    its MinHash estimate reaches `jaccard_floor` of that expectation (the slack
    absorbs k=64 estimator noise, std err <= 0.0625). The table is append-mostly
    when rows grew and >= `column_fraction` of comparable columns are consistent.
    """
    if cur.row_count <= prev.row_count:
        return False
    consistent = comparable = 0
    for name, p in prev.columns.items():
        q = cur.columns.get(name)
        if q is None or not p.minhash or not q.minhash:
            continue
        comparable += 1
        expected = min(1.0, p.distinct_estimate / max(1, q.distinct_estimate))
        if minhash_jaccard(p.minhash, q.minhash) >= jaccard_floor * expected:
            consistent += 1
    return comparable > 0 and consistent / comparable >= column_fraction


def profile_table_detailed(
    data: Any,
    source_id: str,
    table: str,
    *,
    classifier: Optional[SemanticClassifier] = None,
    previous: Optional[TableProfile] = None,
    append_detector: Callable[[TableProfile, TableProfile], bool] = detect_append_mostly,
    max_lhs: int = 3,
    approx_threshold: float = 0.98,
    max_key_size: int = 2,
    fd_max_rows: Optional[int] = None,
    seed: int = _SEED,
) -> tuple[TableProfile, dict[str, UnitInference]]:
    """profile_table plus the full per-column UnitInference (mixed/conflict detail)."""
    columns = columns_of(data)
    n = row_count_of(columns)
    profiles: dict[str, ColumnProfile] = {}
    units: dict[str, UnitInference] = {}
    for name, values in columns.items():
        cp, ui = profile_column(source_id, table, name, values, classifier=classifier, seed=seed)
        profiles[name] = cp
        units[name] = ui

    fds = _fds_from_columns(
        columns, table, max_lhs=max_lhs, approx_threshold=approx_threshold, max_rows=fd_max_rows
    )
    keys = _keys_from_columns(columns, max_key_size=max_key_size, max_rows=fd_max_rows)

    tp = TableProfile(
        source_id=source_id,
        table=table,
        row_count=n,
        columns=profiles,
        candidate_keys=keys,
        fds=tuple(fds),
        append_mostly=False,
    )
    if previous is not None:
        tp.append_mostly = bool(append_detector(previous, tp))
    return tp, units


def profile_table(
    data: Any,
    source_id: str,
    table: str,
    **kwargs: Any,
) -> TableProfile:
    """§11.2 M3 interface: profile(df, source_id, table) -> TableProfile (= φ per column)."""
    return profile_table_detailed(data, source_id, table, **kwargs)[0]


# §11.2 names the interface `profile(stream) -> φ`; the table form is the v0 stream unit.
profile = profile_table
