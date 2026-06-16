"""Object-store CDC connector for S3-compatible storage (whitepaper §11.2 M1).

Reads a single CSV or Parquet object from an S3-compatible bucket and runs the
SAME per-cell hash-diff as the file connectors. The transport is abstracted behind
a tiny ``_open(path) -> bytes`` adapter:

* if ``fsspec`` is installed (the ``connectors`` extra), any fsspec URL works —
  ``s3://bucket/key``, ``gcs://``, ``file://``, a local path — using the
  registered filesystem (``s3fs`` for real S3, ``LocalFileSystem`` for a tmp-dir
  fake, etc.);
* otherwise a clean local-filesystem fallback adapter handles ``file://`` URLs and
  bare paths, so the connector is usable (and TESTABLE, fully offline) without the
  extra. A genuine ``s3://`` URL without fsspec raises an actionable error.

This keeps the test offline: a temp directory stands in for a bucket and is read
through the fallback adapter (or fsspec's LocalFileSystem) — never a live endpoint.

Format is chosen by the object suffix (``.csv`` / ``.parquet``) unless ``fmt`` is
given. Bytes are parsed in-memory with the EXACT same CSV / Parquet semantics as
``CsvConnector`` / ``ParquetConnector`` (encoding-robust CSV, typed Parquet), so an
object and its on-disk twin yield identical atoms.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

import pyarrow as pa
import pyarrow.parquet as pq

from .tabular import _TabularConnector, parse_csv_text

_CSV_SUFFIXES = (".csv",)
_PARQUET_SUFFIXES = (".parquet", ".pq")

_MISSING_FSSPEC_HINT = (
    "ObjectStoreConnector needs fsspec (and a backend such as s3fs) for non-local "
    "URLs. Install the connectors extra:\n"
    "    pip install 'ontoforge[connectors]'\n"
    "Local paths and file:// URLs work without it."
)


def _read_text_bytes(data: bytes) -> str:
    """utf-8-sig then latin-1, mirroring base.read_text_robust over an in-memory blob."""
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("latin-1")


class ObjectStoreConnector(_TabularConnector):
    """CSV/Parquet object in S3-compatible storage; same delta contract as the file connectors.

    Parameters
    ----------
    source_id:
        Stable source id (first URI segment).
    uri:
        Object URL. fsspec URL when fsspec is installed (``s3://bucket/key`` …);
        a ``file://`` URL or bare path always works via the local fallback.
    key_columns:
        Row-identity columns (keyless content-addressing otherwise).
    fmt:
        ``"csv"`` | ``"parquet"``; inferred from the suffix when omitted.
    object_name:
        URI object name; defaults to the object's stem (its filename without suffix).
    storage_options:
        Passed through to ``fsspec.open`` (credentials, endpoint_url, …). Ignored
        by the local fallback.
    """

    def __init__(
        self,
        source_id: str,
        uri: str,
        key_columns: list[str] | tuple[str, ...] = (),
        *,
        fmt: str | None = None,
        object_name: str | None = None,
        storage_options: dict[str, Any] | None = None,
    ) -> None:
        stem = Path(urlparse(uri).path or uri).stem
        super().__init__(
            source_id,
            path=uri,
            key_columns=key_columns,
            object_name=object_name or stem,
        )
        self.uri = uri
        self.storage_options = storage_options or {}
        self.fmt = (fmt or self._infer_fmt(uri)).lower()
        if self.fmt not in ("csv", "parquet"):
            raise ValueError(f"ObjectStoreConnector: unsupported format {self.fmt!r} for {uri!r}")
        self._source_table: pa.Table | None = None

    @staticmethod
    def _infer_fmt(uri: str) -> str:
        suffix = Path(urlparse(uri).path or uri).suffix.lower()
        if suffix in _PARQUET_SUFFIXES:
            return "parquet"
        if suffix in _CSV_SUFFIXES:
            return "csv"
        raise ValueError(
            f"ObjectStoreConnector: cannot infer format from {uri!r}; pass fmt='csv'|'parquet'"
        )

    # ------------------------------------------------------------------ transport

    def _open_bytes(self) -> bytes:
        """Fetch the object as bytes through fsspec, else the local-filesystem fallback."""
        scheme = urlparse(self.uri).scheme
        is_local = scheme in ("", "file")
        try:
            import fsspec
        except ImportError:
            fsspec = None
        if fsspec is not None:
            with fsspec.open(self.uri, "rb", **self.storage_options) as f:
                return f.read()
        # ----- local fallback adapter (no fsspec): file:// or bare path only
        if not is_local:
            raise ImportError(_MISSING_FSSPEC_HINT)
        local = unquote(urlparse(self.uri).path) if scheme == "file" else self.uri
        return Path(local).read_bytes()

    # ------------------------------------------------------------------- _load

    def _load(self) -> tuple[list[str], list[dict[str, Any]]]:
        data = self._open_bytes()
        if self.fmt == "parquet":
            table = pq.read_table(pa.BufferReader(pa.py_buffer(data)))
            self._source_table = table
            return list(table.column_names), table.to_pylist()
        # CSV: reuse the shared parser over the decoded text (identical semantics)
        return parse_csv_text(_read_text_bytes(data), self.uri)

    # --------------------------------------------------------------- snapshot

    def _snapshot_table(self, columns: list[str], rows: list[dict[str, Any]]) -> pa.Table:
        if self.fmt == "parquet":
            assert self._source_table is not None  # set in _load
            return self._source_table
        if not columns:
            return pa.table({})
        return pa.table(
            {c: pa.array([r.get(c) for r in rows], type=pa.string()) for c in columns}
        )


__all__ = ["ObjectStoreConnector"]
