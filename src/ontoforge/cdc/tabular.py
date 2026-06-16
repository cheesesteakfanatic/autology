"""Tabular CDC connectors: CSV and Parquet file hash-diff (whitepaper §11.2 M1).

Delta semantics
---------------
Row identity comes from ``key_columns``. Per pull, every row gets a content hash;
rows whose hash matches the prior state emit nothing. Changed rows are diffed
PER CELL: only cells whose atom_id changed emit an ``AtomDelta`` (kind="update",
``superseded_atom_id`` = the previous atom at the same uri). New rows / columns
emit inserts; vanished rows / columns emit deletes.

Delete representation: a delete carries a TOMBSTONE atom — ``Atom(uri, value=None)``
at the vanished cell's uri — with ``superseded_atom_id`` pointing at the atom that
disappeared. The tombstone is never registered in the ledger (see ingest.py); it
exists so the delta stream alone can reconstruct the new snapshot from the old
(delta-completeness test) and so ``DeltaBatch.changed_atom_ids`` drives exact
invalidation.

Keyless rows (documented limitation)
------------------------------------
Rows missing any key value (or when ``key_columns`` is empty) get a
content-addressed row key (``row-<xxh3 of all cells>``). Such rows cannot be
*tracked* across edits: editing one is observed as delete(old)+insert(new),
never as a per-cell update. Identical duplicate rows are disambiguated with a
``~n`` occurrence suffix assigned in encounter order; because the rows are
byte-identical this is stable in effect under reordering.
"""

from __future__ import annotations

import csv
import io
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from ontoforge.contracts import Atom, AtomDelta, DeltaBatch, cell_uri, value_repr

from .base import JSONState, STATE_FORMAT, check_state, hash64, next_cycle, quote_part, read_text_robust

_STATE_KIND = "tabular"


def parse_csv_text(text: str, where: str) -> tuple[list[str], list[dict[str, Any]]]:
    """Parse CSV text into (ordered columns, rows) — the single CSV parsing rule.

    Shared by ``CsvConnector`` (file) and ``ObjectStoreConnector`` (in-memory blob)
    so both apply byte-for-byte identical semantics:
    - all values are strings; a field present-but-empty is ``""`` while a missing
      trailing field (short row) is ``None`` — distinct under ``value_repr``;
    - completely blank records are skipped;
    - rows longer than the header and duplicate header names raise ``ValueError``.
    ``where`` is a source label used only in error messages.
    """
    reader = csv.reader(io.StringIO(text, newline=""))
    records = [r for r in reader if r]  # drop blank records
    if not records:
        return [], []
    columns = list(records[0])
    if len(set(columns)) != len(columns):
        raise ValueError(f"duplicate column names in CSV header of {where}: {columns}")
    rows: list[dict[str, Any]] = []
    for lineno, rec in enumerate(records[1:], start=2):
        if len(rec) > len(columns):
            raise ValueError(f"{where}:{lineno}: row has {len(rec)} fields, header has {len(columns)}")
        rows.append({c: (rec[i] if i < len(rec) else None) for i, c in enumerate(columns)})
    return columns, rows


class _TabularConnector:
    """Shared per-row hash-diff engine; subclasses provide ``_load``/``_snapshot_table``."""

    def __init__(
        self,
        source_id: str,
        path: Path | str,
        key_columns: list[str] | tuple[str, ...] = (),
        object_name: str | None = None,
    ) -> None:
        self.source_id = source_id
        self.path = Path(path)
        self.key_columns = list(key_columns)
        self.object_name = object_name or self.path.stem
        self._last_table: pa.Table | None = None

    # ------------------------------------------------------------- subclass API

    def _load(self) -> tuple[list[str], list[dict[str, Any]]]:
        """Return (ordered column names, rows as {column: value})."""
        raise NotImplementedError

    def _snapshot_table(self, columns: list[str], rows: list[dict[str, Any]]) -> pa.Table:
        raise NotImplementedError

    # ----------------------------------------------------------------- protocol

    def snapshot_tables(self) -> list[tuple[str, pa.Table]]:
        if self._last_table is None:
            raise RuntimeError("snapshot_tables() requires a prior pull()")
        return [(self.object_name, self._last_table)]

    def pull(self, state: JSONState) -> tuple[DeltaBatch, dict]:
        prior = check_state(state, _STATE_KIND)
        columns, rows = self._load()
        self._last_table = self._snapshot_table(columns, rows)
        cycle = next_cycle(prior)
        old_rows: dict[str, dict] = prior.get("rows", {})

        qobj = quote_part(self.object_name)
        # ---- build the new snapshot index: row_key -> (row_hash, {col: Atom})
        new_rows: dict[str, dict[str, Any]] = {}
        seen_keys: dict[str, int] = {}
        for row in rows:
            row_key = self._row_key(row, columns, seen_keys)
            cells: dict[str, Atom] = {}
            for col in columns:
                value = row.get(col)
                cells[col] = Atom(uri=cell_uri(self.source_id, qobj, row_key, quote_part(col)), value=value)
            row_hash = hash64(*(f"{c}\x1e{value_repr(row.get(c))}" for c in columns))
            new_rows[row_key] = {"h": row_hash, "cells": cells}

        # ---- diff against prior state
        deltas: list[AtomDelta] = []
        for row_key, nr in new_rows.items():
            orow = old_rows.get(row_key)
            if orow is None:
                for col in columns:
                    deltas.append(AtomDelta(kind="insert", atom=nr["cells"][col]))
                continue
            if orow["h"] == nr["h"]:
                continue  # unchanged row: zero deltas
            ocells: dict[str, str] = orow["cells"]  # col -> atom_id
            for col, atom in nr["cells"].items():
                old_id = ocells.get(col)
                if old_id is None:
                    deltas.append(AtomDelta(kind="insert", atom=atom))
                elif old_id != atom.atom_id:
                    deltas.append(AtomDelta(kind="update", atom=atom, superseded_atom_id=old_id))
            for col, old_id in ocells.items():
                if col not in nr["cells"]:
                    deltas.append(self._tombstone(qobj, row_key, col, old_id))
        for row_key, orow in old_rows.items():
            if row_key not in new_rows:
                for col, old_id in orow["cells"].items():
                    deltas.append(self._tombstone(qobj, row_key, col, old_id))

        new_state = {
            "format": STATE_FORMAT,
            "kind": _STATE_KIND,
            "cycle": cycle,
            "columns": list(columns),
            "rows": {
                rk: {"h": nr["h"], "cells": {c: a.atom_id for c, a in nr["cells"].items()}}
                for rk, nr in new_rows.items()
            },
        }
        return DeltaBatch(source_id=self.source_id, cycle=cycle, deltas=deltas), new_state

    # ------------------------------------------------------------------ helpers

    def _tombstone(self, qobj: str, row_key: str, col: str, old_id: str) -> AtomDelta:
        uri = cell_uri(self.source_id, qobj, row_key, quote_part(col))
        return AtomDelta(kind="delete", atom=Atom(uri=uri, value=None), superseded_atom_id=old_id)

    def _row_key(self, row: dict[str, Any], columns: list[str], seen: dict[str, int]) -> str:
        if self.key_columns:
            vals = [row.get(c) for c in self.key_columns]
            if all(v is not None for v in vals):
                base = "|".join(quote_part(value_repr(v)) for v in vals)
                return self._disambiguate(base, seen)
        # keyless: content-addressed row key (documented limitation, module docstring)
        base = "row-" + hash64(*(f"{c}\x1e{value_repr(row.get(c))}" for c in columns))
        return self._disambiguate(base, seen)

    @staticmethod
    def _disambiguate(base: str, seen: dict[str, int]) -> str:
        n = seen.get(base, 0) + 1
        seen[base] = n
        return base if n == 1 else f"{base}~{n}"


class CsvConnector(_TabularConnector):
    """CSV file connector. Every cell becomes a cell atom.

    Parsing rules:
    - encoding: utf-8 (BOM tolerated via utf-8-sig), latin-1 fallback (base.read_text_robust);
    - CRLF / CR / LF line endings all accepted (csv module handles them natively);
    - all values are strings; a field that is *present but empty* is ``""`` while a
      *missing trailing field* (short row) is ``None`` — distinct per contracts
      value_repr, hence distinct atoms;
    - completely blank records are skipped;
    - rows longer than the header and duplicate header names raise ValueError
      (malformed source; failing loudly beats silent data loss).
    """

    def _load(self) -> tuple[list[str], list[dict[str, Any]]]:
        return parse_csv_text(read_text_robust(self.path), str(self.path))

    def _snapshot_table(self, columns: list[str], rows: list[dict[str, Any]]) -> pa.Table:
        if not columns:
            return pa.table({})
        return pa.table(
            {c: pa.array([r.get(c) for r in rows], type=pa.string()) for c in columns}
        )


class ParquetConnector(_TabularConnector):
    """Parquet file connector — same delta contract as CsvConnector, typed values.

    Values are pyarrow's Python projections (int/float/str/date/Decimal/...);
    atom content addressing uses contracts.value_repr over those, which keeps
    None and "" distinct and floats round-trip-stable.
    """

    def _load(self) -> tuple[list[str], list[dict[str, Any]]]:
        table = pq.read_table(self.path)
        self._source_table = table
        return list(table.column_names), table.to_pylist()

    def _snapshot_table(self, columns: list[str], rows: list[dict[str, Any]]) -> pa.Table:
        # lossless: mirror the table exactly as read, preserving source types
        return self._source_table


__all__ = ["CsvConnector", "ParquetConnector", "parse_csv_text"]
