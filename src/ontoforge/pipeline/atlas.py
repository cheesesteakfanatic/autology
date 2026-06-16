"""The Connection Atlas: every induced class as a star, every cross-dataset
join candidate as an arc, tiered by certainty.

``build_atlas(estate, artifacts, inds, ontology)`` classifies cross-table
column relationships into the three tiers the Atlas UI
(server/static/js/apps/constellation.js) renders:

confirmed
    The MATERIALIZED link properties: every ``is_link`` PropertyDef of the
    (enriched) induced ontology whose backing column the class plans recover
    (the same :func:`ontoforge.pipeline.mapping.build_plans` walk the
    materializer uses), plus the IND evidence that admitted it — the IND is
    recovered from the discovery list by matching (source table column,
    target class identity column); evidence (coverage / overlap / shared
    samples) is recomputed exactly from the column value sets either way, so
    ER-resolved link properties (no surviving IND) carry honest evidence too.

likely
    Exactly-verified value-overlap affinities (:func:`scale.pair_affinities`
    at floor ``ATLAS_AFFINITY_FLOOR``) that did NOT become links, kept when
    ``LIKELY_COVERAGE_LO <= coverage < LIKELY_COVERAGE_HI`` OR
    ``name_similarity >= LIKELY_NAME_SIM and coverage >= LIKELY_NAME_COVERAGE``.
    Score = 0.45*coverage + 0.25*name_sim + 0.15*semtype_match
    + 0.15*rhs_uniqueness. (Affinities are recomputed from the estate value
    sets rather than read from the admitted IND list — induction only keeps
    INDs at the 0.95 admission floor, far above the likely band.) One arc per
    (src class, src prop, dst class, dst prop), best evidence first, capped
    at ``LIKELY_CAP`` (600) by score — the same scale-guard discipline the
    contract applies to hints, sized to the UI's proven 600-arc budget.

hint
    Same non-empty semantic type, same discriminating format signature, or
    same unit on both columns while the value sets are essentially disjoint
    (coverage < ``HINT_COVERAGE``). Score = name_sim*0.5 + 0.3; capped at
    ``HINT_CAP`` (400) by score.

Components are the union-find closure of classes over CONFIRMED links only;
a component's label is its largest class's name (most backing tables, then
most backing rows, then name), ``dataset_count`` counts the distinct source
tables backing its classes, and single-class components are silos. Likely and
hint links whose endpoint table backs no induced class attach to a
pseudo-class ``table://<table>`` carried as its own (ClassDef-less, silo)
component entry, so e.g. a keyless routes fact table still shows its
airports<->routes arcs. Self-arcs (both endpoints in one class) and
same-table pairs are excluded: the atlas maps connections BETWEEN datasets.

Persistence & hooks
-------------------
``build_and_persist_atlas(project_dir, estate, artifacts, ...)`` writes
``<project>/atlas.json`` atomically (tmp + os.replace) and registers a ledger
artifact of kind ``'atlas'`` whose provenance is a ONE-leaf term over a single
synthetic ``atom://atlas/build/...`` atom (the documented cheap satisfaction
of constraint H: the atlas is a derived VIEW over the estate; per-arc evidence
stays inspectable through the payload itself).

``materialize_induced(..., atlas_dir=<project>)`` (a new OPTIONAL keyword —
existing callers, including the frozen ``cli.py``, pass nothing and are
unchanged) builds the atlas at the end of materialization. Because the frozen
CLI cannot pass the new argument, CLI/demo projects build the atlas OFFLINE
via the standalone entry point::

    python -m ontoforge.pipeline.atlas <project_dir>

which re-discovers the estate from ``config.json`` (respecting the sticky
``state.json`` row ``limit``), re-profiles through the discovery cache,
recomputes INDs through the scaled band-index path
(:func:`scale.discover_inds_scaled` — equivalent to the frozen discovery,
minutes faster at wild scale), re-induces, loads the materialized ontology
when present, and rebuilds ``atlas.json``. The server's ``GET /api/atlas``
404s with exactly that command line until the file exists.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from ontoforge.contracts import IND, Atom, Ontology, RelationshipType, leaf
from ontoforge.lodestone.model import all_props

from .induce import InducedArtifacts
from .mapping import ClassPlan, build_plans
from .scale import ColumnFacts, _affinity, column_facts, pair_affinities

# The typed-relationship overlay reuses the CLOSED-CORE relationships engine and
# the ensemble reasoning-path gate (v2.1 §1.2 / §1.3) — distribution-aware proxy
# scoring + the typed taxonomy + plurality voting for the ambiguous band. The
# atlas tiering (confirmed/likely/hint) is unchanged; this only ADDS a type.

__all__ = [
    "ATLAS_AFFINITY_FLOOR",
    "ATLAS_FILE",
    "HINT_CAP",
    "HINT_COVERAGE",
    "LIKELY_CAP",
    "LIKELY_COVERAGE_HI",
    "LIKELY_COVERAGE_LO",
    "LIKELY_NAME_COVERAGE",
    "LIKELY_NAME_SIM",
    "AtlasComponent",
    "AtlasEvidence",
    "AtlasLink",
    "AtlasReport",
    "build_and_persist_atlas",
    "build_atlas",
    "rebuild_for_project",
]

ATLAS_FILE = "atlas.json"

#: exact-verification floor for the likely-tier affinity pass (the lowest
#: coverage any likely rule can admit)
ATLAS_AFFINITY_FLOOR = 0.2
#: likely tier: coverage in [LO, HI) ...
LIKELY_COVERAGE_LO = 0.35
LIKELY_COVERAGE_HI = 0.97
#: ... OR name_similarity >= NAME_SIM with coverage >= NAME_COVERAGE
LIKELY_NAME_SIM = 0.5
LIKELY_NAME_COVERAGE = 0.2
#: likely score weights (convex): coverage / name / semtype / rhs uniqueness
W_LIKELY = (0.45, 0.25, 0.15, 0.15)

#: likely arcs kept (by score, then names) — the same scale-guard discipline
#: the contract applies to hints, sized to the UI's proven 600-arc budget
#: (constellation.js ATLAS SCALE GUARD: 250 nodes / 600 arcs). At wild scale
#: the raw band admits tens of thousands of weak numeric/date co-coverage
#: pairs; the cap keeps the strongest evidence and bounds the payload.
LIKELY_CAP = 600
#: hint tier: shared semtype/format/unit with value sets below this coverage
HINT_COVERAGE = 0.05
#: hints kept (by score, then names) — the UI's scale guard budget
HINT_CAP = 400
#: scale guard: hint candidate pairs exactly verified, in score order; pairs
#: beyond this budget score strictly lower than everything examined
HINT_VERIFY_BUDGET = 50_000

PSEUDO_SCHEME = "table://"


# --------------------------------------------------------------------- report


@dataclass(frozen=True, slots=True)
class AtlasEvidence:
    """WHY one arc exists — the evidence card's exact fields."""

    coverage: float
    overlap_count: int
    sample_shared_values: tuple[str, ...] = ()
    name_similarity: float = 0.0
    semtype_match: bool = False


@dataclass(frozen=True, slots=True)
class AtlasLink:
    """One tiered arc between two class URIs (the UI link contract).

    ``rel_type`` / ``rel_summary`` are the ADDITIVE typed-relationship overlay
    (v2.1 §1.2): the relationship taxonomy verdict the closed-core
    :mod:`ontoforge.relationships` engine assigned to this column pair
    (``fk_join`` · ``lookup_dimension`` · ``m2m_bridge`` · ``denormalization`` ·
    ``derived_field`` · ``unrelated`` · ``unknown``) plus a one-line evidence
    summary (which signals fired / conflicted). Both default to ``None`` and are
    omitted from :meth:`AtlasReport.to_payload` when unset, so the existing
    ``/api/atlas`` link contract is unchanged for callers that don't read them.
    """

    src_class: str
    dst_class: str
    src_prop: str
    dst_prop: str
    tier: str               # "confirmed" | "likely" | "hint"
    score: float
    evidence: AtlasEvidence
    rel_type: Optional[str] = None       # the typed-relationship verdict
    rel_summary: str = ""                # short evidence summary (fired/conflicted)


@dataclass(frozen=True, slots=True)
class AtlasComponent:
    """One island (or silo) of confirmed-connected classes."""

    id: str
    label: str
    class_uris: tuple[str, ...]
    dataset_count: int
    is_silo: bool


@dataclass(slots=True)
class AtlasReport:
    """The full atlas — ``to_payload()`` is byte-stable JSON for atlas.json."""

    components: list[AtlasComponent] = field(default_factory=list)
    links: list[AtlasLink] = field(default_factory=list)
    stats: dict[str, int] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        return {
            "components": [
                {
                    "id": c.id,
                    "label": c.label,
                    "class_uris": list(c.class_uris),
                    "dataset_count": c.dataset_count,
                    "is_silo": c.is_silo,
                }
                for c in self.components
            ],
            "links": [self._link_payload(lk) for lk in self.links],
            "stats": dict(self.stats),
        }

    @staticmethod
    def _link_payload(lk: "AtlasLink") -> dict[str, Any]:
        out: dict[str, Any] = {
            "src_class": lk.src_class,
            "dst_class": lk.dst_class,
            "src_prop": lk.src_prop,
            "dst_prop": lk.dst_prop,
            "tier": lk.tier,
            "score": lk.score,
            "evidence": {
                "coverage": lk.evidence.coverage,
                "overlap_count": lk.evidence.overlap_count,
                "sample_shared_values": list(lk.evidence.sample_shared_values),
                "name_similarity": lk.evidence.name_similarity,
                "semtype_match": lk.evidence.semtype_match,
            },
        }
        # ADDITIVE: only emit the typed-relationship overlay when populated, so
        # the existing /api/atlas payload (and its round-trip tests) are byte-
        # identical for links the relationships engine did not type.
        if lk.rel_type is not None:
            out["rel_type"] = lk.rel_type
            out["rel_summary"] = lk.rel_summary
        return out


# ----------------------------------------------------- column <-> class index


class _ClassIndex:
    """Recovered (table, column) -> (class uri, property name) coordinates,
    backing-table sets, and per-class identity columns — all from the same
    ClassPlans the materializer commits through."""

    def __init__(self, plans: list[ClassPlan], onto: Ontology) -> None:
        self.onto = onto
        self.plans = plans
        self.table_plans: dict[str, list[ClassPlan]] = {}
        self.class_tables: dict[str, set[str]] = {}
        self.class_name: dict[str, str] = {c.uri: c.name for c in onto.iter_classes()}
        # (table, column) -> (class_uri, prop_name), most specific plan first
        self._col_map: dict[tuple[str, str], tuple[str, str]] = {}
        self._identity: dict[str, tuple[str, str]] = {}  # class -> (table, column)

        for plan in plans:
            tables: set[str] = set()
            if plan.kind == "hub":
                tables = {t for t, _ in plan.member_columns}
                if plan.identity_column is not None:
                    self._identity.setdefault(plan.class_uri, plan.identity_column)
                for t, c in plan.member_columns:
                    self._col_map.setdefault(
                        (t, c), (plan.class_uri, plan.identity_prop or c)
                    )
            elif plan.table is not None:
                tables = {plan.table}
                self.table_plans.setdefault(plan.table, []).append(plan)
                if plan.kind == "decomp" and plan.lhs is not None:
                    self._identity.setdefault(plan.class_uri, (plan.table, plan.lhs))
                elif plan.key_columns:
                    self._identity.setdefault(
                        plan.class_uri, (plan.table, plan.key_columns[0])
                    )
            self.class_tables.setdefault(plan.class_uri, set()).update(tables)

        # table-kind prop columns override hub membership: the column IS a
        # property of the row class it lives on
        for plan in plans:
            if plan.kind != "table" or plan.table is None:
                continue
            for prop_name, cols in plan.prop_columns.items():
                for c in cols:
                    self._col_map[(plan.table, c)] = (plan.class_uri, prop_name)

    def endpoint(self, table: str, column: str) -> tuple[str, str]:
        """(class_uri, prop_label) for one column; pseudo-class fallback for
        tables backing no induced class (so their arcs still show)."""
        hit = self._col_map.get((table, column))
        if hit is not None:
            return hit
        for plan in self.table_plans.get(table, ()):  # backed table, unmapped col
            return plan.class_uri, column
        return f"{PSEUDO_SCHEME}{table}", column

    def identity_column(self, class_uri: str) -> Optional[tuple[str, str]]:
        return self._identity.get(class_uri)

    def identity_prop(self, class_uri: str) -> str:
        coord = self._identity.get(class_uri)
        if coord is None:
            return "?"
        return self.endpoint(*coord)[1]

    def label_of(self, uri: str) -> str:
        if uri.startswith(PSEUDO_SCHEME):
            return uri[len(PSEUDO_SCHEME):]
        return self.class_name.get(uri, uri)

    def coord_of(self, class_uri: str, prop_label: str) -> Optional[tuple[str, str]]:
        """Recover the backing (table, column) for a (class, prop) endpoint —
        the inverse of :meth:`endpoint`. For a ``table://`` pseudo-class the
        prop label IS the column; for an identity prop fall back to the class's
        identity column. ``None`` when no column backs the endpoint."""
        if class_uri.startswith(PSEUDO_SCHEME):
            return (class_uri[len(PSEUDO_SCHEME):], prop_label)
        # search the col_map for a (table, column) that maps to this endpoint
        for (table, column), (uri, prop) in self._col_map.items():
            if uri == class_uri and prop == prop_label:
                return (table, column)
        # identity prop fallback (link/identity props canonicalize to the key)
        ident = self._identity.get(class_uri)
        if ident is not None and self.endpoint(*ident)[1] == prop_label:
            return ident
        return None


# ------------------------------------------------------------------ the build


def _semtype_of(profiles: Mapping[str, Any], table: str, column: str) -> str:
    tp = profiles.get(table)
    cp = tp.columns.get(column) if tp is not None else None
    return cp.semantic_type if cp is not None else ""


def _semtype_match(profiles: Mapping[str, Any], a: tuple[str, str], b: tuple[str, str]) -> bool:
    sa, sb = _semtype_of(profiles, *a), _semtype_of(profiles, *b)
    return bool(sa) and sa == sb


def _evidence(aff, semtype: bool) -> AtlasEvidence:
    return AtlasEvidence(
        coverage=round(aff.coverage, 4),
        overlap_count=aff.overlap,
        sample_shared_values=aff.shared_samples,
        name_similarity=aff.name_similarity,
        semtype_match=semtype,
    )


def _confirmed_links(
    index: _ClassIndex,
    facts_by: dict[tuple[str, str], ColumnFacts],
    profiles: Mapping[str, Any],
    inds: Sequence[IND],
) -> tuple[list[AtlasLink], set[frozenset[tuple[str, str]]]]:
    """Materialized link properties -> confirmed arcs, with the admitting IND
    recovered by (src column, dst class identity column) match."""
    onto = index.onto
    ind_by_pair = {
        (i.lhs_table, i.lhs_column, i.rhs_table, i.rhs_column): i for i in inds
    }
    out: list[AtlasLink] = []
    used_pairs: set[frozenset[tuple[str, str]]] = set()
    seen: set[tuple[str, str, str]] = set()

    for plan in index.plans:
        if plan.table is None:
            continue
        props = all_props(onto, plan.class_uri)
        for prop_name, pdef in sorted(props.items()):
            if not pdef.is_link or not pdef.range_class:
                continue
            if pdef.range_class == plan.class_uri:
                continue  # self-keys are identity, not a cross-dataset arc
            cols = plan.prop_columns.get(prop_name)
            if not cols:
                # ER-resolved link props record their column as a synonym
                cols = tuple(
                    s for s in pdef.synonyms if (plan.table, s) in facts_by
                )
            if not cols:
                continue
            dst_coord = index.identity_column(pdef.range_class)
            if dst_coord is None or dst_coord not in facts_by:
                continue
            src_coord = (plan.table, cols[0])
            if src_coord not in facts_by or src_coord == dst_coord:
                continue
            key = (plan.class_uri, prop_name, pdef.range_class)
            if key in seen:
                continue
            seen.add(key)
            aff = _affinity(facts_by[src_coord], facts_by[dst_coord])
            ind = ind_by_pair.get((*src_coord, *dst_coord))
            out.append(
                AtlasLink(
                    src_class=plan.class_uri,
                    dst_class=pdef.range_class,
                    src_prop=prop_name,
                    dst_prop=index.identity_prop(pdef.range_class),
                    tier="confirmed",
                    score=ind.score if ind is not None else aff.score,
                    evidence=_evidence(
                        aff, _semtype_match(profiles, src_coord, dst_coord)
                    ),
                )
            )
            used_pairs.add(frozenset((src_coord, dst_coord)))
    out.sort(key=lambda lk: (-lk.score, lk.src_class, lk.src_prop, lk.dst_class))
    return out, used_pairs


def _likely_links(
    index: _ClassIndex,
    facts: list[ColumnFacts],
    profiles: Mapping[str, Any],
    used_pairs: set[frozenset[tuple[str, str]]],
) -> tuple[list[AtlasLink], set[frozenset[tuple[str, str]]]]:
    """Affinities that did not become links, in the likely band; returns the
    arcs plus the column pairs they consumed (hints must not re-show them)."""
    w_cov, w_name, w_sem, w_uniq = W_LIKELY
    best: dict[frozenset[tuple[str, str]], AtlasLink] = {}
    for aff in pair_affinities(facts, floor=ATLAS_AFFINITY_FLOOR):
        if aff.lhs_table == aff.rhs_table:
            continue
        in_cov_band = LIKELY_COVERAGE_LO <= aff.coverage < LIKELY_COVERAGE_HI
        in_name_band = (
            aff.name_similarity >= LIKELY_NAME_SIM
            and aff.coverage >= LIKELY_NAME_COVERAGE
        )
        if not (in_cov_band or in_name_band):
            continue
        src_coord = (aff.lhs_table, aff.lhs_column)
        dst_coord = (aff.rhs_table, aff.rhs_column)
        pair = frozenset((src_coord, dst_coord))
        if pair in used_pairs:
            continue
        src_class, src_prop = index.endpoint(*src_coord)
        dst_class, dst_prop = index.endpoint(*dst_coord)
        if src_class == dst_class:
            continue
        semtype = _semtype_match(profiles, src_coord, dst_coord)
        score = round(
            w_cov * aff.coverage
            + w_name * aff.name_similarity
            + w_sem * (1.0 if semtype else 0.0)
            + w_uniq * aff.rhs_uniqueness,
            4,
        )
        link = AtlasLink(
            src_class=src_class,
            dst_class=dst_class,
            src_prop=src_prop,
            dst_prop=dst_prop,
            tier="likely",
            score=score,
            evidence=_evidence(aff, semtype),
        )
        prev = best.get(pair)
        if prev is None or link.score > prev.score:
            best[pair] = link
    ranked = sorted(
        best.items(),
        key=lambda kv: (-kv[1].score, kv[1].src_class, kv[1].src_prop,
                        kv[1].dst_class, kv[1].dst_prop),
    )
    # one arc per (src class, src prop, dst class, dst prop): parallel source
    # columns canonicalize to the same property and would draw twice; then the
    # LIKELY_CAP scale guard (strongest evidence first)
    out: list[AtlasLink] = []
    consumed: set[frozenset[tuple[str, str]]] = set()
    seen_arcs: set[tuple[str, str, str, str]] = set()
    for pair, link in ranked:
        if len(out) >= LIKELY_CAP:
            break
        arc = (link.src_class, link.src_prop, link.dst_class, link.dst_prop)
        if arc in seen_arcs:
            consumed.add(pair)  # still spent: must not resurface as a hint
            continue
        seen_arcs.add(arc)
        consumed.add(pair)
        out.append(link)
    return out, consumed


def _format_class(profiles: Mapping[str, Any], f: ColumnFacts) -> Optional[str]:
    """A format signature counts as a format CLASS only when discriminating:
    string-typed and structured (run-length counts present, length >= 4)."""
    tp = profiles.get(f.table)
    cp = tp.columns.get(f.column) if tp is not None else None
    if cp is None or not cp.format_signature:
        return None
    sig = cp.format_signature
    if f.dtype.value not in ("string", "text"):
        return None
    if "{" not in sig or len(sig) < 4:
        return None
    return sig


def _hint_links(
    index: _ClassIndex,
    facts: list[ColumnFacts],
    profiles: Mapping[str, Any],
    used_pairs: set[frozenset[tuple[str, str]]],
) -> list[AtlasLink]:
    """Same semtype / format-class / unit, value sets essentially disjoint."""
    from ontoforge.profiling.inds import name_token_jaccard

    groups: dict[tuple[str, str], list[int]] = {}
    for i, f in enumerate(facts):
        if f.distinct < 2:
            continue
        tp = profiles.get(f.table)
        cp = tp.columns.get(f.column) if tp is not None else None
        if cp is not None and cp.semantic_type:
            groups.setdefault(("sem", cp.semantic_type), []).append(i)
        if cp is not None and cp.unit:
            groups.setdefault(("unit", cp.unit), []).append(i)
        fmt = _format_class(profiles, f)
        if fmt is not None:
            groups.setdefault(("fmt", fmt), []).append(i)

    candidates: dict[frozenset[tuple[str, str]], tuple[float, int, int]] = {}
    for members in groups.values():
        for ai in range(len(members)):
            for bi in range(ai + 1, len(members)):
                fa, fb = facts[members[ai]], facts[members[bi]]
                if fa.table == fb.table:
                    continue
                pair = frozenset(((fa.table, fa.column), (fb.table, fb.column)))
                if pair in used_pairs or pair in candidates:
                    continue
                score = round(name_token_jaccard(fa.column, fb.column) * 0.5 + 0.3, 4)
                candidates[pair] = (score, members[ai], members[bi])

    ordered = sorted(
        candidates.items(),
        key=lambda kv: (-kv[1][0], facts[kv[1][1]].table, facts[kv[1][1]].column,
                        facts[kv[1][2]].table, facts[kv[1][2]].column),
    )
    out: list[AtlasLink] = []
    seen_arcs: set[tuple[str, str, str, str]] = set()
    verified = 0
    for _pair, (score, ia, ib) in ordered:
        if len(out) >= HINT_CAP or verified >= HINT_VERIFY_BUDGET:
            break
        verified += 1
        fa, fb = facts[ia], facts[ib]
        aff = _affinity(fa, fb)  # exact: ordered a->b, coverage over a's values
        if aff.coverage >= HINT_COVERAGE:
            continue
        src_class, src_prop = index.endpoint(fa.table, fa.column)
        dst_class, dst_prop = index.endpoint(fb.table, fb.column)
        if src_class == dst_class:
            continue
        arc = (src_class, src_prop, dst_class, dst_prop)
        if arc in seen_arcs:
            continue
        seen_arcs.add(arc)
        out.append(
            AtlasLink(
                src_class=src_class,
                dst_class=dst_class,
                src_prop=src_prop,
                dst_prop=dst_prop,
                tier="hint",
                score=score,
                evidence=_evidence(
                    aff, _semtype_match(profiles, (fa.table, fa.column), (fb.table, fb.column))
                ),
            )
        )
    return out


def _components(
    index: _ClassIndex,
    links: list[AtlasLink],
    profiles: Mapping[str, Any],
) -> list[AtlasComponent]:
    """Union-find over classes using CONFIRMED links only; every emitted
    link's endpoints (incl. table:// pseudo-classes) get a component entry."""
    uris: set[str] = set(index.class_tables)
    for lk in links:
        uris.add(lk.src_class)
        uris.add(lk.dst_class)

    parent: dict[str, str] = {u: u for u in uris}

    def find(u: str) -> str:
        while parent[u] != u:
            parent[u] = parent[parent[u]]
            u = parent[u]
        return u

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    for lk in links:
        if lk.tier == "confirmed":
            union(lk.src_class, lk.dst_class)

    def tables_of(uri: str) -> set[str]:
        if uri.startswith(PSEUDO_SCHEME):
            return {uri[len(PSEUDO_SCHEME):]}
        return set(index.class_tables.get(uri, ()))

    def rows_of(uri: str) -> int:
        total = 0
        for t in tables_of(uri):
            tp = profiles.get(t)
            total += tp.row_count if tp is not None else 0
        return total

    groups: dict[str, list[str]] = {}
    for u in uris:
        groups.setdefault(find(u), []).append(u)

    comps: list[tuple[str, tuple[str, ...], int]] = []
    for members in groups.values():
        members.sort()
        # the largest class names the island: most backing tables, then rows
        largest = max(
            members, key=lambda u: (len(tables_of(u)), rows_of(u), index.label_of(u))
        )
        datasets = set()
        for u in members:
            datasets |= tables_of(u)
        comps.append((index.label_of(largest), tuple(members), len(datasets)))

    comps.sort(key=lambda c: (-len(c[1]), -c[2], c[0]))
    return [
        AtlasComponent(
            id=f"c{i}",
            label=label,
            class_uris=members,
            dataset_count=n_datasets,
            is_silo=len(members) <= 1,
        )
        for i, (label, members, n_datasets) in enumerate(comps)
    ]


# ------------------------------------------------ typed-relationship overlay


def _rel_summary(cand: Any) -> str:
    """A short, human evidence line from a RelationshipCandidate: which signals
    FIRED vs which CONFLICTED (the false-positive discriminator) + the proxy.

    Kept tiny and deterministic — this is the one-liner the UI shows on the arc,
    not the full ScoutPayload."""
    fired = [ev.kind.value for ev in cand.evidence if ev.fired and not ev.conflicts]
    conflicted = [ev.kind.value for ev in cand.evidence if ev.conflicts]
    parts = [f"proxy {cand.confidence:.2f}"]
    if fired:
        parts.append("fired: " + ", ".join(sorted(set(fired))[:4]))
    if conflicted:
        parts.append("conflict: " + ", ".join(sorted(set(conflicted))[:3]))
    if cand.needs_adjudication:
        parts.append("adjudicated")
    return "; ".join(parts)


def _typed_overlay(
    profiles: Mapping[str, Any],
    inds: Sequence[IND],
    arc_pairs: Sequence[frozenset[tuple[str, str]]],
) -> dict[frozenset[tuple[str, str]], tuple[str, str]]:
    """Map each scored column pair to (rel_type, rel_summary).

    Runs the closed-core :func:`~ontoforge.relationships.discover_relationships`
    over the table profiles, SEEDED by the union of the admitted INDs and the
    pairs the atlas actually DREW (``arc_pairs``). Seeding by the drawn arcs is
    what lets the false-positive killer reach the likely / hint tiers — exactly
    the "looks-similar-isn't-related" arcs (same-name, disjoint values) that a
    pure IND seed (admitted at the 0.95 floor) would never include. The arc set
    is already bounded by ``LIKELY_CAP`` / ``HINT_CAP``, so this stays O(arcs),
    not O(n²), at wild scale.

    For pairs the proxy flags ``needs_adjudication`` it consults the ensemble
    :class:`~ontoforge.ensemble.RelationshipGate` (plurality reasoning-path vote)
    so the surfaced type reflects the same adjudication the engineer uses.

    Keyed by the UNORDERED column-coordinate pair (a frozenset), so a link drawn
    either direction finds its type. Pure-deterministic; never invokes a model.
    """
    from ontoforge.ensemble import RelationshipGate, should_vote
    from ontoforge.relationships import discover_relationships

    table_profiles = list(profiles.values())
    if not table_profiles:
        return {}

    # build the seed IND list: admitted INDs + a synthetic IND per drawn arc
    # (both directions), so every arc the UI shows is offered to the engine.
    seed: list[IND] = list(inds) if inds else []
    seed.extend(_arc_seed_inds(arc_pairs))

    # keep_unrelated=True so the auditable UNRELATED verdict (the false-positive
    # killer) can surface on a same-name/disjoint arc; min_confidence low so we
    # type as many viable pairs as the engine will speak to.
    candidates = discover_relationships(
        table_profiles,
        inds=seed if seed else None,
        min_confidence=0.0,
        keep_unrelated=True,
    )
    gate = RelationshipGate()
    overlay: dict[frozenset[tuple[str, str]], tuple[str, str]] = {}
    for cand in candidates:
        rel_type = cand.rel_type
        summary = _rel_summary(cand)
        if should_vote(cand):
            # ambiguous / conflicting: let the reasoning-path gate decide the
            # type (no SQL validation here — that is the engineer's commit-time
            # job; the atlas is a derived view).
            verdict = gate.decide(cand)
            rel_type = verdict.rel_type
            disp = "committed" if verdict.committed else "routed-to-human"
            summary = f"{summary}; vote {rel_type.value} ({disp})"
        pair = frozenset((
            (cand.left.table, cand.left.column),
            (cand.right.table, cand.right.column),
        ))
        # discover yields one ordered candidate per viable pair; the first to
        # land on an unordered pair wins (ranked by descending proxy already).
        overlay.setdefault(pair, (rel_type.value, summary))
    return overlay


def _arc_seed_inds(
    arc_pairs: Sequence[frozenset[tuple[str, str]]],
) -> list[IND]:
    """Synthetic INDs (both directions) for every drawn arc, so the relationships
    engine considers exactly the pairs the UI shows. These carry no admission
    weight (coverage/score 0) — they are pair SEEDS, not evidence; the engine
    re-derives all evidence from the profiles."""
    out: list[IND] = []
    for pair in arc_pairs:
        members = sorted(pair)
        if len(members) != 2:
            continue
        (lt, lc), (rt, rc) = members[0], members[1]
        out.append(IND(lhs_table=lt, lhs_column=lc, rhs_table=rt, rhs_column=rc,
                       coverage=0.0, score=0.0))
        out.append(IND(lhs_table=rt, lhs_column=rc, rhs_table=lt, rhs_column=lc,
                       coverage=0.0, score=0.0))
    return out


def _link_coords(
    links: list[AtlasLink],
    index: _ClassIndex,
) -> dict[int, frozenset[tuple[str, str]]]:
    """For each link, recover the unordered (table, column) pair it was drawn
    from, so the typed-relationship overlay (keyed by column coords) can be
    matched back to the class-URI link. Links whose endpoints have no backing
    column (rare ER-only props) are omitted (no overlay attached)."""
    out: dict[int, frozenset[tuple[str, str]]] = {}
    for i, lk in enumerate(links):
        src = index.coord_of(lk.src_class, lk.src_prop)
        dst = index.coord_of(lk.dst_class, lk.dst_prop)
        if src is not None and dst is not None and src != dst:
            out[i] = frozenset((src, dst))
    return out


def _apply_overlay(
    links: list[AtlasLink],
    overlay: Mapping[frozenset[tuple[str, str]], tuple[str, str]],
    coords: Mapping[int, frozenset[tuple[str, str]]],
) -> list[AtlasLink]:
    """Attach (rel_type, rel_summary) to each link whose column pair the engine
    typed. Links the engine did not speak to keep ``rel_type=None`` (omitted from
    the payload). Confirmed links with no typed verdict default to ``fk_join``
    (a materialized link property IS a foreign-key join by construction)."""
    import dataclasses

    out: list[AtlasLink] = []
    for i, lk in enumerate(links):
        pair = coords.get(i)
        typed = overlay.get(pair) if pair is not None else None
        if typed is not None:
            out.append(dataclasses.replace(lk, rel_type=typed[0], rel_summary=typed[1]))
        elif lk.tier == "confirmed":
            out.append(dataclasses.replace(
                lk, rel_type=RelationshipType.FK_JOIN.value,
                rel_summary="confirmed link property (materialized foreign key)",
            ))
        else:
            out.append(lk)
    return out


def build_atlas(
    estate: dict[str, Any],
    artifacts: InducedArtifacts,
    inds: Optional[Sequence[IND]] = None,
    ontology: Optional[Ontology] = None,
) -> AtlasReport:
    """Tier every cross-table relationship of the estate (module docstring)."""
    onto = ontology if ontology is not None else artifacts.ontology
    if inds is None:
        inds = artifacts.inds
    plans = build_plans(artifacts.strata, onto)
    index = _ClassIndex(plans, onto)
    profiles = artifacts.profiles_by_table

    facts = column_facts(estate["tables"])
    facts_by = {(f.table, f.column): f for f in facts}

    confirmed, used_pairs = _confirmed_links(index, facts_by, profiles, inds)
    likely, likely_pairs = _likely_links(index, facts, profiles, used_pairs)
    hints = _hint_links(index, facts, profiles, used_pairs | likely_pairs)

    links = confirmed + likely + hints
    # ADDITIVE typed-relationship overlay (§1.2): label each arc's column pair
    # with the closed-core taxonomy verdict. Tiering is untouched. The overlay is
    # seeded by the drawn arcs (already capped), so the false-positive killer can
    # type the likely / hint tiers (the "looks-similar" arcs) too.
    coords = _link_coords(links, index)
    overlay = _typed_overlay(profiles, inds, list(coords.values()))
    links = _apply_overlay(links, overlay, coords)
    components = _components(index, links, profiles)
    stats = {
        "classes": sum(len(c.class_uris) for c in components),
        "components": len(components),
        "silos": sum(1 for c in components if c.is_silo),
        "confirmed": len(confirmed),
        "likely": len(likely),
        "hint": len(hints),
    }
    return AtlasReport(components=components, links=links, stats=stats)


# ----------------------------------------------------------------- persistence


def _write_json_atomic(path: Path, blob: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(blob, sort_keys=True, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def build_and_persist_atlas(
    project_dir: Path | str,
    estate: dict[str, Any],
    artifacts: InducedArtifacts,
    *,
    inds: Optional[Sequence[IND]] = None,
    ontology: Optional[Ontology] = None,
    ledger: Any = None,
) -> AtlasReport:
    """Build the atlas, write ``<project>/atlas.json`` atomically, and (when a
    ledger is supplied) record an idempotent 'atlas' artifact whose provenance
    is one Leaf over a synthetic atlas-build atom (constraint H, documented)."""
    project_dir = Path(project_dir)
    report = build_atlas(estate, artifacts, inds=inds, ontology=ontology)
    _write_json_atomic(project_dir / ATLAS_FILE, report.to_payload())
    if ledger is not None:
        payload = json.dumps({"stats": report.stats, "file": ATLAS_FILE}, sort_keys=True)
        atom = Atom(uri="atom://atlas/build", value=payload)
        ledger.register_atoms([atom])
        prov_ref = ledger.intern(leaf(atom.atom_id))
        artifact_id = f"atlas:{atom.atom_id}"
        row = ledger.connection.execute(
            "SELECT 1 FROM artifact WHERE artifact_id = ? AND kind = 'atlas' LIMIT 1",
            (artifact_id,),
        ).fetchone()
        if row is None:
            ledger.append_artifact(
                artifact_id=artifact_id, kind="atlas", payload=payload, prov_ref=prov_ref
            )
    return report


# ------------------------------------------------- offline rebuild (python -m)


def rebuild_for_project(project_dir: Path | str) -> AtlasReport:
    """Rebuild ``<project>/atlas.json`` from the project's own config: estate
    re-discovery (sticky row limit honored), cached profiles, scaled INDs,
    deterministic re-induction, and the persisted MATERIALIZED ontology when
    present (so ER-enriched link properties tier as confirmed)."""
    from ontoforge.profiling import profile_table

    from .discover import discover_sources
    from .induce import induce_estate
    from .scale import discover_inds_scaled

    project_dir = Path(project_dir)
    cfg_path = project_dir / "config.json"
    if not cfg_path.is_file():
        raise FileNotFoundError(f"no project at {project_dir} (missing config.json)")
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    state_path = project_dir / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.is_file() else {}
    limit = state.get("limit")

    if cfg.get("estate") == "generic":
        estate = discover_sources(Path(cfg["source_dir"]), limit=limit)
    else:
        from ontoforge.estates import load_estate

        estate = load_estate(Path(cfg["fixtures_dir"]))
        if limit:
            estate["tables"] = {
                name: df.head(int(limit)).copy() for name, df in estate["tables"].items()
            }
            estate.pop("profiles", None)

    cache = estate.get("profiles") or {}
    meta = estate["metadata"]["tables"]
    profiles = [
        cache[name] if name in cache else profile_table(df, meta[name]["source_id"], name)
        for name, df in estate["tables"].items()
    ]
    inds = discover_inds_scaled(estate["tables"])
    artifacts = induce_estate(estate, None, profiles=profiles, inds=inds)

    ontology = None
    for fname in ("ontology.materialized.json", "ontology.json"):
        p = project_dir / fname
        if p.is_file():
            from ontoforge.vista._pipeline import load_ontology

            ontology = load_ontology(p)
            break

    ledger = None
    ledger_path = project_dir / cfg.get("ledger", "ledger.sqlite")
    if ledger_path.is_file():
        from ontoforge.ledger import SqliteLedger

        ledger = SqliteLedger(str(ledger_path))
    try:
        return build_and_persist_atlas(
            project_dir, estate, artifacts, inds=inds, ontology=ontology, ledger=ledger
        )
    finally:
        if ledger is not None:
            ledger.close()


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1 or args[0] in ("-h", "--help"):
        print("usage: python -m ontoforge.pipeline.atlas <project_dir>", file=sys.stderr)
        return 2
    report = rebuild_for_project(Path(args[0]))
    s = report.stats
    print(
        f"atlas built -> {Path(args[0]) / ATLAS_FILE}\n"
        f"  classes {s['classes']} | components {s['components']} (silos {s['silos']}) | "
        f"confirmed {s['confirmed']} | likely {s['likely']} | hint {s['hint']}"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised via subprocess in tests
    raise SystemExit(main())
