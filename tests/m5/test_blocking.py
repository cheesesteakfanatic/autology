"""Blocking tests (M5 step 2; AMD-0004): LSH banding mechanics + the measured
gates — pairs-recall >= 0.98 vs gold and reduction ratio >= 0.95 vs all-pairs."""

from __future__ import annotations

import pytest

from ontoforge.er import blocking_pairs_recall
from ontoforge.er.blocking import (
    LSH_BANDS,
    LSH_K,
    LSH_ROWS,
    LSHIndex,
    MinHasher,
    sorted_neighborhood_pairs,
)

KINDS = ("aircraft", "operator")


def _grams(prefix: str, n: int) -> set[str]:
    return {f"{prefix}{i}" for i in range(n)}


class TestMinHashLSH:
    def test_banding_parameters(self):
        # k=64 minhash; b bands x r rows tuned for the ~0.7 Jaccard threshold:
        # the s-curve P(cand) = 1-(1-s^r)^b crosses 1/2 at (1/b)^(1/r)
        assert LSH_K == 64
        assert LSH_BANDS * LSH_ROWS <= LSH_K
        threshold = (1.0 / LSH_BANDS) ** (1.0 / LSH_ROWS)
        assert 0.6 <= threshold <= 0.75

    def test_signature_estimates_jaccard(self):
        h = MinHasher()
        shared = _grams("s", 80)
        a = shared | _grams("a", 10)   # |A|=90, |B|=90, J = 80/100 = 0.8
        b = shared | _grams("b", 10)
        sa, sb = h.signature(a), h.signature(b)
        est = float((sa == sb).mean())
        assert est == pytest.approx(0.8, abs=0.15)

    def test_identical_sets_identical_signatures(self):
        h = MinHasher()
        g = _grams("x", 30)
        assert (h.signature(g) == h.signature(set(g))).all()

    def test_lsh_separates_high_and_low_similarity(self):
        idx = LSHIndex()
        shared = _grams("s", 90)
        idx.add("hi_a", shared | _grams("a", 5))    # J ~ 0.9 -> P ~ 1.0
        idx.add("hi_b", shared | _grams("b", 5))
        idx.add("lo_a", _grams("p", 50) | _grams("q", 10))  # J ~ 0.2 vs lo_b
        idx.add("lo_b", _grams("p", 10) | _grams("r", 50))
        pairs = idx.candidate_pairs()
        assert ("hi_a", "hi_b") in pairs
        assert ("lo_a", "lo_b") not in pairs

    def test_lsh_incremental_query(self):
        idx = LSHIndex()
        shared = _grams("s", 90)
        idx.add("n1", shared | _grams("a", 5))
        idx.add("n2", shared | _grams("b", 5))
        assert "n1" in idx.query("n2")
        assert idx.query("missing") == set()

    def test_determinism_across_instances(self):
        g1 = _grams("g", 40)
        s1 = MinHasher().signature(g1)
        s2 = MinHasher().signature(g1)
        assert (s1 == s2).all()


class TestSortedNeighborhood:
    def test_window_pairs(self):
        keyed = [("a", "n1"), ("b", "n2"), ("c", "n3"), ("d", "n4")]
        pairs = sorted_neighborhood_pairs(keyed, window=2)
        assert pairs == {("n1", "n2"), ("n2", "n3"), ("n3", "n4")}

    def test_window_three(self):
        keyed = [("a", "n1"), ("b", "n2"), ("c", "n3")]
        pairs = sorted_neighborhood_pairs(keyed, window=3)
        assert ("n1", "n3") in pairs


class TestBlockingGates:
    """The measured gates of orchestration step 2, on the full estate."""

    def test_pairs_recall_gate(self, batch, gold):
        _, res = batch
        for kind in KINDS:
            closure = blocking_pairs_recall(
                gold.closure_pairs[kind], res.mention_to_node, res.candidate_pairs[kind]
            )
            listed = blocking_pairs_recall(
                gold.listed_pairs[kind], res.mention_to_node, res.candidate_pairs[kind]
            )
            assert closure["recall"] >= 0.98, f"{kind} closure pairs-recall {closure}"
            assert listed["recall"] >= 0.98, f"{kind} listed pairs-recall {listed}"

    def test_reduction_ratio_gate(self, batch):
        _, res = batch
        for kind in KINDS:
            rep = res.blocking[kind]
            assert rep.all_mention_pairs > 1_000_000  # fixture scale sanity
            assert rep.reduction_ratio >= 0.95, (
                f"{kind}: scored {rep.scored_pairs} of {rep.all_mention_pairs} "
                f"(rr={rep.reduction_ratio:.5f})"
            )
            # the coverage view (exact-block intra pairs expanded back in) is
            # reported alongside; it is the honest upper bound on touched pairs
            assert 0.0 <= rep.implied_reduction_ratio <= rep.reduction_ratio

    def test_union_of_passes(self, batch):
        """Every pass contributes, and the union is what gets scored."""
        _, res = batch
        air = res.blocking["aircraft"].pairs_by_pass
        op = res.blocking["operator"].pairs_by_pass
        assert air["exact_key"] > 0
        assert air["minhash_lsh"] > 0
        assert air["sorted_neighborhood"] > 0
        assert op["shared_tail"] > 0          # relational pass (alias surface)
        assert op["minhash_lsh"] > 0
        assert op["sorted_neighborhood"] > 0
        for kind in KINDS:
            rep = res.blocking[kind]
            assert len(res.candidate_pairs[kind]) == rep.scored_pairs

    def test_operator_nodes_are_exact_name_groups(self, batch):
        cascade, res = batch
        blocker = cascade.blockers["operator"]
        names = {str(n.fields["name_norm"]) for n in blocker.nodes.values()}
        assert len(names) == len(blocker.nodes)  # one node per distinct name
        assert res.blocking["operator"].n_nodes < res.blocking["operator"].n_mentions
