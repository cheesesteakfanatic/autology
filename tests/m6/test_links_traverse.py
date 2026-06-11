"""Link store + adjacency traversal (§4.2 link store, §4.4 read paths).

Graph under test (predicate "feeds", one class shard):

    hub -> a -> a1
    hub -> b -> b1 -> b2
plus a second predicate "audits": auditor -> hub.
"""

from __future__ import annotations

from m6_helpers import lc, mint_prov, stance

from ontoforge.contracts import Stance
from ontoforge.hearth import Hearth
from ontoforge.ledger import SqliteLedger

CLASS = "onto://test/Node"


def _build(h: Hearth, ledger: SqliteLedger) -> None:
    prov = mint_prov(ledger, "graph")
    edges = [("hub", "a"), ("hub", "b"), ("a", "a1"), ("b", "b1"), ("b1", "b2")]
    h.commit_links(
        CLASS, "feeds", [lc(f"n://{s}", "feeds", f"n://{o}", prov) for s, o in edges], now=1000
    )
    h.commit_links(CLASS, "audits", [lc("n://auditor", "audits", "n://hub", prov)], now=2000)


def test_traverse_depths_and_reverse(hearth, ledger) -> None:
    _build(hearth, ledger)
    assert hearth.traverse("n://hub", "feeds", depth=1) == ["n://a", "n://b"]
    assert hearth.traverse("n://hub", "feeds", depth=2) == ["n://a", "n://a1", "n://b", "n://b1"]
    assert hearth.traverse("n://hub", "feeds", depth=3) == [
        "n://a",
        "n://a1",
        "n://b",
        "n://b1",
        "n://b2",
    ]
    # reverse traversal
    assert hearth.traverse("n://b2", "feeds", depth=1, reverse=True) == ["n://b1"]
    assert hearth.traverse("n://b2", "feeds", depth=2, reverse=True) == ["n://b", "n://b1"]
    # predicate isolation
    assert hearth.traverse("n://hub", "audits", depth=1) == []
    assert hearth.traverse("n://hub", "audits", depth=1, reverse=True) == ["n://auditor"]
    # depth 0 and unknown node
    assert hearth.traverse("n://hub", "feeds", depth=0) == []
    assert hearth.traverse("n://ghost", "feeds", depth=2) == []


def test_stanced_traversal_after_unlink(hearth, ledger) -> None:
    _build(hearth, ledger)
    prov = mint_prov(ledger, "unlink-ev")
    assert hearth.links.unlink(CLASS, "feeds", "n://hub", "n://b", prov, now=5000) is True
    # current adjacency no longer crosses hub->b
    assert hearth.traverse("n://hub", "feeds", depth=2) == ["n://a", "n://a1"]
    # but the link still held in the world before the unlink (as_of past)
    assert hearth.traverse("n://hub", "feeds", Stance("as_of", valid_at=3000), depth=2) == [
        "n://a",
        "n://a1",
        "n://b",
        "n://b1",
    ]
    # and the system knew it at the time (as_known_at)
    assert "n://b" in hearth.traverse(
        "n://hub", "feeds", Stance("as_known_at", known_at=1500), depth=1
    )
    # audit: what did the system at 1500 believe about world-time 1200?
    assert "n://b" in hearth.traverse("n://hub", "feeds", stance(("audit", 1200, 1500)), depth=1)
    # unlink of a non-existent current link is a no-op returning False
    assert hearth.links.unlink(CLASS, "feeds", "n://hub", "n://b", prov, now=6000) is False


def test_link_supersession_same_triple(hearth, ledger) -> None:
    prov1 = mint_prov(ledger, "edge-v1")
    prov2 = mint_prov(ledger, "edge-v2")
    hearth.commit_links(CLASS, "feeds", [lc("n://x", "feeds", "n://y", prov1)], now=1000)
    hearth.commit_links(CLASS, "feeds", [lc("n://x", "feeds", "n://y", prov2)], now=2000)
    shard = hearth.links.shard(CLASS, "feeds")
    open_cells = [c for c in shard.cells if c.system.open]
    assert len(shard.cells) == 2  # superseded cell retained, append-only
    assert len(open_cells) == 1 and open_cells[0].prov_ref == prov2
    assert hearth.traverse("n://x", "feeds") == ["n://y"]  # no duplicate edges


def test_adjacency_is_derived_and_disposable(hearth, ledger) -> None:
    """§4.2(b): dropping the adjacency and rebuilding from canonical Parquet
    yields identical traversals; reopening from disk does too."""
    _build(hearth, ledger)
    gold = {
        ("n://hub", False): hearth.traverse("n://hub", "feeds", depth=3),
        ("n://b2", True): hearth.traverse("n://b2", "feeds", depth=3, reverse=True),
    }
    hearth.links._fwd.clear()
    hearth.links._rev.clear()
    assert hearth.traverse("n://hub", "feeds", depth=3) == []  # index really gone
    hearth.links.rebuild_adjacency()
    for (uri, rev), want in gold.items():
        assert hearth.traverse(uri, "feeds", depth=3, reverse=rev) == want
    # full reopen from Parquet
    reopened = Hearth(hearth.root, hearth.ledger)
    for (uri, rev), want in gold.items():
        assert reopened.traverse(uri, "feeds", depth=3, reverse=rev) == want
