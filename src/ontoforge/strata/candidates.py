"""STRATA candidate generators (whitepaper §3.3).

Candidate entity types arise from three structural generators (G-doc is the
deferred document path, AMD-0007 scope):

- **G-table** — a table with a detected candidate key is a candidate type; the
  non-key FD-closure columns (= all columns, the key determines the row) are
  its properties.
- **G-decomp** — an exact FD cluster ``X -> Y1..Yk`` inside a wide table where
  X is NOT a table key posits a latent type keyed by X: the classic 3NF
  decomposition signal of §3.1, used generatively. Requires lhs cardinality
  << row count (the cluster must actually *group* rows) and an entity-like
  lhs: temporal and long-text columns cannot key a latent entity type. An lhs
  that is a foreign key into another table's key (IND evidence) is skipped —
  that latent type is already materialized as the target table — and an lhs
  participating in a high-coverage cross-table IND is skipped too: a shared
  value domain is G-join's hub territory, not a per-table latent type.
- **G-join** — an IND hub: a value domain referenced from >= 2 distinct lhs
  tables (or >= 3 INDs sharing one domain) posits a shared reference type even
  when no source table materializes it (the §3.3 Airport example). Hub
  candidates carry ``bypass_sigma=True``: they skip the iceberg support
  threshold but receive explicit spine review (§3.4 failure-mode (b)
  mitigation) — see :mod:`ontoforge.strata.admission`.

Determinism: all outputs are sorted; nothing depends on input ordering.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable, Mapping, Optional, Sequence

from ontoforge.contracts import FD, IND, ColumnProfile, Datatype, TableProfile

from ._norm import GENERIC_SUFFIX_TOKENS, name_tokens, normalize_name

__all__ = [
    "TypeCandidate",
    "generate_candidates",
    "g_table_candidates",
    "g_decomp_candidates",
    "g_join_candidates",
]

#: lhs distinct-count / row-count ceiling for the G-decomp "X groups the rows"
#: requirement (lhs cardinality << row count, §3.3).
DECOMP_MAX_CARDINALITY_RATIO = 0.5
#: minimum rhs columns for a G-decomp cluster. 1: a single exact FD with an
#: entity-like grouping lhs is already a real latent type (COMPONENT ->
#: ATA_CHAPTER is the gold Component class); junk single-FD clusters are
#: filtered by the temporal/text/shared-domain lhs guards instead.
DECOMP_MIN_RHS = 1
#: IND coverage floor for hub graph edges and FK-materialization checks.
HUB_MIN_COVERAGE = 0.95

#: tokens that mark a column name as identifier-like (used to prefer e.g.
#: WORK_ORDER_ID over an accidentally-unique COST column as the table key).
_ID_TOKENS = frozenset({"id", "identifier", "code", "number", "key", "uid"})

_GENERATOR_PRIOR = {"g-table": 0.9, "g-decomp": 0.7, "g-join": 0.6}


@dataclass(frozen=True, slots=True)
class TypeCandidate:
    """One candidate entity type — the input objects G of the formal context."""

    cid: str                                       # stable id, e.g. "g-table:faa_master"
    name_hint: str                                 # normalized snake_case naming hint
    kind: str                                      # "g-table" | "g-decomp" | "g-join"
    member_columns: tuple[tuple[str, str], ...]    # sorted (table, column) pairs
    key_columns: tuple[tuple[str, str], ...]       # the candidate's key coordinates
    evidence_tables: tuple[str, ...]               # sorted source tables
    sample_extent_ids: tuple[str, ...]             # sampled extent identifiers
    source_ids: tuple[str, ...] = ()
    confidence: float = 1.0
    bypass_sigma: bool = False                     # G-join hubs bypass the iceberg σ
    notes: str = ""

    @property
    def generator_prior(self) -> float:
        return _GENERATOR_PRIOR.get(self.kind, 0.5)


# --------------------------------------------------------------------------
# key selection
# --------------------------------------------------------------------------


def _id_likeness(column: str) -> float:
    toks = set(name_tokens(column))
    return 1.0 if toks & _ID_TOKENS else 0.0


def choose_key(tp: TableProfile) -> Optional[tuple[str, ...]]:
    """Pick the candidate key used as the type's identity: smallest arity,
    then identifier-like column names, then lexicographic (deterministic)."""
    if not tp.candidate_keys:
        return None
    return min(
        tp.candidate_keys,
        key=lambda k: (len(k), -sum(_id_likeness(c) for c in k) / len(k), k),
    )


# --------------------------------------------------------------------------
# G-table
# --------------------------------------------------------------------------


def _table_name_hint(tp: TableProfile) -> str:
    src = set(name_tokens(tp.source_id))
    toks = [t for t in name_tokens(tp.table) if t not in src]
    if not toks:  # table name entirely made of source tokens: keep it whole
        toks = list(name_tokens(tp.table))
    return "_".join(toks)


def g_table_candidates(profiles: Sequence[TableProfile]) -> list[TypeCandidate]:
    """§3.3 G-table: every table with a detected candidate key."""
    out: list[TypeCandidate] = []
    for tp in profiles:
        key = choose_key(tp)
        if key is None:
            continue
        members = tuple(sorted((tp.table, c) for c in tp.columns))
        samples = tp.columns[key[0]].sample_values if key[0] in tp.columns else ()
        out.append(
            TypeCandidate(
                cid=f"g-table:{tp.table}",
                name_hint=_table_name_hint(tp),
                kind="g-table",
                member_columns=members,
                key_columns=tuple((tp.table, c) for c in key),
                evidence_tables=(tp.table,),
                sample_extent_ids=tuple(samples),
                source_ids=(tp.source_id,),
                confidence=1.0,
                notes=f"table with candidate key {key}",
            )
        )
    return sorted(out, key=lambda c: c.cid)


# --------------------------------------------------------------------------
# G-decomp
# --------------------------------------------------------------------------


def _decomp_name_hint(lhs: str) -> str:
    toks = list(name_tokens(lhs))
    while len(toks) > 1 and toks[-1] in GENERIC_SUFFIX_TOKENS:
        toks.pop()
    return "_".join(toks)


_TEMPORAL_NAME_TOKENS = frozenset({"date", "time", "datetime", "timestamp"})
_DIGIT_DATE_SIGS = frozenset({"D{8}", "D{6}"})


def _entity_like_lhs(cp: ColumnProfile) -> bool:
    """A latent type's key must be entity-like: temporal columns and long-form
    text cannot key a latent entity type (they group rows by coincidence)."""
    if cp.inferred_type in (Datatype.DATE, Datatype.DATETIME, Datatype.TEXT):
        return False
    if cp.semantic_type == "date" and cp.semantic_confidence >= 0.8:
        return False
    if cp.format_signature in _DIGIT_DATE_SIGS and (
        set(name_tokens(cp.column)) & _TEMPORAL_NAME_TOKENS
    ):
        return False
    return True


def _shared_domains(inds: Iterable[IND]) -> set[tuple[str, str]]:
    """(table, column) coordinates participating in a high-coverage CROSS-table
    IND: such value domains are shared reference domains — G-join hub
    territory, not per-table latent types."""
    out: set[tuple[str, str]] = set()
    for ind in inds:
        if ind.coverage >= HUB_MIN_COVERAGE and ind.lhs_table != ind.rhs_table:
            out.add((ind.lhs_table, ind.lhs_column))
            out.add((ind.rhs_table, ind.rhs_column))
    return out


def _fk_targets(inds: Iterable[IND], by_table: Mapping[str, TableProfile]) -> set[tuple[str, str]]:
    """(table, column) lhs coordinates that are foreign keys into another
    table's singleton candidate key — those latent types are materialized."""
    out: set[tuple[str, str]] = set()
    for ind in inds:
        if ind.coverage < HUB_MIN_COVERAGE or ind.lhs_table == ind.rhs_table:
            continue
        rhs_tp = by_table.get(ind.rhs_table)
        if rhs_tp is not None and (ind.rhs_column,) in rhs_tp.candidate_keys:
            out.add((ind.lhs_table, ind.lhs_column))
    return out


def g_decomp_candidates(
    profiles: Sequence[TableProfile],
    fds: Sequence[FD],
    inds: Sequence[IND] = (),
    *,
    max_cardinality_ratio: float = DECOMP_MAX_CARDINALITY_RATIO,
    min_rhs: int = DECOMP_MIN_RHS,
) -> list[TypeCandidate]:
    """§3.3 G-decomp: exact singleton-lhs FD clusters inside a wide table where
    the lhs is not a table key — the 3NF decomposition signal."""
    by_table = {tp.table: tp for tp in profiles}
    materialized = _fk_targets(inds, by_table)
    shared = _shared_domains(inds)

    clusters: dict[tuple[str, str], set[str]] = defaultdict(set)
    for fd in fds:
        if len(fd.lhs) != 1 or fd.confidence < 1.0 or fd.table not in by_table:
            continue
        clusters[(fd.table, fd.lhs[0])].add(fd.rhs)

    out: list[TypeCandidate] = []
    for (table, lhs), rhss in clusters.items():
        tp = by_table[table]
        if lhs not in tp.columns or len(rhss) < min_rhs:
            continue
        if (lhs,) in tp.candidate_keys:
            continue  # X must NOT be the table key
        if (table, lhs) in materialized:
            continue  # FK into another table's key: type lives there already
        if (table, lhs) in shared:
            continue  # shared cross-table value domain: G-join hub territory
        cp = tp.columns[lhs]
        if not _entity_like_lhs(cp):
            continue  # temporal/long-text lhs cannot key a latent entity type
        distinct = cp.distinct_estimate
        if distinct < 2 or tp.row_count == 0 or distinct > max_cardinality_ratio * tp.row_count:
            continue  # lhs cardinality must be << row count
        members = tuple(sorted((table, c) for c in {lhs, *rhss}))
        out.append(
            TypeCandidate(
                cid=f"g-decomp:{table}:{normalize_name(lhs)}",
                name_hint=_decomp_name_hint(lhs),
                kind="g-decomp",
                member_columns=members,
                key_columns=((table, lhs),),
                evidence_tables=(table,),
                sample_extent_ids=tuple(cp.sample_values),
                source_ids=(tp.source_id,),
                confidence=min(1.0, 1.0 - distinct / max(1, tp.row_count)),
                notes=(
                    f"3NF signal: {lhs} -> {sorted(rhss)} "
                    f"(distinct={distinct}, rows={tp.row_count})"
                ),
            )
        )
    return sorted(out, key=lambda c: c.cid)


# --------------------------------------------------------------------------
# G-join
# --------------------------------------------------------------------------


def g_join_candidates(
    profiles: Sequence[TableProfile],
    inds: Sequence[IND],
    *,
    min_coverage: float = HUB_MIN_COVERAGE,
) -> list[TypeCandidate]:
    """§3.3 G-join: connected components of the high-coverage IND graph that
    behave like a shared reference domain.

    A component qualifies as a hub when it spans >= 2 tables and either
    (a) >= 2 distinct tables appear as IND-lhs pointing across tables, or
    (b) >= 3 INDs share the domain. A component containing another table's
    singleton candidate key is skipped — that domain IS the keyed table
    (it surfaces as a G-table candidate plus a link property instead).
    """
    by_table = {tp.table: tp for tp in profiles}
    strong = [
        i for i in inds
        if i.coverage >= min_coverage
        and i.lhs_table in by_table and i.rhs_table in by_table
        and i.lhs_column in by_table[i.lhs_table].columns
        and i.rhs_column in by_table[i.rhs_table].columns
    ]

    # union-find over (table, column) nodes
    parent: dict[tuple[str, str], tuple[str, str]] = {}

    def find(x: tuple[str, str]) -> tuple[str, str]:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: tuple[str, str], b: tuple[str, str]) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            # deterministic root choice
            lo, hi = sorted((ra, rb))
            parent[hi] = lo

    for ind in strong:
        union((ind.lhs_table, ind.lhs_column), (ind.rhs_table, ind.rhs_column))

    comps: dict[tuple[str, str], set[tuple[str, str]]] = defaultdict(set)
    for node in parent:
        comps[find(node)].add(node)

    out: list[TypeCandidate] = []
    for nodes in comps.values():
        tables = {t for t, _ in nodes}
        if len(tables) < 2:
            continue
        comp_inds = [
            i for i in strong
            if (i.lhs_table, i.lhs_column) in nodes and (i.rhs_table, i.rhs_column) in nodes
        ]
        cross_lhs_tables = {i.lhs_table for i in comp_inds if i.lhs_table != i.rhs_table}
        if not (len(cross_lhs_tables) >= 2 or len(comp_inds) >= 3):
            continue
        if any((c,) in by_table[t].candidate_keys for t, c in nodes):
            continue  # domain materialized as a keyed table
        # canonical domain column: the most-referenced rhs (tie: lexicographic)
        in_deg: dict[tuple[str, str], int] = defaultdict(int)
        for i in comp_inds:
            in_deg[(i.rhs_table, i.rhs_column)] += 1
        key = min(nodes, key=lambda n: (-in_deg.get(n, 0), n))
        key_cp = by_table[key[0]].columns[key[1]]
        coverage = sum(i.coverage for i in comp_inds) / len(comp_inds)
        out.append(
            TypeCandidate(
                cid=f"g-join:{key[0]}.{normalize_name(key[1])}",
                name_hint=_decomp_name_hint(key[1]),
                kind="g-join",
                member_columns=tuple(sorted(nodes)),
                key_columns=(key,),
                evidence_tables=tuple(sorted(tables)),
                sample_extent_ids=tuple(key_cp.sample_values),
                source_ids=tuple(sorted({by_table[t].source_id for t in tables})),
                confidence=round(coverage, 4),
                bypass_sigma=True,
                notes=(
                    f"IND hub: {len(comp_inds)} INDs over {sorted(tables)}; "
                    f"domain column {key}"
                ),
            )
        )
    return sorted(out, key=lambda c: c.cid)


# --------------------------------------------------------------------------
# driver
# --------------------------------------------------------------------------


def generate_candidates(
    profiles: Sequence[TableProfile],
    inds: Sequence[IND] = (),
    fds: Optional[Sequence[FD]] = None,
) -> list[TypeCandidate]:
    """Run all generators. ``fds`` defaults to the union of per-table FDs
    already discovered by M3 (TableProfile.fds)."""
    if fds is None:
        fds = [fd for tp in profiles for fd in tp.fds]
    out = (
        g_table_candidates(profiles)
        + g_decomp_candidates(profiles, fds, inds)
        + g_join_candidates(profiles, inds)
    )
    return sorted(out, key=lambda c: c.cid)
