"""M6 — HEARTH store core: provenance-anchored bi-temporal entity shards
(whitepaper §4 entire section; §11.2 M6; AMD-0001).

Layout (§4.2, AMD-0001)
-----------------------
The CANONICAL layer is plain Parquet — one dataset per (layer, class URI) value
shard and one per (class URI, predicate) link shard — queried through DuckDB
views. All serving structures (the current-value dict, the per-entity cell map,
the link adjacency) are DERIVED, in-memory, disposable, and rebuilt from Parquet
on open (§4.2(b)); they are maintained incrementally on commit and are excluded
from the portability bundle.

Shard record model (§4.2)
-------------------------
Each row is one ``contracts.ValueCell``: value (canonical JSON string + typed
mirror columns for pushdown), world-time interval [valid_from, valid_to),
system-time interval [created_at, expired_at), interned provenance reference,
calibrated confidence, and survivorship rank. A store-side ``seq`` column
preserves total write order (the final survivorship tiebreak) across reloads.

Commit semantics (§4.3)
-----------------------
* Constraint H: every committed cell must carry a prov_ref that resolves in the
  ledger to a derivable (non-ZERO) term. Empty/unknown/ZERO refs are rejected.
* System time is store-stamped and append-monotone: a superseded cell's system
  interval is CLOSED (expired_at = now), never deleted; losing writes are
  recorded dead-on-arrival (system interval closed instantly) so the audit
  trail keeps them without ever making them current.
* Survivorship (§4.3.2): lower src_rank wins; ties broken by higher confidence,
  then newer created_at, then later seq. Rank 0 is reserved for human Actions —
  the public ``commit`` refuses it.
* Open-cell invariant: per (entity, prop), all system-open cells have pairwise
  DISJOINT valid intervals; hence at most one cell is current (both intervals
  open). World-time corrections split the displaced interval into residual
  segments that preserve the old value outside the corrected window.

Atomicity: a commit validates every cell first, applies in memory, then
rewrites the shard Parquet via temp-file + ``os.replace`` (POSIX-atomic).
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Any, Iterator, Mapping, Optional, Sequence

import pyarrow as pa
import pyarrow.parquet as pq
import xxhash

from ontoforge.contracts import (
    CURRENT,
    Instant,
    Interval,
    Layer,
    LinkCell,
    Ledger,
    Ontology,
    Stance,
    ValueCell,
    now_instant,
)

from .errors import CommitRejected

# --------------------------------------------------------------------------
# Value encoding: canonical JSON string + typed mirrors (§4.2 record model)
# --------------------------------------------------------------------------


def encode_value(value: Any) -> str:
    """Canonical JSON encoding of a cell value (sorted keys, compact separators).

    The JSON string is the CANONICAL representation: it round-trips bools,
    ints, floats, strings, None, lists and dicts bit-exactly, which is what
    the export/import equality gate (§7 precursor) compares.
    """
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise CommitRejected(f"cell value is not canonically JSON-encodable: {value!r}") from exc


def decode_value(value_json: str) -> Any:
    return json.loads(value_json)


def _typed_mirrors(value: Any) -> tuple[Optional[str], Optional[float]]:
    """Typed pushdown columns: (value_str, value_num). Lossy by design — the
    canonical column is value_json; these exist for DuckDB predicate pushdown."""
    v_str = value if isinstance(value, str) else None
    v_num = float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None
    return v_str, v_num


CELL_SCHEMA = pa.schema(
    [
        pa.field("seq", pa.int64(), nullable=False),
        pa.field("entity_uri", pa.string(), nullable=False),
        pa.field("prop", pa.string(), nullable=False),
        pa.field("value_json", pa.string(), nullable=False),
        pa.field("value_str", pa.string()),
        pa.field("value_num", pa.float64()),
        pa.field("valid_from", pa.int64(), nullable=False),
        pa.field("valid_to", pa.int64(), nullable=False),
        pa.field("created_at", pa.int64(), nullable=False),
        pa.field("expired_at", pa.int64(), nullable=False),
        pa.field("prov_ref", pa.string(), nullable=False),
        pa.field("confidence", pa.float64(), nullable=False),
        pa.field("src_rank", pa.int32(), nullable=False),
    ]
)

LINK_SCHEMA = pa.schema(
    [
        pa.field("seq", pa.int64(), nullable=False),
        pa.field("subject_uri", pa.string(), nullable=False),
        pa.field("predicate", pa.string(), nullable=False),
        pa.field("object_uri", pa.string(), nullable=False),
        pa.field("valid_from", pa.int64(), nullable=False),
        pa.field("valid_to", pa.int64(), nullable=False),
        pa.field("created_at", pa.int64(), nullable=False),
        pa.field("expired_at", pa.int64(), nullable=False),
        pa.field("prov_ref", pa.string(), nullable=False),
        pa.field("confidence", pa.float64(), nullable=False),
        pa.field("props_json", pa.string(), nullable=False),
    ]
)


def cell_to_row(seq: int, c: ValueCell) -> dict[str, Any]:
    v_str, v_num = _typed_mirrors(c.value)
    return {
        "seq": seq,
        "entity_uri": c.entity_uri,
        "prop": c.prop,
        "value_json": encode_value(c.value),
        "value_str": v_str,
        "value_num": v_num,
        "valid_from": c.valid.start,
        "valid_to": c.valid.end,
        "created_at": c.system.start,
        "expired_at": c.system.end,
        "prov_ref": c.prov_ref,
        "confidence": c.confidence,
        "src_rank": c.src_rank,
    }


def row_to_cell(row: Mapping[str, Any]) -> ValueCell:
    return ValueCell(
        entity_uri=row["entity_uri"],
        prop=row["prop"],
        value=decode_value(row["value_json"]),
        valid=Interval(row["valid_from"], row["valid_to"]),
        system=Interval(row["created_at"], row["expired_at"]),
        prov_ref=row["prov_ref"],
        confidence=row["confidence"],
        src_rank=row["src_rank"],
    )


def link_to_row(seq: int, link: LinkCell) -> dict[str, Any]:
    return {
        "seq": seq,
        "subject_uri": link.subject_uri,
        "predicate": link.predicate,
        "object_uri": link.object_uri,
        "valid_from": link.valid.start,
        "valid_to": link.valid.end,
        "created_at": link.system.start,
        "expired_at": link.system.end,
        "prov_ref": link.prov_ref,
        "confidence": link.confidence,
        "props_json": json.dumps(list(link.props), sort_keys=True, separators=(",", ":")),
    }


def row_to_link(row: Mapping[str, Any]) -> LinkCell:
    props = tuple((k, v) for k, v in json.loads(row["props_json"]))
    return LinkCell(
        subject_uri=row["subject_uri"],
        predicate=row["predicate"],
        object_uri=row["object_uri"],
        valid=Interval(row["valid_from"], row["valid_to"]),
        system=Interval(row["created_at"], row["expired_at"]),
        prov_ref=row["prov_ref"],
        confidence=row["confidence"],
        props=props,
    )


# --------------------------------------------------------------------------
# Survivorship ordering (§4.3.2)
# --------------------------------------------------------------------------


def survivorship_key(seq: int, c: ValueCell) -> tuple[int, float, int, int]:
    """Sort ASCENDING; the FIRST cell wins: lower src_rank, then higher
    confidence, then newer created_at, then later seq."""
    return (c.src_rank, -c.confidence, -c.system.start, -seq)


def supersedes(new_rank: int, new_conf: float, now: Instant, old: ValueCell) -> bool:
    """Does an incoming write (stamped created_at=now) close `old`'s system
    interval? Mirrors `survivorship_key` exactly: rank, then confidence, then
    recency — with the incoming cell the newer of the two at equal rank+conf."""
    if new_rank != old.src_rank:
        return new_rank < old.src_rank
    if new_conf != old.confidence:
        return new_conf > old.confidence
    return now >= old.system.start


# --------------------------------------------------------------------------
# Atomic Parquet write
# --------------------------------------------------------------------------


def write_parquet_atomic(table: pa.Table, dest: Path) -> None:
    """temp file in the destination directory + os.replace = atomic on POSIX (§4.3)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=dest.name + ".tmp-", dir=dest.parent)
    os.close(fd)
    try:
        pq.write_table(table, tmp, compression="zstd")
        os.replace(tmp, dest)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def shard_key(uri: str) -> str:
    """Filesystem-safe shard directory name: slug of the URI tail + 8-hex
    content hash of the full URI (collision-proof, reversible via _meta.json)."""
    tail = uri.rstrip("/").rsplit("/", 1)[-1] or "root"
    slug = re.sub(r"[^A-Za-z0-9_]+", "_", tail).strip("_")[:48] or "shard"
    return f"{slug}-{xxhash.xxh3_64(uri.encode()).intdigest():016x}"[:64]


# --------------------------------------------------------------------------
# Value shard
# --------------------------------------------------------------------------


class ValueShard:
    """One (layer, class URI) Parquet dataset + its derived in-memory indexes.

    Canonical state: ``cells`` in seq order (the Parquet rows). Derived state
    (§4.2(b), disposable): ``open_by_key`` (system-open cells per (entity,prop)),
    ``current`` (the unique both-open cell per key — the point-read fast path),
    ``by_entity`` (all seqs per entity, for stanced reads/history).
    """

    __slots__ = ("layer", "class_uri", "path", "cells", "open_by_key", "current", "by_entity")

    def __init__(self, layer: Layer, class_uri: str, path: Path) -> None:
        self.layer = layer
        self.class_uri = class_uri
        self.path = path  # .../cells.parquet
        self.cells: list[ValueCell] = []
        self.open_by_key: dict[tuple[str, str], list[int]] = {}
        self.current: dict[tuple[str, str], int] = {}
        self.by_entity: dict[str, list[int]] = {}

    # -- derived-index maintenance ------------------------------------------

    def rebuild_indexes(self) -> None:
        """Full rebuild from canonical cells (used on load/import; §4.2(b))."""
        self.open_by_key.clear()
        self.current.clear()
        self.by_entity.clear()
        for seq, c in enumerate(self.cells):
            self.by_entity.setdefault(c.entity_uri, []).append(seq)
            if c.system.open:
                self.open_by_key.setdefault((c.entity_uri, c.prop), []).append(seq)
        for key, seqs in self.open_by_key.items():
            for seq in seqs:
                if self.cells[seq].valid.open:
                    self.current[key] = seq

    def _refresh_current(self, key: tuple[str, str]) -> None:
        self.current.pop(key, None)
        for seq in self.open_by_key.get(key, ()):
            if self.cells[seq].valid.open:
                self.current[key] = seq

    def _append(self, c: ValueCell, *, open_: bool) -> int:
        seq = len(self.cells)
        self.cells.append(c)
        self.by_entity.setdefault(c.entity_uri, []).append(seq)
        if open_:
            self.open_by_key.setdefault((c.entity_uri, c.prop), []).append(seq)
        return seq

    # -- the per-cell commit step (§4.3) --------------------------------------

    def apply(self, c: ValueCell, now: Instant) -> None:
        """Apply one validated incoming cell with system time stamped at `now`.

        Incremental index maintenance: only the touched (entity, prop) key is
        recomputed — never the whole shard.
        """
        key = (c.entity_uri, c.prop)
        overlapping = [
            seq for seq in self.open_by_key.get(key, ()) if self.cells[seq].valid.overlaps(c.valid)
        ]
        losers = [
            seq for seq in overlapping if not supersedes(c.src_rank, c.confidence, now, self.cells[seq])
        ]
        if losers:
            # Dead on arrival: a higher-precedence cell already covers (part of)
            # this valid window. Record the write append-only with an instantly
            # closed system interval — auditable, never current, never clobbers
            # the winner (§4.3.2: pipeline writes do not clobber Actions).
            doa = replace(c, system=Interval(now, now + 1))
            self._append(doa, open_=False)
            return
        residuals: list[ValueCell] = []
        for seq in overlapping:
            old = self.cells[seq]
            # Close the superseded cell's system interval — append-only system
            # time: the row stays, only its expired_at closes (§4.3).
            expire_at = max(now, old.system.start + 1)
            self.cells[seq] = replace(old, system=Interval(old.system.start, expire_at))
            self.open_by_key[key].remove(seq)
            # World-time residuals: the old value still held outside the
            # displaced window; re-assert it under the new system epoch.
            if old.valid.start < c.valid.start:
                residuals.append(
                    replace(old, valid=Interval(old.valid.start, c.valid.start), system=Interval(now))
                )
            if c.valid.end < old.valid.end:
                residuals.append(
                    replace(old, valid=Interval(c.valid.end, old.valid.end), system=Interval(now))
                )
        self._append(replace(c, system=Interval(now)), open_=True)
        for r in residuals:
            self._append(r, open_=True)
        self._refresh_current(key)

    # -- persistence ----------------------------------------------------------

    def to_table(self) -> pa.Table:
        rows = [cell_to_row(seq, c) for seq, c in enumerate(self.cells)]
        return pa.Table.from_pylist(rows, schema=CELL_SCHEMA)

    def save(self) -> None:
        write_parquet_atomic(self.to_table(), self.path)

    def load(self) -> None:
        table = pq.read_table(self.path)
        rows = sorted(table.to_pylist(), key=lambda r: r["seq"])
        if [r["seq"] for r in rows] != list(range(len(rows))):
            raise CommitRejected(f"corrupt shard {self.path}: seq column is not a dense 0..n-1 range")
        self.cells = [row_to_cell(r) for r in rows]
        self.rebuild_indexes()


# --------------------------------------------------------------------------
# The store
# --------------------------------------------------------------------------

_META_NAME = "_meta.json"


class Hearth:
    """The provenance-anchored bi-temporal entity store (whitepaper §4).

    Parameters
    ----------
    root_dir : where canonical Parquet lives (created if missing).
    ledger   : the M0 Ledger — constraint-H gatekeeper (prov_ref resolution)
               and registry for human-edit atoms/artifacts.
    ontology : optional contracts.Ontology used to SHACL-validate Actions
               (§4.3.2). Without one, Actions skip shape validation.
    """

    def __init__(self, root_dir: str | Path, ledger: Ledger, ontology: Optional[Ontology] = None) -> None:
        self.root = Path(root_dir)
        self.ledger = ledger
        self.ontology = ontology
        self.root.mkdir(parents=True, exist_ok=True)
        self._shards: dict[tuple[Layer, str], ValueShard] = {}
        self._entity_classes: dict[str, set[str]] = {}  # ENTITY layer: entity -> class URIs
        self._clock: Instant = 0  # store-wide system-time monotonicity floor
        self._duck = None  # lazy DuckDB connection
        self._duck_views: dict[tuple[Layer, str], str] = {}
        from .links import LinkStore  # local import: links.py imports store helpers

        self.links = LinkStore(self)
        self._discover()

    # ---------------------------------------------------------------- layout

    def _values_dir(self, layer: Layer) -> Path:
        return self.root / "values" / layer.value

    def _shard_dir(self, layer: Layer, class_uri: str) -> Path:
        return self._values_dir(layer) / shard_key(class_uri)

    def _discover(self) -> None:
        """Open existing shards from disk; rebuild all derived indexes (§4.2(b))."""
        for layer in Layer:
            base = self._values_dir(layer)
            if not base.is_dir():
                continue
            for d in sorted(base.iterdir()):
                meta_path = d / _META_NAME
                if not meta_path.is_file():
                    continue
                meta = json.loads(meta_path.read_text())
                shard = ValueShard(layer, meta["class_uri"], d / "cells.parquet")
                if shard.path.is_file():
                    shard.load()
                self._register_shard(shard)
        self.links.discover()

    def _register_shard(self, shard: ValueShard) -> None:
        self._shards[(shard.layer, shard.class_uri)] = shard
        if shard.layer is Layer.ENTITY:
            for entity_uri in shard.by_entity:
                self._entity_classes.setdefault(entity_uri, set()).add(shard.class_uri)
        self._clock = max(
            self._clock,
            max((c.system.start for c in shard.cells), default=0),
            max((c.system.end for c in shard.cells if not c.system.open), default=0),
        )

    def shard(self, layer: Layer, class_uri: str) -> ValueShard:
        key = (layer, class_uri)
        if key not in self._shards:
            d = self._shard_dir(layer, class_uri)
            d.mkdir(parents=True, exist_ok=True)
            (d / _META_NAME).write_text(
                json.dumps({"layer": layer.value, "class_uri": class_uri}, indent=1)
            )
            self._shards[key] = ValueShard(layer, class_uri, d / "cells.parquet")
        return self._shards[key]

    def value_shard_items(self) -> Iterator[ValueShard]:
        for key in sorted(self._shards, key=lambda k: (k[0].value, k[1])):
            yield self._shards[key]

    def classes(self, layer: Layer = Layer.ENTITY) -> list[str]:
        return sorted(uri for (ly, uri) in self._shards if ly is layer)

    # ---------------------------------------------------------------- commit

    def _stamp_now(self, now: Optional[Instant]) -> Instant:
        t = now_instant() if now is None else now
        if t < self._clock:
            raise CommitRejected(
                f"system time must be append-monotone: commit at {t} < store clock {self._clock}"
            )
        self._clock = t
        return t

    def _validate_cell(self, c: ValueCell, *, allow_rank0: bool, prov_cache: dict[str, bool]) -> None:
        if not isinstance(c, ValueCell):
            raise CommitRejected(f"commit expects ValueCell, got {type(c).__name__}")
        if not c.prov_ref:
            raise CommitRejected(
                f"constraint H violated: empty prov_ref on ({c.entity_uri!r}, {c.prop!r})"
            )
        if c.prov_ref not in prov_cache:
            try:
                prov_cache[c.prov_ref] = bool(self.ledger.valuate_ref(c.prov_ref, "derivable"))
            except KeyError:
                prov_cache[c.prov_ref] = False
        if not prov_cache[c.prov_ref]:
            raise CommitRejected(
                f"constraint H violated: prov_ref {c.prov_ref!r} is unknown to the ledger "
                f"or resolves to ZERO (no derivation)"
            )
        if not c.system.open:
            raise CommitRejected(
                "invalid interval: incoming cells must have an OPEN system interval "
                "(system time is store-stamped on commit)"
            )
        if not (0.0 <= c.confidence <= 1.0) or c.confidence != c.confidence:
            raise CommitRejected(f"confidence must be in [0,1], got {c.confidence!r}")
        if c.src_rank < 0:
            raise CommitRejected(f"src_rank must be >= 0, got {c.src_rank}")
        if c.src_rank == 0 and not allow_rank0:
            raise CommitRejected(
                "src_rank 0 is reserved for human Actions (§4.3.2); pipeline commits use rank >= 1"
            )
        encode_value(c.value)  # raises CommitRejected if not canonically encodable

    def commit(
        self,
        layer: Layer,
        class_uri: str,
        cells: Sequence[ValueCell],
        *,
        now: Optional[Instant] = None,
    ) -> int:
        """Pipeline write path (§4.3.1). Validates ALL cells, then applies and
        atomically rewrites the shard. Returns the number of cells applied."""
        return self._commit_cells(layer, class_uri, cells, now=now, allow_rank0=False)

    def _commit_cells(
        self,
        layer: Layer,
        class_uri: str,
        cells: Sequence[ValueCell],
        *,
        now: Optional[Instant],
        allow_rank0: bool,
    ) -> int:
        prov_cache: dict[str, bool] = {}
        for c in cells:
            self._validate_cell(c, allow_rank0=allow_rank0, prov_cache=prov_cache)
        if not cells:
            return 0
        t = self._stamp_now(now)
        shard = self.shard(layer, class_uri)
        for c in cells:
            shard.apply(c, t)
            if layer is Layer.ENTITY:
                self._entity_classes.setdefault(c.entity_uri, set()).add(class_uri)
        shard.save()
        return len(cells)

    def rebuild_indexes(self) -> None:
        """Drop and rebuild every derived structure from canonical Parquet state
        (§4.2(b): serving indexes are disposable)."""
        for shard in self._shards.values():
            shard.rebuild_indexes()
        self._entity_classes.clear()
        for shard in self._shards.values():
            if shard.layer is Layer.ENTITY:
                for entity_uri in shard.by_entity:
                    self._entity_classes.setdefault(entity_uri, set()).add(shard.class_uri)
        self.links.rebuild_adjacency()

    # ----------------------------------------------------------------- reads
    # implementations live in read.py; thin delegation keeps one public object

    def read(
        self,
        entity_uri: str,
        stance: Stance = CURRENT,
        *,
        class_uri: Optional[str] = None,
        layer: Layer = Layer.ENTITY,
    ) -> dict[str, Any]:
        from .read import read as _impl

        return _impl(self, entity_uri, stance, class_uri=class_uri, layer=layer)

    def current_value(
        self, class_uri: str, entity_uri: str, prop: str, *, layer: Layer = Layer.ENTITY
    ) -> Any:
        """O(1) current-value point read: the §4.2 fast-path dict. Raises
        KeyError when (entity, prop) has no current cell in the shard."""
        shard = self._shards[(layer, class_uri)]
        return shard.cells[shard.current[(entity_uri, prop)]].value

    def history(
        self,
        entity_uri: str,
        prop: str,
        *,
        class_uri: Optional[str] = None,
        layer: Layer = Layer.ENTITY,
    ) -> list[ValueCell]:
        from .read import history as _impl

        return _impl(self, entity_uri, prop, class_uri=class_uri, layer=layer)

    def scan(
        self,
        class_uri: str,
        stance: Stance = CURRENT,
        filters: Optional[Mapping[str, Any]] = None,
        *,
        layer: Layer = Layer.ENTITY,
    ) -> pa.Table:
        from .read import scan as _impl

        return _impl(self, class_uri, stance, filters, layer=layer)

    def scan_duckdb(
        self,
        class_uri: str,
        stance: Stance = CURRENT,
        filters: Optional[Mapping[str, Any]] = None,
        *,
        layer: Layer = Layer.ENTITY,
    ) -> pa.Table:
        from .read import scan_duckdb as _impl

        return _impl(self, class_uri, stance, filters, layer=layer)

    # ----------------------------------------------------------------- links

    def commit_links(
        self,
        class_uri: str,
        predicate: str,
        links: Sequence[LinkCell],
        *,
        now: Optional[Instant] = None,
    ) -> int:
        return self.links.commit(class_uri, predicate, links, now=now)

    def traverse(
        self,
        uri: str,
        predicate: str,
        stance: Stance = CURRENT,
        depth: int = 1,
        reverse: bool = False,
    ) -> list[str]:
        return self.links.traverse(uri, predicate, stance, depth=depth, reverse=reverse)

    # --------------------------------------------------------------- actions

    def action(self, actor: str, op: Any, *, now: Optional[Instant] = None):
        from .actions import perform as _impl

        return _impl(self, actor, op, now=now)

    # ----------------------------------------------------------- portability

    def export_canonical(self, out_dir: str | Path) -> Path:
        from .portability import export_canonical as _impl

        return _impl(self, out_dir)

    # ------------------------------------------------------------------ duck

    @property
    def duck(self):
        """The store's DuckDB connection (lazy). Views over shard Parquet are
        registered via :meth:`duckdb_view`."""
        if self._duck is None:
            import duckdb

            self._duck = duckdb.connect()
        return self._duck

    def duckdb_view(self, layer: Layer, class_uri: str) -> str:
        """CREATE OR REPLACE a DuckDB view over the shard's Parquet file and
        return its name. The view reads the file at query time, so it stays
        fresh across commits (the shard path is stable)."""
        key = (layer, class_uri)
        shard = self._shards.get(key)
        if shard is None or not shard.path.is_file():
            raise KeyError(f"no committed shard for ({layer.value}, {class_uri!r})")
        name = f"v_{layer.value}_{shard_key(class_uri)}".replace("-", "_")
        path_sql = str(shard.path).replace("'", "''")
        self.duck.execute(f"CREATE OR REPLACE VIEW {name} AS SELECT * FROM read_parquet('{path_sql}')")
        self._duck_views[key] = name
        return name
