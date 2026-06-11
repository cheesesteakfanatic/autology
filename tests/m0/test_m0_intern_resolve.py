"""Two-level provenance interning: intern/resolve round-trip (§4.2)."""

import pytest
from hypothesis import given, settings

from m0_strategies import terms
from ontoforge.contracts.provenance import (
    ONE,
    ZERO,
    leaf,
    map_leaves,
    prov_prod,
    prov_sum,
    term_hash,
)
from ontoforge.ledger import SqliteLedger

LEDGER = SqliteLedger(":memory:")


@settings(derandomize=True, max_examples=250, deadline=None)
@given(terms(max_leaves=24))
def test_intern_resolve_roundtrip(t):
    ref = LEDGER.intern(t)
    norm = map_leaves(t, leaf)  # the contracts' normal form
    assert ref == term_hash(norm)
    resolved = LEDGER.resolve(ref)
    assert resolved == norm                      # exact round-trip on normalized terms
    assert term_hash(resolved) == ref            # hash-stable reconstruction
    assert LEDGER.intern(resolved) == ref        # idempotent re-intern


def test_roundtrip_repeated_atom_in_multiple_slots():
    led = SqliteLedger()
    t = prov_prod([leaf("x"), prov_sum([leaf("x"), leaf("y")]), leaf("x")])
    ref = led.intern(t)
    assert led.resolve(ref) == t


def test_roundtrip_zero_and_one():
    led = SqliteLedger()
    zref = led.intern(ZERO)
    oref = led.intern(ONE)
    assert led.resolve(zref) == ZERO
    assert led.resolve(oref) == ONE
    assert zref != oref


def test_unnormalized_input_interns_to_normal_form():
    led = SqliteLedger()
    # Build a messy equivalent of x*y + z by nesting; smart constructors flatten.
    messy = prov_sum([prov_sum([prov_prod([leaf("x"), prov_prod([leaf("y"), ONE])])]), leaf("z"), ZERO])
    clean = prov_sum([prov_prod([leaf("x"), leaf("y")]), leaf("z")])
    assert led.intern(messy) == led.intern(clean)
    assert led.resolve(led.intern(messy)) == clean


def test_resolve_unknown_ref_raises():
    led = SqliteLedger()
    with pytest.raises(KeyError):
        led.resolve("ffffffffffffffff")
