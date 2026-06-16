"""The ER cascade: blocking -> Fellegi-Sunter banding -> spine -> clustering
(M5 step 4 wiring; whitepaper §2.2 "ER cascade + incremental clustering",
MVP plan §4.5 — the canonical example of the whole architecture).

Per-pair decision path:

  T0   exact normalized-key dedup (operator exact-name groups collapse to one
       blocking node) and the FS two-threshold weight band: posterior >= hi
       auto-accepts, <= lo auto-rejects — zero model cost.
  T1   ambiguous band -> DecisionKind.ER through ontoforge.spine.DecisionSpine
       with the continuous pair features + FS weight; the spine's calibrated
       logistic + split-conformal gate decides when it can. T1 is recalibrated
       once per run from a bootstrap labeled set drawn from the gold TRAIN
       split only (eval.SPLIT_SEED; the >= 50% TEST split is never touched).
  T2   still-ambiguous -> ModelClient task 'spine.adjudicate.er' served by the
       deterministic weighted-field-agreement HeuristicAdapter handler
       (heuristics.py), which carries the temporal-reuse guard.
  HUMAN tiers exhausted -> the edge is NOT accepted (fail-closed) and the
       deferral is counted against the <= 25% v1 target.

Accepted edges feed pivot correlation clustering with anchor-stable URIs
(clustering.py).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Sequence

from ontoforge.contracts import (
    CalibrationSample,
    DecisionKind,
    DecisionRequest,
    SpineProfile,
)
from ontoforge.ledger import HeuristicAdapter
from ontoforge.spine import DecisionSpine

from .blocking import Blocker, BlockingReport
from .clustering import Cluster, assign_uris, kwikcluster
from .fs import AIRCRAFT_FIELDS, OPERATOR_FIELDS, FellegiSunter, PairFeatures, pair_features
from .heuristics import ER_HEURISTIC_TASKS, build_pair_context, er_adjudicate_handler
from .records import EntityMention

__all__ = ["CascadeConfig", "PairDecision", "ERRunResult", "ERCascade"]

KINDS = ("aircraft", "operator")
FS_FIELDS = {"aircraft": AIRCRAFT_FIELDS, "operator": OPERATOR_FIELDS}


@dataclass(slots=True)
class CascadeConfig:
    seed: int = 17                 # EM init seed (documented, fixed)
    lsh_seed: int = 0x5EEDED
    snm_window: int = 4
    posterior_high: float = 0.95   # FS auto-accept band edge
    posterior_low: float = 0.05    # FS auto-reject band edge
    spine_profile: SpineProfile = field(default_factory=lambda: SpineProfile(name="economy"))
    kinds: tuple[str, ...] = KINDS


@dataclass(slots=True)
class PairDecision:
    kind: str
    node_a: str
    node_b: str
    fs_weight: float
    fs_posterior: float
    outcome: str                   # 'yes' | 'no'
    stage: str                     # 'fs_high' | 'fs_low' | 'spine:T0..T3' | 'spine:HUMAN'
    confidence: float
    deferred: bool = False
    quarantined: bool = False


@dataclass(slots=True)
class ERRunResult:
    mention_to_uri: dict[str, str]
    clusters: dict[str, dict[str, Cluster]]            # kind -> uri -> Cluster
    decisions: dict[str, dict[tuple[str, str], PairDecision]]
    accepted_edges: dict[str, set[tuple[str, str]]]
    blocking: dict[str, BlockingReport]
    candidate_pairs: dict[str, set[tuple[str, str]]]
    mention_to_node: dict[str, str]
    fs_models: dict[str, FellegiSunter]
    metrics: dict[str, Any]


def _pair_key(a: str, b: str) -> tuple[str, str]:
    return (a, b) if a < b else (b, a)


class ERCascade:
    """Batch resolver; also the shared engine for the incremental path
    (frozen FS models + fitted spine calibrator are reused by add_mentions)."""

    def __init__(
        self,
        config: Optional[CascadeConfig] = None,
        ledger: Any = None,
        model_client: Optional[Any] = None,
    ) -> None:
        self.config = config or CascadeConfig()
        # the deterministic ER adjudicator (temporal-reuse guard) is the keyless
        # fallback; an explicit model_client (e.g. a cassette in tests) is honored
        # as-is. With no provider env resolve_client returns this SAME object, so
        # the keyless path is byte-identical; with a provider + key it wraps a
        # live adapter behind the secure + validating + deterministic-fallback chain.
        deterministic = HeuristicAdapter({t: er_adjudicate_handler for t in ER_HEURISTIC_TASKS})
        if model_client is not None:
            self.model_client = model_client
        else:
            from ontoforge.aimodels import resolve_client

            self.model_client = resolve_client(
                "spine.adjudicate.er", fallback=deterministic
            )
        self.spine = DecisionSpine(self.config.spine_profile, model_client=self.model_client, ledger=ledger)
        self.fs_models: dict[str, FellegiSunter] = {}
        self.blockers: dict[str, Blocker] = {}

    # ----------------------------------------------------------- features

    def node_compare_fields(self, kind: str, node_id: str) -> dict[str, Any]:
        node = self.blockers[kind].nodes[node_id]
        f = dict(node.fields)
        if kind == "operator":
            f["tails"] = set(node.tails)
        return f

    def features_for(self, kind: str, node_a: str, node_b: str) -> PairFeatures:
        return pair_features(
            kind,
            self.node_compare_fields(kind, node_a),
            self.node_compare_fields(kind, node_b),
        )

    def t1_features(self, pf: PairFeatures, fs: FellegiSunter) -> tuple[tuple[str, float], ...]:
        w = fs.weight(pf.levels)
        post = fs.posterior(pf.levels)
        squashed = max(-1.0, min(1.0, w / 12.0))
        return pf.continuous + (("fs_posterior", post), ("fs_weight_scaled", squashed))

    # ----------------------------------------------------------- pipeline

    def run(
        self,
        mentions: Sequence[EntityMention],
        gold_train_labels: Optional[Mapping[str, Mapping[str, str]]] = None,
    ) -> ERRunResult:
        """Batch resolve. gold_train_labels: kind -> mention_id -> entity_id,
        drawn from the gold TRAIN split only (bootstrap recalibration)."""
        cfg = self.config
        candidate_pairs: dict[str, set[tuple[str, str]]] = {}
        feats: dict[str, dict[tuple[str, str], PairFeatures]] = {}
        pass_counts: dict[str, dict[str, int]] = {}

        for kind in cfg.kinds:
            blocker = Blocker(kind, lsh_seed=cfg.lsh_seed, window=cfg.snm_window)
            self.blockers[kind] = blocker
            blocker.build_nodes([m for m in mentions if m.entity_kind == kind])
            pairs, by_pass = blocker.candidate_pairs()
            candidate_pairs[kind] = pairs
            pass_counts[kind] = by_pass
            kf: dict[tuple[str, str], PairFeatures] = {}
            for a, b in sorted(pairs):
                kf[(a, b)] = self.features_for(kind, a, b)
            feats[kind] = kf
            fs = FellegiSunter(fields=FS_FIELDS[kind], seed=cfg.seed)
            fs.fit([pf.levels for pf in (kf[p] for p in sorted(kf))])
            self.fs_models[kind] = fs

        # ---- bootstrap T1 recalibration from gold TRAIN labels only
        n_cal = 0
        if gold_train_labels:
            samples = self._bootstrap_samples(feats, gold_train_labels)
            n_cal = len(samples)
            if samples:
                self.spine.recalibrate(DecisionKind.ER, samples)

        # ---- per-pair decisions + clustering
        mention_to_uri: dict[str, str] = {}
        clusters: dict[str, dict[str, Cluster]] = {}
        decisions: dict[str, dict[tuple[str, str], PairDecision]] = {}
        accepted: dict[str, set[tuple[str, str]]] = {}
        blocking_reports: dict[str, BlockingReport] = {}
        mention_to_node: dict[str, str] = {}

        for kind in cfg.kinds:
            kd: dict[tuple[str, str], PairDecision] = {}
            edges: set[tuple[str, str]] = set()
            for key in sorted(feats[kind]):
                dec = self.decide_pair(kind, key[0], key[1], feats[kind][key])
                kd[key] = dec
                if dec.outcome == "yes":
                    edges.add(key)
            decisions[kind] = kd
            accepted[kind] = edges

            blocker = self.blockers[kind]
            node_mentions = {nid: list(n.mention_ids) for nid, n in blocker.nodes.items()}
            parts = kwikcluster(blocker.nodes.keys(), edges)
            assignment = assign_uris(kind, parts, node_mentions)
            clusters[kind] = assignment.clusters
            mention_to_uri.update(assignment.mention_to_uri)
            for nid, node in blocker.nodes.items():
                for m in node.mention_ids:
                    mention_to_node[m] = nid

            rep = blocker.report(scored_pairs=len(feats[kind]))
            rep.pairs_by_pass = pass_counts[kind]
            rep.implied_mention_pairs = self._implied_pairs(kind, feats[kind].keys())
            rep.finalize()
            blocking_reports[kind] = rep

        metrics = self._run_metrics(decisions, n_cal)
        return ERRunResult(
            mention_to_uri=mention_to_uri,
            clusters=clusters,
            decisions=decisions,
            accepted_edges=accepted,
            blocking=blocking_reports,
            candidate_pairs=candidate_pairs,
            mention_to_node=mention_to_node,
            fs_models=dict(self.fs_models),
            metrics=metrics,
        )

    # ----------------------------------------------------- pair decision

    def decide_pair(self, kind: str, node_a: str, node_b: str, pf: PairFeatures) -> PairDecision:
        """T0 FS band, then spine escalation for the ambiguous middle."""
        fs = self.fs_models[kind]
        w = fs.weight(pf.levels)
        post = fs.posterior(pf.levels)
        if post >= self.config.posterior_high:
            return PairDecision(kind, node_a, node_b, w, post, "yes", "fs_high", post)
        if post <= self.config.posterior_low:
            return PairDecision(kind, node_a, node_b, w, post, "no", "fs_low", 1.0 - post)

        req = DecisionRequest(
            kind=DecisionKind.ER,
            decision_id=f"er:{kind}:{node_a}||{node_b}",
            candidates=("no", "yes"),
            features=self.t1_features(pf, fs),
            context=build_pair_context(
                kind,
                self.node_compare_fields(kind, node_a),
                self.node_compare_fields(kind, node_b),
            ),
            prov_atoms=(node_a, node_b),
        )
        res = self.spine.decide(req)
        outcome = res.outcome if res.auto_decided else "no"  # fail-closed
        return PairDecision(
            kind,
            node_a,
            node_b,
            w,
            post,
            outcome,
            f"spine:{res.tier.name}",
            res.confidence,
            deferred=res.deferred_to_human,
            quarantined=res.quarantined,
        )

    # ------------------------------------------------------- calibration

    def _node_label(self, kind: str, node_id: str, labels: Mapping[str, str]) -> Optional[str]:
        node = self.blockers[kind].nodes[node_id]
        found: set[str] = {labels[m] for m in node.mention_ids if m in labels}
        if len(found) == 1:
            return next(iter(found))
        return None  # unlabeled or (never in this gold) conflicting

    def _bootstrap_samples(
        self,
        feats: Mapping[str, Mapping[tuple[str, str], PairFeatures]],
        gold_train_labels: Mapping[str, Mapping[str, str]],
    ) -> list[CalibrationSample]:
        samples: list[CalibrationSample] = []
        for kind in self.config.kinds:
            labels = gold_train_labels.get(kind, {})
            if not labels:
                continue
            fs = self.fs_models[kind]
            for (a, b) in sorted(feats[kind]):
                la = self._node_label(kind, a, labels)
                lb = self._node_label(kind, b, labels)
                if la is None or lb is None:
                    continue
                samples.append(
                    CalibrationSample(
                        kind=DecisionKind.ER,
                        features=self.t1_features(feats[kind][(a, b)], fs),
                        candidates=("no", "yes"),
                        true_outcome="yes" if la == lb else "no",
                    )
                )
        return samples

    # ----------------------------------------------------------- metrics

    def _implied_pairs(self, kind: str, scored: Any) -> int:
        nodes = self.blockers[kind].nodes
        intra = sum(
            len(n.mention_ids) * (len(n.mention_ids) - 1) // 2 for n in nodes.values()
        )
        expanded = sum(
            len(nodes[a].mention_ids) * len(nodes[b].mention_ids) for a, b in scored
        )
        return intra + expanded

    @staticmethod
    def _run_metrics(
        decisions: Mapping[str, Mapping[tuple[str, str], PairDecision]], n_cal: int
    ) -> dict[str, Any]:
        out: dict[str, Any] = {"calibration_samples": n_cal}
        for kind, kd in decisions.items():
            stages: dict[str, int] = {}
            deferred = 0
            for dec in kd.values():
                stages[dec.stage] = stages.get(dec.stage, 0) + 1
                deferred += int(dec.deferred)
            n = max(len(kd), 1)
            escalated = sum(v for s, v in stages.items() if s.startswith("spine:"))
            out[kind] = {
                "pairs": len(kd),
                "stages": stages,
                "escalated": escalated,
                "escalation_rate": escalated / n,
                "deferred": deferred,
                "deferral_rate": deferred / n,
            }
        return out
