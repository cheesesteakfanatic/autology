"""M5 — ER Cascade & Incremental Clustering (whitepaper §11.2 M5; AMD-0004).

blocking -> Fellegi-Sunter(EM) two-threshold banding -> DecisionSpine
escalation -> pivot correlation clustering -> anchor-stable URIs, plus the
delta path (IncrementalER.add_mentions).
"""

from .blocking import Blocker, BlockingReport, LSHIndex, MinHasher, sorted_neighborhood_pairs
from .cascade import CascadeConfig, ERCascade, ERRunResult, PairDecision
from .clustering import Cluster, assign_uris, kwikcluster, mint_uri, pivot_order_key
from .eval import SPLIT_SEED, GoldER, blocking_pairs_recall, load_gold, pairwise_prf
from .fs import AIRCRAFT_FIELDS, OPERATOR_FIELDS, FellegiSunter, PairFeatures, pair_features
from .heuristics import ER_HEURISTIC_TASKS, er_adjudicate_handler
from .incremental import CycleReport, IncrementalER
from .records import EntityMention, extract_mentions
from .similarity import jaro, jaro_winkler

__all__ = [
    "Blocker",
    "BlockingReport",
    "LSHIndex",
    "MinHasher",
    "sorted_neighborhood_pairs",
    "CascadeConfig",
    "ERCascade",
    "ERRunResult",
    "PairDecision",
    "Cluster",
    "assign_uris",
    "kwikcluster",
    "mint_uri",
    "pivot_order_key",
    "SPLIT_SEED",
    "GoldER",
    "blocking_pairs_recall",
    "load_gold",
    "pairwise_prf",
    "AIRCRAFT_FIELDS",
    "OPERATOR_FIELDS",
    "FellegiSunter",
    "PairFeatures",
    "pair_features",
    "ER_HEURISTIC_TASKS",
    "er_adjudicate_handler",
    "CycleReport",
    "IncrementalER",
    "EntityMention",
    "extract_mentions",
    "jaro",
    "jaro_winkler",
]
