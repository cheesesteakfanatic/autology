"""M6 — HEARTH read paths (whitepaper §4.4).

Every read declares a temporal stance (current | as_of | as_known_at | audit).
Visibility is ``ValueCell.visible_under``; among visible cells for one
(entity, prop) the SURVIVORSHIP winner is returned: lower src_rank, then higher
confidence, then newer created_at, then later seq (identical to the commit-side
ordering in store.supersedes — one rule, two call sites).

Two scan implementations cross-check each other (a §4.5 acceptance test):
* ``scan``        — over the in-memory derived indexes (the serving fast path);
* ``scan_duckdb`` — pure SQL over the canonical Parquet through a DuckDB view
                    (stance predicate + ROW_NUMBER survivorship), proving the
                    fast path agrees with the open-format canonical layer.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Mapping, Optional

import pyarrow as pa

from ontoforge.contracts import FOREVER, Layer, Stance, ValueCell

from .store import decode_value, survivorship_key

if TYPE_CHECKING:  # pragma: no cover
    from .store import Hearth, ValueShard


# --------------------------------------------------------------------------
# point reads
# --------------------------------------------------------------------------


def _entity_shards(store: "Hearth", entity_uri: str, class_uri: Optional[str], layer: Layer):
    if class_uri is not None:
        shard = store._shards.get((layer, class_uri))
        return [shard] if shard is not None else []
    if layer is Layer.ENTITY:
        uris = store._entity_classes.get(entity_uri, ())
        return [store._shards[(layer, u)] for u in sorted(uris)]
    return [s for s in store._shards.values() if s.layer is layer and entity_uri in s.by_entity]


def _winner(visible: list[tuple[int, ValueCell]]) -> ValueCell:
    return min(visible, key=lambda sc: survivorship_key(sc[0], sc[1]))[1]


def read(
    store: "Hearth",
    entity_uri: str,
    stance: Stance,
    *,
    class_uri: Optional[str] = None,
    layer: Layer = Layer.ENTITY,
) -> dict[str, Any]:
    """prop -> value under the stance. Current stance takes the fast path:
    the per-shard (entity, prop) -> current-cell dict, no scan."""
    out: dict[str, Any] = {}
    for shard in _entity_shards(store, entity_uri, class_uri, layer):
        if stance.kind == "current":
            # Fast path: the open-cell invariant guarantees at most one
            # is_current cell per (entity, prop), so no survivorship pass.
            for seq in shard.by_entity.get(entity_uri, ()):
                c = shard.cells[seq]
                if c.is_current:
                    out[c.prop] = c.value
            continue
        by_prop: dict[str, list[tuple[int, ValueCell]]] = {}
        for seq in shard.by_entity.get(entity_uri, ()):
            c = shard.cells[seq]
            if c.visible_under(stance):
                by_prop.setdefault(c.prop, []).append((seq, c))
        for prop, visible in by_prop.items():
            out[prop] = _winner(visible).value
    return out


def history(
    store: "Hearth",
    entity_uri: str,
    prop: str,
    *,
    class_uri: Optional[str] = None,
    layer: Layer = Layer.ENTITY,
) -> list[ValueCell]:
    """ALL cells ever written for (entity, prop) — current, superseded, and
    dead-on-arrival — ordered by (created_at, seq). The audit trail."""
    found: list[tuple[int, int, ValueCell]] = []
    for shard in _entity_shards(store, entity_uri, class_uri, layer):
        for seq in shard.by_entity.get(entity_uri, ()):
            c = shard.cells[seq]
            if c.prop == prop:
                found.append((c.system.start, seq, c))
    found.sort(key=lambda t: (t[0], t[1]))
    return [c for _, _, c in found]


# --------------------------------------------------------------------------
# scans
# --------------------------------------------------------------------------


def _stanced_rows(shard: "ValueShard", stance: Stance) -> dict[str, dict[str, Any]]:
    """entity -> {prop: value} under the stance, survivorship applied."""
    per_key: dict[tuple[str, str], list[tuple[int, ValueCell]]] = {}
    if stance.kind == "current":
        for key, seq in shard.current.items():
            per_key[key] = [(seq, shard.cells[seq])]
    else:
        for seq, c in enumerate(shard.cells):
            if c.visible_under(stance):
                per_key.setdefault((c.entity_uri, c.prop), []).append((seq, c))
    rows: dict[str, dict[str, Any]] = {}
    for (entity_uri, prop), visible in per_key.items():
        rows.setdefault(entity_uri, {})[prop] = _winner(visible).value
    return rows


def _apply_filters(
    rows: dict[str, dict[str, Any]], filters: Optional[Mapping[str, Any]]
) -> dict[str, dict[str, Any]]:
    if not filters:
        return rows
    return {
        e: props
        for e, props in rows.items()
        if all(prop in props and props[prop] == want for prop, want in filters.items())
    }


def _pivot_table(rows: dict[str, dict[str, Any]]) -> pa.Table:
    """One row per entity, properties as columns; deterministic order (sorted
    entity URIs / sorted prop names). Mixed-type prop columns fall back to a
    canonical-JSON string column rather than failing Arrow type unification."""
    entities = sorted(rows)
    props = sorted({p for vals in rows.values() for p in vals})
    data: dict[str, Any] = {"entity_uri": pa.array(entities, type=pa.string())}
    for prop in props:
        values = [rows[e].get(prop) for e in entities]
        try:
            data[prop] = pa.array(values)
        except (pa.ArrowInvalid, pa.ArrowTypeError):
            from .store import encode_value

            data[prop] = pa.array(
                [None if v is None else encode_value(v) for v in values], type=pa.string()
            )
    return pa.table(data)


def scan(
    store: "Hearth",
    class_uri: str,
    stance: Stance,
    filters: Optional[Mapping[str, Any]] = None,
    *,
    layer: Layer = Layer.ENTITY,
) -> pa.Table:
    shard = store._shards.get((layer, class_uri))
    if shard is None:
        return _pivot_table({})
    return _pivot_table(_apply_filters(_stanced_rows(shard, stance), filters))


# --------------------------------------------------------------------------
# DuckDB scan over canonical Parquet (the §4.5 cross-check)
# --------------------------------------------------------------------------


def _stance_predicate(stance: Stance) -> str:
    if stance.kind == "current":
        return f"valid_to >= {FOREVER} AND expired_at >= {FOREVER}"
    if stance.kind == "as_of":
        t = stance.valid_at
        return f"expired_at >= {FOREVER} AND valid_from <= {t} AND {t} < valid_to"
    if stance.kind == "as_known_at":
        t = stance.known_at
        return (
            f"created_at <= {t} AND {t} < expired_at AND valid_from <= {t} AND {t} < valid_to"
        )
    tv, ts = stance.valid_at, stance.known_at
    return (
        f"created_at <= {ts} AND {ts} < expired_at AND valid_from <= {tv} AND {tv} < valid_to"
    )


def scan_duckdb(
    store: "Hearth",
    class_uri: str,
    stance: Stance,
    filters: Optional[Mapping[str, Any]] = None,
    *,
    layer: Layer = Layer.ENTITY,
) -> pa.Table:
    """Stance + survivorship computed entirely in SQL over the shard Parquet.
    The ORDER BY in the window mirrors store.survivorship_key term for term."""
    try:
        view = store.duckdb_view(layer, class_uri)
    except KeyError:
        return _pivot_table({})
    sql = f"""
        WITH ranked AS (
            SELECT entity_uri, prop, value_json,
                   ROW_NUMBER() OVER (
                       PARTITION BY entity_uri, prop
                       ORDER BY src_rank ASC, confidence DESC, created_at DESC, seq DESC
                   ) AS rn
            FROM {view}
            WHERE {_stance_predicate(stance)}
        )
        SELECT entity_uri, prop, value_json FROM ranked WHERE rn = 1
    """
    result = store.duck.execute(sql).fetchall()
    rows: dict[str, dict[str, Any]] = {}
    for entity_uri, prop, value_json in result:
        rows.setdefault(entity_uri, {})[prop] = decode_value(value_json)
    return _pivot_table(_apply_filters(rows, filters))
