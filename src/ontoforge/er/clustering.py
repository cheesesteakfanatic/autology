"""Correlation clustering + anchor-stable URI minting (M5 step 5).

KwikCluster (Ailon, Charikar & Newman 2008 pivot algorithm, derandomized):
nodes are visited in a DETERMINISTIC pivot order — sorted by the stable
xxh3_64 hash of the node id (ties by id) — and each unclustered pivot absorbs
its unclustered positive neighbours. This resolves A=B, B=C, A!=C conflict
triangles into a proper partition (the output is transitive by construction;
the violated edge is the one the pivot order pays for, the classic
3-approximation argument).

URI minting — anchor protocol (whitepaper v1 G1, §350 "identifier stability"):

    uri = ent://<kind>/<xxh3_64 hex of the lexicographically-min FOUNDING mention_id>

The founding anchor is fixed at mint time. Hysteresis: an existing cluster
keeps its URI for as long as it retains its anchor mention; on a merge the
surviving URI is the one whose anchor mention_id is lexicographically
smallest (deterministic), and every other absorbed URI is a churn event; on
a split, the side keeping the anchor keeps the URI and the other side mints
fresh. Churn is tracked per cycle for the gate (<= 1 URI change per affected
entity per cycle).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Mapping, Optional, Sequence

import xxhash

__all__ = ["pivot_order_key", "kwikcluster", "Cluster", "assign_uris", "UriAssignment"]


def pivot_order_key(node_id: str) -> tuple[int, str]:
    """Stable pivot sort key: xxh3_64 of the id, tie-broken by the id itself."""
    return (xxhash.xxh3_64_intdigest(node_id.encode("utf-8")), node_id)


def kwikcluster(nodes: Iterable[str], positive_edges: Iterable[tuple[str, str]]) -> list[set[str]]:
    """Pivot correlation clustering over the accepted-match graph."""
    node_list = sorted(set(nodes), key=pivot_order_key)
    adj: dict[str, set[str]] = {n: set() for n in node_list}
    for a, b in positive_edges:
        if a in adj and b in adj and a != b:
            adj[a].add(b)
            adj[b].add(a)
    clustered: set[str] = set()
    clusters: list[set[str]] = []
    for pivot in node_list:
        if pivot in clustered:
            continue
        members = {pivot} | {u for u in adj[pivot] if u not in clustered}
        clustered |= members
        clusters.append(members)
    return clusters


@dataclass(slots=True)
class Cluster:
    uri: str
    kind: str
    anchor_mention_id: str          # founding anchor (fixed at mint time)
    node_ids: set[str] = field(default_factory=set)
    mention_ids: set[str] = field(default_factory=set)


@dataclass(slots=True)
class UriAssignment:
    clusters: dict[str, Cluster]                    # uri -> cluster
    mention_to_uri: dict[str, str]
    churn_by_uri: dict[str, int]                    # final uri -> retired prior uris
    retired_uris: set[str]
    affected_uris: set[str]                         # final uris of affected entities


def mint_uri(kind: str, anchor_mention_id: str) -> str:
    return f"ent://{kind}/{xxhash.xxh3_64_hexdigest(anchor_mention_id.encode('utf-8'))}"


def assign_uris(
    kind: str,
    node_clusters: Sequence[set[str]],
    node_mentions: Mapping[str, Sequence[str]],
    prior: Optional[Mapping[str, Cluster]] = None,
    prior_mention_to_uri: Optional[Mapping[str, str]] = None,
) -> UriAssignment:
    """Anchor-protocol URI assignment with hysteresis + churn accounting.

    node_clusters: the (re)computed partition over blocking nodes.
    prior: existing Cluster registry (uri -> Cluster) for the touched scope;
    clusters whose membership is untouched should simply not be passed here.
    """
    prior = prior or {}
    prior_m2u = dict(prior_mention_to_uri or {})
    anchor_to_uri = {c.anchor_mention_id: uri for uri, c in prior.items()}

    out_clusters: dict[str, Cluster] = {}
    mention_to_uri: dict[str, str] = {}
    churn_by_uri: dict[str, int] = {}
    retired: set[str] = set()
    affected: set[str] = set()

    for node_set in node_clusters:
        mentions = sorted(m for n in node_set for m in node_mentions[n])
        if not mentions:
            continue
        # hysteresis: any prior anchors present in this cluster?
        anchors_here = sorted(m for m in mentions if m in anchor_to_uri)
        if anchors_here:
            keep_anchor = anchors_here[0]  # lexicographically-min anchor wins a merge
            uri = anchor_to_uri[keep_anchor]
            anchor = keep_anchor
        else:
            anchor = mentions[0]           # lexicographically-min founding mention
            uri = mint_uri(kind, anchor)
        prior_uris = {prior_m2u[m] for m in mentions if m in prior_m2u}
        changed = sorted(prior_uris - {uri})
        churn_by_uri[uri] = len(changed)
        affected.add(uri)
        out_clusters[uri] = Cluster(
            uri=uri,
            kind=kind,
            anchor_mention_id=anchor,
            node_ids=set(node_set),
            mention_ids=set(mentions),
        )
        for m in mentions:
            mention_to_uri[m] = uri

    # a prior URI is retired only when no surviving cluster carries it
    seen_prior = {u for u in prior} | {prior_m2u[m] for m in prior_m2u}
    retired = {u for u in seen_prior if u not in out_clusters}

    return UriAssignment(
        clusters=out_clusters,
        mention_to_uri=mention_to_uri,
        churn_by_uri=churn_by_uri,
        retired_uris=retired,
        affected_uris=affected,
    )
