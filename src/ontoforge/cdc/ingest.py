"""Ingest driver + RAW mirror (whitepaper §2 RAW layer, §11.2 M1).

ingest(connector, ledger, state) pulls a DeltaBatch, registers its atoms with
any object implementing the contracts Ledger protocol (only ``register_atoms``
is required — accepted structurally, never importing the M0 implementation),
and optionally mirrors the pulled snapshot to the RAW layer.

Registration policy: insert and update atoms are registered (they carry real
source values — they ARE the provenance leaves every downstream artifact's
N[X] term bottoms out in, constraint H). Delete tombstones are NOT registered:
they carry no value; the disappearance is conveyed to M0 through
``DeltaBatch.changed_atom_ids`` (the superseded ids), which is the exact
invalidation key set.

RAW mirror
----------
One directory per (source, object): ``{root}/raw/{source_id}/{object_name}/``.
Each snapshot is written as content-addressed Parquet (``{xxh3(bytes)}.parquet``)
plus an append-only ``manifest.jsonl`` line carrying (cycle, pulled_at,
content_hash, row/column counts) — the provenance of the mirror write.
Byte-stability for unchanged data falls out of content addressing: an unchanged
snapshot serializes to byte-identical Parquet (pyarrow writes are deterministic
for identical tables), hashes to the same name, and is NOT rewritten; only a
manifest line is appended. (cycle, pulled_at) live in the manifest rather than
in the Parquet metadata precisely so the data bytes stay stable across cycles.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from ontoforge.contracts import DeltaBatch

from .base import AtomRegistrar, Connector, JSONState, hash64_bytes


class RawMirror:
    """Content-addressed Parquet mirror of pulled snapshots."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)

    def _dir(self, source_id: str, object_name: str) -> Path:
        return self.root / "raw" / source_id / object_name

    def write_snapshot(
        self, source_id: str, object_name: str, table: pa.Table, cycle: int, pulled_at: str
    ) -> dict:
        """Write one snapshot; returns the manifest entry (incl. content_hash)."""
        sink = pa.BufferOutputStream()
        pq.write_table(table, sink)
        data = sink.getvalue().to_pybytes()
        content_hash = hash64_bytes(data)

        d = self._dir(source_id, object_name)
        d.mkdir(parents=True, exist_ok=True)
        fname = f"{content_hash}.parquet"
        fpath = d / fname
        if not fpath.exists():  # unchanged data is never rewritten -> byte-stable
            fpath.write_bytes(data)

        entry = {
            "cycle": cycle,
            "pulled_at": pulled_at,
            "file": fname,
            "content_hash": content_hash,
            "num_rows": table.num_rows,
            "num_columns": table.num_columns,
        }
        with open(d / "manifest.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, sort_keys=True) + "\n")
        return entry

    def manifest(self, source_id: str, object_name: str) -> list[dict]:
        path = self._dir(source_id, object_name) / "manifest.jsonl"
        if not path.exists():
            return []
        with open(path, encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]

    def read_snapshot(self, source_id: str, object_name: str, cycle: int | None = None) -> pa.Table:
        """Read the snapshot for a cycle (default: the latest manifest entry)."""
        entries = self.manifest(source_id, object_name)
        if not entries:
            raise FileNotFoundError(f"no RAW snapshots for {source_id}/{object_name}")
        if cycle is None:
            entry = entries[-1]
        else:
            matches = [e for e in entries if e["cycle"] == cycle]
            if not matches:
                raise FileNotFoundError(f"no RAW snapshot for {source_id}/{object_name} cycle {cycle}")
            entry = matches[-1]
        return pq.read_table(self._dir(source_id, object_name) / entry["file"])


def ingest(
    connector: Connector,
    ledger: AtomRegistrar,
    state: JSONState,
    *,
    mirror: RawMirror | None = None,
    pulled_at: str | None = None,
) -> tuple[DeltaBatch, dict]:
    """Pull a delta, register its atoms, optionally mirror the snapshot.

    Returns (batch, new_state); the caller owns persisting new_state (JSON-able).
    """
    batch, new_state = connector.pull(state)
    atoms = [d.atom for d in batch.deltas if d.kind != "delete"]
    if atoms:
        ledger.register_atoms(atoms)
    if mirror is not None:
        ts = pulled_at if pulled_at is not None else datetime.now(timezone.utc).isoformat()
        for object_name, table in connector.snapshot_tables():
            mirror.write_snapshot(connector.source_id, object_name, table, batch.cycle, ts)
    return batch, new_state


__all__ = ["RawMirror", "ingest"]
