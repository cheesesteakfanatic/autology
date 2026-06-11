"""Correlation clustering + anchor-URI tests (M5 step 5): the adversarial
A=B, B=C, A!=C triangle, partition transitivity, deterministic pivots,
anchor protocol and hysteresis under merge/split."""

from __future__ import annotations

import xxhash
from hypothesis import given, settings
from hypothesis import strategies as st

from ontoforge.er.clustering import (
    assign_uris,
    kwikcluster,
    mint_uri,
    pivot_order_key,
)


class TestKwikCluster:
    def test_adversarial_triangle_conflict_resolved(self):
        """A=B and B=C accepted, A=C rejected (absent edge): an intransitive
        relation. The output must be a PARTITION (transitive by construction)
        that disagrees with exactly ONE input judgement — the optimum — and
        which one is decided by the deterministic pivot order:

        - pivot at an endpoint (A or C): the partition splits B's other edge,
          honoring A!=C;
        - pivot at the middle (B): the partition merges all three, paying the
          single negative edge instead (the KwikCluster 3-approx trade).
        """
        for a, b, c in (("A", "B", "C"), ("t1", "t2", "t3"), ("x9", "q4", "m7")):
            nodes = [a, b, c]
            edges = [(a, b), (b, c)]
            clusters = kwikcluster(nodes, edges)
            # partition property (transitivity holds by construction)
            flat = sorted(m for cl in clusters for m in cl)
            assert flat == sorted(nodes)
            first = min(nodes, key=pivot_order_key)
            if first == b:
                # middle pivot absorbs both endpoints: 1 violated negative edge
                assert clusters == [{a, b, c}]
            else:
                # endpoint pivot: A!=C honored, 1 violated positive edge
                assert sorted(len(cl) for cl in clusters) == [1, 2]
                cluster_of = {m: i for i, cl in enumerate(clusters) for m in cl}
                assert cluster_of[a] != cluster_of[c]
            # deterministic: same input -> same output
            assert kwikcluster(nodes, edges) == clusters

    def test_triangle_endpoint_pivot_separates_negative_edge(self):
        """Force the endpoint-pivot branch: relabel so the hash-min node is an
        endpoint, and assert the A!=C judgement survives."""
        base = ["n1", "n2", "n3"]
        order = sorted(base, key=pivot_order_key)
        endpoint, middle = order[0], order[1]
        other = next(n for n in base if n not in (endpoint, middle))
        # chain: endpoint = middle, middle = other; endpoint != other
        clusters = kwikcluster(base, [(endpoint, middle), (middle, other)])
        cluster_of = {m: i for i, cl in enumerate(clusters) for m in cl}
        assert cluster_of[endpoint] == cluster_of[middle]
        assert cluster_of[endpoint] != cluster_of[other]
        assert sorted(len(cl) for cl in clusters) == [1, 2]

    def test_disconnected_singletons(self):
        clusters = kwikcluster(["X", "Y"], [])
        assert sorted(map(sorted, clusters)) == [["X"], ["Y"]]

    def test_pivot_order_is_stable_hash(self):
        k1 = pivot_order_key("aircraft/faa_master/10084|1617")
        assert k1 == (
            xxhash.xxh3_64_intdigest(b"aircraft/faa_master/10084|1617"),
            "aircraft/faa_master/10084|1617",
        )

    @settings(max_examples=50, deadline=None)
    @given(
        st.lists(st.integers(0, 15), min_size=0, max_size=30),
        st.lists(st.tuples(st.integers(0, 15), st.integers(0, 15)), max_size=40),
    )
    def test_partition_property(self, node_ints, edge_ints):
        nodes = [f"n{i}" for i in set(node_ints)]
        edges = [
            (f"n{a}", f"n{b}") for a, b in edge_ints if a != b and a in set(node_ints) and b in set(node_ints)
        ]
        clusters = kwikcluster(nodes, edges)
        flat = [m for c in clusters for m in c]
        assert sorted(flat) == sorted(nodes)          # exhaustive
        assert len(flat) == len(set(flat))            # disjoint


class TestAnchorProtocol:
    def test_uri_format_and_anchor(self):
        uri = mint_uri("aircraft", "aircraft/faa_master/10084|1617")
        h = xxhash.xxh3_64_hexdigest(b"aircraft/faa_master/10084|1617")
        assert uri == f"ent://aircraft/{h}"

    def test_founding_anchor_is_lexicographic_min(self):
        a = assign_uris(
            "aircraft",
            [{"n1", "n2"}],
            {"n1": ["m_bbb"], "n2": ["m_aaa"]},
        )
        (cluster,) = a.clusters.values()
        assert cluster.anchor_mention_id == "m_aaa"
        assert cluster.uri == mint_uri("aircraft", "m_aaa")
        assert a.mention_to_uri == {"m_aaa": cluster.uri, "m_bbb": cluster.uri}

    def test_hysteresis_keeps_uri_while_anchor_retained(self):
        first = assign_uris("op", [{"n1"}], {"n1": ["m1"]})
        (uri,) = first.clusters
        # the cluster grows but retains its anchor mention -> URI unchanged
        second = assign_uris(
            "op",
            [{"n1", "n2"}],
            {"n1": ["m1"], "n2": ["m2"]},
            prior=first.clusters,
            prior_mention_to_uri=first.mention_to_uri,
        )
        assert set(second.clusters) == {uri}
        assert second.churn_by_uri[uri] == 0
        assert second.retired_uris == set()

    def test_merge_keeps_lexicographically_min_anchor_uri(self):
        a = assign_uris("op", [{"n1"}, {"n2"}], {"n1": ["m_a"], "n2": ["m_b"]})
        uri_a = mint_uri("op", "m_a")
        uri_b = mint_uri("op", "m_b")
        assert set(a.clusters) == {uri_a, uri_b}
        merged = assign_uris(
            "op",
            [{"n1", "n2"}],
            {"n1": ["m_a"], "n2": ["m_b"]},
            prior=a.clusters,
            prior_mention_to_uri=a.mention_to_uri,
        )
        assert set(merged.clusters) == {uri_a}        # min-anchor URI survives
        assert merged.churn_by_uri[uri_a] == 1        # one retired prior URI
        assert merged.retired_uris == {uri_b}

    def test_split_keeps_anchor_side(self):
        whole = assign_uris("op", [{"n1", "n2"}], {"n1": ["m_a"], "n2": ["m_b"]})
        (uri,) = whole.clusters
        anchor = whole.clusters[uri].anchor_mention_id
        assert anchor == "m_a"
        split = assign_uris(
            "op",
            [{"n1"}, {"n2"}],
            {"n1": ["m_a"], "n2": ["m_b"]},
            prior=whole.clusters,
            prior_mention_to_uri=whole.mention_to_uri,
        )
        # the side retaining the anchor keeps the URI; the other minted fresh
        assert split.mention_to_uri["m_a"] == uri
        new_uri = split.mention_to_uri["m_b"]
        assert new_uri != uri and new_uri == mint_uri("op", "m_b")
        assert split.churn_by_uri[uri] == 0
        assert split.churn_by_uri[new_uri] == 1       # m_b left its prior URI
        assert split.retired_uris == set()            # old URI still alive

    def test_batch_estate_clusters_are_anchored(self, batch):
        _, res = batch
        for kind in ("aircraft", "operator"):
            for uri, cluster in res.clusters[kind].items():
                assert uri == mint_uri(kind, cluster.anchor_mention_id)
                assert cluster.anchor_mention_id == min(cluster.mention_ids)
                assert cluster.mention_ids
