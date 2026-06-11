"""Unary inclusion-dependency discovery and join-candidate scoring (whitepaper §3.1).

§3.1 specifies BINDER-style divide-and-conquer over a value index; at v0 fixture
scale a direct value-set-hash intersection over all (table, column) pairs is
exact and simpler (BINDER's partitioning matters when value sets exceed memory —
registered as a scale challenger per §19.1). The n-ary apriori refinement over
validated unary INDs (v1 G3) is deferred with the rest of that scope.

Pipeline
--------
1. Hash every non-null value with the shared canonical `value_key` and xxhash64.
   Integral floats collapse to integer keys, so a BIGINT FK is found inside a
   DOUBLE PK (the cross-engine wart called out in `_values`). 64-bit collisions
   are negligible at fixture scale (~1e-9 at 10^5 distinct values).
2. Keep ordered pairs of type-compatible columns with
   coverage = |values(lhs) ∩ values(rhs)| / |values(lhs)| >= min_coverage (0.95).
   Compatibility groups: {INTEGER, FLOAT}, {STRING, TEXT}, {DATE}, {DATETIME}.
   BOOLEAN and near-constant columns (distinct < min_distinct) are excluded —
   they produce vacuous inclusions, not join evidence.
3. Score join candidates with a fixed convex combination (§3.1: "INDs +
   name/type/cardinality scoring yield join candidates"):

       score = 0.40·coverage + 0.20·name-token Jaccard
             + 0.15·type-match + 0.25·rhs-uniqueness

   rhs-uniqueness because a join target should be key-like; name tokens come
   from snake/camel splitting with light plural stemming so `o_custkey` and
   `c_custkey` share the token `custkey`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from ontoforge.contracts import IND, Datatype

from ._values import columns_of, hash64, is_null, value_key
from .semantic_types import infer_datatype
from .units_infer import split_name_tokens

__all__ = ["discover_inds", "name_token_jaccard"]

# weights of the composite join-candidate score (documented above; tests pin behavior)
_W_COVERAGE = 0.40
_W_NAME = 0.20
_W_TYPE = 0.15
_W_RHS_UNIQ = 0.25

_GROUP: dict[Datatype, str] = {
    Datatype.INTEGER: "numeric",
    Datatype.FLOAT: "numeric",
    Datatype.STRING: "string",
    Datatype.TEXT: "string",
    Datatype.DATE: "date",
    Datatype.DATETIME: "datetime",
}


def _type_match(a: Datatype, b: Datatype) -> float:
    if a not in _GROUP or b not in _GROUP:
        return 0.0
    if a == b:
        return 1.0
    return 0.6 if _GROUP[a] == _GROUP[b] else 0.0


def _name_tokens(name: str) -> frozenset[str]:
    toks = set()
    for t in split_name_tokens(name):
        toks.add(t[:-1] if len(t) > 3 and t.endswith("s") else t)
    return frozenset(toks)


def name_token_jaccard(a: str, b: str) -> float:
    """Jaccard over snake/camel name tokens with light plural stemming."""
    ta, tb = _name_tokens(a), _name_tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


@dataclass(frozen=True, slots=True)
class _ColumnFacts:
    table: str
    column: str
    dtype: Datatype
    hashes: frozenset[int]
    nonnull: int

    @property
    def distinct(self) -> int:
        return len(self.hashes)

    @property
    def uniqueness(self) -> float:
        return self.distinct / self.nonnull if self.nonnull else 0.0


def _column_facts(corpus: Mapping[str, Any]) -> list[_ColumnFacts]:
    facts: list[_ColumnFacts] = []
    for tname in corpus:
        for cname, values in columns_of(corpus[tname]).items():
            nn = [v for v in values if not is_null(v)]
            dtype = infer_datatype(values)
            hashes = frozenset(hash64(value_key(v)) for v in nn)
            facts.append(_ColumnFacts(tname, cname, dtype, hashes, len(nn)))
    return facts


def discover_inds(
    corpus: Mapping[str, Any],
    *,
    min_coverage: float = 0.95,
    min_distinct: int = 2,
) -> list[IND]:
    """§11.2 M3 interface: `discover_inds(corpus) -> [IND, score]`.

    `corpus` maps table name -> pyarrow Table / pandas DataFrame / column mapping.
    Returns contracts.IND sorted by descending score (then names, deterministic).
    """
    facts = [
        f for f in _column_facts(corpus)
        if f.dtype is not Datatype.BOOLEAN and f.distinct >= min_distinct
    ]
    out: list[IND] = []
    for lhs in facts:
        for rhs in facts:
            if lhs is rhs or (lhs.table == rhs.table and lhs.column == rhs.column):
                continue
            tm = _type_match(lhs.dtype, rhs.dtype)
            if tm == 0.0:
                continue
            coverage = len(lhs.hashes & rhs.hashes) / len(lhs.hashes)
            if coverage < min_coverage:
                continue
            score = (
                _W_COVERAGE * coverage
                + _W_NAME * name_token_jaccard(lhs.column, rhs.column)
                + _W_TYPE * tm
                + _W_RHS_UNIQ * rhs.uniqueness
            )
            out.append(IND(
                lhs_table=lhs.table, lhs_column=lhs.column,
                rhs_table=rhs.table, rhs_column=rhs.column,
                coverage=round(coverage, 4), score=round(score, 4),
            ))
    out.sort(key=lambda i: (-i.score, i.lhs_table, i.lhs_column, i.rhs_table, i.rhs_column))
    return out
