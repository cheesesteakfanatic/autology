"""Lazy, usage-driven criticality recompute (Crew A, §6).

:class:`CriticalityModel` maintains a per-element *criticality score* over the
induced ontology relationship graph and updates it **lazily**: each
:meth:`CriticalityModel.update` consumes only the *new* usage events
(``log.since(watermark)``) and recomputes scores for ONLY the elements those
events could have changed — the touched elements plus their structural
neighbors. The whole graph is never re-scored. This keeps recompute cost
proportional to recent activity, not to ontology size, which is the entire
point of a "lazy" criticality layer.

The score is a documented, deterministic blend of four normalized signals whose
weights sum to 1.0 (see the module constants below):

    score = USAGE_WEIGHT      * usage_freq_norm
          + CENTRALITY_WEIGHT * centrality_norm
          + RECENCY_WEIGHT    * recency
          + DEPENDENTS_WEIGHT * dependents_norm

HARD INVARIANTS: keyless, offline, fully deterministic. "Recency" decays over
the integer ``seq`` axis supplied by :class:`~ontoforge.criticality.usage.UsageLog`
(half-life :data:`HALF_LIFE` seqs) — there is NO wall-clock. Every division is
guarded against zero so an empty or single-node graph never raises.
"""

from __future__ import annotations

from .usage import UsageLog

# --- Score blend weights (MUST sum to 1.0). Documented as module constants so
# downstream crews and tests can assert the contract. -------------------------
USAGE_WEIGHT: float = 0.40
CENTRALITY_WEIGHT: float = 0.25
RECENCY_WEIGHT: float = 0.20
DEPENDENTS_WEIGHT: float = 0.15

#: Recency half-life measured in usage ``seq`` units: an element last touched
#: HALF_LIFE seqs before the current max_seq contributes recency 0.5.
HALF_LIFE: float = 50.0


def _blend_weights_sum() -> float:
    return USAGE_WEIGHT + CENTRALITY_WEIGHT + RECENCY_WEIGHT + DEPENDENTS_WEIGHT


# Fail at import time if someone edits a weight and breaks the invariant.
assert abs(_blend_weights_sum() - 1.0) < 1e-9, "criticality blend weights must sum to 1.0"


class CriticalityModel:
    """Lazy criticality scorer over an induced ontology graph.

    Parameters
    ----------
    adjacency:
        Maps an ``element_uri`` to the list of its structurally-linked neighbor
        uris. Treated as an UNDIRECTED ontology relationship graph: degree is
        the neighbor count and a neighbor gaining usage marks this element dirty
        (a neighbor's usage shifts the centrality-weighted blend).
    dependents:
        Optional map from an ``element_uri`` to the elements that depend on it.
        Used for the ``dependents_norm`` signal. Defaults to empty.
    """

    def __init__(
        self,
        adjacency: dict[str, list[str]],
        dependents: dict[str, list[str]] | None = None,
    ) -> None:
        self._adjacency: dict[str, list[str]] = adjacency
        self._dependents: dict[str, list[str]] = dependents if dependents is not None else {}

        self._scores: dict[str, float] = {}
        self._watermark: int = 0
        self._last_touch_seq: dict[str, int] = {}
        self._usage_count: dict[str, float] = {}
        self._last_recomputed: set[str] = set()

        # Cached graph maxima for normalization. Degree / dependents are static
        # properties of the supplied graph, so we compute them once.
        self._max_degree: int = max((len(v) for v in self._adjacency.values()), default=0)
        self._max_dependents: int = max((len(v) for v in self._dependents.values()), default=0)

    # -- introspection -------------------------------------------------------
    @property
    def watermark(self) -> int:
        """Highest usage ``seq`` already folded into the model."""
        return self._watermark

    @property
    def nodes(self) -> set[str]:
        """All uris known to the graph (adjacency keys plus dependents keys)."""
        return set(self._adjacency) | set(self._dependents)

    def _degree(self, uri: str) -> int:
        return len(self._adjacency.get(uri, ()))

    # -- the lazy update -----------------------------------------------------
    def update(self, log: UsageLog) -> set[str]:
        """Fold the *unseen* tail of ``log`` into the model, lazily.

        Reads only ``log.since(self._watermark)``. For each new event it
        accumulates weighted usage on the event's element and marks that element
        AND its adjacency-neighbors dirty. Scores are recomputed for ONLY the
        dirty set; ``last_recomputed`` is set to exactly that set; the watermark
        advances to ``log.max_seq``. Returns the recomputed set.
        """
        new_events = log.since(self._watermark)
        if not new_events:
            # Nothing new: recompute nothing, leave scores/watermark untouched.
            self._last_recomputed = set()
            return self._last_recomputed

        dirty: set[str] = set()
        for event in new_events:
            uri = event.element_uri
            self._usage_count[uri] = self._usage_count.get(uri, 0.0) + event.weight
            self._last_touch_seq[uri] = event.seq
            dirty.add(uri)
            # A neighbor gaining usage changes this element's centrality-weighted
            # standing, so neighbors are dirty too.
            for neighbor in self._adjacency.get(uri, ()):
                dirty.add(neighbor)

        # Advance the watermark BEFORE scoring so recency uses the final max_seq.
        self._watermark = log.max_seq

        # Maxima for the usage signal are global (a node's freq is relative to
        # the busiest node), so they are read across all accumulated usage.
        max_usage = max(self._usage_count.values(), default=0.0)

        for uri in dirty:
            self._scores[uri] = self._compute_score(uri, max_usage)

        self._last_recomputed = dirty
        return dirty

    def _compute_score(self, uri: str, max_usage: float) -> float:
        # usage_freq_norm — relative to the busiest element (guard /0).
        usage = self._usage_count.get(uri, 0.0)
        usage_freq_norm = (usage / max_usage) if max_usage > 0 else 0.0

        # centrality_norm — degree relative to the max-degree node (guard /0).
        centrality_norm = (self._degree(uri) / self._max_degree) if self._max_degree > 0 else 0.0

        # dependents_norm — dependent count relative to the most-depended-on
        # element (guard /0).
        dep_count = len(self._dependents.get(uri, ()))
        dependents_norm = (dep_count / self._max_dependents) if self._max_dependents > 0 else 0.0

        # recency — exponential decay over integer seq distance from max_seq.
        # An element never touched has no last_touch_seq and gets recency 0.0.
        if uri in self._last_touch_seq:
            age = self._watermark - self._last_touch_seq[uri]
            recency = 0.5 ** (age / HALF_LIFE)
        else:
            recency = 0.0

        return (
            USAGE_WEIGHT * usage_freq_norm
            + CENTRALITY_WEIGHT * centrality_norm
            + RECENCY_WEIGHT * recency
            + DEPENDENTS_WEIGHT * dependents_norm
        )

    # -- query surface -------------------------------------------------------
    def score(self, uri: str) -> float:
        """Current criticality score for ``uri`` (0.0 if never touched)."""
        return self._scores.get(uri, 0.0)

    def top_k(self, n: int) -> list[tuple[str, float]]:
        """Top ``n`` ``(uri, score)`` pairs, score desc then uri asc (stable)."""
        ranked = sorted(self._scores.items(), key=lambda kv: (-kv[1], kv[0]))
        if n < 0:
            n = 0
        return ranked[:n]

    def is_dirty(self, uri: str) -> bool:
        """Whether ``uri`` was recomputed in the most recent :meth:`update`."""
        return uri in self._last_recomputed

    def last_recomputed(self) -> set[str]:
        """Copy of the uris recomputed in the most recent :meth:`update`."""
        return set(self._last_recomputed)

    @property
    def scores(self) -> dict[str, float]:
        """Live mapping of every scored uri to its score (treat as read-only)."""
        return self._scores
