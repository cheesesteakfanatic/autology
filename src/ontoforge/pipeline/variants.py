"""Identifier-variant domain discovery (generic lexical-variant unification).

Real estates write the SAME identifier in different lexical forms across
sources: one system stores the bare registry key, another prefixes it with a
constant alpha tag ("4669X" vs "N4669X"; "123" vs "INV-123"). STRATA's IND
discovery sees disjoint value sets and emits no join evidence — the variant
relationship is INSTANCE-level knowledge, so it belongs to the pipeline.

Discovery is purely data-driven (§18.3 anti-hardcoding: no estate-specific
prefixes anywhere):

1. candidate columns are identifier-ish strings (not TEXT, not temporal, not
   numeric) with enough distinct values;
2. each value canonicalizes (alnum + uppercase); a value "carries a prefix"
   when a 1-2 letter alpha run is followed by a digit ("N4669X" -> N + 4669X);
3. two columns in DIFFERENT tables form a variant pair when their residual
   sets overlap strongly AND the plain canonical overlap is materially
   weaker — i.e. prefix-stripping is what unifies them (a plain shared
   domain is ordinary IND territory and stays out);
4. pairs union-find into domains; the domain's dominant explicit prefix is
   the canonical lexical form ("the most explicit spelling wins"), so the
   conformance layer rewrites bare values into it.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Any, Mapping

from ontoforge.contracts import Datatype, TableProfile

__all__ = ["VariantDomain", "canon_id", "discover_variant_domains", "split_prefix"]

#: residual overlap (containment over the smaller set) to accept a pair
VARIANT_OVERLAP = 0.6
#: prefix-stripping must beat the plain overlap by this factor
VARIANT_GAIN = 2.0
#: minimum share of explicitly-prefixed values on the prefixed side
PREFIX_SHARE = 0.3
#: minimum distinct values per column
MIN_DISTINCT = 10
#: an identifier domain must IDENTIFY something: at least one column of every
#: accepted pair is near-unique in its table (kills code-list collisions like
#: 3-digit county codes overlapping 3-digit staff-id residuals)
IDENTITY_UNIQUENESS = 0.8

_CANON_RE = re.compile(r"[^A-Za-z0-9]")
_PREFIX_RE = re.compile(r"^([A-Z]{1,2})(\d[A-Z0-9]*)$")


def canon_id(v: Any) -> str:
    return _CANON_RE.sub("", str(v)).upper()


def split_prefix(canon: str) -> tuple[str, str]:
    """'N4669X' -> ('N', '4669X'); '4669X' -> ('', '4669X')."""
    m = _PREFIX_RE.match(canon)
    if m is not None:
        return m.group(1), m.group(2)
    return "", canon


@dataclass(frozen=True)
class VariantDomain:
    """One cross-table identifier domain unified by prefix-stripping."""

    columns: tuple[tuple[str, str], ...]   # (table, column), >= 2 tables
    prefix: str                            # dominant explicit prefix ("N")
    residuals: frozenset[str]              # union of canonical residuals

    @property
    def tables(self) -> tuple[str, ...]:
        return tuple(sorted({t for t, _ in self.columns}))


def _column_facts(values: list[str]) -> tuple[set[str], set[str], Counter, int]:
    plain: set[str] = set()
    resid: set[str] = set()
    prefixes: Counter = Counter()
    n_prefixed = 0
    for raw in values:
        c = canon_id(raw)
        if not c:
            continue
        plain.add(c)
        p, r = split_prefix(c)
        resid.add(r)
        if p:
            prefixes[p] += 1
            n_prefixed += 1
    return plain, resid, prefixes, n_prefixed


def _identifier_ish(cp) -> bool:
    if cp is None:
        return False
    if cp.inferred_type is not Datatype.STRING:
        return False
    if cp.distinct_estimate < MIN_DISTINCT:
        return False
    name = cp.column.lower()
    if any(t in name for t in ("date", "time", "narrative", "description")):
        return False
    return True


def discover_variant_domains(
    estate: dict[str, Any], profiles_by_table: Mapping[str, TableProfile]
) -> list[VariantDomain]:
    facts: dict[tuple[str, str], tuple[set, set, Counter, int, int]] = {}
    for table, df in sorted(estate["tables"].items()):
        tp = profiles_by_table.get(table)
        if tp is None:
            continue
        for column in df.columns:
            cp = tp.columns.get(str(column))
            if not _identifier_ish(cp):
                continue
            values = [v for v in df[str(column)].tolist() if str(v).strip()]
            plain, resid, prefixes, n_prefixed = _column_facts(values)
            if len(resid) >= MIN_DISTINCT:
                facts[(table, str(column))] = (plain, resid, prefixes, n_prefixed, len(values))

    cols = sorted(facts)
    parent: dict[tuple[str, str], tuple[str, str]] = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            lo, hi = sorted((ra, rb))
            parent[hi] = lo

    def _uniqueness(col: tuple[str, str]) -> float:
        tp = profiles_by_table.get(col[0])
        cp = tp.columns.get(col[1]) if tp is not None else None
        return cp.uniqueness if cp is not None else 0.0

    paired: set[tuple[str, str]] = set()
    for i, a in enumerate(cols):
        for b in cols[i + 1:]:
            if a[0] == b[0]:
                continue
            if max(_uniqueness(a), _uniqueness(b)) < IDENTITY_UNIQUENESS:
                continue
            pa, ra, prefa, npa, na = facts[a]
            pb, rb, prefb, npb, nb = facts[b]
            # at least one side writes the prefix explicitly
            share_a = npa / na if na else 0.0
            share_b = npb / nb if nb else 0.0
            if max(share_a, share_b) < PREFIX_SHARE:
                continue
            # variant evidence means the SAME identifier written with and
            # without its tag: either one side is mostly bare, or both sides
            # use the SAME dominant prefix (mixed forms). Two fully-prefixed
            # columns with DIFFERENT tags ('C0042' vs 'P0042') are different
            # id schemes whose digit residuals collide — never a domain.
            dom_a = max(prefa, key=lambda p: (prefa[p], p)) if prefa else ""
            dom_b = max(prefb, key=lambda p: (prefb[p], p)) if prefb else ""
            if min(share_a, share_b) >= 0.5 and dom_a != dom_b:
                continue
            denom = min(len(ra), len(rb))
            resid_overlap = len(ra & rb) / denom if denom else 0.0
            plain_overlap = len(pa & pb) / min(len(pa), len(pb)) if pa and pb else 0.0
            if resid_overlap >= VARIANT_OVERLAP and resid_overlap >= VARIANT_GAIN * max(
                plain_overlap, 1e-9
            ):
                union(a, b)
                paired.add(a)
                paired.add(b)

    comps: dict[tuple[str, str], set[tuple[str, str]]] = {}
    for c in paired:
        comps.setdefault(find(c), set()).add(c)

    out: list[VariantDomain] = []
    for members in comps.values():
        if len({t for t, _ in members}) < 2:
            continue
        prefixes: Counter = Counter()
        residuals: set[str] = set()
        for m in members:
            _, resid, prefs, _, _ = facts[m]
            prefixes.update(prefs)
            residuals |= resid
        if not prefixes:
            continue
        prefix = max(prefixes, key=lambda p: (prefixes[p], p))
        out.append(
            VariantDomain(
                columns=tuple(sorted(members)),
                prefix=prefix,
                residuals=frozenset(residuals),
            )
        )
    return sorted(out, key=lambda d: d.columns)
