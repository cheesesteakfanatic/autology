"""Datatype inference + semantic typing tests (§3.2; aviation-flavored per §17.2.1)."""

from __future__ import annotations

import datetime

from ontoforge.contracts import Datatype
from ontoforge.profiling import (
    CONFIDENCE_FLOOR,
    SklearnSemanticHook,
    extract_semantic_features,
    infer_datatype,
    infer_semantic_type,
)

# ------------------------------------------------------------------ datatypes


def test_datatype_native_python():
    assert infer_datatype([1, 2, 3]) is Datatype.INTEGER
    assert infer_datatype([1.5, 2.0, None]) is Datatype.FLOAT
    assert infer_datatype([True, False]) is Datatype.BOOLEAN
    assert infer_datatype([datetime.date(2024, 1, 1)]) is Datatype.DATE
    assert infer_datatype([datetime.datetime(2024, 1, 1, 12)]) is Datatype.DATETIME
    assert infer_datatype([]) is Datatype.STRING


def test_datatype_string_parsing_with_dirty_tolerance():
    # 97% agreement threshold: a couple of dirty cells must not flip the type
    vals = [str(i) for i in range(200)] + ["n/a"]
    assert infer_datatype(vals) is Datatype.INTEGER
    assert infer_datatype(["1.5", "2.25", "3.0"]) is Datatype.FLOAT
    assert infer_datatype(["2024-01-15", "2023-12-09"]) is Datatype.DATE
    assert infer_datatype(["2024-01-15T10:00:00", "2023-12-09 08:30:00"]) is Datatype.DATETIME
    assert infer_datatype(["yes", "no", "yes"]) is Datatype.BOOLEAN


def test_zero_padded_codes_stay_string():
    # zero-padded identifiers losing padding is a classic silent corruption
    assert infer_datatype(["00123", "00456", "07890"]) is Datatype.STRING


def test_long_narrative_becomes_text():
    narr = ["The aircraft departed runway 27 and during initial climb the crew "
            "observed an unsafe gear indication and elected to return to the field "
            "for an uneventful landing after holding to burn fuel." ] * 5
    assert infer_datatype(narr) is Datatype.TEXT


# -------------------------------------------------------------- semantic rules


def test_tail_numbers():
    vals = ["N123AB", "N4567", "N89C", "N1", "N5523K"]
    label, conf = infer_semantic_type(vals, "tail_number")
    assert label == "tail_number"
    assert conf >= CONFIDENCE_FLOOR


def test_icao_codes_need_a_name_hint():
    vals = ["KJFK", "KLAX", "EGLL", "KSFO", "KORD"]
    label, conf = infer_semantic_type(vals, "dest_icao")
    assert label == "icao_code" and conf >= CONFIDENCE_FLOOR
    # formatting-generic: 4 uppercase letters under a non-airport name must NOT clear the floor
    label2, _ = infer_semantic_type(vals, "misc_code")
    assert label2 != "icao_code"


def test_emails():
    vals = ["pilot@example.com", "ops@faa.gov", "a.b@x.org"]
    label, conf = infer_semantic_type(vals, "contact_email")
    assert label == "email" and conf >= CONFIDENCE_FLOOR


def test_us_states():
    vals = ["CA", "TX", "NY", "WA", "FL", "AK"]
    label, conf = infer_semantic_type(vals, "state")
    assert label == "us_state" and conf >= CONFIDENCE_FLOOR


def test_currency_symbol_strings_and_named_numeric():
    label, conf = infer_semantic_type(["$1,234.56", "$99.00", "$12,000.00"], "total")
    assert label == "currency_amount" and conf >= CONFIDENCE_FLOOR
    label2, conf2 = infer_semantic_type([1200.5, 870.0, 15000.0], "fare_usd")
    assert label2 == "currency_amount" and conf2 >= CONFIDENCE_FLOOR


def test_dates_and_narrative():
    label, _ = infer_semantic_type([datetime.date(2024, 1, 1), datetime.date(2024, 2, 2)], "flight_date")
    assert label == "date"
    narr = ["While taxiing to the gate the first officer noticed a fuel truck crossing "
            "without clearance and stopped the aircraft; ground control was advised and "
            "operations resumed without further incident after a short delay."] * 4
    label2, _ = infer_semantic_type(narr, "narrative")
    assert label2 == "narrative_text"


def test_unmatched_returns_empty_with_zero_confidence():
    label, conf = infer_semantic_type(["zzz", "qqq", "www"], "blob")
    assert label == "" and conf == 0.0


# ------------------------------------------------------------ classifier hook


class _FixedClassifier:
    def classify(self, features, column_name):
        assert "avg_len" in features and "digit_frac" in features
        return ("airport_name", 0.91)


def test_classifier_hook_consulted_only_when_rules_miss():
    vals = ["Chicago O'Hare Intl", "Los Angeles Intl", "Heathrow"]
    label, conf = infer_semantic_type(vals, "facility", classifier=_FixedClassifier())
    assert label == "airport_name" and conf == 0.91
    # a rule hit must short-circuit the hook
    label2, _ = infer_semantic_type(["N123AB", "N4567"], "tail_number", classifier=_FixedClassifier())
    assert label2 == "tail_number"


def test_sklearn_hook_adapter_with_real_estimator():
    from sklearn.tree import DecisionTreeClassifier

    # tiny real fit: discriminate "numeric-ish" vs "wordy" columns by features
    wordy = extract_semantic_features(["some words here", "more words present"], "a")
    nums = extract_semantic_features(["123", "456", "789"], "b")
    feats = sorted(wordy)
    X = [[wordy[f] for f in feats], [nums[f] for f in feats]]
    est = DecisionTreeClassifier(random_state=0).fit(X, [0, 1])
    hook = SklearnSemanticHook(est, labels=["wordy", "numericish"], feature_names=feats)
    label, conf = infer_semantic_type(["101", "202", "303"], "qcol", classifier=hook)
    # zero-padded? no — plain ints parse as INTEGER; no rule fires; hook decides
    assert label == "numericish" and conf >= CONFIDENCE_FLOOR
