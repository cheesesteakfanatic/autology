"""Tiny, self-contained table normalization for the OPEN-SHELL anonymizer.

Kept LOCAL (not imported from ``profiling._values``) so the anonymizer stays a
clean open-shell package that reaches into NO closed-core internal submodule — it
is a candidate for open-sourcing on its own. Stdlib + duck-typed pandas/pyarrow,
matching the engine's null semantics (NaN / NaT / pandas-NA all normalize to
``None``) so a profiling pass over the anonymized tables behaves identically.
"""

from __future__ import annotations

import math
from typing import Any, Mapping

__all__ = ["columns_of_local", "is_null_local"]


def is_null_local(v: Any) -> bool:
    if v is None:
        return True
    if isinstance(v, float) and math.isnan(v):
        return True
    if type(v).__name__ == "NAType":  # pandas NA
        return True
    try:
        if v != v:  # noqa: PLR0124 — NaN/NaT self-inequality probe
            return True
    except Exception:
        pass
    return False


def _norm(v: Any) -> Any:
    return None if is_null_local(v) else v


def columns_of_local(data: Any) -> dict[str, list]:
    """Normalize a pyarrow Table / pandas DataFrame / mapping → ``{col: list}``."""
    if hasattr(data, "to_pydict") and hasattr(data, "num_rows"):  # pyarrow.Table
        raw = data.to_pydict()
        return {str(c): [_norm(v) for v in vals] for c, vals in raw.items()}
    if hasattr(data, "columns") and hasattr(data, "dtypes"):  # pandas.DataFrame
        return {str(c): [_norm(v) for v in data[c].tolist()] for c in data.columns}
    if isinstance(data, Mapping):
        return {str(c): [_norm(v) for v in vals] for c, vals in data.items()}
    raise TypeError(f"unsupported table type for anonymization: {type(data)!r}")
