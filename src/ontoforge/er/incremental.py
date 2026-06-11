"""Incremental ER: delta mentions against existing clusters (M5 step 6).

add_mentions(delta):

1. The delta's mentions are registered with the SAME blocking structures the
   batch run built (the LSH index, exact/tail blocks and sorted-neighborhood
   key lists all support incremental add), and candidate pairs are generated
   ONLY for the touched nodes (Blocker.candidate_pairs_for).
2. Each NEW pair — never a previously decided one — is scored with the frozen
   Fellegi-Sunter model and routed through the same FS-band -> spine cascade
   (re-adjudicating only edges the delta touches).
3. Re-clustering is LOCAL: the subgraph re-run through KwikCluster contains
   only the new nodes plus the members of clusters touched by a new accepted
   edge or by group growth; every other cluster is untouched by construction.
4. URIs follow the anchor protocol with hysteresis (clustering.assign_uris):
   a cluster keeps its URI while it retains its anchor mention. Churn is
   logged per cycle; the gate is <= 1 URI change per affected entity per
   cycle, and exactly 0 for unaffected entities.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence

from .cascade import ERCascade, ERRunResult, PairDecision
from .clustering import Cluster, assign_uris, kwikcluster
from .records import EntityMention

__all__ = ["CycleReport", "IncrementalER"]


@dataclass(slots=True)
class CycleReport:
    cycle: int
    new_mentions: int = 0
    new_pairs_scored: int = 0
    decisions: dict[str, dict[tuple[str, str], PairDecision]] = field(default_factory=dict)
    affected_entities: dict[str, set[str]] = field(default_factory=dict)   # kind -> final uris
    churn_by_entity: dict[str, dict[str, int]] = field(default_factory=dict)
    mention_uri_changes: dict[str, int] = field(default_factory=dict)      # per-mention change count
    retired_uris: dict[str, set[str]] = field(default_factory=dict)

    @property
    def max_churn_per_affected_entity(self) -> int:
        return max(
            (max(v.values()) for v in self.churn_by_entity.values() if v),
            default=0,
        )


class IncrementalER:
    """Holds resolved state and links deltas into it."""

    def __init__(self, cascade: ERCascade, result: ERRunResult) -> None:
        self.cascade = cascade
        self.clusters: dict[str, dict[str, Cluster]] = {
            k: dict(v) for k, v in result.clusters.items()
        }
        self.mention_to_uri: dict[str, str] = dict(result.mention_to_uri)
        self.accepted_edges: dict[str, set[tuple[str, str]]] = {
            k: set(v) for k, v in result.accepted_edges.items()
        }
        self.decided: dict[str, set[tuple[str, str]]] = {
            k: set(v.keys()) for k, v in result.decisions.items()
        }
        self._node_to_uri: dict[str, dict[str, str]] = {}
        for kind, by_uri in self.clusters.items():
            self._node_to_uri[kind] = {}
            for uri, cl in by_uri.items():
                for nid in cl.node_ids:
                    self._node_to_uri[kind][nid] = uri
        self._cycle = 0

    # ----------------------------------------------------------------- api

    def add_mentions(self, delta: Sequence[EntityMention]) -> CycleReport:
        self._cycle += 1
        report = CycleReport(cycle=self._cycle, new_mentions=len(delta))

        for kind in self.cascade.config.kinds:
            kind_delta = [m for m in delta if m.entity_kind == kind]
            if not kind_delta:
                continue
            self._add_kind(kind, kind_delta, report)
        return report

    # ------------------------------------------------------------ internals

    def _add_kind(self, kind: str, delta: Sequence[EntityMention], report: CycleReport) -> None:
        blocker = self.cascade.blockers[kind]
        known_nodes = set(self._node_to_uri[kind])
        touched = blocker.build_nodes(delta)
        touched_ids = [n.node_id for n in touched]
        new_node_ids = [nid for nid in touched_ids if nid not in known_nodes]
        grown_node_ids = [nid for nid in touched_ids if nid in known_nodes]

        # ---- 1+2: delta-scoped candidate generation and adjudication
        decided = self.decided.setdefault(kind, set())
        edges = self.accepted_edges.setdefault(kind, set())
        new_pairs = sorted(
            p for p in blocker.candidate_pairs_for(sorted(touched_ids)) if p not in decided
        )
        kd: dict[tuple[str, str], PairDecision] = {}
        for a, b in new_pairs:
            pf = self.cascade.features_for(kind, a, b)
            dec = self.cascade.decide_pair(kind, a, b, pf)
            kd[(a, b)] = dec
            decided.add((a, b))
            if dec.outcome == "yes":
                edges.add((a, b))
        report.decisions[kind] = kd
        report.new_pairs_scored += len(kd)

        # ---- 3: local re-clustering scope
        node_to_uri = self._node_to_uri[kind]
        affected_uris: set[str] = set()
        for nid in grown_node_ids:
            affected_uris.add(node_to_uri[nid])
        for (a, b) in kd:
            if kd[(a, b)].outcome != "yes":
                continue
            for nid in (a, b):
                if nid in node_to_uri:
                    affected_uris.add(node_to_uri[nid])

        scope_nodes: set[str] = set(new_node_ids)
        prior_scope: dict[str, Cluster] = {}
        for uri in sorted(affected_uris):
            cl = self.clusters[kind][uri]
            prior_scope[uri] = cl
            scope_nodes |= cl.node_ids
        scope_edges = [
            (a, b) for (a, b) in sorted(edges) if a in scope_nodes and b in scope_nodes
        ]
        prior_m2u = {
            m: self.mention_to_uri[m]
            for cl in prior_scope.values()
            for m in cl.mention_ids
        }

        node_mentions = {nid: list(blocker.nodes[nid].mention_ids) for nid in scope_nodes}
        parts = kwikcluster(scope_nodes, scope_edges)
        assignment = assign_uris(kind, parts, node_mentions, prior_scope, prior_m2u)

        # ---- 4: state update + churn accounting
        for uri in prior_scope:
            self.clusters[kind].pop(uri, None)
        for uri, cl in assignment.clusters.items():
            self.clusters[kind][uri] = cl
        for uri, cl in assignment.clusters.items():
            for nid in cl.node_ids:
                self._node_to_uri[kind][nid] = uri
        for m, uri in assignment.mention_to_uri.items():
            prev = self.mention_to_uri.get(m)
            if prev is not None and prev != uri:
                report.mention_uri_changes[m] = report.mention_uri_changes.get(m, 0) + 1
            self.mention_to_uri[m] = uri

        report.affected_entities[kind] = set(assignment.affected_uris)
        report.churn_by_entity[kind] = dict(assignment.churn_by_uri)
        report.retired_uris[kind] = set(assignment.retired_uris)

    # ------------------------------------------------------------- queries

    def snapshot_mention_to_uri(self) -> dict[str, str]:
        return dict(self.mention_to_uri)

    def entity_count(self, kind: str) -> int:
        return len(self.clusters.get(kind, {}))

    def state_summary(self) -> dict[str, Any]:
        return {
            kind: {
                "entities": len(by_uri),
                "mentions": sum(len(c.mention_ids) for c in by_uri.values()),
            }
            for kind, by_uri in self.clusters.items()
        }
