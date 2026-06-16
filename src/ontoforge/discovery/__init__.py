"""Discovery — semantic retrieval over CACHED data-engineering work (v2.1 §5).

CLOSED-CORE IP per OntoForge_Build_Instructions.md §18.

The v2.1 mandate (§5) wants every executed join / transform / result kept as a
*versioned object* with provenance and an auto-generated description, then made
semantically retrievable for BOTH humans (natural-language search) and MODELS
(RAG bootstrap: "here's what we already know about import↔weather joins"), so the
system gets faster and cheaper the more it works — it never re-derives a join it
has already validated.

:mod:`cached_work` is the keyless foundation of that flywheel:
:class:`~ontoforge.discovery.cached_work.CachedWorkStore` keeps versioned
:class:`~ontoforge.discovery.cached_work.WorkObject`s, auto-describes them, and
serves :meth:`~ontoforge.discovery.cached_work.CachedWorkStore.search` (humans)
and :meth:`~ontoforge.discovery.cached_work.CachedWorkStore.retrieve_for_model`
(adjudicator bootstrap) over a pure-python hashing TF-IDF index. The engine ships
KEYLESS / DETERMINISTIC / ZERO-NETWORK today; real embeddings would later route
through the ``aimodels`` router behind the same interface.
"""

from .cached_work import (
    CachedWorkStore,
    WorkKind,
    WorkObject,
    WorkRetrieval,
    describe_work,
)

__all__ = [
    "CachedWorkStore",
    "WorkKind",
    "WorkObject",
    "WorkRetrieval",
    "describe_work",
]
