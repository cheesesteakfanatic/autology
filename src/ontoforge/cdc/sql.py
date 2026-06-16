"""SQL CDC connector via SQLAlchemy (whitepaper §11.2 M1, §17.3 backlog promotion).

One interface for Postgres / MySQL / SQLite behind a SQLAlchemy connection URL.
Introspects a table, pulls rows in deterministic chunks, and maps every cell to a
cell atom — the SAME delta contract as ``CsvConnector`` / ``ParquetConnector``
(per-row content hash, per-cell update granularity, tombstone deletes, RAW Parquet
mirror). Reuses ``_TabularConnector`` so the diff engine, URI grammar, and state
format are byte-for-byte identical to the file connectors.

Why a table-snapshot diff rather than logical decoding
------------------------------------------------------
Logical-decoding / binlog CDC is provider-specific, requires elevated privileges,
and is non-deterministic to test offline. M1's spec line is ``pull(source) -> Δbatch``;
a deterministic per-cell hash-diff over an introspected snapshot satisfies it for
every SQLAlchemy-reachable engine with zero special privileges, and degrades
gracefully (a table with a stable primary key gets tracked per-cell; a keyless
table falls back to content-addressed row keys, exactly like CSV).

Driver policy
-------------
``sqlalchemy`` is lazy-imported inside ``_load`` / ``pull`` (and the engine is built
lazily) so importing this module never drags in the optional ``connectors`` extra.
A missing driver raises a clear, actionable error naming the extra.

Determinism
-----------
Rows are pulled ordered by the key columns (or, keyless, by every column) so the
snapshot — and therefore the content-addressed RAW Parquet bytes and the keyless
``~n`` occurrence suffixes — are stable across pulls regardless of physical row
order. Chunking (``chunk_size``) bounds memory: rows stream in via a server-side /
buffered cursor and are materialized one chunk at a time.
"""

from __future__ import annotations

from typing import Any

import pyarrow as pa

from .tabular import _TabularConnector

_MISSING_DRIVER_HINT = (
    "SqlConnector requires SQLAlchemy. Install the connectors extra:\n"
    "    pip install 'ontoforge[connectors]'\n"
    "(plus the DBAPI driver for your URL, e.g. psycopg2-binary for postgresql://, "
    "PyMySQL for mysql+pymysql://; sqlite:// needs only the stdlib)."
)


def _require_sqlalchemy():
    """Lazy-import SQLAlchemy; raise an actionable error if the extra is missing."""
    try:
        import sqlalchemy  # noqa: F401
    except ImportError as exc:  # pragma: no cover - exercised via monkeypatch in tests
        raise ImportError(_MISSING_DRIVER_HINT) from exc
    return sqlalchemy


class SqlConnector(_TabularConnector):
    """Snapshot-diff connector over any SQLAlchemy-reachable table.

    Parameters
    ----------
    source_id:
        Stable source identifier; the first URI path segment.
    url:
        A SQLAlchemy connection URL — e.g. ``sqlite:///file.db``,
        ``sqlite://`` (in-memory), ``postgresql+psycopg2://user@host/db``,
        ``mysql+pymysql://user@host/db``.
    table:
        The table to ingest. Becomes the URI object name (``object_name``)
        unless one is supplied explicitly.
    key_columns:
        Primary-key / business-key columns for row identity. If omitted, the
        connector introspects the table's primary key; if there is none, rows
        are keyless (content-addressed) exactly as in the file connectors.
    schema:
        Optional database schema/namespace for introspection.
    chunk_size:
        Rows materialized per fetch (constant memory for large tables).
    engine:
        Optional pre-built SQLAlchemy Engine (tests pass a shared in-memory one;
        an in-memory ``sqlite://`` URL otherwise yields a fresh empty DB per
        connection, so reuse matters there). When given, ``url`` is ignored for
        connecting but retained for diagnostics.
    """

    def __init__(
        self,
        source_id: str,
        url: str,
        table: str,
        key_columns: list[str] | tuple[str, ...] | None = None,
        *,
        schema: str | None = None,
        chunk_size: int = 10_000,
        object_name: str | None = None,
        engine: Any | None = None,
    ) -> None:
        # _TabularConnector wants a path; SQL has none. Pass the table name as a
        # synthetic path stem so object_name defaults to the table.
        super().__init__(
            source_id,
            path=table,
            key_columns=key_columns or (),
            object_name=object_name or table,
        )
        self.url = url
        self.table = table
        self.schema = schema
        self.chunk_size = max(1, int(chunk_size))
        self._explicit_keys = key_columns is not None
        self._engine = engine
        self._source_table: pa.Table | None = None

    # ------------------------------------------------------------------ engine

    def _get_engine(self):
        sa = _require_sqlalchemy()
        if self._engine is None:
            self._engine = sa.create_engine(self.url)
        return self._engine

    # --------------------------------------------------------------- introspect

    def _introspect(self, engine) -> tuple[list[str], list[str]]:
        """Return (ordered column names, primary-key columns) via SQLAlchemy reflection."""
        sa = _require_sqlalchemy()
        insp = sa.inspect(engine)
        cols = insp.get_columns(self.table, schema=self.schema)
        if not cols:
            raise ValueError(
                f"SqlConnector: table {self.table!r} not found or has no columns "
                f"(url={self.url!r}, schema={self.schema!r})"
            )
        columns = [c["name"] for c in cols]
        pk = insp.get_pk_constraint(self.table, schema=self.schema) or {}
        pk_cols = [c for c in (pk.get("constrained_columns") or []) if c in columns]
        return columns, pk_cols

    # ------------------------------------------------------------------- _load

    def _load(self) -> tuple[list[str], list[dict[str, Any]]]:
        sa = _require_sqlalchemy()
        engine = self._get_engine()
        columns, pk_cols = self._introspect(engine)

        # Resolve the effective key columns: explicit override > introspected PK.
        if self._explicit_keys:
            missing = [c for c in self.key_columns if c not in columns]
            if missing:
                raise ValueError(
                    f"SqlConnector: key_columns {missing} absent from table {self.table!r} "
                    f"(columns: {columns})"
                )
            effective_keys = list(self.key_columns)
        else:
            effective_keys = pk_cols
        # Make the diff engine and keyless fallback use the resolved keys.
        self.key_columns = effective_keys

        order_cols = effective_keys if effective_keys else columns

        meta = sa.MetaData()
        tbl = sa.Table(
            self.table, meta, autoload_with=engine, schema=self.schema
        )
        col_objs = [tbl.c[name] for name in columns]
        order_objs = [tbl.c[name] for name in order_cols]
        stmt = sa.select(*col_objs).order_by(*order_objs)

        rows: list[dict[str, Any]] = []
        with engine.connect() as conn:
            result = conn.execution_options(stream_results=True).execute(stmt)
            while True:
                chunk = result.fetchmany(self.chunk_size)
                if not chunk:
                    break
                for r in chunk:
                    rows.append({col: r[i] for i, col in enumerate(columns)})

        self._source_columns = columns
        return columns, rows

    # --------------------------------------------------------------- snapshot

    def _snapshot_table(self, columns: list[str], rows: list[dict[str, Any]]) -> pa.Table:
        """Mirror the pulled rows to an Arrow table, preserving DB-projected types.

        pyarrow infers a column type from its Python values; an all-null column
        is given an explicit string type so the schema is stable and the RAW
        Parquet write never fails on a null-only column.
        """
        if not columns:
            return pa.table({})
        arrays: dict[str, pa.Array] = {}
        for c in columns:
            vals = [r.get(c) for r in rows]
            if all(v is None for v in vals):
                arrays[c] = pa.array(vals, type=pa.string())
            else:
                arrays[c] = pa.array(vals)
        return pa.table(arrays)


__all__ = ["SqlConnector"]
