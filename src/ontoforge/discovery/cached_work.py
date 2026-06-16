"""Cached data-engineering work — versioned objects + keyless semantic retrieval.

v2.1 build instructions §5. CLOSED-CORE IP per OntoForge_Build_Instructions.md §18.

Every piece of executed data engineering — a validated join, a synthesized
transform, a materialized result — is kept here as a VERSIONED object with its
provenance and an auto-generated text DESCRIPTION, then made semantically
retrievable two ways:

* :meth:`CachedWorkStore.search` — natural-language search for HUMANS ("show me
  the import/weather joins"), ranked by relevance.
* :meth:`CachedWorkStore.retrieve_for_model` — the RAG bootstrap for a MODEL /
  adjudicator: given the context of a pair under consideration, return "what we
  already KNOW about this kind of join" (the prior validated work), so a later
  LLM adjudicator starts from the system's accumulated knowledge instead of cold.

This is the flywheel: the more the system engineers, the faster the next ask is,
because a previously-validated join is retrieved, not re-derived.

KEYLESS / DETERMINISTIC / ZERO-NETWORK: retrieval is a pure-python hashing
TF-IDF vectorizer (a fixed feature hashing into ``HASH_DIM`` buckets) with cosine
similarity — no embeddings model, no network, no RNG, no wall clock. Identical
inputs yield identical rankings. Real embeddings would later route through the
``aimodels`` router behind the SAME :meth:`search` / :meth:`retrieve_for_model`
interface — the store and its callers do not change when that swap happens.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Optional

__all__ = [
    "HASH_DIM",
    "CachedWorkStore",
    "WorkKind",
    "WorkObject",
    "WorkRetrieval",
    "describe_work",
]

#: hashing-vectorizer dimensionality (feature-hashing bucket count). Fixed so the
#: vector space is stable across runs and processes (no learned vocabulary).
HASH_DIM = 4096

_TOKEN_RE = re.compile(r"[a-z0-9]+")


class WorkKind(str, Enum):
    """The kind of cached data-engineering object."""

    JOIN = "join"               # an executed/validated relationship join
    TRANSFORM = "transform"     # a synthesized column/row transform
    RESULT = "result"           # a materialized query/extract result


@dataclass(frozen=True, slots=True)
class WorkObject:
    """One versioned unit of executed data-engineering work.

    ``key`` is the stable logical identity (e.g. the join shape ``orders.cust_id↔
    customers.cust_id:fk_join``); successive observations of the same ``key`` are
    new VERSIONS (``version`` increments), so the history is auditable. ``payload``
    holds the structured facts (metrics, columns, op spec); ``provenance`` is the
    free-form lineage reference (ledger prov_ref, source ids); ``description`` is
    the auto-generated human/searchable text. ``tenant_id`` scopes the object —
    retrieval is always tenant-filtered (never cross-tenant, §1.5)."""

    key: str
    kind: WorkKind
    description: str
    payload: Mapping[str, Any] = field(default_factory=dict)
    provenance: str = ""
    tenant_id: str = ""
    version: int = 1
    created_ts: float = 0.0

    @property
    def object_id(self) -> str:
        """Stable per-version id: ``<tenant>/<kind>/<key>@v<version>``."""
        t = self.tenant_id or "_"
        return f"{t}/{self.kind.value}/{self.key}@v{self.version}"


@dataclass(frozen=True, slots=True)
class WorkRetrieval:
    """A scored retrieval hit (the ranked search / RAG result)."""

    obj: WorkObject
    score: float


# --------------------------------------------------------------------- describe


def describe_work(kind: WorkKind, payload: Mapping[str, Any]) -> str:
    """Auto-generate a searchable text description from a work payload.

    Deterministic and template-driven (no model). For a JOIN it surfaces the two
    sides, the typed verdict and the executed match/fan-out shape — exactly the
    terms a human or an adjudicator would query by ("import weather join fk")."""
    if kind is WorkKind.JOIN:
        left = payload.get("left", "?")
        right = payload.get("right", "?")
        rel = payload.get("rel_type", "relationship")
        bits = [f"join {left} to {right}", f"typed {rel}"]
        mr = payload.get("match_rate")
        if mr is not None:
            bits.append(f"match rate {float(mr)*100:.0f}%")
        fo = payload.get("fanout_avg")
        if fo is not None:
            bits.append(f"fan-out {float(fo):.1f}")
        if payload.get("validated"):
            bits.append("backward-validated executed join")
        if payload.get("rationale"):
            bits.append(str(payload["rationale"]))
        return "; ".join(bits)
    if kind is WorkKind.TRANSFORM:
        col = payload.get("column", "?")
        kind_s = payload.get("transform", "transform")
        return f"transform {kind_s} on {col}; " + str(payload.get("rationale", ""))
    # RESULT
    q = payload.get("question") or payload.get("title") or "result"
    cols = payload.get("columns") or []
    return f"result for {q}; columns " + ", ".join(str(c) for c in cols)


# --------------------------------------------------------------- vectorizer


def _tokens(text: str) -> list[str]:
    """Lowercase alnum tokens plus character trigrams of each token (so partial
    / morphological matches retrieve — 'weather' finds 'weatherstation')."""
    toks: list[str] = []
    for w in _TOKEN_RE.findall(text.lower()):
        toks.append(w)
        if len(w) > 3:
            toks.extend(w[i:i + 3] for i in range(len(w) - 2))
    return toks


def _hash_bucket(token: str) -> int:
    """Stable feature hash of a token into [0, HASH_DIM) (md5 — content-only)."""
    h = hashlib.md5(token.encode("utf-8")).hexdigest()
    return int(h[:8], 16) % HASH_DIM


def _tf_vector(text: str) -> dict[int, float]:
    """Term-frequency hashing vector (sublinear tf), sparse as bucket -> weight."""
    counts = Counter(_tokens(text))
    if not counts:
        return {}
    vec: dict[int, float] = {}
    for tok, n in counts.items():
        b = _hash_bucket(tok)
        vec[b] = vec.get(b, 0.0) + (1.0 + math.log(n))
    return vec


def _cosine(a: Mapping[int, float], b: Mapping[int, float], idf: Mapping[int, float]) -> float:
    """IDF-weighted cosine similarity between two sparse hashing vectors."""
    if not a or not b:
        return 0.0
    # apply idf weights
    aw = {k: v * idf.get(k, 1.0) for k, v in a.items()}
    bw = {k: v * idf.get(k, 1.0) for k, v in b.items()}
    dot = sum(aw[k] * bw[k] for k in aw.keys() & bw.keys())
    na = math.sqrt(sum(v * v for v in aw.values()))
    nb = math.sqrt(sum(v * v for v in bw.values()))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


# --------------------------------------------------------------------- store


class CachedWorkStore:
    """An in-memory, versioned store of executed DE work with keyless semantic
    retrieval. Deterministic; no network; tenant-scoped on retrieval.

    Not thread-affine to any external handle — it is a pure data structure. A
    SQLite-backed variant can land later behind the same interface; today it is
    process-local (the playground/world owns one)."""

    def __init__(self) -> None:
        # latest version per logical key (insertion is monotone by version)
        self._latest: dict[str, WorkObject] = {}
        # full version history per key (append-only)
        self._history: dict[str, list[WorkObject]] = {}
        # cached tf vectors per object_id
        self._vectors: dict[str, dict[int, float]] = {}
        self._clock = 0

    # ------------------------------------------------------------- mutation

    def record(
        self,
        key: str,
        kind: WorkKind,
        payload: Mapping[str, Any],
        *,
        provenance: str = "",
        tenant_id: str = "",
        description: Optional[str] = None,
    ) -> WorkObject:
        """Record a new VERSION of a unit of work under ``key``.

        Re-recording the same key increments its version (the prior versions stay
        in history). The description is auto-generated unless supplied. Returns
        the stored :class:`WorkObject`."""
        prev = self._latest.get(key)
        version = (prev.version + 1) if prev is not None else 1
        desc = description if description is not None else describe_work(kind, payload)
        # a monotone integer clock keeps created_ts deterministic & ordered
        # without a wall-clock read (zero non-determinism in tests).
        self._clock += 1
        obj = WorkObject(
            key=key,
            kind=kind,
            description=desc,
            payload=dict(payload),
            provenance=provenance,
            tenant_id=tenant_id,
            version=version,
            created_ts=float(self._clock),
        )
        self._latest[key] = obj
        self._history.setdefault(key, []).append(obj)
        self._vectors[obj.object_id] = _tf_vector(_index_text(obj))
        return obj

    def record_join(
        self,
        left: str,
        right: str,
        rel_type: str,
        *,
        match_rate: Optional[float] = None,
        fanout_avg: Optional[float] = None,
        validated: bool = False,
        rationale: str = "",
        provenance: str = "",
        tenant_id: str = "",
        extra: Optional[Mapping[str, Any]] = None,
    ) -> WorkObject:
        """Convenience: record an executed/validated join as cached work.

        The logical key is the order-insensitive join SHAPE + verdict, so the same
        join re-run is a new version (not a duplicate object)."""
        sides = sorted((left, right))
        key = f"{sides[0]}<->{sides[1]}:{rel_type}"
        payload: dict[str, Any] = {
            "left": left, "right": right, "rel_type": rel_type,
            "match_rate": match_rate, "fanout_avg": fanout_avg,
            "validated": validated, "rationale": rationale,
        }
        if extra:
            payload.update(extra)
        return self.record(
            key, WorkKind.JOIN, payload, provenance=provenance, tenant_id=tenant_id
        )

    # ------------------------------------------------------------- read

    def objects(self, *, tenant_id: Optional[str] = None) -> list[WorkObject]:
        """All LATEST-version objects, optionally tenant-filtered, sorted by
        recency then id (deterministic)."""
        out = [
            o for o in self._latest.values()
            if tenant_id is None or o.tenant_id == tenant_id
        ]
        out.sort(key=lambda o: (-o.created_ts, o.object_id))
        return out

    def history(self, key: str) -> list[WorkObject]:
        """All versions recorded under ``key`` (oldest first)."""
        return list(self._history.get(key, ()))

    def _idf(self, corpus: list[WorkObject]) -> dict[int, float]:
        """Smoothed IDF over the (tenant-scoped) corpus' hashing buckets."""
        n = len(corpus)
        if n == 0:
            return {}
        df: Counter[int] = Counter()
        for o in corpus:
            for b in self._vectors.get(o.object_id, {}):
                df[b] += 1
        return {b: math.log((1.0 + n) / (1.0 + d)) + 1.0 for b, d in df.items()}

    # ------------------------------------------------------------- retrieval

    def search(
        self,
        query: str,
        *,
        tenant_id: Optional[str] = None,
        limit: int = 10,
        kind: Optional[WorkKind] = None,
        min_score: float = 0.0,
    ) -> list[WorkRetrieval]:
        """Human-facing natural-language search over the cached work.

        Ranks the latest-version objects (tenant-scoped) by IDF-weighted cosine
        similarity of the query against each object's index text. Deterministic
        tie-break on (−score, object_id)."""
        corpus = [
            o for o in self.objects(tenant_id=tenant_id)
            if kind is None or o.kind is kind
        ]
        if not corpus:
            return []
        idf = self._idf(corpus)
        qv = _tf_vector(query)
        scored: list[WorkRetrieval] = []
        for o in corpus:
            s = _cosine(qv, self._vectors.get(o.object_id, {}), idf)
            if s > min_score:
                scored.append(WorkRetrieval(obj=o, score=round(s, 6)))
        scored.sort(key=lambda r: (-r.score, r.obj.object_id))
        return scored[:limit]

    def retrieve_for_model(
        self,
        context: Mapping[str, Any],
        *,
        tenant_id: Optional[str] = None,
        limit: int = 5,
    ) -> list[WorkRetrieval]:
        """RAG bootstrap for an adjudicator: 'what we already KNOW about this
        kind of join.' Builds a query from the candidate context (left / right /
        rel_type / any free text) and returns the most relevant prior validated
        work, so a later LLM call starts from accumulated knowledge.

        Today this is the same keyless cosine retrieval as :meth:`search`; the
        interface is stable for a real-embedding swap (via ``aimodels``)."""
        terms: list[str] = []
        for fld in ("left", "right", "rel_type", "hypothesis", "question", "text"):
            v = context.get(fld)
            if v:
                terms.append(str(v))
        # bias the bootstrap toward executed joins (the reusable validated work)
        query = " ".join(terms) if terms else ""
        joins = self.search(query, tenant_id=tenant_id, limit=limit, kind=WorkKind.JOIN)
        if joins:
            return joins
        return self.search(query, tenant_id=tenant_id, limit=limit)


def _index_text(obj: WorkObject) -> str:
    """The text actually vectorized for an object: its description plus the key
    and a few high-signal payload fields (so a search on a column name hits)."""
    parts = [obj.description, obj.key, obj.kind.value]
    for fld in ("left", "right", "rel_type", "column", "transform", "question"):
        v = obj.payload.get(fld)
        if v:
            parts.append(str(v))
    return " ".join(parts)
