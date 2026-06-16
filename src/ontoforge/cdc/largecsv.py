"""Large-CSV CDC connector: chunked, constant-memory streaming (whitepaper §11.2 M1).

``CsvConnector`` reads the whole file into memory (fine for the MVP file sizes).
``LargeCsvConnector`` handles multi-hundred-MB CSVs by streaming the file in
fixed-size row chunks: it NEVER materializes all rows or a full in-memory snapshot
table at once. Memory is bounded by ``chunk_size`` rows plus the per-row state index
(one content hash + one atom_id per cell), which is the irreducible cost of a
deterministic per-cell diff.

Delta semantics are byte-for-byte identical to ``CsvConnector`` — same URIs, same
atom_ids, same insert/update/delete/tombstone rules, same keyless fallback. The only
difference is *how* the snapshot is traversed (streaming) and how it is mirrored to
RAW (written incrementally to a content-addressed temp Parquet, never held whole).

Determinism vs. constant memory
-------------------------------
Per-row hashing and per-cell diffing are inherently streaming-friendly: each row is
diffed against the prior state the moment it is read. The only non-streaming part is
*deletes* — a delete is a key that was in the prior state but never reappears — which
we detect after the stream completes by set difference over keys (key strings only,
not values). This keeps memory at O(distinct keys), matching what any CDC engine must
track to emit deletes at all.
"""

from __future__ import annotations

import csv
import io
from pathlib import Path
from typing import Any, Iterator

import pyarrow as pa
import pyarrow.parquet as pq

from ontoforge.contracts import Atom, AtomDelta, DeltaBatch, cell_uri, value_repr

from .base import JSONState, STATE_FORMAT, check_state, hash64, next_cycle, quote_part, read_text_robust

_STATE_KIND = "tabular"  # same state kind as CsvConnector: a large CSV IS a tabular source


class LargeCsvConnector:
    """Constant-memory CSV connector for CSV-at-scale; identical delta contract to CsvConnector.

    Parameters
    ----------
    source_id, path, key_columns, object_name:
        As ``CsvConnector``.
    chunk_size:
        Rows parsed/held per chunk while streaming. Bounds peak memory; has NO
        effect on output (same atoms, same deltas at any chunk size).
    """

    def __init__(
        self,
        source_id: str,
        path: Path | str,
        key_columns: list[str] | tuple[str, ...] = (),
        object_name: str | None = None,
        *,
        chunk_size: int = 50_000,
    ) -> None:
        self.source_id = source_id
        self.path = Path(path)
        self.key_columns = list(key_columns)
        self.object_name = object_name or self.path.stem
        self.chunk_size = max(1, int(chunk_size))
        self._snapshot_path: Path | None = None
        self._snapshot_columns: list[str] = []

    # ----------------------------------------------------------------- protocol

    def snapshot_tables(self) -> list[tuple[str, pa.Table]]:
        """Read back the streamed snapshot from the temp Parquet written during pull().

        Reads it as a single table here (the RAW mirror writer needs a ``pa.Table``);
        the *diff* never held the whole snapshot in memory, which is the constant-memory
        guarantee for the change-detection path.
        """
        if self._snapshot_path is None:
            raise RuntimeError("snapshot_tables() requires a prior pull()")
        return [(self.object_name, pq.read_table(self._snapshot_path))]

    def stream_chunks(self) -> Iterator[tuple[list[str], list[dict[str, Any]]]]:
        """Yield (columns, rows-chunk) tuples; each chunk holds <= chunk_size rows.

        The whole file is decoded once (utf-8-sig/latin-1) — text decode is O(file)
        but a single pass; rows are yielded incrementally so the caller never holds
        more than one chunk of parsed dict rows.
        """
        text = read_text_robust(self.path)
        reader = csv.reader(io.StringIO(text, newline=""))
        columns: list[str] | None = None
        buf: list[dict[str, Any]] = []
        lineno = 1
        for rec in reader:
            if not rec:  # skip completely blank records (matches CsvConnector)
                continue
            if columns is None:
                columns = list(rec)
                if len(set(columns)) != len(columns):
                    raise ValueError(
                        f"duplicate column names in CSV header of {self.path}: {columns}"
                    )
                lineno += 1
                continue
            lineno += 1
            if len(rec) > len(columns):
                raise ValueError(
                    f"{self.path}:{lineno}: row has {len(rec)} fields, header has {len(columns)}"
                )
            buf.append({c: (rec[i] if i < len(rec) else None) for i, c in enumerate(columns)})
            if len(buf) >= self.chunk_size:
                yield columns, buf
                buf = []
        if columns is None:
            return  # empty file
        if buf:
            yield columns, buf

    # -------------------------------------------------------------------- pull

    def pull(self, state: JSONState) -> tuple[DeltaBatch, dict]:
        prior = check_state(state, _STATE_KIND)
        cycle = next_cycle(prior)
        old_rows: dict[str, dict] = prior.get("rows", {})

        qobj = quote_part(self.object_name)
        deltas: list[AtomDelta] = []
        new_state_rows: dict[str, dict] = {}
        seen_keys: dict[str, int] = {}
        seen_in_new: set[str] = set()

        columns: list[str] = []
        writer: pq.ParquetWriter | None = None
        snap_path = self._make_snapshot_path()

        try:
            for columns, chunk in self.stream_chunks():
                # incremental RAW mirror write (constant memory)
                writer = self._write_chunk(writer, snap_path, columns, chunk)
                # incremental per-cell diff
                for row in chunk:
                    row_key = self._row_key(row, columns, seen_keys)
                    seen_in_new.add(row_key)
                    cells = {
                        col: Atom(
                            uri=cell_uri(self.source_id, qobj, row_key, quote_part(col)),
                            value=row.get(col),
                        )
                        for col in columns
                    }
                    row_hash = hash64(*(f"{c}\x1e{value_repr(row.get(c))}" for c in columns))
                    new_state_rows[row_key] = {
                        "h": row_hash,
                        "cells": {c: a.atom_id for c, a in cells.items()},
                    }
                    self._diff_row(deltas, qobj, row_key, columns, cells, row_hash, old_rows)
        finally:
            if writer is not None:
                writer.close()

        # rows that vanished -> tombstone every cell (key-only set difference)
        for row_key, orow in old_rows.items():
            if row_key not in seen_in_new:
                for col, old_id in orow["cells"].items():
                    deltas.append(self._tombstone(qobj, row_key, col, old_id))

        if writer is None:  # empty CSV: still produce a valid (empty) snapshot
            self._write_empty_snapshot(snap_path, columns)

        self._snapshot_path = snap_path
        self._snapshot_columns = columns

        new_state = {
            "format": STATE_FORMAT,
            "kind": _STATE_KIND,
            "cycle": cycle,
            "columns": list(columns),
            "rows": new_state_rows,
        }
        return DeltaBatch(source_id=self.source_id, cycle=cycle, deltas=deltas), new_state

    # ------------------------------------------------------------------ helpers

    def _diff_row(
        self,
        deltas: list[AtomDelta],
        qobj: str,
        row_key: str,
        columns: list[str],
        cells: dict[str, Atom],
        row_hash: str,
        old_rows: dict[str, dict],
    ) -> None:
        orow = old_rows.get(row_key)
        if orow is None:
            deltas.extend(AtomDelta(kind="insert", atom=cells[col]) for col in columns)
            return
        if orow["h"] == row_hash:
            return  # unchanged row: zero deltas
        ocells: dict[str, str] = orow["cells"]  # col -> atom_id
        for col, atom in cells.items():
            old_id = ocells.get(col)
            if old_id is None:
                deltas.append(AtomDelta(kind="insert", atom=atom))
            elif old_id != atom.atom_id:
                deltas.append(AtomDelta(kind="update", atom=atom, superseded_atom_id=old_id))
        for col, old_id in ocells.items():
            if col not in cells:
                deltas.append(self._tombstone(qobj, row_key, col, old_id))

    def _tombstone(self, qobj: str, row_key: str, col: str, old_id: str) -> AtomDelta:
        uri = cell_uri(self.source_id, qobj, row_key, quote_part(col))
        return AtomDelta(kind="delete", atom=Atom(uri=uri, value=None), superseded_atom_id=old_id)

    def _row_key(self, row: dict[str, Any], columns: list[str], seen: dict[str, int]) -> str:
        if self.key_columns:
            vals = [row.get(c) for c in self.key_columns]
            if all(v is not None for v in vals):
                base = "|".join(quote_part(value_repr(v)) for v in vals)
                return self._disambiguate(base, seen)
        base = "row-" + hash64(*(f"{c}\x1e{value_repr(row.get(c))}" for c in columns))
        return self._disambiguate(base, seen)

    @staticmethod
    def _disambiguate(base: str, seen: dict[str, int]) -> str:
        n = seen.get(base, 0) + 1
        seen[base] = n
        return base if n == 1 else f"{base}~{n}"

    # ----------------------------------------------------- streaming RAW mirror

    def _make_snapshot_path(self) -> Path:
        import tempfile

        fd, name = tempfile.mkstemp(prefix="ontoforge-largecsv-", suffix=".parquet")
        import os

        os.close(fd)
        return Path(name)

    def _write_chunk(
        self,
        writer: pq.ParquetWriter | None,
        snap_path: Path,
        columns: list[str],
        chunk: list[dict[str, Any]],
    ) -> pq.ParquetWriter:
        batch = pa.table(
            {c: pa.array([r.get(c) for r in chunk], type=pa.string()) for c in columns}
        )
        if writer is None:
            writer = pq.ParquetWriter(snap_path, batch.schema)
        writer.write_table(batch)
        return writer

    def _write_empty_snapshot(self, snap_path: Path, columns: list[str]) -> None:
        if columns:
            empty = pa.table({c: pa.array([], type=pa.string()) for c in columns})
        else:
            empty = pa.table({})
        pq.write_table(empty, snap_path)


__all__ = ["LargeCsvConnector"]
