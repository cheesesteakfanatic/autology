"""STRATA formal context K = (G, M, I) (whitepaper §3.4.1–§3.4.2).

Objects G are the type candidates (§3.3); attributes M are discretized
profile-sketch features per member column:

    has-prop:<canonical-name>   shared-property evidence (the workhorse)
    semtype:<label>             T1 semantic type (conf >= 0.8)
    dim:<dimension>             physical dimension (§3.2)
    fmt:<class>                 coarse format class of the column
    key-arity:<n>               arity of the candidate's identity key
    has-timestamp               any timestamp-like member column
    has-narrative-text          any long-form text member column

Canonical property names come from a *synonym map built from evidence*, never
from hardcoded table knowledge: column names are normalized (token-level
abbreviation expansion, :mod:`._norm`), then clustered by union-find over six
edge rules combining name-token Jaccard, IND links, value-overlap (MinHash),
format-signature shape, and shared identity-like semantic types. On the
aviation estate this yields e.g. {N-NUMBER, TAIL_NUMBER, ACFT_REGIST_NMBR}
-> ``tail_number`` purely from the evidence.

Attribute clarification & reduction (§3.4.2): attributes with identical
extents are merged and reducible attributes (extent = intersection of strictly
larger attribute extents) are dropped *for lattice construction only*; every
reported concept intent is re-expanded against the ORIGINAL context, so
concept identity (intent hashes, §3.4.4) is independent of the reduction.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Iterable, Mapping, Optional, Sequence

import xxhash

from ontoforge.contracts import IND, ColumnProfile, Datatype, TableProfile, minhash_jaccard

from ._norm import name_tokens, normalize_name, token_jaccard
from .candidates import TypeCandidate

__all__ = [
    "PropertyClusters",
    "FormalContext",
    "build_property_clusters",
    "build_context",
    "candidate_attributes",
    "intent_hash_of",
    "ATTRIBUTE_WEIGHTS",
    "attribute_weight",
    "is_timestampish",
]

# ---------------------------------------------------------------------------
# synonym-edge thresholds (measured against the aviation estate; see README)
# ---------------------------------------------------------------------------

IND_EDGE_COVERAGE = 0.95        # rule (a): IND link with any name-token overlap
SEMTYPE_MIN_CONF = 0.9          # rule (b): shared identity-like semantic type
STRONG_NAME_JACCARD = 0.6       # rule (c): name evidence alone suffices
MED_NAME_JACCARD = 0.5          # rule (d): name + format-shape agreement
WEAK_NAME_JACCARD = 1 / 3       # rule (e): name + code-like format agreement
VALUE_NAME_JACCARD = 0.2        # rule (f): name + value-domain overlap
FMT_SIM_MED = 0.40              # rule (d) format-kind-sequence similarity floor
FMT_SIM_STRICT = 0.60           # rule (e) floor
MINHASH_OVERLAP = 0.02          # rule (f) MinHash-Jaccard floor
VALUE_DOMAIN_MIN_DISTINCT = 15  # rule (f): both sides must be entity-domain-sized

#: semantic types too generic to drive synonym merging or canonical naming
#: (every date column is 'date'; merging on that would conflate open/close/event).
GENERIC_SEMTYPES = frozenset({"date", "datetime", "narrative_text", "us_state", "url", "email"})

#: name tokens (post-normalization) that carry no entity meaning on their own:
#: two ...-ID columns sharing only "id" are different identifier namespaces.
#: Rule (e) — weak name + format shape — requires a shared NON-generic token.
GENERIC_NAME_TOKENS = frozenset({"id", "identifier", "code", "number", "no", "key", "uid"})

#: attribute-kind weights for intent distinctiveness (§3.4.3 admission features)
ATTRIBUTE_WEIGHTS: dict[str, float] = {
    "has-prop": 1.0,
    "semtype": 0.6,
    "dim": 0.4,
    "has-timestamp": 0.4,
    "has-narrative-text": 0.4,
    "fmt": 0.2,
    "key-arity": 0.2,
}


def attribute_weight(attr: str) -> float:
    kind = attr.split(":", 1)[0]
    return ATTRIBUTE_WEIGHTS.get(kind, 0.3)


# ---------------------------------------------------------------------------
# per-column derived facts
# ---------------------------------------------------------------------------

_DTYPE_GROUP: dict[Datatype, str] = {
    Datatype.INTEGER: "numeric",
    Datatype.FLOAT: "numeric",
    Datatype.STRING: "string",
    Datatype.TEXT: "text",
    Datatype.DATE: "temporal",
    Datatype.DATETIME: "temporal",
    Datatype.BOOLEAN: "boolean",
}

_TEMPORAL_NAME_TOKENS = frozenset({"date", "time", "datetime", "timestamp"})
_DIGIT_DATE_SIGS = frozenset({"D{8}", "D{6}"})


def fmt_kinds(signature: str) -> tuple[str, ...]:
    """Kind sequence of a rendered format signature: 'A D{2,5} A{0,2}' ->
    ('A', 'D', 'A'). Quantifiers and optionality markers are stripped."""
    out: list[str] = []
    for group in signature.split():
        head = group.split("{", 1)[0].rstrip("?")
        out.append(head if head else group)
    return tuple(out)


def fmt_similarity(sig_a: str, sig_b: str) -> float:
    """Shape similarity of two format signatures: SequenceMatcher ratio over
    kind sequences. Two empty signatures (long-text columns) are similar."""
    ka, kb = fmt_kinds(sig_a), fmt_kinds(sig_b)
    if not ka and not kb:
        return 1.0
    if not ka or not kb:
        return 0.0
    return SequenceMatcher(None, ka, kb).ratio()


def is_code_like(signature: str) -> bool:
    """Compact identifier-shaped format: 1..4 token groups, none open-ended."""
    groups = signature.split()
    return 0 < len(groups) <= 4 and not any(g.startswith("ANY") for g in groups)


def is_timestampish(cp: ColumnProfile) -> bool:
    """Timestamp-dimension evidence (§3.5): temporal dtype, 'date' semantic
    type, or an all-digit YYYYMMDD/YYYYMM column whose name says date/time."""
    if cp.inferred_type in (Datatype.DATE, Datatype.DATETIME):
        return True
    if cp.semantic_type == "date" and cp.semantic_confidence >= 0.8:
        return True
    if cp.inferred_type is Datatype.INTEGER and cp.format_signature in _DIGIT_DATE_SIGS:
        return bool(set(name_tokens(cp.column)) & _TEMPORAL_NAME_TOKENS)
    return False


def fmt_class(cp: ColumnProfile) -> str:
    """Coarse format class used as a (low-weight) context attribute."""
    if cp.inferred_type is Datatype.TEXT:
        return "text"
    if is_timestampish(cp):
        return "temporal"
    kinds = fmt_kinds(cp.format_signature)
    if kinds and all(k == "D" for k in kinds):
        return "numeric"
    if is_code_like(cp.format_signature):
        return "code"
    if len(kinds) > 6:
        return "wordy"
    return "mixed"


# ---------------------------------------------------------------------------
# property synonym clustering
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _ColFacts:
    table: str
    column: str
    tokens: frozenset[str]
    norm: str
    group: str
    timestampish: bool
    code_like: bool
    kinds_len: int
    signature: str
    distinct: int
    semtype: str
    semconf: float
    minhash: tuple[int, ...]


def _facts(cp: ColumnProfile) -> _ColFacts:
    return _ColFacts(
        table=cp.table,
        column=cp.column,
        tokens=frozenset(name_tokens(cp.column)),
        norm=normalize_name(cp.column),
        group=_DTYPE_GROUP.get(cp.inferred_type, "other"),
        timestampish=is_timestampish(cp),
        code_like=is_code_like(cp.format_signature),
        kinds_len=len(fmt_kinds(cp.format_signature)),
        signature=cp.format_signature,
        distinct=cp.distinct_estimate,
        semtype=cp.semantic_type,
        semconf=cp.semantic_confidence,
        minhash=cp.minhash,
    )


def _compatible_group(a: _ColFacts, b: _ColFacts) -> bool:
    return a.group == b.group or (a.timestampish and b.timestampish)


def _synonym_edge(a: _ColFacts, b: _ColFacts, ind_linked: bool) -> bool:
    """The six evidence rules; see module docstring and README for the
    measured estate values that pinned each threshold."""
    jac = token_jaccard(a.tokens, b.tokens)
    # (a) IND link: value containment plus any name-token agreement
    if ind_linked and jac > 0:
        return True
    # (b) shared identity-like semantic type
    if (
        a.semtype and a.semtype == b.semtype
        and a.semtype not in GENERIC_SEMTYPES
        and min(a.semconf, b.semconf) >= SEMTYPE_MIN_CONF
    ):
        return True
    if not _compatible_group(a, b):
        return False
    # temporal columns only merge on strong name evidence: every date column
    # shares values/format with every other date column (open vs close vs event)
    temporal = a.timestampish or b.timestampish
    # (c) strong name evidence alone
    if jac >= STRONG_NAME_JACCARD:
        return True
    fsim = fmt_similarity(a.signature, b.signature)
    if temporal:
        return jac >= MED_NAME_JACCARD and a.timestampish and b.timestampish
    # (d) medium name + format-shape agreement (or identical/both-text shape)
    if jac >= MED_NAME_JACCARD:
        if a.signature and a.signature == b.signature:
            return True
        if a.group == "text" and b.group == "text":
            return True
        if fsim >= FMT_SIM_MED and a.kinds_len >= 2 and b.kinds_len >= 2:
            return True
    # (e) weak name + strict code-like shape agreement; the shared name
    # evidence must include a non-generic token (sharing just "id"/"code"
    # would conflate unrelated identifier namespaces)
    if (
        jac >= WEAK_NAME_JACCARD
        and (a.tokens & b.tokens) - GENERIC_NAME_TOKENS
        and a.code_like and b.code_like
        and a.kinds_len >= 2 and b.kinds_len >= 2
        and fsim >= FMT_SIM_STRICT
    ):
        return True
    # (f) weak name + genuine value-domain overlap on entity-sized domains
    if (
        jac >= VALUE_NAME_JACCARD
        and min(a.distinct, b.distinct) >= VALUE_DOMAIN_MIN_DISTINCT
        and minhash_jaccard(a.minhash, b.minhash) >= MINHASH_OVERLAP
    ):
        return True
    return False


@dataclass
class PropertyClusters:
    """Column -> canonical property name map plus cluster membership."""

    canonical: dict[tuple[str, str], str]                 # (table, col) -> canonical
    members: dict[str, tuple[tuple[str, str], ...]]       # canonical -> sorted cols

    def canonical_of(self, table: str, column: str) -> str:
        return self.canonical.get((table, column), normalize_name(column))

    def members_of(self, canonical: str) -> tuple[tuple[str, str], ...]:
        return self.members.get(canonical, ())


def build_property_clusters(
    profiles: Sequence[TableProfile],
    inds: Sequence[IND] = (),
) -> PropertyClusters:
    """Cluster the column universe into synonym groups (evidence-driven; see
    module docstring) and pick canonical names.

    Canonical-name rule: a non-generic semantic type asserted with conf >= 0.9
    on any member wins (e.g. ``tail_number``); otherwise the shortest (then
    lexicographically first) normalized member name. Distinct clusters that
    normalize to the same canonical get deterministic numeric suffixes.
    """
    cols: dict[tuple[str, str], _ColFacts] = {}
    for tp in profiles:
        for cp in tp.columns.values():
            cols[(tp.table, cp.column)] = _facts(cp)

    ind_pairs: set[frozenset[tuple[str, str]]] = set()
    for ind in inds:
        # same-table INDs are value-range containment coincidences (NO-ENG's
        # 0..8 inside TYPE-ENG's 0..11), not foreign-key synonym evidence.
        if ind.coverage >= IND_EDGE_COVERAGE and ind.lhs_table != ind.rhs_table:
            ind_pairs.add(
                frozenset({(ind.lhs_table, ind.lhs_column), (ind.rhs_table, ind.rhs_column)})
            )

    keys = sorted(cols)
    parent: dict[tuple[str, str], tuple[str, str]] = {k: k for k in keys}

    def find(x: tuple[str, str]) -> tuple[str, str]:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i, ka in enumerate(keys):
        for kb in keys[i + 1:]:
            if ka == kb:
                continue
            linked = frozenset({ka, kb}) in ind_pairs
            if _synonym_edge(cols[ka], cols[kb], linked):
                ra, rb = sorted((find(ka), find(kb)))
                parent[rb] = ra

    groups: dict[tuple[str, str], list[tuple[str, str]]] = defaultdict(list)
    for k in keys:
        groups[find(k)].append(k)

    # canonical names, deterministic over the *set* of members
    canon_of_group: list[tuple[str, tuple[tuple[str, str], ...]]] = []
    for members in groups.values():
        members_t = tuple(sorted(members))
        sem_votes = [
            (cols[m].semconf, cols[m].semtype)
            for m in members_t
            if cols[m].semtype and cols[m].semtype not in GENERIC_SEMTYPES
            and cols[m].semconf >= SEMTYPE_MIN_CONF
        ]
        if sem_votes:
            base = normalize_name(max(sem_votes)[1])
        else:
            base = min((cols[m].norm for m in members_t), key=lambda n: (len(n), n))
        canon_of_group.append((base, members_t))

    canonical: dict[tuple[str, str], str] = {}
    members_map: dict[str, tuple[tuple[str, str], ...]] = {}
    used: Counter[str] = Counter()
    for base, members_t in sorted(canon_of_group, key=lambda x: (x[0], x[1])):
        used[base] += 1
        name = base if used[base] == 1 else f"{base}_{used[base]}"
        members_map[name] = members_t
        for m in members_t:
            canonical[m] = name
    return PropertyClusters(canonical=canonical, members=members_map)


# ---------------------------------------------------------------------------
# candidate attributes & the formal context
# ---------------------------------------------------------------------------


def candidate_attributes(
    cand: TypeCandidate,
    clusters: PropertyClusters,
    profiles_by_table: Mapping[str, TableProfile],
) -> frozenset[str]:
    """Discretized profile-sketch features of one candidate (§3.4.1)."""
    attrs: set[str] = set()
    for table, column in cand.member_columns:
        tp = profiles_by_table.get(table)
        if tp is None or column not in tp.columns:
            continue
        cp = tp.columns[column]
        attrs.add(f"has-prop:{clusters.canonical_of(table, column)}")
        if cp.semantic_type and cp.semantic_confidence >= 0.8:
            attrs.add(f"semtype:{cp.semantic_type}")
        if cp.dimension is not None and not cp.dimension.dimensionless:
            attrs.add(f"dim:{cp.dimension}")
        attrs.add(f"fmt:{fmt_class(cp)}")
        if is_timestampish(cp):
            attrs.add("has-timestamp")
        if cp.inferred_type is Datatype.TEXT or (
            cp.semantic_type == "narrative_text" and cp.semantic_confidence >= 0.8
        ):
            attrs.add("has-narrative-text")
    attrs.add(f"key-arity:{len(cand.key_columns)}")
    return frozenset(attrs)


def intent_hash_of(intent: Iterable[str]) -> str:
    """§3.4.4 concept identity anchor: stable hash of the sorted canonical
    intent. Class URIs derive from this, so re-induction on permuted input
    yields identical URIs (M4 acceptance test)."""
    h = xxhash.xxh3_64()
    for attr in sorted(set(intent)):
        h.update(attr.encode("utf-8"))
        h.update(b"\x1f")
    return f"{h.intdigest():016x}"


@dataclass
class FormalContext:
    """K = (G, M, I) with original/clarified/reduced views.

    ``objects`` maps candidate id -> attribute set (the incidence relation).
    The Galois connection operators are :meth:`prime_objects` (X -> X', common
    attributes) and :meth:`prime_attrs` (Y -> Y', objects having all of Y).
    """

    objects: dict[str, frozenset[str]] = field(default_factory=dict)
    candidates: dict[str, TypeCandidate] = field(default_factory=dict)
    clusters: Optional[PropertyClusters] = None

    # -- incidence -----------------------------------------------------------

    def add_object(self, cid: str, attrs: frozenset[str], cand: Optional[TypeCandidate] = None) -> None:
        if cid in self.objects:
            raise ValueError(f"object {cid!r} already in context")
        self.objects[cid] = frozenset(attrs)
        if cand is not None:
            self.candidates[cid] = cand

    @property
    def all_objects(self) -> frozenset[str]:
        return frozenset(self.objects)

    @property
    def all_attributes(self) -> frozenset[str]:
        out: set[str] = set()
        for attrs in self.objects.values():
            out |= attrs
        return frozenset(out)

    def attr_extent(self, attr: str) -> frozenset[str]:
        return frozenset(g for g, attrs in self.objects.items() if attr in attrs)

    # -- Galois connection ----------------------------------------------------

    def prime_objects(self, objs: Iterable[str]) -> frozenset[str]:
        """X -> X': attributes common to every object in X (X = {} -> M)."""
        objs = list(objs)
        if not objs:
            return self.all_attributes
        it = iter(objs)
        common = set(self.objects[next(it)])
        for g in it:
            common &= self.objects[g]
            if not common:
                break
        return frozenset(common)

    def prime_attrs(self, attrs: Iterable[str]) -> frozenset[str]:
        """Y -> Y': objects having every attribute in Y (Y = {} -> G)."""
        a = frozenset(attrs)
        return frozenset(g for g, gattrs in self.objects.items() if a <= gattrs)

    def closure_objects(self, objs: Iterable[str]) -> frozenset[str]:
        return self.prime_attrs(self.prime_objects(objs))

    def closure_attrs(self, attrs: Iterable[str]) -> frozenset[str]:
        return self.prime_objects(self.prime_attrs(attrs))

    # -- clarification & reduction (§3.4.2) ------------------------------------

    def clarified_attributes(self) -> dict[str, tuple[str, ...]]:
        """Merge attributes with identical extents: representative -> group.
        The representative is the lexicographically smallest group member."""
        by_extent: dict[frozenset[str], list[str]] = defaultdict(list)
        for attr in sorted(self.all_attributes):
            by_extent[self.attr_extent(attr)].append(attr)
        return {group[0]: tuple(group) for group in by_extent.values()}

    def reduced_attributes(self) -> list[str]:
        """Clarified representatives minus reducible ones (extent equal to the
        intersection of strictly-larger representative extents). Reduction
        preserves the concept lattice; intents are re-expanded later."""
        reps = self.clarified_attributes()
        extents = {rep: self.attr_extent(rep) for rep in reps}
        keep: list[str] = []
        for rep, ext in extents.items():
            larger = [e for r, e in extents.items() if r != rep and ext < e]
            if larger:
                inter = frozenset(self.all_objects)
                for e in larger:
                    inter &= e
                if inter == ext:
                    continue  # reducible
            keep.append(rep)
        return sorted(keep)


def build_context(
    candidates: Sequence[TypeCandidate],
    profiles: Sequence[TableProfile],
    inds: Sequence[IND] = (),
    clusters: Optional[PropertyClusters] = None,
) -> FormalContext:
    """Assemble the formal context from candidates + the M3 evidence substrate."""
    if clusters is None:
        clusters = build_property_clusters(profiles, inds)
    by_table = {tp.table: tp for tp in profiles}
    ctx = FormalContext(clusters=clusters)
    for cand in sorted(candidates, key=lambda c: c.cid):
        ctx.add_object(cand.cid, candidate_attributes(cand, clusters, by_table), cand)
    return ctx
