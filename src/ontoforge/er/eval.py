"""Gold handling + evaluation for the ER cascade (M5 test harness surface).

Gold format (fixtures/aviation/gold/er_gold_pairs.csv):
ENTITY_TYPE, ENTITY_ID, LEFT_TABLE, LEFT_KEY, RIGHT_TABLE, RIGHT_KEY, NOTE —
each row asserts that the LEFT mention and RIGHT mention refer to ENTITY_ID.
Mentions sharing an ENTITY_ID form a gold cluster; the evaluation pair set is
the TRANSITIVE CLOSURE of those clusters (stricter than the listed pairs).

Hold-out protocol (anti-leakage, orchestration step 4): gold ENTITIES are
split 50/50 by a fixed-seed permutation (SPLIT_SEED below). TRAIN entities
feed the spine's bootstrap calibration only; TEST entities are untouchable —
every reported P/R/F1 gate runs on TEST mentions exclusively.

Pairwise P/R/F1: standard cluster-pair counting restricted to labeled
mentions — TP = pairs sharing both predicted URI and gold entity;
P = TP / predicted-pairs, R = TP / gold-pairs (computed by group sizes, no
materialized O(n^2) pair sets).
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Mapping

import numpy as np

from ontoforge.estates.aviation import load_er_gold_pairs

__all__ = [
    "SPLIT_SEED",
    "GoldER",
    "load_gold",
    "pairwise_prf",
    "blocking_pairs_recall",
]

SPLIT_SEED = 20260611  # documented split seed (orchestration step 4)
TEST_FRACTION = 0.5    # >= 50% of gold held out as untouchable test set


def _mention_id(kind: str, table: str, key: str) -> str:
    return f"{kind}/{table}/{key}"


@dataclass(slots=True)
class GoldER:
    """Per-kind gold labels with the train/test entity split applied."""

    labels: dict[str, dict[str, str]]          # kind -> mention_id -> entity_id
    listed_pairs: dict[str, set[tuple[str, str]]]   # kind -> file-listed pairs
    closure_pairs: dict[str, set[tuple[str, str]]]  # kind -> transitive closure
    train_entities: dict[str, set[str]]
    test_entities: dict[str, set[str]]

    def split_labels(self, kind: str, split: str) -> dict[str, str]:
        ents = self.train_entities[kind] if split == "train" else self.test_entities[kind]
        return {m: e for m, e in self.labels[kind].items() if e in ents}

    def n_pairs(self, kind: str, split: str) -> int:
        ents = self.train_entities[kind] if split == "train" else self.test_entities[kind]
        sizes = Counter(e for e in self.labels[kind].values() if e in ents)
        return sum(n * (n - 1) // 2 for n in sizes.values())


def load_gold(fixtures_dir=None, split_seed: int = SPLIT_SEED) -> GoldER:
    df = load_er_gold_pairs(fixtures_dir)
    labels: dict[str, dict[str, str]] = defaultdict(dict)
    listed: dict[str, set[tuple[str, str]]] = defaultdict(set)

    for row in df.to_dict("records"):
        kind = str(row["ENTITY_TYPE"]).strip()
        eid = str(row["ENTITY_ID"]).strip()
        left = _mention_id(kind, str(row["LEFT_TABLE"]).strip(), str(row["LEFT_KEY"]).strip())
        right = _mention_id(kind, str(row["RIGHT_TABLE"]).strip(), str(row["RIGHT_KEY"]).strip())
        for m in (left, right):
            prev = labels[kind].get(m)
            if prev is not None and prev != eid:
                raise ValueError(f"gold conflict: mention {m} labeled {prev} and {eid}")
            labels[kind][m] = eid
        listed[kind].add((left, right) if left < right else (right, left))

    closure: dict[str, set[tuple[str, str]]] = {}
    train_e: dict[str, set[str]] = {}
    test_e: dict[str, set[str]] = {}
    for kind, lab in labels.items():
        by_ent: dict[str, list[str]] = defaultdict(list)
        for m, e in lab.items():
            by_ent[e].append(m)
        pairs: set[tuple[str, str]] = set()
        for ms in by_ent.values():
            ms = sorted(ms)
            for i in range(len(ms)):
                for j in range(i + 1, len(ms)):
                    pairs.add((ms[i], ms[j]))
        closure[kind] = pairs

        ents = sorted(by_ent)
        rng = np.random.default_rng(split_seed)
        perm = rng.permutation(len(ents))
        n_test = int(np.ceil(len(ents) * TEST_FRACTION))
        test = {ents[i] for i in perm[:n_test]}
        train_e[kind] = set(ents) - test
        test_e[kind] = test

    return GoldER(
        labels={k: dict(v) for k, v in labels.items()},
        listed_pairs=dict(listed),
        closure_pairs=closure,
        train_entities=train_e,
        test_entities=test_e,
    )


# ---------------------------------------------------------------- metrics


def pairwise_prf(
    mention_to_uri: Mapping[str, str], labels: Mapping[str, str]
) -> dict[str, float]:
    """Pairwise precision/recall/F1 over the labeled mention subset.

    Mentions missing from mention_to_uri count as singletons (recall hit, no
    silent exclusion).
    """
    common = sorted(labels)
    pred_groups: Counter = Counter()
    gold_groups: Counter = Counter()
    joint: Counter = Counter()
    for i, m in enumerate(common):
        uri = mention_to_uri.get(m, f"__singleton__{i}")
        pred_groups[uri] += 1
        gold_groups[labels[m]] += 1
        joint[(uri, labels[m])] += 1
    tp = sum(n * (n - 1) // 2 for n in joint.values())
    pred_pairs = sum(n * (n - 1) // 2 for n in pred_groups.values())
    gold_pairs = sum(n * (n - 1) // 2 for n in gold_groups.values())
    precision = tp / pred_pairs if pred_pairs else 1.0
    recall = tp / gold_pairs if gold_pairs else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "tp_pairs": float(tp),
        "predicted_pairs": float(pred_pairs),
        "gold_pairs": float(gold_pairs),
        "n_mentions": float(len(common)),
    }


def blocking_pairs_recall(
    gold_pairs: set[tuple[str, str]],
    mention_to_node: Mapping[str, str],
    candidate_node_pairs: set[tuple[str, str]],
) -> dict[str, float]:
    """Fraction of gold mention pairs covered by blocking.

    A gold pair is covered when both mentions map to the SAME blocking node
    (exact normalized-key block) or their two nodes form a generated candidate
    pair. Pairs whose mentions were not extracted at all count as misses.
    """
    total = 0
    covered = 0
    for a, b in gold_pairs:
        total += 1
        na, nb = mention_to_node.get(a), mention_to_node.get(b)
        if na is None or nb is None:
            continue
        if na == nb:
            covered += 1
        elif ((na, nb) if na < nb else (nb, na)) in candidate_node_pairs:
            covered += 1
    return {
        "recall": covered / total if total else 1.0,
        "covered": float(covered),
        "total": float(total),
    }
