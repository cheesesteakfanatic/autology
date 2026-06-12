"""Candidate -> induced-class evidence recovery (the swap-in's load-bearing map).

STRATA's emitter keeps the candidate -> member-table/column evidence alive in
its public artifacts (``StrataResult.candidates`` / ``.context`` / ``.lattice``
/ ``.admission``); this module reads it back into per-class materialization
plans:

- a **g-table** candidate materializes one entity per source row;
- a **g-decomp** candidate materializes one entity per distinct lhs value of
  its host table (the synthesized 3NF latent type);
- a **g-join** hub materializes one entity per distinct value of the shared
  domain across all member columns.

Property -> column resolution uses the SAME normalizer chain STRATA used:
``context.PropertyClusters.canonical_of(table, column)`` is exactly the name
the emitter gave the PropertyDef, so matching it against the class's (own +
inherited) property names recovers the column mapping losslessly. The
candidate -> admitted-class walk is the emitter's own
``_class_of_candidate`` (imported read-only from the frozen module — using
the identical function guarantees the materializer and the emitter can never
disagree about which class a candidate's rows belong to).
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Optional

from ontoforge.contracts import Ontology, PropertyDef
from ontoforge.lodestone.model import all_props
from ontoforge.strata import StrataResult
from ontoforge.strata.emit import _class_of_candidate

from .discover import slugify

__all__ = ["ClassPlan", "build_plans", "entity_slug", "row_entity_uri"]


def entity_slug(s: str) -> str:
    """Readable-but-collision-safe URI component (same scheme the gold world
    builder uses: trimmed slug + content hash)."""
    safe = re.sub(r"[^A-Za-z0-9]+", "-", s.strip()).strip("-").lower()[:48]
    h = hashlib.sha1(s.strip().encode()).hexdigest()[:8]
    return f"{safe}-{h}" if safe else h


def row_entity_uri(class_name: str, row_key: str) -> str:
    """The deterministic entity URI of one source row of a table-backed class."""
    return f"ent://{slugify(class_name)}/{entity_slug(row_key)}"


@dataclass
class ClassPlan:
    """How one candidate's evidence materializes into one induced class."""

    class_uri: str
    class_name: str
    kind: str                                       # "table" | "decomp" | "hub"
    cid: str                                        # backing TypeCandidate id
    table: Optional[str]                            # backing table (None for hubs)
    key_columns: tuple[str, ...] = ()               # within `table` (table kind)
    lhs: Optional[str] = None                       # decomp grouping column
    member_columns: tuple[tuple[str, str], ...] = ()
    # prop name -> columns of `table` carrying it (sorted; >1 = concatenation)
    prop_columns: dict[str, tuple[str, ...]] = field(default_factory=dict)
    identity_prop: Optional[str] = None             # hub/decomp identity prop name
    identity_column: tuple[str, str] | None = None  # hub canonical domain column


_KIND = {"g-table": "table", "g-decomp": "decomp", "g-join": "hub"}


def _prop_columns_for(
    cand_member_columns: tuple[tuple[str, str], ...],
    table: str,
    clusters,
    prop_names: set[str],
) -> dict[str, tuple[str, ...]]:
    out: dict[str, list[str]] = {}
    for t, c in cand_member_columns:
        if t != table:
            continue
        canonical = clusters.canonical_of(t, c)
        if canonical in prop_names:
            out.setdefault(canonical, []).append(c)
    return {p: tuple(sorted(cols)) for p, cols in out.items()}


def build_plans(artifacts: StrataResult, onto: Ontology) -> list[ClassPlan]:
    """Recover one materialization plan per surviving candidate whose object
    concept maps to an admitted class."""
    ctx = artifacts.context
    clusters = ctx.clusters
    assert clusters is not None, "StrataResult.context carries no property clusters"
    by_hash = {c.intent_hash: c for c in onto.iter_classes()}

    plans: list[ClassPlan] = []
    for cand in sorted(artifacts.candidates, key=lambda c: c.cid):
        ih = _class_of_candidate(cand.cid, ctx, artifacts.lattice, artifacts.admission)
        cls = by_hash.get(ih) if ih is not None else None
        if cls is None:
            continue
        prop_names = set(all_props(onto, cls.uri))
        kind = _KIND.get(cand.kind)
        if kind == "table":
            table = cand.evidence_tables[0]
            plans.append(
                ClassPlan(
                    class_uri=cls.uri,
                    class_name=cls.name,
                    kind="table",
                    cid=cand.cid,
                    table=table,
                    key_columns=tuple(c for _, c in cand.key_columns),
                    member_columns=cand.member_columns,
                    prop_columns=_prop_columns_for(
                        cand.member_columns, table, clusters, prop_names
                    ),
                )
            )
        elif kind == "decomp":
            table, lhs = cand.key_columns[0]
            plans.append(
                ClassPlan(
                    class_uri=cls.uri,
                    class_name=cls.name,
                    kind="decomp",
                    cid=cand.cid,
                    table=table,
                    key_columns=(lhs,),
                    lhs=lhs,
                    member_columns=cand.member_columns,
                    prop_columns=_prop_columns_for(
                        cand.member_columns, table, clusters, prop_names
                    ),
                    identity_prop=clusters.canonical_of(table, lhs),
                )
            )
        elif kind == "hub":
            key_table, key_column = cand.key_columns[0]
            plans.append(
                ClassPlan(
                    class_uri=cls.uri,
                    class_name=cls.name,
                    kind="hub",
                    cid=cand.cid,
                    table=None,
                    member_columns=cand.member_columns,
                    identity_prop=clusters.canonical_of(key_table, key_column),
                    identity_column=(key_table, key_column),
                )
            )
    return plans


def link_props(onto: Ontology, class_uri: str) -> dict[str, PropertyDef]:
    """name -> link PropertyDef visible on a class (own + inherited)."""
    return {n: p for n, p in all_props(onto, class_uri).items() if p.is_link}
