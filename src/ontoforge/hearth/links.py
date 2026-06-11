"""M6 — HEARTH link store (whitepaper §4.2 "Link store").

Links are first-class edges with the same versioned-cell bi-temporal model as
values. Storage is dual, per §4.2:

(a) CANONICAL: one Parquet dataset per (class URI, predicate) — open,
    scannable, exportable; the only thing AMBER carries.
(b) DERIVED:   an in-memory adjacency index {subject -> [(predicate, object)]}
    plus its reverse, over CURRENT links only. It is rebuilt incrementally
    from committed link deltas and is DISPOSABLE — dropping it and calling
    ``rebuild_adjacency()`` reconstructs it exactly from (a). It is excluded
    from the export bundle (a documented, capability-neutral loss).

Link supersession: there is no src_rank on edges — for the same
(subject, object) triple in a shard, the NEWER commit closes the older cell's
system interval (latest-wins), with world-time residual splitting identical to
the value path. ``unlink`` is a belief change too: it expires the current cell
and re-asserts the link with a CLOSED valid interval (the link held until now),
so ``as_of`` reads in the past still see it.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Optional, Sequence

import pyarrow as pa
import pyarrow.parquet as pq

from ontoforge.contracts import Instant, Interval, LinkCell, Stance

from .errors import CommitRejected
from .store import LINK_SCHEMA, link_to_row, row_to_link, shard_key, write_parquet_atomic

if TYPE_CHECKING:  # pragma: no cover
    from .store import Hearth

_META_NAME = "_meta.json"


def link_visible(link: LinkCell, stance: Stance) -> bool:
    """Same visibility semantics as ValueCell.visible_under (contracts.cells)."""
    if stance.kind == "current":
        return link.valid.open and link.system.open
    if stance.kind == "as_of":
        return link.system.open and link.valid.contains(stance.valid_at)  # type: ignore[arg-type]
    if stance.kind == "as_known_at":
        return link.system.contains(stance.known_at) and link.valid.contains(  # type: ignore[arg-type]
            stance.known_at  # type: ignore[arg-type]
        )
    return link.system.contains(stance.known_at) and link.valid.contains(stance.valid_at)  # type: ignore[arg-type]


class LinkShard:
    """One (class URI, predicate) Parquet dataset + derived per-triple index."""

    __slots__ = ("class_uri", "predicate", "path", "cells", "open_by_pair")

    def __init__(self, class_uri: str, predicate: str, path: Path) -> None:
        self.class_uri = class_uri
        self.predicate = predicate
        self.path = path
        self.cells: list[LinkCell] = []
        self.open_by_pair: dict[tuple[str, str], list[int]] = {}

    def rebuild_indexes(self) -> None:
        self.open_by_pair.clear()
        for seq, link in enumerate(self.cells):
            if link.system.open:
                self.open_by_pair.setdefault((link.subject_uri, link.object_uri), []).append(seq)

    def apply(self, link: LinkCell, now: Instant) -> tuple[list[LinkCell], list[LinkCell]]:
        """Apply one validated link write. Returns the adjacency delta:
        (cells that STOPPED being current, cells that BECAME current)."""
        pair = (link.subject_uri, link.object_uri)
        removed: list[LinkCell] = []
        added: list[LinkCell] = []
        residuals: list[LinkCell] = []
        for seq in [s for s in self.open_by_pair.get(pair, ()) if self.cells[s].valid.overlaps(link.valid)]:
            old = self.cells[seq]
            expire_at = max(now, old.system.start + 1)
            self.cells[seq] = replace(old, system=Interval(old.system.start, expire_at))
            self.open_by_pair[pair].remove(seq)
            if old.valid.open:
                removed.append(old)
            if old.valid.start < link.valid.start:
                residuals.append(
                    replace(old, valid=Interval(old.valid.start, link.valid.start), system=Interval(now))
                )
            if link.valid.end < old.valid.end:
                residuals.append(
                    replace(old, valid=Interval(link.valid.end, old.valid.end), system=Interval(now))
                )
        stamped = replace(link, system=Interval(now))
        for cell in (stamped, *residuals):
            self.open_by_pair.setdefault(pair, []).append(len(self.cells))
            self.cells.append(cell)
            if cell.valid.open:
                added.append(cell)
        return removed, added

    def to_table(self) -> pa.Table:
        rows = [link_to_row(seq, c) for seq, c in enumerate(self.cells)]
        return pa.Table.from_pylist(rows, schema=LINK_SCHEMA)

    def save(self) -> None:
        write_parquet_atomic(self.to_table(), self.path)

    def load(self) -> None:
        rows = sorted(pq.read_table(self.path).to_pylist(), key=lambda r: r["seq"])
        if [r["seq"] for r in rows] != list(range(len(rows))):
            raise CommitRejected(f"corrupt link shard {self.path}: seq column not dense")
        self.cells = [row_to_link(r) for r in rows]
        self.rebuild_indexes()


class LinkStore:
    """All link shards + the derived adjacency index (§4.2(b))."""

    def __init__(self, store: "Hearth") -> None:
        self._store = store
        self._shards: dict[tuple[str, str], LinkShard] = {}
        # Derived, disposable. Counted (not just set-membership) so the same
        # triple current in two class shards survives one shard's retraction.
        self._fwd: dict[str, dict[str, dict[str, int]]] = {}  # subj -> pred -> obj -> count
        self._rev: dict[str, dict[str, dict[str, int]]] = {}  # obj  -> pred -> subj -> count

    # ------------------------------------------------------------ layout

    def _links_dir(self) -> Path:
        return self._store.root / "links"

    def _shard_dir(self, class_uri: str, predicate: str) -> Path:
        return self._links_dir() / shard_key(class_uri) / shard_key(predicate)

    def discover(self) -> None:
        base = self._links_dir()
        if not base.is_dir():
            return
        for class_dir in sorted(base.iterdir()):
            if not class_dir.is_dir():
                continue
            for pred_dir in sorted(class_dir.iterdir()):
                meta_path = pred_dir / _META_NAME
                if not meta_path.is_file():
                    continue
                meta = json.loads(meta_path.read_text())
                shard = LinkShard(meta["class_uri"], meta["predicate"], pred_dir / "links.parquet")
                if shard.path.is_file():
                    shard.load()
                self._shards[(shard.class_uri, shard.predicate)] = shard
        self.rebuild_adjacency()

    def shard(self, class_uri: str, predicate: str) -> LinkShard:
        key = (class_uri, predicate)
        if key not in self._shards:
            d = self._shard_dir(class_uri, predicate)
            d.mkdir(parents=True, exist_ok=True)
            (d / _META_NAME).write_text(
                json.dumps({"class_uri": class_uri, "predicate": predicate}, indent=1)
            )
            self._shards[key] = LinkShard(class_uri, predicate, d / "links.parquet")
        return self._shards[key]

    def link_shard_items(self):
        for key in sorted(self._shards):
            yield self._shards[key]

    # --------------------------------------------------------- adjacency

    @staticmethod
    def _bump(index: dict, a: str, pred: str, b: str, delta: int) -> None:
        preds = index.setdefault(a, {})
        objs = preds.setdefault(pred, {})
        n = objs.get(b, 0) + delta
        if n > 0:
            objs[b] = n
        else:
            objs.pop(b, None)
            if not objs:
                preds.pop(pred, None)
                if not preds:
                    index.pop(a, None)

    def _adj_delta(self, removed: Sequence[LinkCell], added: Sequence[LinkCell]) -> None:
        for link in removed:
            self._bump(self._fwd, link.subject_uri, link.predicate, link.object_uri, -1)
            self._bump(self._rev, link.object_uri, link.predicate, link.subject_uri, -1)
        for link in added:
            self._bump(self._fwd, link.subject_uri, link.predicate, link.object_uri, +1)
            self._bump(self._rev, link.object_uri, link.predicate, link.subject_uri, +1)

    def rebuild_adjacency(self) -> None:
        """Full rebuild from canonical cells — the proof the index is derived
        and disposable (§4.2(b))."""
        self._fwd.clear()
        self._rev.clear()
        for shard in self._shards.values():
            for link in shard.cells:
                if link.valid.open and link.system.open:
                    self._adj_delta((), (link,))

    def neighbors(self, uri: str, predicate: Optional[str] = None, reverse: bool = False) -> list[tuple[str, str]]:
        """Current-stance fast path: [(predicate, neighbor_uri)] from the index."""
        index = self._rev if reverse else self._fwd
        preds = index.get(uri, {})
        out: list[tuple[str, str]] = []
        for pred, objs in preds.items():
            if predicate is not None and pred != predicate:
                continue
            out.extend((pred, o) for o in objs)
        out.sort()
        return out

    # -------------------------------------------------------------- writes

    def _validate(self, link: LinkCell, prov_cache: dict[str, bool]) -> None:
        if not isinstance(link, LinkCell):
            raise CommitRejected(f"commit_links expects LinkCell, got {type(link).__name__}")
        if not link.prov_ref:
            raise CommitRejected(
                f"constraint H violated: empty prov_ref on link "
                f"({link.subject_uri!r} -{link.predicate}-> {link.object_uri!r})"
            )
        if link.prov_ref not in prov_cache:
            try:
                prov_cache[link.prov_ref] = bool(
                    self._store.ledger.valuate_ref(link.prov_ref, "derivable")
                )
            except KeyError:
                prov_cache[link.prov_ref] = False
        if not prov_cache[link.prov_ref]:
            raise CommitRejected(
                f"constraint H violated: link prov_ref {link.prov_ref!r} unknown or ZERO"
            )
        if not link.system.open:
            raise CommitRejected("invalid interval: incoming links must have an OPEN system interval")
        if not (0.0 <= link.confidence <= 1.0):
            raise CommitRejected(f"link confidence must be in [0,1], got {link.confidence!r}")

    def commit(
        self,
        class_uri: str,
        predicate: str,
        links: Sequence[LinkCell],
        *,
        now: Optional[Instant] = None,
    ) -> int:
        prov_cache: dict[str, bool] = {}
        for link in links:
            self._validate(link, prov_cache)
            if link.predicate != predicate:
                raise CommitRejected(
                    f"link predicate {link.predicate!r} does not match shard predicate {predicate!r}"
                )
        if not links:
            return 0
        t = self._store._stamp_now(now)
        shard = self.shard(class_uri, predicate)
        for link in links:
            removed, added = shard.apply(link, t)
            self._adj_delta(removed, added)
        shard.save()
        return len(links)

    def unlink(
        self,
        class_uri: str,
        predicate: str,
        subject_uri: str,
        object_uri: str,
        prov_ref: str,
        *,
        now: Optional[Instant] = None,
    ) -> bool:
        """Close the CURRENT link for the triple: expire its system interval and
        re-assert it with valid time ending now. Append-only; as_of(past) still
        sees the link. Returns False when no current link exists."""
        shard = self._shards.get((class_uri, predicate))
        if shard is None:
            return False
        pair = (subject_uri, object_uri)
        target = None
        for seq in shard.open_by_pair.get(pair, ()):
            if shard.cells[seq].valid.open:
                target = seq
        if target is None:
            return False
        t = self._store._stamp_now(now)
        old = shard.cells[target]
        expire_at = max(t, old.system.start + 1)
        shard.cells[target] = replace(old, system=Interval(old.system.start, expire_at))
        shard.open_by_pair[pair].remove(target)
        closed = replace(
            old,
            valid=Interval(old.valid.start, max(t, old.valid.start + 1)),
            system=Interval(t),
            prov_ref=prov_ref,
        )
        shard.open_by_pair.setdefault(pair, []).append(len(shard.cells))
        shard.cells.append(closed)
        self._adj_delta((old,), ())
        shard.save()
        return True

    # ------------------------------------------------------------ traverse

    def traverse(
        self,
        uri: str,
        predicate: str,
        stance: Stance,
        *,
        depth: int = 1,
        reverse: bool = False,
    ) -> list[str]:
        """BFS over `predicate` edges, up to `depth` hops, under the stance.
        Returns all reached URIs (the start node excluded), sorted.

        Current stance walks the in-memory adjacency (the §4.2(b) fast path);
        other stances fall back to filtering the canonical link cells — the
        documented read-path fallback of §4.5.
        """
        if depth < 1:
            return []
        if stance.kind == "current":
            step = lambda u: [o for _, o in self.neighbors(u, predicate, reverse=reverse)]  # noqa: E731
        else:
            adj: dict[str, set[str]] = {}
            for shard in self._shards.values():
                if shard.predicate != predicate:
                    continue
                for link in shard.cells:
                    if link_visible(link, stance):
                        a, b = (
                            (link.object_uri, link.subject_uri)
                            if reverse
                            else (link.subject_uri, link.object_uri)
                        )
                        adj.setdefault(a, set()).add(b)
            step = lambda u: sorted(adj.get(u, ()))  # noqa: E731
        seen: set[str] = {uri}
        frontier = [uri]
        reached: set[str] = set()
        for _ in range(depth):
            nxt: list[str] = []
            for node in frontier:
                for nb in step(node):
                    if nb not in seen:
                        seen.add(nb)
                        reached.add(nb)
                        nxt.append(nb)
            if not nxt:
                break
            frontier = nxt
        return sorted(reached)
