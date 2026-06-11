"""Multi-pass blocking (M5 step 2; AMD-0004: MinHash-LSH + sorted-neighborhood
hybrid, no neural blocking in v0).

Passes (union = candidate pairs):

(a) exact normalized-key block — tail number for aircraft, normalized operator
    name for operators. For aircraft a tail block is a CANDIDATE generator
    only (the temporal-reuse trap means tail equality must never merge); for
    operators exact-normalized-name equality is the T0 dedup rule, so each
    distinct name becomes one blocking NODE (its mention group) and intra-group
    pairs are matched at T0 without pairwise scoring.
(b) MinHash-LSH over character-3-gram sets of the name/identifier surface.
    k=64 minhash functions, banded b=10 x r=6 (60 rows used): the s-curve
    P(candidate) = 1-(1-s^r)^b crosses 1/2 at s = (1/b)^(1/r) = 0.681 — the
    required ~0.7 Jaccard operating point.
(c) sorted-neighborhood over the normalized sort key (window w): tail+serial
    for aircraft; the normalized name AND the token-sorted name for operators
    (catches word-order swaps).
(d) shared-tail relational pass (operators only): operator names co-occurring
    on the same aircraft tail are candidates — this is what surfaces alias
    pairs with near-zero string overlap (FedEx Express ~ Federal Express Corp).

MinHash is implemented from first principles: grams -> xxh3_64 integers ->
k independent multiplicative-shift hash functions (odd multipliers from a
seeded PCG64), minimum per function, vectorized in numpy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Optional, Sequence

import numpy as np
import xxhash

from .records import EntityMention
from .similarity import char_ngrams

__all__ = [
    "MinHasher",
    "LSHIndex",
    "BlockingNode",
    "BlockingReport",
    "Blocker",
    "sorted_neighborhood_pairs",
]

LSH_K = 64
LSH_BANDS = 10
LSH_ROWS = 6  # b*r = 60 <= k; threshold (1/b)^(1/r) ~= 0.681
SNM_WINDOW = 4


def _pair(a: str, b: str) -> tuple[str, str]:
    return (a, b) if a < b else (b, a)


class MinHasher:
    """k-permutation MinHash over string-gram sets.

    Grams are first mapped to 64-bit integers with xxh3_64 (stable across
    runs/processes); each of the k hash functions is a multiplicative-shift
    universal hash h_i(x) = (a_i * x + b_i) mod 2^64 with odd a_i drawn from
    a fixed-seed generator. Deterministic by construction.
    """

    def __init__(self, k: int = LSH_K, seed: int = 0x5EEDED) -> None:
        rng = np.random.default_rng(seed)
        self.k = k
        self._a = (rng.integers(1, 2**63, size=k, dtype=np.uint64) << np.uint64(1)) | np.uint64(1)
        self._b = rng.integers(0, 2**63, size=k, dtype=np.uint64)

    def signature(self, grams: Iterable[str]) -> Optional[np.ndarray]:
        xs = [xxhash.xxh3_64_intdigest(g) for g in grams]
        if not xs:
            return None
        x = np.array(xs, dtype=np.uint64)
        # (n_grams, k) wrap-around multiplicative hashing, then column minimum
        with np.errstate(over="ignore"):
            h = x[:, None] * self._a[None, :] + self._b[None, :]
        return h.min(axis=0)


class LSHIndex:
    """Banded LSH over MinHash signatures with incremental add() (M5 step 6)."""

    def __init__(self, hasher: Optional[MinHasher] = None, bands: int = LSH_BANDS, rows: int = LSH_ROWS) -> None:
        self.hasher = hasher or MinHasher()
        if bands * rows > self.hasher.k:
            raise ValueError("bands*rows must be <= k")
        self.bands = bands
        self.rows = rows
        self._buckets: dict[tuple[int, bytes], list[str]] = {}
        self._signatures: dict[str, np.ndarray] = {}

    def _band_keys(self, sig: np.ndarray) -> list[tuple[int, bytes]]:
        return [
            (band, sig[band * self.rows : (band + 1) * self.rows].tobytes())
            for band in range(self.bands)
        ]

    def add(self, node_id: str, grams: Iterable[str]) -> None:
        sig = self.hasher.signature(grams)
        if sig is None:
            return
        self._signatures[node_id] = sig
        for key in self._band_keys(sig):
            self._buckets.setdefault(key, []).append(node_id)

    def query(self, node_id: str) -> set[str]:
        """Co-bucketed nodes for an already-added node (excluding itself)."""
        sig = self._signatures.get(node_id)
        if sig is None:
            return set()
        out: set[str] = set()
        for key in self._band_keys(sig):
            out.update(self._buckets.get(key, ()))
        out.discard(node_id)
        return out

    def candidate_pairs(self) -> set[tuple[str, str]]:
        pairs: set[tuple[str, str]] = set()
        for bucket in self._buckets.values():
            if len(bucket) < 2:
                continue
            for i in range(len(bucket)):
                for j in range(i + 1, len(bucket)):
                    pairs.add(_pair(bucket[i], bucket[j]))
        return pairs


def sorted_neighborhood_pairs(keyed: Sequence[tuple[str, str]], window: int = SNM_WINDOW) -> set[tuple[str, str]]:
    """Sorted-neighborhood: sort by key, pair each node with the next w-1."""
    order = sorted(keyed)  # (key, node_id) lexicographic — deterministic
    pairs: set[tuple[str, str]] = set()
    n = len(order)
    for i in range(n):
        for j in range(i + 1, min(i + window, n)):
            a, b = order[i][1], order[j][1]
            if a != b:
                pairs.add(_pair(a, b))
    return pairs


# ----------------------------------------------------------------- nodes


@dataclass(slots=True)
class BlockingNode:
    """Unit of pairwise comparison.

    aircraft: one node per mention. operator: one node per distinct normalized
    name (exact normalized-key block (a) applied as T0 dedup), carrying every
    mention of that name.
    """

    node_id: str
    entity_kind: str
    mention_ids: list[str]
    fields: dict[str, object]  # representative comparison fields
    tails: set[str] = field(default_factory=set)  # operator relational surface


@dataclass(slots=True)
class BlockingReport:
    """Measured blocking quality (gates in §11.2 M5 / orchestration step 2)."""

    kind: str
    n_mentions: int = 0
    n_nodes: int = 0
    all_mention_pairs: int = 0
    scored_pairs: int = 0          # node pairs actually compared downstream
    implied_mention_pairs: int = 0  # scored pairs expanded + intra-node pairs
    pairs_by_pass: dict[str, int] = field(default_factory=dict)
    reduction_ratio: float = 0.0          # 1 - scored/all (comparison cost)
    implied_reduction_ratio: float = 0.0  # 1 - implied/all (coverage view)

    def finalize(self) -> None:
        ap = max(self.all_mention_pairs, 1)
        self.reduction_ratio = 1.0 - self.scored_pairs / ap
        self.implied_reduction_ratio = 1.0 - self.implied_mention_pairs / ap


# ----------------------------------------------------------------- blocker


def _aircraft_identifier_grams(f: dict[str, object]) -> set[str]:
    surface = f"{f.get('tail', '')}|{f.get('serial', '')}|{f.get('model', '')}"
    return char_ngrams(surface, 3)


def _operator_name_grams(f: dict[str, object]) -> set[str]:
    return char_ngrams(str(f.get("name_norm", "")), 3)


class Blocker:
    """Multi-pass candidate generation for one entity kind, with incremental
    add support (the same index structures serve batch and delta paths)."""

    def __init__(self, kind: str, lsh_seed: int = 0x5EEDED, window: int = SNM_WINDOW) -> None:
        self.kind = kind
        self.window = window
        self.lsh = LSHIndex(MinHasher(LSH_K, lsh_seed))
        self.nodes: dict[str, BlockingNode] = {}
        self._exact_blocks: dict[str, list[str]] = {}      # tail block (aircraft)
        self._tail_to_nodes: dict[str, list[str]] = {}     # operator relational
        self._name_to_node: dict[str, str] = {}            # operator exact-name
        self._snm_keys: dict[str, list[tuple[str, str]]] = {}

    # ------------------------------------------------------------- building

    def build_nodes(self, mentions: Sequence[EntityMention]) -> list[BlockingNode]:
        """Create/extend nodes from mentions; returns nodes new OR extended."""
        touched: dict[str, BlockingNode] = {}
        for m in mentions:
            if m.entity_kind != self.kind:
                continue
            if self.kind == "operator":
                key = str(m.fields["name_norm"])
                node_id = self._name_to_node.get(key)
                if node_id is None:
                    node_id = f"op-name/{key}"
                    self._name_to_node[key] = node_id
                    node = BlockingNode(node_id, self.kind, [], dict(m.fields))
                    self.nodes[node_id] = node
                    self._register_new_node(node)
                node = self.nodes[node_id]
                node.mention_ids.append(m.mention_id)
                tail = str(m.fields.get("tail", ""))
                if tail:
                    if tail not in node.tails:
                        node.tails.add(tail)
                        self._tail_to_nodes.setdefault(tail, []).append(node_id)
                touched[node_id] = node
            else:
                node = BlockingNode(m.mention_id, self.kind, [m.mention_id], dict(m.fields))
                self.nodes[node.node_id] = node
                self._register_new_node(node)
                touched[node.node_id] = node
        return list(touched.values())

    def _register_new_node(self, node: BlockingNode) -> None:
        f = node.fields
        if self.kind == "aircraft":
            tail = str(f.get("tail", ""))
            if tail:
                self._exact_blocks.setdefault(tail, []).append(node.node_id)
            grams = _aircraft_identifier_grams(f)
            keys = [("tail_serial", f"{tail}|{f.get('serial', '')}")]
        else:
            grams = _operator_name_grams(f)
            nn = str(f.get("name_norm", ""))
            keys = [("name", nn), ("name_tokens_sorted", " ".join(sorted(nn.split())))]
        if grams:
            self.lsh.add(node.node_id, grams)
        for label, key in keys:
            if key:
                self._snm_keys.setdefault(label, []).append((key, node.node_id))

    # ------------------------------------------------------------ batch pass

    def candidate_pairs(self) -> tuple[set[tuple[str, str]], dict[str, int]]:
        """Union of all passes over every node currently registered."""
        by_pass: dict[str, set[tuple[str, str]]] = {}

        if self.kind == "aircraft":
            exact: set[tuple[str, str]] = set()
            for block in self._exact_blocks.values():
                for i in range(len(block)):
                    for j in range(i + 1, len(block)):
                        exact.add(_pair(block[i], block[j]))
            by_pass["exact_key"] = exact
        else:
            shared: set[tuple[str, str]] = set()
            for nodes in self._tail_to_nodes.values():
                for i in range(len(nodes)):
                    for j in range(i + 1, len(nodes)):
                        shared.add(_pair(nodes[i], nodes[j]))
            by_pass["shared_tail"] = shared

        by_pass["minhash_lsh"] = self.lsh.candidate_pairs()
        snm: set[tuple[str, str]] = set()
        for keyed in self._snm_keys.values():
            snm |= sorted_neighborhood_pairs(keyed, self.window)
        by_pass["sorted_neighborhood"] = snm

        union: set[tuple[str, str]] = set()
        for pairs in by_pass.values():
            union |= pairs
        return union, {name: len(p) for name, p in by_pass.items()}

    # ------------------------------------------------------------ delta pass

    def candidate_pairs_for(self, node_ids: Sequence[str]) -> set[tuple[str, str]]:
        """Candidate pairs touching the given nodes only (incremental path).

        Uses the same passes: exact/shared-tail block membership, LSH bucket
        co-occupancy, and the sorted-neighborhood window around each node's
        sort keys (computed against the FULL current key lists).
        """
        target = set(node_ids)
        out: set[tuple[str, str]] = set()

        if self.kind == "aircraft":
            for nid in node_ids:
                tail = str(self.nodes[nid].fields.get("tail", ""))
                for other in self._exact_blocks.get(tail, ()):
                    if other != nid:
                        out.add(_pair(nid, other))
        else:
            for nid in node_ids:
                for tail in sorted(self.nodes[nid].tails):
                    for other in self._tail_to_nodes.get(tail, ()):
                        if other != nid:
                            out.add(_pair(nid, other))

        for nid in node_ids:
            for other in self.lsh.query(nid):
                out.add(_pair(nid, other))

        for keyed in self._snm_keys.values():
            order = sorted(keyed)
            positions = [i for i, (_, nid) in enumerate(order) if nid in target]
            n = len(order)
            for i in positions:
                lo = max(0, i - self.window + 1)
                hi = min(n, i + self.window)
                for j in range(lo, hi):
                    if j != i and order[j][1] != order[i][1]:
                        out.add(_pair(order[i][1], order[j][1]))
        return out

    # ------------------------------------------------------------- reporting

    def report(self, scored_pairs: int) -> BlockingReport:
        n_mentions = sum(len(n.mention_ids) for n in self.nodes.values())
        rep = BlockingReport(
            kind=self.kind,
            n_mentions=n_mentions,
            n_nodes=len(self.nodes),
            all_mention_pairs=n_mentions * (n_mentions - 1) // 2,
            scored_pairs=scored_pairs,
        )
        return rep
