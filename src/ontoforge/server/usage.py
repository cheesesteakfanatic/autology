"""Process-local usage → criticality bridge for the active world (§6).

This is the BACKEND seam that feeds the keyless, deterministic criticality
module (:mod:`ontoforge.criticality`) from the live server. It owns, per active
world, an append-only :class:`~ontoforge.criticality.usage.UsageLog` and a lazy
:class:`~ontoforge.criticality.recompute.CriticalityModel` whose adjacency graph
is induced from the world's ontology + connection atlas — exactly the structures
``/api/ontology`` and ``/api/atlas`` already expose:

* **nodes** = ontology class URIs (every class the world knows);
* **edges** = the typed relationships — the ontology's link properties
  (``source --link--> range_class``) UNIONED with the atlas arcs
  (``src_class <-> dst_class``). Treated undirected, as the criticality model's
  adjacency is undirected.
* **dependents** = the reverse of every directed link/arc (the elements that
  depend on a given target), feeding the ``dependents_norm`` signal.

The graph is cached by a world key (the active project path + active world name)
and rebuilt lazily on world change, so a playground build that flips the active
world transparently re-induces the criticality graph against the new ontology.

HARD INVARIANTS preserved end to end: keyless (no key at import or call),
offline (pure in-process), fully deterministic (the UsageLog assigns its own
integer seqs — there is NO wall-clock here). Every entry point is DEFENSIVE: a
world with no ontology yet yields an empty graph and ``top_criticality`` returns
``[]`` rather than raising, so wiring this into request handlers can never turn
a working endpoint into a 500.
"""

from __future__ import annotations

import threading
from typing import Any, Optional

from ontoforge.contracts.oqir import (
    Aggregate,
    AsOf,
    OQIRTerm,
    Select,
    TextJoin,
    TopK,
    Traverse,
)
from ontoforge.criticality import CriticalityModel, UsageLog

#: Default top-N when a caller does not specify one (matches the CLI default).
DEFAULT_TOP_N = 10


class _WorldCriticality:
    """One world's usage log + lazily-built criticality model + labels."""

    __slots__ = ("key", "adjacency", "dependents", "labels", "log", "model")

    def __init__(
        self,
        key: str,
        adjacency: dict[str, list[str]],
        dependents: dict[str, list[str]],
        labels: dict[str, str],
    ) -> None:
        self.key = key
        self.adjacency = adjacency
        self.dependents = dependents
        self.labels = labels
        self.log = UsageLog()
        self.model = CriticalityModel(adjacency, dependents=dependents)


# Module-level state, guarded by a lock so concurrent request handlers folding
# usage into the SAME world never race the log/model. Keyed by world so a world
# switch drops the prior model cleanly.
_LOCK = threading.RLock()
_CURRENT: Optional[_WorldCriticality] = None


# --------------------------------------------------------------- graph builder


def _world_key(world: Any) -> str:
    """Stable identity for the active world (path + world name)."""
    project = getattr(world, "active_project", None)
    name = getattr(world, "active_world", "")
    return f"{project}::{name}"


def _build_graph(world: Any) -> tuple[dict[str, list[str]], dict[str, list[str]], dict[str, str]]:
    """Induce (adjacency, dependents, labels) from the active world.

    Nodes are ontology class URIs; edges are the union of the ontology's link
    properties and the persisted atlas arcs (the typed relationships). Fully
    defensive: any missing piece (no ontology, no atlas) simply contributes
    nothing — the graph is always well-formed (possibly empty)."""
    adjacency: dict[str, set[str]] = {}
    dependents: dict[str, set[str]] = {}
    labels: dict[str, str] = {}

    def node(uri: str) -> None:
        adjacency.setdefault(uri, set())
        dependents.setdefault(uri, set())

    def edge(src: str, dst: str) -> None:
        # undirected adjacency; directed dependents (dst depends-on relationship
        # to src — src is depended-on BY dst, so dst lands in deps[src]).
        node(src)
        node(dst)
        adjacency[src].add(dst)
        adjacency[dst].add(src)
        dependents[src].add(dst)

    # 1) ontology: every class is a node; every link property is a directed edge.
    onto = None
    try:
        onto = world.ontology
    except Exception:
        onto = None
    if onto is not None:
        try:
            for uri, cls in onto.classes.items():
                node(uri)
                name = getattr(cls, "name", None)
                if name:
                    labels[uri] = str(name)
            for cls, prop in onto.link_properties():
                tgt = getattr(prop, "range_class", None)
                if tgt:
                    edge(cls.uri, tgt)
        except Exception:
            # an exotic ontology shape never breaks the bridge
            pass

    # 2) atlas: every tiered arc is an additional (typed-relationship) edge.
    atlas = None
    try:
        atlas = world.read_atlas()
    except Exception:
        atlas = None
    if atlas:
        for lk in atlas.get("links", []) or []:
            src = lk.get("src_class")
            dst = lk.get("dst_class")
            if src and dst:
                edge(str(src), str(dst))
        # component labels are a nice human fallback for class uris the ontology
        # did not name (never overrides a real ontology class name).
        for comp in atlas.get("components", []) or []:
            label = comp.get("label")
            for uri in comp.get("class_uris", []) or []:
                if label and uri not in labels:
                    labels[str(uri)] = str(label)

    adj = {uri: sorted(neigh) for uri, neigh in adjacency.items()}
    dep = {uri: sorted(d) for uri, d in dependents.items() if d}
    return adj, dep, labels


def _active(world: Any) -> _WorldCriticality:
    """Return the criticality state for the active world, (re)building on change.

    Caller MUST hold ``_LOCK``."""
    global _CURRENT
    key = _world_key(world)
    if _CURRENT is None or _CURRENT.key != key:
        adjacency, dependents, labels = _build_graph(world)
        _CURRENT = _WorldCriticality(key, adjacency, dependents, labels)
    return _CURRENT


# ----------------------------------------------------------- public bridge API


def reset() -> None:
    """Drop the cached world model (next call rebuilds). For tests / reload."""
    global _CURRENT
    with _LOCK:
        _CURRENT = None


def label_for(world: Any, uri: str) -> str:
    """Human label for a class uri (ontology name → atlas component → uri tail)."""
    with _LOCK:
        state = _active(world)
        if uri in state.labels:
            return state.labels[uri]
    return uri.rsplit("/", 1)[-1]


def record_usage(world: Any, element_uris: list[str], kind: str) -> None:
    """Append one usage event per element then fold the tail into the model.

    Only elements that are KNOWN nodes of the current graph are recorded — a
    derived uri that is not a class node (e.g. a property uri) is ignored rather
    than polluting the graph with phantom nodes. Fully defensive: never raises
    into the calling request handler.
    """
    if not element_uris:
        return
    try:
        with _LOCK:
            state = _active(world)
            known = state.model.nodes
            touched = False
            for uri in element_uris:
                if uri in known:
                    state.log.append(uri, kind)
                    touched = True
            if touched:
                state.model.update(state.log)
    except Exception:
        # usage recording is a pure side effect — swallow everything so it can
        # never alter an endpoint's response or status.
        return


def scores_by_class(world: Any) -> dict[str, float]:
    """Every class uri → its criticality score, with a STRUCTURAL fallback.

    The lazy model only assigns a usage/recency-weighted score to uris that
    actual usage has touched; a fresh / never-queried world therefore scores
    every class 0. To rank FIELDS meaningfully before any usage accrues we fall
    back to a normalized graph DEGREE (the same centrality signal the model
    blends), so a highly-connected class still ranks above an isolated one.

    Returns ``{}`` for an unbuilt world (no graph) or any failure — never raises.
    The mapping is a pure function of the ontology/atlas + accrued usage, so it
    is deterministic for a given world state.
    """
    try:
        with _LOCK:
            state = _active(world)
            model = state.model
            adjacency = state.adjacency
            max_deg = max((len(n) for n in adjacency.values()), default=0)
            live = model.scores  # treat as read-only
            out: dict[str, float] = {}
            for uri in model.nodes:
                s = float(live.get(uri, 0.0))
                if s <= 0.0 and max_deg > 0:
                    # structural prior: normalized degree, discounted so any real
                    # usage score always dominates a purely-structural one.
                    s = 0.25 * (len(adjacency.get(uri, ())) / max_deg)
                out[uri] = s
            return out
    except Exception:
        return {}


def top_criticality(world: Any, n: int = DEFAULT_TOP_N) -> list[dict[str, Any]]:
    """Top-``n`` critical elements as ``[{uri, label, score}]`` (score desc).

    Returns ``[]`` for an unbuilt world (no ontology / empty graph) or any
    failure — never raises.
    """
    try:
        with _LOCK:
            state = _active(world)
            ranked = state.model.top_k(int(n))
            return [
                {
                    "uri": uri,
                    "label": state.labels.get(uri, uri.rsplit("/", 1)[-1]),
                    "score": float(score),
                }
                for uri, score in ranked
            ]
    except Exception:
        return []


# ----------------------------------------------- deriving touched class uris


def class_uris_from_oqir(term: Optional[OQIRTerm]) -> list[str]:
    """Walk an OQIR term and collect every class uri it touches, in a stable
    (depth-first, deduplicated) order.

    These are exactly the typed graph nodes an answer exercised — the right
    granularity for a ``query`` usage event. Defensive against partial / exotic
    terms: an unrecognized node simply contributes nothing.
    """
    out: list[str] = []
    seen: set[str] = set()

    def visit(t: Any) -> None:
        if t is None:
            return
        if isinstance(t, Select):
            uri = t.class_uri
            if uri and uri not in seen:
                seen.add(uri)
                out.append(uri)
        elif isinstance(t, (Traverse, TextJoin, Aggregate, TopK)):
            visit(getattr(t, "source", None))
        elif isinstance(t, AsOf):
            visit(getattr(t, "term", None))

    try:
        visit(term)
    except Exception:
        return out
    return out


def class_uris_from_answer(answer: Any) -> list[str]:
    """Best-effort touched class uris for an :class:`Answer` (from its OQIR)."""
    return class_uris_from_oqir(getattr(answer, "oqir", None))
