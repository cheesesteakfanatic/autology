"""Generic materialization: induced ontology + source rows -> HEARTH world.

``materialize_induced(estate, induced_ontology, strata_artifacts, hearth,
ledger)`` commits, for EVERY induced class with backing evidence:

- one entity per source row (g-table plans) or per ER cluster where generic
  ER ran over the class's identity domain;
- one entity per distinct grouping value (g-decomp latent types) and per
  distinct shared-domain value (g-join hubs);
- a ValueCell per mapped property, value conformed by the
  :mod:`ontoforge.pipeline.conform` layer (trim, null tokens, unit suffix via
  the profiling unit table), with constraint-H provenance: every cell's
  ``prov_ref`` is an interned Leaf (or product/sum) over atoms minted from the
  ACTUAL source cells and registered in the ledger;
- a LinkCell per IND-backed link property (subject row value -> target class
  key match) and per ER-resolved cross-table identity reference.

The exact ontology the world is committed under (with conformance/ER
enrichment applied) is returned in the stats so callers persist what `ask`
must load.
"""

from __future__ import annotations

import re
from typing import Any, Optional

from ontoforge.contracts import Interval, Layer, LinkCell, Ontology, ValueCell, leaf, make_cell_atom
from ontoforge.contracts.provenance import prov_prod, prov_sum
from ontoforge.lodestone.model import all_props

from .conform import ColumnConformance, conform_value, decide_column
from .discover import slugify, table_row_keys
from .enrich import enrich_ontology
from .er_generic import ClassResolution, resolve_generic
from .induce import InducedArtifacts
from .mapping import ClassPlan, build_plans, entity_slug, row_entity_uri

__all__ = ["materialize_induced"]

#: provenance fan-in cap for aggregated identity cells (decomp/hub heads)
PROV_FANIN = 8


def _identity_norm(raw: Any) -> str:
    return re.sub(r"\s+", " ", str(raw).strip()).upper()


class _Builder:
    """Atom minting + provenance interning + batched commits (constraint H)."""

    def __init__(self, estate: dict[str, Any], ledger: Any, hearth: Any) -> None:
        self.meta = estate["metadata"]["tables"]
        self.ledger = ledger
        self.hearth = hearth
        self.cells: dict[str, list[ValueCell]] = {}
        self.links: dict[tuple[str, str], list[LinkCell]] = {}
        self._atom_cache: dict[tuple[str, str, str], str] = {}

    def atom(self, table: str, row_key: str, column: str, value: Any) -> str:
        key = (table, row_key, column)
        if key not in self._atom_cache:
            a = make_cell_atom(self.meta[table]["source_id"], table, row_key, column, value)
            self.ledger.register_atoms([a])
            self._atom_cache[key] = a.atom_id
        return self._atom_cache[key]

    def ref_leaf(self, table: str, row_key: str, column: str, value: Any) -> str:
        return self.ledger.intern(leaf(self.atom(table, row_key, column, value)))

    def ref_prod(self, atom_ids: list[str]) -> str:
        return self.ledger.intern(prov_prod([leaf(a) for a in atom_ids]))

    def ref_sum(self, atom_ids: list[str]) -> str:
        return self.ledger.intern(prov_sum([leaf(a) for a in sorted(set(atom_ids))[:PROV_FANIN]]))

    def put(self, class_uri: str, uri: str, prop: str, value: Any, prov: str) -> None:
        self.cells.setdefault(class_uri, []).append(
            ValueCell(
                entity_uri=uri, prop=prop, value=value, valid=Interval(0),
                system=Interval(0), prov_ref=prov, confidence=1.0, src_rank=1,
            )
        )

    def link(self, class_uri: str, pred: str, subj: str, obj: str, prov: str) -> None:
        self.links.setdefault((class_uri, pred), []).append(
            LinkCell(subject_uri=subj, predicate=pred, object_uri=obj,
                     valid=Interval(0), system=Interval(0), prov_ref=prov)
        )

    def commit_all(self, class_names: dict[str, str]) -> dict[str, Any]:
        n_cells = n_links = 0
        entities: set[str] = set()
        per_class: dict[str, int] = {}
        for class_uri in sorted(self.cells):
            # one cell per (entity, prop): first writer wins (deterministic row order)
            seen: set[tuple[str, str]] = set()
            unique: list[ValueCell] = []
            for c in self.cells[class_uri]:
                if (c.entity_uri, c.prop) in seen:
                    continue
                seen.add((c.entity_uri, c.prop))
                unique.append(c)
            self.hearth.commit(Layer.ENTITY, class_uri, unique)
            n_cells += len(unique)
            uris = {c.entity_uri for c in unique}
            entities |= uris
            per_class[class_names.get(class_uri, class_uri)] = len(uris)
        for class_uri, pred in sorted(self.links):
            batch = self.links[(class_uri, pred)]
            dedup: dict[tuple[str, str], LinkCell] = {}
            for lc in batch:
                dedup.setdefault((lc.subject_uri, lc.object_uri), lc)
            self.hearth.commit_links(class_uri, pred, list(dedup.values()))
            n_links += len(dedup)
        return {"entities": len(entities), "cells": n_cells, "links": n_links,
                "classes": per_class}


def _conformance_for_plans(
    estate: dict[str, Any],
    artifacts: InducedArtifacts,
    plans: list[ClassPlan],
    onto: Ontology,
) -> dict[tuple[str, str], ColumnConformance]:
    """One conformance decision per (table, column) referenced by any plan."""
    out: dict[tuple[str, str], ColumnConformance] = {}
    profiles = artifacts.profiles_by_table
    # identity coordinates are NAMES, not measures: every plan key column
    # (and hub member) keeps its lexical string form
    id_cols: set[tuple[str, str]] = set()
    for plan in plans:
        if plan.kind == "hub":
            id_cols |= set(plan.member_columns)
        elif plan.table is not None:
            id_cols |= {(plan.table, k) for k in plan.key_columns}
    for plan in plans:
        props = all_props(onto, plan.class_uri)
        targets: list[tuple[str, str, Optional[str]]] = []
        if plan.kind == "hub":
            for t, c in plan.member_columns:
                targets.append((t, c, None))
        elif plan.table is not None:
            for prop_name, cols in plan.prop_columns.items():
                declared = props.get(prop_name)
                unit = declared.unit if declared is not None else None
                for c in cols:
                    targets.append((plan.table, c, unit))
        for t, c, unit in targets:
            if (t, c) in out or t not in estate["tables"]:
                continue
            df = estate["tables"][t]
            if c not in df.columns:
                continue
            tp = profiles.get(t)
            cp = tp.columns.get(c) if tp is not None else None
            out[(t, c)] = decide_column(
                df[c].tolist(), cp, unit, identifier=(t, c) in id_cols
            )
    return out


def _apply_variant_prefixes(
    conformance: dict[tuple[str, str], ColumnConformance],
    resolutions: dict[str, ClassResolution],
) -> None:
    """Identifier-variant columns conform to the domain's explicit lexical
    form (bare '4669X' -> 'N4669X'), so value probes in either spelling hit."""
    import dataclasses

    for res in resolutions.values():
        if res.method != "exact-variant" or not res.variant_prefix:
            continue
        for t, c in res.domain.identity_columns:
            conf = conformance.get((t, c), ColumnConformance(kind="string"))
            if conf.kind == "string":
                conformance[(t, c)] = dataclasses.replace(
                    conf, lexical_prefix=res.variant_prefix
                )


def materialize_induced(
    estate: dict[str, Any],
    induced_ontology: Ontology,
    strata_artifacts: InducedArtifacts,
    hearth: Any,
    ledger: Any,
    *,
    resolutions: Optional[dict[str, ClassResolution]] = None,
) -> dict[str, Any]:
    """Commit the estate into HEARTH under the INDUCED ontology. Returns stats
    ``{entities, cells, links, classes, er, plans}``; ``induced_ontology`` is
    enriched IN PLACE (callers persist it as the materialized ontology)."""
    onto = induced_ontology
    artifacts = strata_artifacts
    plans = build_plans(artifacts.strata, onto)
    if resolutions is None:
        resolutions = resolve_generic(estate, artifacts, plans, ledger=ledger)
    conformance = _conformance_for_plans(estate, artifacts, plans, onto)
    _apply_variant_prefixes(conformance, resolutions)
    er_links = enrich_ontology(onto, plans, conformance, resolutions, estate)

    b = _Builder(estate, ledger, hearth)
    meta = estate["metadata"]["tables"]
    tables = estate["tables"]
    row_keys: dict[str, list[str]] = {
        t: table_row_keys(df, meta[t]["key_columns"]) for t, df in tables.items()
    }
    records: dict[str, list[dict[str, Any]]] = {t: df.to_dict("records") for t, df in tables.items()}

    # ---------------- pass 1: entity URIs + identity (FK) indexes ------------
    # fk_index[class_uri][normalized identity value] -> entity uri
    fk_index: dict[str, dict[str, str]] = {}
    row_uri: dict[tuple[str, str], dict[int, str]] = {}   # (class_uri, table) -> row idx -> uri

    def res_uri(class_uri: str, raw: Any) -> Optional[str]:
        res = resolutions.get(class_uri)
        if res is None:
            return None
        return res.value_to_uri.get(res.norm(raw))

    for plan in plans:
        idx = fk_index.setdefault(plan.class_uri, {})
        if plan.kind == "table":
            uris: dict[int, str] = {}
            res = resolutions.get(plan.class_uri)
            # a name-like ER resolution whose home column lives on this table
            # assigns CLUSTER uris to the rows themselves (entity dedupe)
            res_col = None
            if (
                res is not None
                and res.method == "er-cascade"
                and res.domain.home_column is not None
                and res.domain.home_column[0] == plan.table
            ):
                res_col = res.domain.home_column[1]
            for i, (rk, row) in enumerate(zip(row_keys[plan.table], records[plan.table])):
                uri = None
                if res_col is not None:
                    uri = res_uri(plan.class_uri, row.get(res_col))
                if uri is None:
                    uri = row_entity_uri(plan.class_name, rk)
                uris[i] = uri
                for kc in plan.key_columns:
                    val = _identity_norm(row.get(kc, ""))
                    if val:
                        idx.setdefault(val, uri)
            row_uri[(plan.class_uri, plan.table)] = uris
        elif plan.kind == "decomp" and plan.table is not None and plan.lhs is not None:
            cls_slug = slugify(plan.class_name)
            for row in records[plan.table]:
                raw = row.get(plan.lhs, "")
                val = _identity_norm(raw)
                if not val:
                    continue
                uri = res_uri(plan.class_uri, raw) or f"ent://{cls_slug}/{entity_slug(val)}"
                idx.setdefault(val, uri)
        elif plan.kind == "hub":
            cls_slug = slugify(plan.class_name)
            for t, c in plan.member_columns:
                if t not in records or c not in tables[t].columns:
                    continue
                for row in records[t]:
                    raw = row.get(c, "")
                    val = _identity_norm(raw)
                    if not val:
                        continue
                    uri = res_uri(plan.class_uri, raw) or f"ent://{cls_slug}/{entity_slug(val)}"
                    idx.setdefault(val, uri)

    # ---------------- pass 2: cells + links ----------------------------------
    er_link_by_plan: dict[tuple[str, str], list] = {}
    for el in er_links:
        er_link_by_plan.setdefault((el.subject_class_uri, el.table), []).append(el)

    def emit_prop_cell(
        plan: ClassPlan, props: dict, prop_name: str, cols: tuple[str, ...],
        row: dict[str, Any], rk: str, uri: str,
    ) -> None:
        pdef = props.get(prop_name)
        if pdef is None:
            return
        table = plan.table
        assert table is not None
        if len(cols) > 1:  # multi-column concatenation (e.g. make + model)
            parts, atoms = [], []
            for c in cols:
                raw = row.get(c, "")
                v, _ = conform_value(raw, ColumnConformance(kind="string"))
                if v:
                    parts.append(str(v))
                    atoms.append(b.atom(table, rk, c, raw))
            if parts and not pdef.is_link:
                b.put(plan.class_uri, uri, prop_name, " ".join(parts), b.ref_prod(atoms))
            return
        col = cols[0]
        raw = row.get(col, "")
        conf = conformance.get((table, col), ColumnConformance(kind="string"))
        value, src_unit = conform_value(raw, conf)
        if value is None:
            return
        is_self_key = pdef.range_class == plan.class_uri and col in plan.key_columns
        if pdef.is_link and pdef.range_class and not is_self_key:
            target = fk_index.get(pdef.range_class, {}).get(_identity_norm(raw)) or res_uri(
                pdef.range_class, raw
            )
            if target is not None and target != uri:
                b.link(plan.class_uri, prop_name, uri, target,
                       b.ref_leaf(table, rk, col, raw))
            if col in plan.key_columns:  # key stays queryable as a value too
                b.put(plan.class_uri, uri, prop_name, value, b.ref_leaf(table, rk, col, raw))
            return
        prov = b.ref_leaf(table, rk, col, raw)
        b.put(plan.class_uri, uri, prop_name, value, prov)
        if conf.annotate_unit and src_unit is not None:
            b.put(plan.class_uri, uri, f"{prop_name}_unit", src_unit, prov)

    for plan in plans:
        props = all_props(onto, plan.class_uri)
        if plan.kind == "table" and plan.table is not None:
            uris = row_uri[(plan.class_uri, plan.table)]
            plan_er_links = er_link_by_plan.get((plan.class_uri, plan.table), [])
            spot_res: dict[str, re.Pattern] = {}
            for el in plan_er_links:
                if el.from_text:
                    res = resolutions.get(el.target_class_uri)
                    if res is not None and res.variant_prefix:
                        spot_res[el.target_class_uri] = re.compile(
                            rf"\b{re.escape(res.variant_prefix)}\d[A-Z0-9]+\b"
                        )
            for i, (rk, row) in enumerate(zip(row_keys[plan.table], records[plan.table])):
                uri = uris[i]
                for prop_name, cols in sorted(plan.prop_columns.items()):
                    emit_prop_cell(plan, props, prop_name, cols, row, rk, uri)
                for el in plan_er_links:
                    raw = row.get(el.column, "")
                    if el.from_text:
                        pattern = spot_res.get(el.target_class_uri)
                        if pattern is None or not str(raw).strip():
                            continue
                        targets = set()
                        for token in pattern.findall(str(raw).upper()):
                            t_uri = res_uri(el.target_class_uri, token)
                            if t_uri is not None and t_uri != uri:
                                targets.add(t_uri)
                        if targets:
                            prov = b.ref_leaf(plan.table, rk, el.column, raw)
                            for t_uri in sorted(targets):
                                b.link(plan.class_uri, el.predicate, uri, t_uri, prov)
                        continue
                    target = res_uri(el.target_class_uri, raw) or fk_index.get(
                        el.target_class_uri, {}
                    ).get(_identity_norm(raw))
                    if target is not None and target != uri:
                        b.link(plan.class_uri, el.predicate, uri, target,
                               b.ref_leaf(plan.table, rk, el.column, raw))
        elif plan.kind == "decomp" and plan.table is not None and plan.lhs is not None:
            seen_vals: set[str] = set()
            for rk, row in zip(row_keys[plan.table], records[plan.table]):
                raw = row.get(plan.lhs, "")
                val = _identity_norm(raw)
                if not val or val in seen_vals:
                    continue
                seen_vals.add(val)
                uri = fk_index[plan.class_uri][val]
                for prop_name, cols in sorted(plan.prop_columns.items()):
                    emit_prop_cell(plan, props, prop_name, cols, row, rk, uri)
        elif plan.kind == "hub":
            head_atoms: dict[str, list[str]] = {}
            head_value: dict[str, str] = {}
            for t, c in plan.member_columns:
                if t not in records or c not in tables[t].columns:
                    continue
                for rk, row in zip(row_keys[t], records[t]):
                    raw = row.get(c, "")
                    val = _identity_norm(raw)
                    if not val:
                        continue
                    head_atoms.setdefault(val, []).append(b.atom(t, rk, c, raw))
                    head_value.setdefault(val, str(raw).strip())
            prop_name = plan.identity_prop or "value"
            for val in sorted(head_atoms):
                uri = fk_index[plan.class_uri][val]
                b.put(plan.class_uri, uri, prop_name, head_value[val], b.ref_sum(head_atoms[val]))

    class_names = {c.uri: c.name for c in onto.iter_classes()}
    stats = b.commit_all(class_names)
    stats["er"] = {
        class_names.get(cu, cu): {
            "method": res.method,
            "clusters": len(res.clusters),
            "mentions": len(res.mention_to_uri),
            "identities": len(res.value_to_uri),
            "tables": list(res.domain.tables),
        }
        for cu, res in sorted(resolutions.items())
    }
    stats["plans"] = [
        {"class": p.class_name, "kind": p.kind, "cid": p.cid, "table": p.table}
        for p in plans
    ]
    return stats
