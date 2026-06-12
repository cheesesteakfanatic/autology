"""Generic entity resolution over induced classes (M5, estate-agnostic).

The M5 cascade's feature paths are keyed by entity kind: "aircraft" expects
registry-shaped fields, while "operator" is the NAME path — string identity
with token/acronym/fused-prefix alias evidence and exact-name node folding.
That name path IS the generic one: any induced class whose identity domain is
name-like resolves through it unchanged (no edits to er/).

Identity domains, per materialization plan:

- **hub** plans span >= 2 tables by construction (the IND-hub member columns);
- **table** plans contribute their candidate key column plus every column the
  STRATA property-cluster map (name-token + IND + value-overlap synonym
  evidence) places in the same cluster in ANOTHER table, plus IND lhs columns
  pointing into the key;
- **alternate identity**: a unique (uniqueness >= 0.98) non-key column of a
  table plan whose cluster spans other tables — e.g. a supplier_name carried
  denormalized on a products table — also identifies the class.

Code-like domains (stable format-signature identifiers: customer ids, state
codes) do NOT run the fuzzy cascade: exact normalized equality is their
identity, which the materializer's FK index already implements. Single-table
classes get exact-key dedupe only, by the same rule.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from ontoforge.contracts import Datatype
from ontoforge.er import CascadeConfig, ERCascade, EntityMention
from ontoforge.er.records import norm_name, norm_text
from ontoforge.strata import StrataResult
from ontoforge.strata.context import is_code_like

from .discover import slugify, table_row_keys
from .induce import InducedArtifacts
from .mapping import ClassPlan, row_entity_uri
from .variants import VariantDomain, canon_id, discover_variant_domains, split_prefix

__all__ = ["ClassResolution", "IdentityDomain", "identity_domains", "resolve_generic"]

#: IND coverage floor for treating an lhs column as part of a key's domain
DOMAIN_IND_COVERAGE = 0.95
#: uniqueness floor for alternate identity columns
ALT_KEY_UNIQUENESS = 0.98


@dataclass(frozen=True)
class IdentityDomain:
    """A shared identity value-domain of one induced class."""

    class_uri: str
    class_name: str
    plan_cid: str
    identity_columns: tuple[tuple[str, str], ...]   # (table, column), >= 2 tables
    home_column: Optional[tuple[str, str]]          # the class's own identity column
    name_like: bool
    variant: Optional[VariantDomain] = None         # identifier-variant evidence

    @property
    def tables(self) -> tuple[str, ...]:
        return tuple(sorted({t for t, _ in self.identity_columns}))


@dataclass
class ClassResolution:
    """Resolution outcome for one class's cross-table identity domain."""

    domain: IdentityDomain
    value_to_uri: dict[str, str] = field(default_factory=dict)  # norm(v) -> entity uri
    clusters: dict[str, list[str]] = field(default_factory=dict)  # uri -> sorted mention ids
    mention_to_uri: dict[str, str] = field(default_factory=dict)
    method: str = "er-cascade"                       # "er-cascade" | "exact-variant"
    variant_prefix: Optional[str] = None

    def norm(self, raw) -> str:
        """The identity normalizer link resolution must use for this class."""
        if self.method == "exact-variant":
            return split_prefix(canon_id(raw))[1]
        return norm_name(raw)


def _cluster_mates(artifacts: InducedArtifacts | StrataResult, table: str, column: str):
    ctx = (artifacts.strata if isinstance(artifacts, InducedArtifacts) else artifacts).context
    clusters = ctx.clusters
    if clusters is None:
        return ()
    canonical = clusters.canonical_of(table, column)
    return tuple(
        (t, c) for t, c in clusters.members_of(canonical) if t != table
    )


def _name_like_column(cp) -> bool:
    """A column whose VALUES are proper names: plain strings (not long-form
    text, not numbers, not dates) that are not stable code-like identifiers."""
    if cp is None:
        return False
    if cp.inferred_type is not Datatype.STRING:
        return False
    if is_code_like(cp.format_signature):
        return False
    samples = [s for s in cp.sample_values if s and str(s).strip()]
    if not samples:
        return False
    # names carry letters; identifier-ish or numeric-ish samples disqualify
    alpha = sum(1 for s in samples if any(ch.isalpha() for ch in str(s)))
    return alpha >= 0.9 * len(samples)


def _is_name_like(columns, profiles_by_table) -> bool:
    """Every column of the domain must be name-like for the fuzzy cascade."""
    for t, c in columns:
        tp = profiles_by_table.get(t)
        cp = tp.columns.get(c) if tp is not None else None
        if not _name_like_column(cp):
            return False
    return True


def identity_domains(
    artifacts: InducedArtifacts,
    plans: list[ClassPlan],
    estate: Optional[dict[str, Any]] = None,
) -> list[IdentityDomain]:
    """Cross-table identity domains, one per (plan, identity column)."""
    profiles = artifacts.profiles_by_table
    out: list[IdentityDomain] = []
    seen: set[tuple[str, tuple[tuple[str, str], ...]]] = set()

    def add(
        plan: ClassPlan,
        cols: set[tuple[str, str]],
        home: Optional[tuple[str, str]],
        variant: Optional[VariantDomain] = None,
    ) -> None:
        ordered = tuple(sorted(cols))
        if len({t for t, _ in ordered}) < 2:
            return
        key = (plan.class_uri, ordered)
        if key in seen:
            return
        seen.add(key)
        out.append(
            IdentityDomain(
                class_uri=plan.class_uri,
                class_name=plan.class_name,
                plan_cid=plan.cid,
                identity_columns=ordered,
                home_column=home,
                name_like=variant is None and _is_name_like(ordered, profiles),
                variant=variant,
            )
        )

    for plan in plans:
        if plan.kind == "hub":
            add(plan, set(plan.member_columns), plan.identity_column)
            continue
        if plan.table is None:
            continue
        # primary key domain (single-column keys only: a composite key has no
        # single shared value-domain to resolve over)
        if plan.kind == "table" and len(plan.key_columns) == 1:
            key_col = (plan.table, plan.key_columns[0])
            cols = {key_col, *_cluster_mates(artifacts, *key_col)}
            for ind in artifacts.inds:
                if (
                    (ind.rhs_table, ind.rhs_column) == key_col
                    and ind.coverage >= DOMAIN_IND_COVERAGE
                    and ind.lhs_table != plan.table
                ):
                    cols.add((ind.lhs_table, ind.lhs_column))
            add(plan, cols, key_col)
        # alternate identity columns
        if plan.kind == "table":
            tp = profiles.get(plan.table)
            if tp is None:
                continue
            for _, c in plan.member_columns:
                if c in plan.key_columns:
                    continue
                cp = tp.columns.get(c)
                if cp is None or cp.uniqueness < ALT_KEY_UNIQUENESS:
                    continue
                if not _name_like_column(cp):
                    continue  # alternate identity must be a NAME, never text/codes
                mates = _cluster_mates(artifacts, plan.table, c)
                if mates:
                    add(plan, {(plan.table, c), *mates}, (plan.table, c))

    # identifier-variant domains (instance-level lexical unification): the
    # HOME class is the one whose table covers the largest share of the
    # domain's residual values (the master list); other columns reference it.
    if estate is not None:
        table_plans = {p.table: p for p in plans if p.kind == "table" and p.table is not None}
        for vd in discover_variant_domains(estate, profiles):
            best: Optional[tuple[int, str, tuple[str, str]]] = None
            for t, c in vd.columns:
                plan = table_plans.get(t)
                if plan is None or (t, c) not in plan.member_columns:
                    continue
                distinct = 0
                tp = profiles.get(t)
                if tp is not None and c in tp.columns:
                    distinct = tp.columns[c].distinct_estimate
                cand = (distinct, plan.class_uri, (t, c))
                if best is None or cand > best:
                    best = cand
            if best is None:
                continue
            home = best[2]
            plan = table_plans[home[0]]
            add(plan, set(vd.columns), home, variant=vd)
    return out


def _mentions_for(
    estate: dict[str, Any], domain: IdentityDomain
) -> list[EntityMention]:
    meta = estate["metadata"]["tables"]
    mentions: list[EntityMention] = []
    for t, c in domain.identity_columns:
        df = estate["tables"][t]
        if c not in df.columns:
            continue
        row_keys = table_row_keys(df, meta[t]["key_columns"])
        for rk, raw in zip(row_keys, df[c].tolist()):
            nn = norm_name(raw)
            if not nn:
                continue
            mentions.append(
                EntityMention(
                    mention_id=f"gen/{slugify(domain.class_name)}/{t}/{rk}#{slugify(c)}",
                    source_id=meta[t]["source_id"],
                    table=t,
                    row_key=rk,
                    entity_kind="operator",  # the M5 NAME feature path
                    fields={"name": norm_text(raw), "name_norm": nn, "tail": ""},
                )
            )
    return mentions


def _resolve_variant(
    estate: dict[str, Any], domain: IdentityDomain
) -> Optional[ClassResolution]:
    """Exact identifier-variant resolution: residual -> home-class row entity.

    Duplicate residuals (e.g. registry key reuse over time) pick the greatest
    row key — deterministic; the temporal disambiguation a date-aware matcher
    would add is documented future work."""
    vd = domain.variant
    home = domain.home_column
    if vd is None or home is None:
        return None
    t, c = home
    df = estate["tables"].get(t)
    if df is None or c not in df.columns:
        return None
    meta = estate["metadata"]["tables"]
    row_keys = table_row_keys(df, meta[t]["key_columns"])
    best_rk: dict[str, str] = {}
    for rk, raw in zip(row_keys, df[c].tolist()):
        resid = split_prefix(canon_id(raw))[1]
        if not resid:
            continue
        if resid not in best_rk or rk > best_rk[resid]:
            best_rk[resid] = rk
    if not best_rk:
        return None
    res = ClassResolution(domain=domain, method="exact-variant", variant_prefix=vd.prefix)
    for resid, rk in best_rk.items():
        res.value_to_uri[resid] = row_entity_uri(domain.class_name, rk)
    return res


def resolve_generic(
    estate: dict[str, Any],
    artifacts: InducedArtifacts,
    plans: list[ClassPlan],
    ledger: Any = None,
) -> dict[str, ClassResolution]:
    """Resolve every cross-table identity domain: the M5 cascade for name-like
    domains, exact residual identity for identifier-variant domains.

    Returns class_uri -> ClassResolution (first resolvable domain per class
    wins; code-like and single-table identities need no fuzzy resolution).
    """
    out: dict[str, ClassResolution] = {}
    for domain in identity_domains(artifacts, plans, estate):
        if domain.class_uri in out:
            continue
        if domain.variant is not None:
            res = _resolve_variant(estate, domain)
            if res is not None:
                out[domain.class_uri] = res
            continue
        if not domain.name_like:
            continue
        mentions = _mentions_for(estate, domain)
        if len(mentions) < 2:
            continue
        cascade = ERCascade(CascadeConfig(kinds=("operator",)), ledger=ledger)
        result = cascade.run(mentions)
        res = ClassResolution(domain=domain, mention_to_uri=dict(result.mention_to_uri))
        slug = slugify(domain.class_name)
        rename: dict[str, str] = {}
        for kind_clusters in result.clusters.values():
            for uri, cluster in kind_clusters.items():
                rename[uri] = f"ent://{slug}/{uri.rsplit('/', 1)[-1]}"
                res.clusters[rename[uri]] = sorted(cluster.mention_ids)
        for m in mentions:
            uri = result.mention_to_uri.get(m.mention_id)
            if uri is not None:
                res.value_to_uri[m.fields["name_norm"]] = rename.get(uri, uri)
                res.mention_to_uri[m.mention_id] = rename.get(uri, uri)
        out[domain.class_uri] = res
    return out
