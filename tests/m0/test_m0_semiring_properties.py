"""Property tests: semiring axioms for N[X] terms and valuation homomorphisms (§9).

Strategy for comparisons (per spec):
- where the smart-constructor normal form makes an axiom hold STRUCTURALLY
  (associativity, identities, annihilation), compare term_hash of normalized forms;
- where normalization differs structurally (commutativity, distributivity),
  compare under the 'citations' + 'derivable' (+ 'confidence') valuations,
  run through the REAL ledger (intern -> valuate_ref).
"""

import math

from hypothesis import given, settings

from m0_strategies import CONF, terms
from ontoforge.contracts.provenance import ONE, ZERO, leaf, prov_prod, prov_sum, term_hash
from ontoforge.ledger import SqliteLedger

# One shared in-memory ledger: interning is idempotent, so reuse across
# hypothesis examples is safe and fast.
LEDGER = SqliteLedger(":memory:")

_SETTINGS = settings(derandomize=True, max_examples=150, deadline=None)


def val(term, name):
    ref = LEDGER.intern(term)
    if name == "confidence":
        return LEDGER.valuate_ref(ref, name, atom_confidence=CONF)
    return LEDGER.valuate_ref(ref, name)


def assert_same_under_valuations(x, y):
    assert val(x, "citations") == val(y, "citations")
    assert val(x, "derivable") == val(y, "derivable")
    assert math.isclose(val(x, "confidence"), val(y, "confidence"), rel_tol=1e-12, abs_tol=1e-15)


# ----------------------------------------------------------------- axioms


@_SETTINGS
@given(terms(), terms(), terms())
def test_sum_associativity_exact(a, b, c):
    lhs = prov_sum([prov_sum([a, b]), c])
    rhs = prov_sum([a, prov_sum([b, c])])
    assert term_hash(lhs) == term_hash(rhs)
    assert lhs == rhs


@_SETTINGS
@given(terms(), terms(), terms())
def test_prod_associativity_exact(a, b, c):
    lhs = prov_prod([prov_prod([a, b]), c])
    rhs = prov_prod([a, prov_prod([b, c])])
    assert term_hash(lhs) == term_hash(rhs)
    assert lhs == rhs


@_SETTINGS
@given(terms(), terms())
def test_sum_commutativity_under_valuations(a, b):
    assert_same_under_valuations(prov_sum([a, b]), prov_sum([b, a]))


@_SETTINGS
@given(terms(), terms())
def test_prod_commutativity_under_valuations(a, b):
    assert_same_under_valuations(prov_prod([a, b]), prov_prod([b, a]))


@_SETTINGS
@given(terms())
def test_additive_identity_exact(a):
    assert term_hash(prov_sum([a, ZERO])) == term_hash(a)
    assert term_hash(prov_sum([ZERO, a])) == term_hash(a)


@_SETTINGS
@given(terms())
def test_multiplicative_identity_exact(a):
    assert term_hash(prov_prod([a, ONE])) == term_hash(a)
    assert term_hash(prov_prod([ONE, a])) == term_hash(a)


@_SETTINGS
@given(terms())
def test_annihilation_exact(a):
    assert prov_prod([a, ZERO]) == ZERO
    assert prov_prod([ZERO, a]) == ZERO
    assert term_hash(prov_prod([a, ZERO])) == term_hash(ZERO)


@_SETTINGS
@given(terms(), terms(), terms())
def test_distributivity_under_valuations(a, b, c):
    lhs = prov_prod([a, prov_sum([b, c])])
    rhs = prov_sum([prov_prod([a, b]), prov_prod([a, c])])
    assert_same_under_valuations(lhs, rhs)


# -------------------------------------------------- valuation homomorphism


@_SETTINGS
@given(terms(), terms())
def test_citations_homomorphism(a, b):
    # + always merges supports.
    assert val(prov_sum([a, b]), "citations") == val(a, "citations") | val(b, "citations")
    # x merges supports unless a factor is ZERO (annihilation: no derivation at all).
    if a == ZERO or b == ZERO:
        expected = frozenset()
    else:
        expected = val(a, "citations") | val(b, "citations")
    assert val(prov_prod([a, b]), "citations") == expected


@_SETTINGS
@given(terms(), terms())
def test_confidence_homomorphism(a, b):
    ca, cb = val(a, "confidence"), val(b, "confidence")
    assert math.isclose(
        val(prov_sum([a, b]), "confidence"), max(ca, cb), rel_tol=1e-12, abs_tol=1e-15
    )
    assert math.isclose(
        val(prov_prod([a, b]), "confidence"), ca * cb, rel_tol=1e-12, abs_tol=1e-15
    )


@_SETTINGS
@given(terms(), terms())
def test_derivable_homomorphism(a, b):
    da, db = val(a, "derivable"), val(b, "derivable")
    assert val(prov_sum([a, b]), "derivable") == (da or db)
    assert val(prov_prod([a, b]), "derivable") == (da and db)


@_SETTINGS
@given(terms())
def test_derivable_iff_nonzero(a):
    # On normalized terms ZERO is the unique non-derivable polynomial.
    assert val(a, "derivable") == (a != ZERO)


# ------------------------------------------------------- concrete anchors


def test_named_valuations_concrete():
    led = SqliteLedger()
    t = prov_sum([prov_prod([leaf("x"), leaf("y")]), leaf("z")])
    ref = led.intern(t)
    assert led.valuate_ref(ref, "citations") == frozenset({"x", "y", "z"})
    assert led.valuate_ref(ref, "derivable") is True
    # default leaf confidence 1.0: max(1*1, 1) = 1
    assert led.valuate_ref(ref, "confidence") == 1.0
    conf = {"x": 0.5, "y": 0.4, "z": 0.1}
    assert math.isclose(
        led.valuate_ref(ref, "confidence", atom_confidence=conf), max(0.5 * 0.4, 0.1)
    )
    zref = led.intern(ZERO)
    assert led.valuate_ref(zref, "derivable") is False
    assert led.valuate_ref(zref, "citations") == frozenset()
    assert led.valuate_ref(zref, "confidence") == 0.0
    oref = led.intern(ONE)
    assert led.valuate_ref(oref, "derivable") is True
    assert led.valuate_ref(oref, "confidence") == 1.0


def test_unknown_valuation_raises():
    led = SqliteLedger()
    ref = led.intern(leaf("x"))
    try:
        led.valuate_ref(ref, "cost")
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for unknown valuation name")
