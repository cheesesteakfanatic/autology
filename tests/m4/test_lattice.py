"""Hand-computable micro-context lattice (exact concept set), the iceberg
sigma threshold, the G-join bypass, and the stability index.

Micro context (4 objects, 4 attributes):

    a: {p1, p2}
    b: {p1, p3}
    c: {p1, p2, p3}
    d: {p4}

Closed concepts (computed by hand):

    ({a,b,c,d}, {})            top
    ({a,b,c},  {p1})
    ({a,c},    {p1,p2})
    ({b,c},    {p1,p3})
    ({c},      {p1,p2,p3})
    ({d},      {p4})
"""

from __future__ import annotations

import pytest

from ontoforge.strata.context import FormalContext, intent_hash_of
from ontoforge.strata.lattice import build_lattice, stability

INCIDENCE = {
    "a": frozenset({"p1", "p2"}),
    "b": frozenset({"p1", "p3"}),
    "c": frozenset({"p1", "p2", "p3"}),
    "d": frozenset({"p4"}),
}

EXPECTED = {
    frozenset("abcd"): frozenset(),
    frozenset("abc"): frozenset({"p1"}),
    frozenset("ac"): frozenset({"p1", "p2"}),
    frozenset("bc"): frozenset({"p1", "p3"}),
    frozenset("c"): frozenset({"p1", "p2", "p3"}),
    frozenset("d"): frozenset({"p4"}),
}


@pytest.fixture()
def ctx() -> FormalContext:
    out = FormalContext()
    for g, attrs in INCIDENCE.items():
        out.add_object(g, attrs)
    return out


def test_exact_concept_set(ctx):
    lattice = build_lattice(ctx, sigma=1)
    got = {c.extent: c.intent for c in lattice.concepts.values()}
    assert got == EXPECTED
    # intent hashes key the concepts
    for c in lattice.concepts.values():
        assert lattice.concepts[intent_hash_of(c.intent)] is c


def test_exact_cover_relation(ctx):
    lattice = build_lattice(ctx, sigma=1)
    by_extent = {c.extent: c for c in lattice.concepts.values()}

    def parents_of(extent):
        return {
            frozenset(p_ext)
            for p_ext, p in by_extent.items()
            if p.intent_hash in by_extent[frozenset(extent)].parents
        }

    assert parents_of("abc") == {frozenset("abcd")}
    assert parents_of("d") == {frozenset("abcd")}
    assert parents_of("ac") == {frozenset("abc")}
    assert parents_of("bc") == {frozenset("abc")}
    assert parents_of("c") == {frozenset("ac"), frozenset("bc")}   # multiple inheritance
    assert by_extent[frozenset("abcd")].parents == ()
    assert by_extent[frozenset("c")].children == ()


def test_iceberg_threshold_prunes_small_extents(ctx):
    lattice = build_lattice(ctx, sigma=2)
    got = {c.extent for c in lattice.concepts.values()}
    assert got == {frozenset("abcd"), frozenset("abc"), frozenset("ac"), frozenset("bc")}
    assert all(c.support >= 2 for c in lattice.concepts.values())


def test_bypass_objects_survive_below_sigma(ctx):
    lattice = build_lattice(ctx, sigma=2, bypass_objects=["d"])
    by_extent = {c.extent: c for c in lattice.concepts.values()}
    assert frozenset("d") in by_extent            # force-included hub concept
    assert by_extent[frozenset("d")].bypass is True
    assert by_extent[frozenset("d")].support == 1  # genuinely below sigma
    # everything else still respects the iceberg cut
    for ext, c in by_extent.items():
        assert c.support >= 2 or c.bypass


def test_sigma_validation(ctx):
    with pytest.raises(ValueError):
        build_lattice(ctx, sigma=0)


def test_stability_exact_hand_value():
    """G={a,b}, a:{p}, b:{p,q}; concept ({a,b},{p}). Subsets: {}'={p,q},
    {a}'={p}, {b}'={p,q}, {a,b}'={p} -> 2 of 4 hit -> stability 0.5."""
    ctx = FormalContext()
    ctx.add_object("a", frozenset({"p"}))
    ctx.add_object("b", frozenset({"p", "q"}))
    assert stability(ctx, frozenset({"a", "b"}), frozenset({"p"})) == 0.5
    # object concept of b: subsets {b} and... {}'={p,q} hits too -> 2/2... no:
    # subsets of {b}: {} -> {p,q} == intent, {b} -> {p,q} == intent -> 1.0
    assert stability(ctx, frozenset({"b"}), frozenset({"p", "q"})) == 1.0


def test_top_down_order_is_deterministic(ctx):
    lattice = build_lattice(ctx, sigma=1)
    order = [c.extent for c in lattice.top_down()]
    assert order[0] == frozenset("abcd")
    assert order == [c.extent for c in lattice.top_down()]
    supports = [len(e) for e in order]
    assert supports == sorted(supports, reverse=True)
