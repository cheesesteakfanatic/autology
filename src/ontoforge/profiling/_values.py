"""Input normalization for M3: pyarrow Table / pandas DataFrame / dict -> plain columns.

Every profiling routine works over `dict[str, list]` with Python-native scalars and
``None`` for nulls (NaN/NaT are normalized to None — a profiling pass must treat all
"missing" markers identically or null_rate becomes engine-dependent).

`value_key` is the canonical hashing form shared by HLL / MinHash / IND value-set
hashing. Integral floats collapse to the integer key so a BIGINT FK matches a DOUBLE
PK across engines (a real cross-source wart in the §17.2 estates).
"""

from __future__ import annotations

import datetime as _dt
import math
from typing import Any, Iterable, Mapping

import xxhash

__all__ = ["columns_of", "is_null", "value_key", "hash64", "display_str", "sample_evenly"]


def is_null(v: Any) -> bool:
    if v is None:
        return True
    if isinstance(v, float) and math.isnan(v):
        return True
    # pandas NA: (NA != NA) is NA, whose truthiness raises — catch it by type name
    if type(v).__name__ == "NAType":
        return True
    # pandas NaT / numpy nan satisfy v != v without importing pandas here
    try:
        if v != v:  # noqa: PLR0124 — deliberate NaN-style self-inequality probe
            return True
    except Exception:
        pass
    return False


def _norm_scalar(v: Any) -> Any:
    return None if is_null(v) else v


def columns_of(data: Any) -> dict[str, list]:
    """Normalize a pyarrow Table, pandas DataFrame, or mapping of lists to plain columns."""
    # pyarrow Table (duck-typed to avoid a hard import dependency at call sites)
    if hasattr(data, "to_pydict") and hasattr(data, "num_rows"):
        raw = data.to_pydict()
        return {c: [_norm_scalar(v) for v in vals] for c, vals in raw.items()}
    # pandas DataFrame
    if hasattr(data, "columns") and hasattr(data, "dtypes"):
        out: dict[str, list] = {}
        for c in data.columns:
            out[str(c)] = [_norm_scalar(v) for v in data[c].tolist()]
        return out
    if isinstance(data, Mapping):
        return {str(c): [_norm_scalar(v) for v in vals] for c, vals in data.items()}
    raise TypeError(f"unsupported table type for profiling: {type(data)!r}")


def row_count_of(columns: Mapping[str, list]) -> int:
    return max((len(v) for v in columns.values()), default=0)


def value_key(v: Any) -> str:
    """Canonical string form used for value-set hashing (HLL, MinHash, INDs)."""
    if isinstance(v, bool):
        return "b:" + ("1" if v else "0")
    if isinstance(v, int):
        return f"n:{v}"
    if isinstance(v, float):
        if v.is_integer():
            return f"n:{int(v)}"
        return f"f:{v!r}"
    if isinstance(v, _dt.datetime):
        return "t:" + v.isoformat()
    if isinstance(v, _dt.date):
        return "d:" + v.isoformat()
    return "s:" + str(v)


def hash64(key: str, seed: int = 0) -> int:
    return xxhash.xxh64_intdigest(key.encode("utf-8", "surrogatepass"), seed=seed)


def display_str(v: Any) -> str:
    """Human-form string of a value (format signatures, semantic typing, samples)."""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    if isinstance(v, (_dt.datetime, _dt.date)):
        return v.isoformat()
    return str(v)


def sample_evenly(items: Iterable[str], k: int) -> list[str]:
    """Deterministic stratified sample: sort distinct values, take k evenly spaced."""
    uniq = sorted(set(items))
    if len(uniq) <= k:
        return uniq
    step = (len(uniq) - 1) / (k - 1) if k > 1 else 0.0
    return [uniq[round(i * step)] for i in range(k)]
