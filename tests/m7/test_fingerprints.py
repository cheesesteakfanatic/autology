"""Data fingerprints: stable, content-based, row-order-insensitive."""

from __future__ import annotations

import numpy as np
import pandas as pd

from ontoforge.transforms import fingerprint_dataframe, memo_key


def test_row_order_insensitive() -> None:
    a = pd.DataFrame({"x": [1, 2, 3], "y": ["a", "b", "c"]})
    b = pd.DataFrame({"x": [3, 1, 2], "y": ["c", "a", "b"]})
    assert fingerprint_dataframe(a) == fingerprint_dataframe(b)


def test_column_order_insensitive() -> None:
    a = pd.DataFrame({"x": [1], "y": ["a"]})
    b = pd.DataFrame({"y": ["a"], "x": [1]})
    assert fingerprint_dataframe(a) == fingerprint_dataframe(b)


def test_value_change_changes_fingerprint() -> None:
    a = pd.DataFrame({"x": [1, 2]})
    b = pd.DataFrame({"x": [1, 3]})
    assert fingerprint_dataframe(a) != fingerprint_dataframe(b)


def test_column_rename_changes_fingerprint() -> None:
    a = pd.DataFrame({"x": [1]})
    b = pd.DataFrame({"z": [1]})
    assert fingerprint_dataframe(a) != fingerprint_dataframe(b)


def test_type_distinction_and_null_handling() -> None:
    ints = pd.DataFrame({"x": [1]})
    strs = pd.DataFrame({"x": ["1"]})
    assert fingerprint_dataframe(ints) != fingerprint_dataframe(strs)
    nan = pd.DataFrame({"x": [np.nan]})
    none = pd.DataFrame({"x": [None]}, dtype=object)
    assert fingerprint_dataframe(nan) == fingerprint_dataframe(none)  # both "absent"


def test_empty_and_duplicate_rows() -> None:
    empty = pd.DataFrame({"x": pd.Series([], dtype="int64")})
    assert fingerprint_dataframe(empty) == fingerprint_dataframe(empty.copy())
    one = pd.DataFrame({"x": [7]})
    two = pd.DataFrame({"x": [7, 7]})
    assert fingerprint_dataframe(one) != fingerprint_dataframe(two)


def test_memo_key_depends_on_transform_and_each_input() -> None:
    k = memo_key("fp1", {"raw.a": "da", "raw.b": "db"})
    assert k == memo_key("fp1", {"raw.b": "db", "raw.a": "da"})  # order-free
    assert k != memo_key("fp2", {"raw.a": "da", "raw.b": "db"})
    assert k != memo_key("fp1", {"raw.a": "XX", "raw.b": "db"})
    assert k != memo_key("fp1", {"raw.a": "da"})
