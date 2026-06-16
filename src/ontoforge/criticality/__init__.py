"""M-CRIT — lazy, usage-driven criticality recompute (Crew A, §6).

OntoForge induces a typed ontology graph over the user's data. Not every element
of that ontology matters equally at runtime: some classes/relationships are
queried constantly, sit at high-degree hubs, were touched recently, or have many
dependents. This module scores each element's *criticality* and keeps that score
fresh **lazily** — recomputing only the elements that recent usage could have
changed, never the whole graph.

The pieces:

* :mod:`usage` — :class:`UsageEvent` / :class:`UsageLog`: an append-only,
  wall-clock-free record of how the ontology is used (query / join / materialize
  / answer). Each event gets a deterministic integer ``seq``.
* :mod:`recompute` — :class:`CriticalityModel`: consumes only the unseen tail of
  a log (``since(watermark)``), marks touched elements and their neighbors dirty,
  and re-scores ONLY that dirty set via a documented 0.40/0.25/0.20/0.15 blend of
  usage-frequency, centrality, recency, and dependents.
* :mod:`store` — :func:`save_scores` / :func:`load_scores`: byte-stable JSON
  persistence of the score snapshot.

HARD INVARIANTS shared with the whole engine: keyless, offline, and fully
deterministic — no ``time.time()`` / ``datetime.now()`` / ``random`` anywhere;
"time" is the integer usage ``seq``, so fixed input yields byte-identical output.
"""

from __future__ import annotations

from .recompute import (
    CENTRALITY_WEIGHT,
    DEPENDENTS_WEIGHT,
    HALF_LIFE,
    RECENCY_WEIGHT,
    USAGE_WEIGHT,
    CriticalityModel,
)
from .store import SCORES_FORMAT, load_scores, save_scores, to_dict
from .usage import USAGE_KINDS, UsageEvent, UsageLog

__all__ = [
    "CENTRALITY_WEIGHT",
    "DEPENDENTS_WEIGHT",
    "HALF_LIFE",
    "RECENCY_WEIGHT",
    "SCORES_FORMAT",
    "USAGE_KINDS",
    "USAGE_WEIGHT",
    "CriticalityModel",
    "UsageEvent",
    "UsageLog",
    "load_scores",
    "save_scores",
    "to_dict",
]
