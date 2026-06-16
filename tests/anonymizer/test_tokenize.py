"""Tokenization invariants: determinism, join-preservation, structure-preservation."""

from __future__ import annotations

import datetime as _dt

from ontoforge.anonymizer import Policy, Sensitivity, Tokenizer, classify_column, monotone_numeric_map
from ontoforge.anonymizer.tokenize import TOKEN_PREFIX, classify_value

KEY = b"customer-secret-key-001"


# ----------------------------------------------------------------- determinism


def test_string_token_is_deterministic_for_a_fixed_key() -> None:
    a = Tokenizer(KEY)
    b = Tokenizer(KEY)
    assert a.token_for_string("alice@acme.com") == b.token_for_string("alice@acme.com")


def test_same_value_maps_to_same_token_across_calls_and_tables() -> None:
    """Join-preservation at the value level: equal raw → equal token, always."""
    tok = Tokenizer(KEY)
    t1 = tok.token_for_string("ACME-123")
    t2 = tok.token_for_string("ACME-123")
    assert t1 == t2
    # numeric join key: equal raw int → equal token int regardless of column
    assert tok.token_for_number(42, dimension="*", as_int=True) == tok.token_for_number(
        42, dimension="*", as_int=True
    )


def test_different_key_yields_different_token_same_structure() -> None:
    """A DIFFERENT key scrambles the token but keeps the structure (format)."""
    raw = "AB1234"
    t1 = Tokenizer(KEY).token_for_string(raw)
    t2 = Tokenizer(b"a-totally-different-key").token_for_string(raw)
    assert t1 != t2
    # both are format-preserving: same prefix layout (char-class body + OFX tag)
    assert TOKEN_PREFIX in t1 and TOKEN_PREFIX in t2


# ---------------------------------------------------------- structure-preserving


def test_format_preserving_keeps_length_and_char_classes() -> None:
    tok = Tokenizer(KEY, format_preserving=True)
    raw = "AB-1234"
    token = tok.token_for_string(raw)
    body = token.split("~", 1)[0]
    assert len(body) == len(raw)
    # digit→digit, upper→upper, punctuation preserved verbatim
    for rc, tc in zip(raw, body):
        if rc.isdigit():
            assert tc.isdigit()
        elif rc.isupper():
            assert tc.isupper()
        elif not rc.isalnum():
            assert tc == rc  # the dash stays a dash


def test_non_format_preserving_token_is_opaque() -> None:
    tok = Tokenizer(KEY, format_preserving=False)
    token = tok.token_for_string("alice@acme.com")
    assert token.startswith(f"{TOKEN_PREFIX}_")
    assert "@" not in token


# ------------------------------------------------------ numeric monotone map


def test_integer_map_is_strictly_monotone_and_injective() -> None:
    tok = Tokenizer(KEY)
    vals = list(range(0, 500))
    mapped = [tok.token_for_number(v, dimension="*", as_int=True) for v in vals]
    # strictly increasing (order preserved) and injective (no collisions)
    assert mapped == sorted(mapped)
    assert len(set(mapped)) == len(vals)


def test_float_map_preserves_order() -> None:
    tok = Tokenizer(KEY)
    vals = [0.1, 1.5, 3.0, 7.25, 100.0]
    mapped = [tok.token_for_number(v, dimension="*") for v in vals]
    assert mapped == sorted(mapped)


def test_monotone_map_slope_is_positive() -> None:
    a_f, _ = monotone_numeric_map(KEY, "*", integer=False)
    a_i, _ = monotone_numeric_map(KEY, "*", integer=True)
    assert a_f > 0
    assert a_i >= 1 and float(a_i).is_integer()


# ------------------------------------------------------------------- dates


def test_date_token_is_monotone_and_a_valid_date() -> None:
    tok = Tokenizer(KEY)
    dates = [_dt.date(2020, 1, 1), _dt.date(2021, 6, 15), _dt.date(2023, 12, 31)]
    mapped = [tok.token_for_date(d) for d in dates]
    assert all(m is not None for m in mapped)
    # ISO strings sort lexicographically in temporal order → order preserved
    assert mapped == sorted(mapped)
    # round-trips to a real date
    _dt.date.fromisoformat(mapped[0])


def test_iso_string_dates_tokenize_to_valid_dates() -> None:
    """A CSV-sourced date column arrives as ISO STRINGS (dtype=str). Each must map
    to a VALID, parseable, order-preserving date — not format-preserving string
    garbage (which would produce impossible months like 2025-62-17 and silently
    flip the engine's date-aware distribution signals)."""
    tok = Tokenizer(KEY)
    iso = ["2021-03-15", "2022-05-15", "2025-09-15"]
    mapped = [tok.token_for_date(s) for s in iso]
    assert all(m is not None for m in mapped)
    # every token is a real date, and order is preserved
    parsed = [_dt.date.fromisoformat(m) for m in mapped]
    assert parsed == sorted(parsed)
    # injective: distinct raw dates → distinct tokens (multiplicity/distribution preserved)
    assert len(set(mapped)) == len(set(iso))


def test_date_sentinel_9999_does_not_overflow() -> None:
    """The ubiquitous ERP "forever" sentinel 9999-12-31 sits at the top of the
    representable date range; a naive positive shift overflows date.max. It must
    instead wrap to a valid date without raising."""
    tok = Tokenizer(KEY)
    token = tok.token_for_date("9999-12-31")
    assert token is not None
    _dt.date.fromisoformat(token)  # valid, no OverflowError
    # the datetime sentinel must be safe too
    token_dt = tok.token_for_date("9999-12-31T23:59:59")
    assert token_dt is not None
    _dt.datetime.fromisoformat(token_dt)


def test_string_date_column_preserves_value_overlap() -> None:
    """The join-survival property at the date-column level: two columns sharing
    raw dates map those shared dates to the SAME tokens, so the cross-column value
    overlap (what the engine's IND / containment reads) is preserved exactly."""
    from ontoforge.anonymizer import anonymize
    from ontoforge.anonymizer.tokenize import Policy

    raw = {
        "a": {"launch": ["2021-03-15", "2021-06-15", "2022-05-15", "2025-09-15"]},
        "b": {"effective": ["2021-03-15", "2021-06-15", "2022-05-15", "2030-01-01"]},
    }
    pol = Policy(allow=frozenset({"launch", "effective"}))
    anon, _ = anonymize(raw, KEY, pol)
    raw_overlap = set(raw["a"]["launch"]) & set(raw["b"]["effective"])
    anon_overlap = set(anon["a"]["launch"]) & set(anon["b"]["effective"])
    assert len(raw_overlap) == len(anon_overlap) == 3
    # every anonymized date is still a valid ISO date
    for col in (anon["a"]["launch"], anon["b"]["effective"]):
        for v in col:
            _dt.date.fromisoformat(v.split("T", 1)[0])


# ------------------------------------------------------- sensitivity policy


def test_classify_value_detects_pii() -> None:
    assert classify_value("alice@acme.com")
    assert classify_value("123-45-6789")  # SSN
    assert classify_value("alice")        # gazetteer name
    assert not classify_value("widget-blue")


def test_classify_column_flags_email_and_name_columns() -> None:
    emails = [f"user{i}@acme.com" for i in range(50)]
    assert classify_column("email", emails) is Sensitivity.PII
    # a name-hinted column whose values are free text → QUASI
    assert classify_column("home_address", [f"{i} Elm St" for i in range(50)]) is Sensitivity.QUASI
    assert classify_column("widget_color", ["red", "blue", "green"] * 10) is Sensitivity.NONE


def test_policy_deny_beats_allow_beats_auto() -> None:
    emails = [f"user{i}@acme.com" for i in range(20)]
    # deny wins: never tokenize
    pol = Policy(deny=frozenset({"email"}), allow=frozenset({"email"}))
    take, _ = pol.selects("t", "email", emails)
    assert take is False
    # allow forces tokenization of a non-PII id
    pol2 = Policy(allow=frozenset({"id"}))
    take2, why2 = pol2.selects("t", "id", list(range(20)))
    assert take2 is True and why2 is Sensitivity.IDENTIFIER
    # auto=False tokenizes only the allow set
    pol3 = Policy(allow=frozenset({"id"}), auto=False)
    take3, _ = pol3.selects("t", "email", emails)
    assert take3 is False


def test_policy_matches_table_qualified_names() -> None:
    pol = Policy(allow=frozenset({"orders.customer_id"}))
    assert pol.selects("orders", "customer_id", list(range(10)))[0] is True
    assert pol.selects("returns", "customer_id", list(range(10)))[0] is False
