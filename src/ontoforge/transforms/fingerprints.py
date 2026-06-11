"""M7 data fingerprints: stable content hash of a tabular input.

Row-order-insensitive by construction: hash each row independently, sort the
row hashes, hash the sorted sequence together with the (sorted) column header.
Column order is therefore also canonical. Two DataFrames with the same cell
contents — in any row/column order — get the same fingerprint.

Cost: O(rows × cols) value formatting + O(rows log rows) sort, single pass,
in Python. At AMD-0001 fixture scale (≤ a few thousand rows per table) this is
sub-millisecond-to-low-millisecond; for large tables a vectorized or sampled
variant would be the upgrade path (documented trade-off, not needed here).
"""

from __future__ import annotations

from typing import Any, Mapping

import pandas as pd
import xxhash

__all__ = ["fingerprint_dataframe", "memo_key"]

_SEP = b"\x1f"


def _cell_repr(v: Any) -> str:
    if v is None or v is pd.NA or (isinstance(v, float) and v != v):  # None/NA/NaN
        return "\x00null"
    return f"{type(v).__name__}:{v!r}"


def fingerprint_dataframe(df: pd.DataFrame) -> str:
    """Stable, row-order- and column-order-insensitive content hash."""
    cols = sorted(map(str, df.columns))
    header = xxhash.xxh3_64()
    for c in cols:
        header.update(c.encode())
        header.update(_SEP)
    row_hashes: list[int] = []
    if len(df):
        ordered = df[cols] if list(df.columns) != cols else df
        for row in ordered.itertuples(index=False, name=None):
            h = xxhash.xxh3_64()
            for v in row:
                h.update(_cell_repr(v).encode())
                h.update(_SEP)
            row_hashes.append(h.intdigest())
    row_hashes.sort()
    final = xxhash.xxh3_64()
    final.update(header.digest())
    for rh in row_hashes:
        final.update(rh.to_bytes(8, "little"))
    return f"{final.intdigest():016x}"


def memo_key(transform_fingerprint: str, input_fingerprints: Mapping[str, str]) -> str:
    """Virtual-environment memo key (§5.1): a materialization is identified by
    the transform's content fingerprint plus its inputs' data fingerprints."""
    h = xxhash.xxh3_64()
    h.update(transform_fingerprint.encode())
    h.update(_SEP)
    for table in sorted(input_fingerprints):
        h.update(table.encode())
        h.update(b"=")
        h.update(input_fingerprints[table].encode())
        h.update(_SEP)
    return f"{h.intdigest():016x}"
