"""M6 — HEARTH portability: canonical export/import (whitepaper §4.2 constraint
(P), §4.5 export-import idempotence; the AMBER §7 precursor).

``export_canonical`` writes every value shard and link shard as PLAIN Parquet
(the exact canonical schemas of store.py) plus ``manifest.json``: class URIs,
layers, predicates, row counts, and CONTENT hashes. Content hashes are computed
over the canonical row encoding — not over file bytes — so they are stable
across Parquet writer metadata differences and across export/import cycles.

``import_canonical`` reconstructs a NEW Hearth with bit-equivalent canonical
state: identical cell multisets in identical seq order. Serving indexes
(current-value dicts, adjacency) are NOT carried — they are derived structures
and rebuild on import (§4.2(b)): a documented, capability-neutral loss.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

import pyarrow.parquet as pq
import xxhash

from ontoforge.contracts import Layer, Ledger, Ontology

from .errors import PortabilityError
from .store import (
    CELL_SCHEMA,
    LINK_SCHEMA,
    cell_to_row,
    link_to_row,
    shard_key,
    write_parquet_atomic,
)

if TYPE_CHECKING:  # pragma: no cover
    from .store import Hearth

MANIFEST_NAME = "manifest.json"
FORMAT_VERSION = 1


def _content_hash(rows: list[dict[str, Any]]) -> str:
    """xxh3 over the canonical JSON encoding of rows in seq order."""
    h = xxhash.xxh3_64()
    for row in rows:
        h.update(json.dumps(row, sort_keys=True, separators=(",", ":")).encode())
        h.update(b"\n")
    return f"{h.intdigest():016x}"


def export_canonical(store: "Hearth", out_dir: str | Path) -> Path:
    """Write all shards + links as plain Parquet with a manifest. Returns the
    manifest path. Deterministic: same canonical state -> same content hashes."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    entries: list[dict[str, Any]] = []
    for shard in store.value_shard_items():
        rows = [cell_to_row(seq, c) for seq, c in enumerate(shard.cells)]
        rel = f"values/{shard.layer.value}/{shard_key(shard.class_uri)}.parquet"
        write_parquet_atomic(shard.to_table(), out / rel)
        entries.append(
            {
                "kind": "values",
                "layer": shard.layer.value,
                "class_uri": shard.class_uri,
                "rows": len(rows),
                "content_hash": _content_hash(rows),
                "path": rel,
            }
        )
    for lshard in store.links.link_shard_items():
        rows = [link_to_row(seq, c) for seq, c in enumerate(lshard.cells)]
        rel = f"links/{shard_key(lshard.class_uri)}/{shard_key(lshard.predicate)}.parquet"
        write_parquet_atomic(lshard.to_table(), out / rel)
        entries.append(
            {
                "kind": "links",
                "class_uri": lshard.class_uri,
                "predicate": lshard.predicate,
                "rows": len(rows),
                "content_hash": _content_hash(rows),
                "path": rel,
            }
        )
    manifest = {
        "format": "hearth-canonical",
        "format_version": FORMAT_VERSION,
        "shards": entries,
        "derived_excluded": [
            "current-value dicts",
            "per-entity cell maps",
            "link adjacency (forward/reverse)",
            "duckdb views",
        ],
    }
    manifest_path = out / MANIFEST_NAME
    manifest_path.write_text(json.dumps(manifest, indent=1, sort_keys=True))
    return manifest_path


def _verify_entry(bundle: Path, entry: dict[str, Any]) -> list[dict[str, Any]]:
    path = bundle / entry["path"]
    if not path.is_file():
        raise PortabilityError(f"bundle missing shard file {entry['path']!r}")
    table = pq.read_table(path)
    rows = sorted(table.to_pylist(), key=lambda r: r["seq"])
    if len(rows) != entry["rows"]:
        raise PortabilityError(
            f"{entry['path']}: row count {len(rows)} != manifest {entry['rows']}"
        )
    if _content_hash(rows) != entry["content_hash"]:
        raise PortabilityError(f"{entry['path']}: content hash mismatch (bundle corrupt or tampered)")
    return rows


def import_canonical(
    bundle_dir: str | Path,
    root_dir: str | Path,
    ledger: Ledger,
    ontology: Optional[Ontology] = None,
) -> "Hearth":
    """Reconstruct a new Hearth from a canonical bundle. Verifies every content
    hash and row count against the manifest BEFORE building state, then rebuilds
    all derived serving indexes from the imported Parquet."""
    from .store import Hearth, row_to_cell, row_to_link

    bundle = Path(bundle_dir)
    manifest_path = bundle / MANIFEST_NAME
    if not manifest_path.is_file():
        raise PortabilityError(f"no {MANIFEST_NAME} in {bundle}")
    manifest = json.loads(manifest_path.read_text())
    if manifest.get("format") != "hearth-canonical":
        raise PortabilityError(f"not a hearth-canonical bundle: format={manifest.get('format')!r}")
    if manifest.get("format_version") != FORMAT_VERSION:
        raise PortabilityError(f"unsupported format_version {manifest.get('format_version')!r}")

    # Verify everything first — import is all-or-nothing.
    verified: list[tuple[dict[str, Any], list[dict[str, Any]]]] = []
    for entry in manifest["shards"]:
        rows = _verify_entry(bundle, entry)
        verified.append((entry, rows))

    store = Hearth(root_dir, ledger, ontology)
    if store._shards or store.links._shards:
        raise PortabilityError(f"import target {root_dir!r} is not an empty Hearth root")
    for entry, rows in verified:
        if entry["kind"] == "values":
            layer = Layer(entry["layer"])
            shard = store.shard(layer, entry["class_uri"])
            shard.cells = [row_to_cell(r) for r in rows]
            shard.rebuild_indexes()
            # persist the canonical state in the new root (same writer/schema)
            write_parquet_atomic(shard.to_table(), shard.path)
        else:
            lshard = store.links.shard(entry["class_uri"], entry["predicate"])
            lshard.cells = [row_to_link(r) for r in rows]
            lshard.rebuild_indexes()
            write_parquet_atomic(lshard.to_table(), lshard.path)
    store.rebuild_indexes()
    # clock floor: nothing already committed may be superseded "in the past"
    for shard in store.value_shard_items():
        for c in shard.cells:
            store._clock = max(store._clock, c.system.start, 0 if c.system.open else c.system.end)
    for lshard in store.links.link_shard_items():
        for c in lshard.cells:
            store._clock = max(store._clock, c.system.start, 0 if c.system.open else c.system.end)
    # round-trip sanity: re-derived content hashes must equal the manifest's
    for entry in manifest["shards"]:
        if entry["kind"] == "values":
            shard = store._shards[(Layer(entry["layer"]), entry["class_uri"])]
            rows_now = [cell_to_row(seq, c) for seq, c in enumerate(shard.cells)]
        else:
            lshard = store.links._shards[(entry["class_uri"], entry["predicate"])]
            rows_now = [link_to_row(seq, c) for seq, c in enumerate(lshard.cells)]
        if _content_hash(rows_now) != entry["content_hash"]:
            raise PortabilityError(
                f"import round-trip hash mismatch for {entry['path']} — decode/encode not bit-stable"
            )
    return store


def canonical_state(store: "Hearth") -> dict[str, list[str]]:
    """The full canonical cell state as sorted canonical-JSON row encodings,
    keyed per shard — the cell-set equality oracle used by the §4.5 tests."""
    out: dict[str, list[str]] = {}
    for shard in store.value_shard_items():
        key = f"values/{shard.layer.value}/{shard.class_uri}"
        out[key] = [
            json.dumps(cell_to_row(seq, c), sort_keys=True, separators=(",", ":"))
            for seq, c in enumerate(shard.cells)
        ]
    for lshard in store.links.link_shard_items():
        key = f"links/{lshard.class_uri}/{lshard.predicate}"
        out[key] = [
            json.dumps(link_to_row(seq, c), sort_keys=True, separators=(",", ":"))
            for seq, c in enumerate(lshard.cells)
        ]
    return out


__all__ = [
    "MANIFEST_NAME",
    "FORMAT_VERSION",
    "export_canonical",
    "import_canonical",
    "canonical_state",
    "CELL_SCHEMA",
    "LINK_SCHEMA",
]
