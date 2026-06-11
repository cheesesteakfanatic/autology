"""Unit tests for the from-scratch string-similarity primitives (M5 step 3)."""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from ontoforge.er.similarity import (
    char_ngrams,
    fuzzy_token_containment,
    fuzzy_token_jaccard,
    is_abbreviation,
    jaro,
    jaro_winkler,
    token_jaccard,
)


class TestJaroWinkler:
    """Reference values from Winkler (1990) / standard literature tables."""

    def test_martha(self):
        assert jaro("MARTHA", "MARHTA") == pytest.approx(0.944444, abs=1e-5)
        assert jaro_winkler("MARTHA", "MARHTA") == pytest.approx(0.961111, abs=1e-5)

    def test_dixon(self):
        assert jaro("DIXON", "DICKSONX") == pytest.approx(0.766667, abs=1e-5)
        assert jaro_winkler("DIXON", "DICKSONX") == pytest.approx(0.813333, abs=1e-5)

    def test_dwayne(self):
        assert jaro("DWAYNE", "DUANE") == pytest.approx(0.822222, abs=1e-5)
        assert jaro_winkler("DWAYNE", "DUANE") == pytest.approx(0.84, abs=1e-5)

    def test_identity_and_empty(self):
        assert jaro_winkler("ABC", "ABC") == 1.0
        assert jaro("", "") == 1.0
        assert jaro("", "X") == 0.0
        assert jaro_winkler("X", "") == 0.0

    def test_no_matches(self):
        assert jaro("ABC", "XYZ") == 0.0

    def test_prefix_cap(self):
        # prefix boost is capped at 4 characters
        long_a, long_b = "ABCDEFGH", "ABCDEXYZ"
        j = jaro(long_a, long_b)
        assert jaro_winkler(long_a, long_b) == pytest.approx(j + 4 * 0.1 * (1 - j), abs=1e-12)

    @settings(max_examples=100, deadline=None)
    @given(st.text(alphabet="ABCDEFG", max_size=12), st.text(alphabet="ABCDEFG", max_size=12))
    def test_properties(self, a, b):
        s = jaro_winkler(a, b)
        assert 0.0 <= s <= 1.0
        assert s == pytest.approx(jaro_winkler(b, a), abs=1e-12)  # symmetric
        if a == b:
            assert s == 1.0


class TestTokensAndGrams:
    def test_char_ngrams(self):
        assert char_ngrams("AB", 3) == {" AB", "AB "}
        assert char_ngrams("", 3) == set()

    def test_token_jaccard(self):
        assert token_jaccard({"A", "B"}, {"B", "C"}) == pytest.approx(1 / 3)
        assert token_jaccard(set(), set()) == 1.0
        assert token_jaccard({"A"}, set()) == 0.0

    def test_abbreviation(self):
        assert is_abbreviation("DEPT", "DEPARTMENT")
        assert is_abbreviation("INTL", "INTERNATIONAL")
        assert not is_abbreviation("XYZ", "DEPARTMENT")
        assert not is_abbreviation("DE", "DEPARTMENT")  # too short
        assert not is_abbreviation("DEPARTMENT", "DEPT")  # wrong direction

    def test_fuzzy_token_jaccard(self):
        # DEPT ~ DEPARTMENT via abbreviation; OF/THE dropped upstream
        a = ["US", "DEPT", "INTERIOR"]
        b = ["US", "DEPARTMENT", "INTERIOR"]
        assert fuzzy_token_jaccard(a, b) == 1.0
        assert fuzzy_token_jaccard([], []) == 1.0
        assert fuzzy_token_jaccard(["A"], []) == 0.0

    def test_containment(self):
        assert fuzzy_token_containment(["REPUBLIC", "AIRWAYS"], ["REPUBLIC", "AIRWAYS", "HOLDINGS"])
        assert not fuzzy_token_containment(["REPUBLIC", "AIRWAYS"], ["DELTA", "AIR", "LINES"])
        assert not fuzzy_token_containment([], ["X"])
