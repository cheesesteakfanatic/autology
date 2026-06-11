"""Format-signature lattice tests (§3.1: minimal covering signature over samples)."""

from __future__ import annotations

import re

from hypothesis import given, settings
from hypothesis import strategies as st

from ontoforge.profiling import format_signature, generalize, render, to_regex, tokenize


def test_tokenize_run_length_collapses():
    toks = tokenize("N123AB")
    assert [(t.cls, t.lo, t.hi) for t in toks] == [("A", 1, 1), ("D", 3, 3), ("A", 2, 2)]


def test_tail_number_signature():
    values = ["N12345", "N1", "N123AB", "N99X", "N4567"]
    assert format_signature(values) == "A D{1,5} A{0,2}"


def test_date_signature_with_literal_dashes():
    values = ["2024-01-15", "2023-12-09", "1995-03-31"]
    assert format_signature(values) == "D{4} - D{2} - D{2}"


def test_icao_signature():
    assert format_signature(["KJFK", "KLAX", "EGLL", "KSFO"]) == "A{4}"


def test_mixed_case_widens_to_letter_class():
    # alignment may tie-break to an optional-token form; the lattice invariants are:
    # every sample covered, widening stops at L (letter), never ANY
    values = ["Abc", "abc", "ABC"]
    toks = generalize(values)
    rx = re.compile(to_regex(toks))
    for v in values:
        assert rx.fullmatch(v), v
    sig = render(toks)
    assert "L" in sig and "ANY" not in sig
    # same-shape case mixing collapses cleanly to the letter class
    assert format_signature(["abc", "ABC"]) == "L{3}"


def test_signature_regex_covers_every_sample():
    values = ["N12345", "N1", "N123AB", "FL-350", "12:30", "a_b_c"]
    rx = re.compile(to_regex(generalize(values)))
    for v in values:
        assert rx.fullmatch(v), v


def test_space_renders_visibly():
    sig = format_signature(["AB 12", "CD 34"])
    assert "␣" in sig


def test_empty_and_blank_inputs():
    assert format_signature([]) == ""
    assert format_signature(["", ""]) == ""


@settings(derandomize=True, max_examples=60, deadline=None)
@given(st.lists(st.text(alphabet=st.characters(min_codepoint=32, max_codepoint=126), min_size=1, max_size=12),
                min_size=1, max_size=8))
def test_property_minimal_covering_signature_matches_all(strings):
    """Lattice invariant: every sampled value matches the generalized signature."""
    toks = generalize(strings)
    rx = re.compile(to_regex(toks))
    for s in strings:
        assert rx.fullmatch(s), (s, render(toks))


def test_generalize_deterministic_across_input_order():
    a = ["N123AB", "N1", "N99X"]
    b = list(reversed(a))
    assert render(generalize(a)) == render(generalize(b))
