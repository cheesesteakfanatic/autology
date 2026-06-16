"""CriticalityModel scoring: blend weights, centrality, recency, dependents, guards."""

from __future__ import annotations

from ontoforge.criticality import (
    CENTRALITY_WEIGHT,
    DEPENDENTS_WEIGHT,
    HALF_LIFE,
    RECENCY_WEIGHT,
    USAGE_WEIGHT,
    CriticalityModel,
    UsageLog,
)


def test_blend_weights_sum_to_one() -> None:
    assert abs(USAGE_WEIGHT + CENTRALITY_WEIGHT + RECENCY_WEIGHT + DEPENDENTS_WEIGHT - 1.0) < 1e-9


def test_untouched_uri_scores_zero() -> None:
    model = CriticalityModel({"a": ["b"], "b": ["a"]})
    assert model.score("a") == 0.0
    assert model.score("never-seen") == 0.0


def test_centrality_hub_outranks_leaf_at_equal_usage() -> None:
    # Hub is connected to many nodes; leaf to one. Touch hub and leaf equally.
    adjacency = {
        "hub": ["n1", "n2", "n3", "n4", "n5"],
        "leaf": ["n1"],
        "n1": ["hub", "leaf"],
        "n2": ["hub"],
        "n3": ["hub"],
        "n4": ["hub"],
        "n5": ["hub"],
    }
    model = CriticalityModel(adjacency)
    log = UsageLog()
    log.append("hub", "query")
    log.append("leaf", "query")
    model.update(log)
    # Equal usage and equal recency, so the higher-degree hub must win on the
    # centrality term.
    assert model.score("hub") > model.score("leaf")


def test_recency_recent_outranks_equally_used_but_old() -> None:
    adjacency = {"a": [], "b": []}  # equal (zero) degree to isolate recency
    model = CriticalityModel(adjacency)
    log = UsageLog()
    # 'old' is touched once early, 'recent' once late; pad with unrelated events
    # so the seq distance for 'old' is large relative to HALF_LIFE.
    log.append("old", "query")  # seq 1
    for _ in range(int(HALF_LIFE) * 3):
        log.append("filler", "query")
    log.append("recent", "query")  # large seq
    # graph only knows a/b; add old/recent/filler as isolated nodes so they are
    # scorable (degree 0). Build a fresh model that includes them.
    model = CriticalityModel({"old": [], "recent": [], "filler": []})
    model.update(log)
    assert model.score("recent") > model.score("old")


def test_dependents_signal_raises_score() -> None:
    adjacency = {"a": [], "b": []}
    dependents = {"a": ["x", "y", "z"], "b": []}
    model = CriticalityModel(adjacency, dependents)
    log = UsageLog()
    log.append("a", "query")
    log.append("b", "query")
    model.update(log)
    # a and b have equal usage/centrality/recency; a has dependents, b does not.
    assert model.score("a") > model.score("b")


def test_weight_amplifies_usage() -> None:
    model = CriticalityModel({"heavy": [], "light": []})
    log = UsageLog()
    log.append("light", "query", weight=1.0)
    log.append("heavy", "query", weight=5.0)
    model.update(log)
    assert model.score("heavy") > model.score("light")


def test_zero_division_guards_single_node_no_edges() -> None:
    # max_degree=0, max_dependents=0, and after touching, max_usage>0.
    model = CriticalityModel({"solo": []})
    log = UsageLog()
    log.append("solo", "query")
    model.update(log)  # must not raise
    s = model.score("solo")
    # Only usage + recency terms can be nonzero; both normalized fine.
    assert 0.0 <= s <= 1.0


def test_top_k_sorted_score_desc_then_uri_asc() -> None:
    # Two nodes that will tie on score must order by uri ascending.
    model = CriticalityModel({"zeta": [], "alpha": [], "mid": ["x"], "x": ["mid"]})
    log = UsageLog()
    log.append("zeta", "query")
    log.append("alpha", "query")
    log.append("mid", "query")
    model.update(log)
    ranked = model.top_k(10)
    # mid has degree>0 so highest; zeta/alpha tie -> alpha before zeta.
    uris = [u for u, _ in ranked]
    assert uris[0] == "mid"
    assert uris.index("alpha") < uris.index("zeta")
    # scores are non-increasing
    scores = [s for _, s in ranked]
    assert scores == sorted(scores, reverse=True)


def test_top_k_respects_n() -> None:
    model = CriticalityModel({f"u{i}": [] for i in range(10)})
    log = UsageLog()
    for i in range(10):
        log.append(f"u{i}", "query")
    model.update(log)
    assert len(model.top_k(3)) == 3
    assert len(model.top_k(0)) == 0
    assert len(model.top_k(-1)) == 0


def test_score_in_unit_range_when_signals_bounded() -> None:
    adjacency = {"hub": ["a", "b"], "a": ["hub"], "b": ["hub"]}
    dependents = {"hub": ["a", "b"]}
    model = CriticalityModel(adjacency, dependents)
    log = UsageLog()
    log.append("hub", "query")
    log.append("a", "query")
    log.append("b", "query")
    model.update(log)
    for uri in ("hub", "a", "b"):
        assert 0.0 <= model.score(uri) <= 1.0
