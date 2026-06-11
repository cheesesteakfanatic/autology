"""Galois-connection property tests over random formal contexts (hypothesis).

The derivation operators X -> X' (prime_objects) and Y -> Y' (prime_attrs)
must form a Galois connection between the powersets of G and M:

- antitone:    X1 ⊆ X2  ⇒  X2' ⊆ X1'   (both directions)
- extensive:   X ⊆ X''  and  Y ⊆ Y''
- idempotent:  X'' '' = X''  (closures are closure operators)
- triple rule: X''' = X'
- lattice completeness: for sigma=1, the CbO-enumerated extents are exactly
  {closure(S) : S ⊆ G} — attribute clarification/reduction must not lose or
  invent concepts.
"""

from __future__ import annotations

from itertools import chain, combinations

from hypothesis import given, settings, strategies as st

from ontoforge.strata.context import FormalContext
from ontoforge.strata.lattice import build_lattice

OBJECTS = ["g1", "g2", "g3", "g4", "g5", "g6"]
ATTRS = ["p", "q", "r", "s", "t"]

contexts = st.dictionaries(
    st.sampled_from(OBJECTS),
    st.frozensets(st.sampled_from(ATTRS)),
    min_size=1,
    max_size=len(OBJECTS),
)

object_subsets = st.frozensets(st.sampled_from(OBJECTS))
attr_subsets = st.frozensets(st.sampled_from(ATTRS))


def make_ctx(incidence: dict[str, frozenset[str]]) -> FormalContext:
    ctx = FormalContext()
    for g, attrs in sorted(incidence.items()):
        ctx.add_object(g, attrs)
    return ctx


@settings(max_examples=200, deadline=None, derandomize=True)
@given(incidence=contexts, x1=object_subsets, x2=object_subsets)
def test_prime_objects_is_antitone(incidence, x1, x2):
    ctx = make_ctx(incidence)
    a = x1 & ctx.all_objects
    b = (x1 | x2) & ctx.all_objects
    assert a <= b
    assert ctx.prime_objects(b) <= ctx.prime_objects(a)


@settings(max_examples=200, deadline=None, derandomize=True)
@given(incidence=contexts, y1=attr_subsets, y2=attr_subsets)
def test_prime_attrs_is_antitone(incidence, y1, y2):
    ctx = make_ctx(incidence)
    assert ctx.prime_attrs(y1 | y2) <= ctx.prime_attrs(y1)


@settings(max_examples=200, deadline=None, derandomize=True)
@given(incidence=contexts, x=object_subsets)
def test_closure_is_extensive_and_idempotent(incidence, x):
    ctx = make_ctx(incidence)
    x = x & ctx.all_objects
    closed = ctx.closure_objects(x)
    assert x <= closed                                  # X ⊆ extent(intent(X))
    assert ctx.closure_objects(closed) == closed        # idempotent


@settings(max_examples=200, deadline=None, derandomize=True)
@given(incidence=contexts, y=attr_subsets)
def test_attr_closure_is_extensive_and_idempotent(incidence, y):
    ctx = make_ctx(incidence)
    closed = ctx.closure_attrs(y)
    assert (y & ctx.all_attributes) <= closed
    assert ctx.closure_attrs(closed) == closed


@settings(max_examples=200, deadline=None, derandomize=True)
@given(incidence=contexts, x=object_subsets)
def test_triple_prime_equals_prime(incidence, x):
    ctx = make_ctx(incidence)
    x = x & ctx.all_objects
    primed = ctx.prime_objects(x)
    assert ctx.prime_objects(ctx.prime_attrs(primed)) == primed


@settings(max_examples=120, deadline=None, derandomize=True)
@given(incidence=contexts)
def test_lattice_extents_are_exactly_all_closures(incidence):
    """CbO over the clarified+reduced attribute set must enumerate exactly the
    nonempty closed extents (sigma=1 keeps everything with support >= 1)."""
    ctx = make_ctx(incidence)
    lattice = build_lattice(ctx, sigma=1)
    objs = sorted(ctx.all_objects)
    expected = set()
    for subset in chain.from_iterable(combinations(objs, r) for r in range(len(objs) + 1)):
        closed = ctx.closure_objects(subset)
        if closed:
            expected.add(closed)
    assert {c.extent for c in lattice.concepts.values()} == expected
    for c in lattice.concepts.values():
        # every concept is closed and intent matches the original context
        assert ctx.prime_objects(c.extent) == c.intent
        assert ctx.prime_attrs(c.intent) == c.extent
        assert 0.0 <= c.stability <= 1.0


@settings(max_examples=120, deadline=None, derandomize=True)
@given(incidence=contexts)
def test_cover_links_are_the_covering_relation(incidence):
    ctx = make_ctx(incidence)
    lattice = build_lattice(ctx, sigma=1)
    concepts = list(lattice.concepts.values())
    for c in concepts:
        for p_hash in c.parents:
            p = lattice.concepts[p_hash]
            assert c.extent < p.extent
            assert not any(
                z is not c and z is not p and c.extent < z.extent < p.extent
                for z in concepts
            )
        # no missed covers: any strict superset extent is reachable upward
        uppers = {z.intent_hash for z in concepts if c.extent < z.extent}
        assert uppers == lattice.ancestors(c.intent_hash)
