"""Datatype inference + rule-based semantic typing with a classifier hook (§3.2).

Datatype (contracts.Datatype) comes from value parsing over a sample: native Python
types map directly; strings are tested against boolean/int/float/date/datetime
parsers with a 97% agreement threshold (a few dirty cells must not flip a column's
type). Long/high-token string columns become TEXT (textJoin-eligible).

Semantic typing is a two-stage cascade mirroring the spine's tiering:
  T0/T1 rules — a registry of (regex | predicate) rules with name hints; confidence
  is match_fraction x rule prior + a name-hint bonus. Aviation-flavored rules
  (tail numbers, ICAO codes) sit alongside generic ones (emails, US states,
  currency amounts, dates, narrative text).
  Classifier hook — anything implementing SemanticClassifier (e.g. the provided
  SklearnSemanticHook wrapping a fitted sklearn estimator over
  extract_semantic_features vectors, Sherlock/Sato-class per §3.1) is consulted
  when no rule clears the confidence floor.

Deliberate asymmetries:
- ICAO codes are formatting-generic (any 4 uppercase letters), so the rule prior is
  low and only a name hint (icao/airport/origin/dest/...) lifts it past the floor.
- integers with leading zeros ('00123') are treated as string codes, not numbers —
  zero-padded identifiers silently losing their padding is a classic corruption.
"""

from __future__ import annotations

import datetime as _dt
import re
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Optional, Protocol, Sequence

from ontoforge.contracts import Datatype

from ._values import display_str, is_null

__all__ = [
    "infer_datatype",
    "infer_semantic_type",
    "extract_semantic_features",
    "SemanticClassifier",
    "SklearnSemanticHook",
    "SEMANTIC_RULES",
    "SemanticRule",
    "CONFIDENCE_FLOOR",
]

CONFIDENCE_FLOOR = 0.6
_PARSE_AGREEMENT = 0.97

# ------------------------------------------------------------------ datatypes

_BOOL_TOKENS = {"true", "false", "t", "f", "yes", "no", "y", "n"}
_INT_RE = re.compile(r"^[+-]?\d+$")
_FLOAT_RE = re.compile(r"^[+-]?(\d+\.\d*|\.\d+|\d+)([eE][+-]?\d+)?$")
_DATE_FORMATS = ("%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y", "%d-%b-%Y", "%Y%m%d")
_DT_FORMATS = (
    "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S%z",
    "%Y-%m-%d %H:%M", "%m/%d/%Y %H:%M",
)


def _parses_date(s: str) -> bool:
    for fmt in _DATE_FORMATS:
        try:
            _dt.datetime.strptime(s, fmt)
            return True
        except ValueError:
            continue
    return False


def _parses_datetime(s: str) -> bool:
    for fmt in _DT_FORMATS:
        try:
            _dt.datetime.strptime(s, fmt)
            return True
        except ValueError:
            continue
    return False


def _is_int_str(s: str) -> bool:
    if not _INT_RE.match(s):
        return False
    digits = s.lstrip("+-")
    return not (len(digits) > 1 and digits[0] == "0")  # zero-padded => identifier code


def _is_float_str(s: str) -> bool:
    if not _FLOAT_RE.match(s):
        return False
    digits = s.lstrip("+-")
    if "." not in digits and "e" not in digits.lower() and len(digits) > 1 and digits[0] == "0":
        return False  # pure-digit zero-padded => identifier code, not a number
    return True


def _frac(values: Sequence[str], pred: Callable[[str], bool]) -> float:
    if not values:
        return 0.0
    return sum(1 for v in values if pred(v)) / len(values)


def infer_datatype(values: Sequence[Any], max_samples: int = 1000) -> Datatype:
    nn = [v for v in values if not is_null(v)][:max_samples]
    if not nn:
        return Datatype.STRING
    if all(isinstance(v, bool) for v in nn):
        return Datatype.BOOLEAN
    if all(isinstance(v, int) and not isinstance(v, bool) for v in nn):
        return Datatype.INTEGER
    if all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in nn):
        return Datatype.FLOAT
    if all(isinstance(v, _dt.datetime) for v in nn):
        return Datatype.DATETIME
    if all(isinstance(v, _dt.date) and not isinstance(v, _dt.datetime) for v in nn):
        return Datatype.DATE
    # heterogeneous natives that are all date-kind
    if all(isinstance(v, _dt.date) for v in nn):
        return Datatype.DATETIME

    strs = [display_str(v).strip() for v in nn]
    strs = [s for s in strs if s != ""]
    if not strs:
        return Datatype.STRING
    if _frac(strs, lambda s: s.lower() in _BOOL_TOKENS) >= _PARSE_AGREEMENT:
        return Datatype.BOOLEAN
    if _frac(strs, _is_int_str) >= _PARSE_AGREEMENT:
        return Datatype.INTEGER
    if _frac(strs, _is_float_str) >= _PARSE_AGREEMENT:
        return Datatype.FLOAT
    if _frac(strs, _parses_date) >= _PARSE_AGREEMENT:
        return Datatype.DATE
    if _frac(strs, _parses_datetime) >= _PARSE_AGREEMENT:
        return Datatype.DATETIME
    avg_len = sum(len(s) for s in strs) / len(strs)
    avg_tokens = sum(len(s.split()) for s in strs) / len(strs)
    if avg_len > 80 or avg_tokens > 12:
        return Datatype.TEXT
    return Datatype.STRING


# ------------------------------------------------------------- semantic rules

_US_STATES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID", "IL", "IN",
    "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV",
    "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC", "SD", "TN",
    "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY", "DC", "PR",
}


@dataclass(frozen=True, slots=True)
class SemanticRule:
    name: str
    matcher: Callable[[Sequence[str], str, Datatype], float]  # -> match fraction in [0,1]
    prior: float                       # rule confidence prior given a full match
    name_hints: tuple[str, ...] = ()   # substring hints in the column name
    hint_bonus: float = 0.1
    min_fraction: float = 0.9


def _regex_matcher(pattern: str) -> Callable[[Sequence[str], str, Datatype], float]:
    rx = re.compile(pattern)
    def match(values: Sequence[str], _name: str, _dt_: Datatype) -> float:
        if not values:
            return 0.0
        return sum(1 for v in values if rx.fullmatch(v.strip())) / len(values)
    return match


def _us_state_matcher(values: Sequence[str], _name: str, _dt_: Datatype) -> float:
    if not values:
        return 0.0
    return sum(1 for v in values if v.strip().upper() in _US_STATES and len(v.strip()) == 2) / len(values)


def _date_matcher(values: Sequence[str], _name: str, dtype: Datatype) -> float:
    return 1.0 if dtype in (Datatype.DATE, Datatype.DATETIME) else 0.0


def _narrative_matcher(values: Sequence[str], _name: str, dtype: Datatype) -> float:
    if not values or dtype not in (Datatype.TEXT, Datatype.STRING):
        return 0.0
    avg_len = sum(len(v) for v in values) / len(values)
    avg_tokens = sum(len(v.split()) for v in values) / len(values)
    return 1.0 if (avg_len > 100 and avg_tokens > 15) else 0.0


def _currency_matcher(values: Sequence[str], name: str, dtype: Datatype) -> float:
    sym_rx = re.compile(r"[$€£]\s?-?\d{1,3}(,\d{3})*(\.\d+)?")
    if values:
        sym_frac = sum(1 for v in values if sym_rx.fullmatch(v.strip())) / len(values)
        if sym_frac >= 0.9:
            return sym_frac
    if dtype in (Datatype.FLOAT, Datatype.INTEGER):
        low = name.lower()
        if any(h in low for h in ("usd", "eur", "gbp", "cost", "price", "amount", "fare",
                                  "revenue", "total_due", "charge")):
            return 1.0
    return 0.0


SEMANTIC_RULES: tuple[SemanticRule, ...] = (
    SemanticRule("email", _regex_matcher(r"[^@\s]+@[^@\s]+\.[A-Za-z]{2,}"), 0.95,
                 ("email", "mail")),
    SemanticRule("tail_number", _regex_matcher(r"N\d{1,5}[A-Z]{0,2}"), 0.9,
                 ("tail", "n_number", "nnumber", "registration", "reg", "aircraft")),
    SemanticRule("us_state", _us_state_matcher, 0.85, ("state", "st"), min_fraction=0.95),
    SemanticRule("icao_code", _regex_matcher(r"[A-Z]{4}"), 0.5,
                 ("icao", "airport", "apt", "dep", "dest", "arr", "origin", "station", "facility"),
                 hint_bonus=0.4, min_fraction=0.95),
    SemanticRule("currency_amount", _currency_matcher, 0.85,
                 ("usd", "cost", "price", "amount", "fare", "revenue")),
    SemanticRule("date", _date_matcher, 0.9, ("date", "dt", "day", "time")),
    SemanticRule("narrative_text", _narrative_matcher, 0.9,
                 ("narrative", "description", "remarks", "synopsis", "report", "text", "comment")),
)


# ----------------------------------------------------------- classifier hook


class SemanticClassifier(Protocol):
    """T1 hook (§3.1 Sherlock/Sato-class). Returns (label, confidence) or None."""

    def classify(self, features: Mapping[str, float], column_name: str) -> Optional[tuple[str, float]]: ...


def extract_semantic_features(values: Sequence[str], column_name: str = "") -> dict[str, float]:
    """Numeric feature vector over sampled string values for classifier hooks."""
    if not values:
        return {k: 0.0 for k in ("avg_len", "max_len", "digit_frac", "alpha_frac", "upper_frac",
                                 "punct_frac", "distinct_ratio", "avg_tokens", "numeric_frac")}
    total_chars = sum(len(v) for v in values) or 1
    digits = sum(sum(c.isdigit() for c in v) for v in values)
    alphas = sum(sum(c.isalpha() for c in v) for v in values)
    uppers = sum(sum(c.isupper() for c in v) for v in values)
    puncts = sum(sum(not c.isalnum() and not c.isspace() for c in v) for v in values)
    return {
        "avg_len": total_chars / len(values),
        "max_len": float(max(len(v) for v in values)),
        "digit_frac": digits / total_chars,
        "alpha_frac": alphas / total_chars,
        "upper_frac": uppers / total_chars,
        "punct_frac": puncts / total_chars,
        "distinct_ratio": len(set(values)) / len(values),
        "avg_tokens": sum(len(v.split()) for v in values) / len(values),
        "numeric_frac": sum(1 for v in values if _FLOAT_RE.match(v.strip()) is not None) / len(values),
    }


FEATURE_NAMES: tuple[str, ...] = (
    "avg_len", "max_len", "digit_frac", "alpha_frac", "upper_frac",
    "punct_frac", "distinct_ratio", "avg_tokens", "numeric_frac",
)


class SklearnSemanticHook:
    """Adapter: a fitted sklearn classifier (predict_proba over FEATURE_NAMES order)."""

    def __init__(self, estimator: Any, labels: Sequence[str],
                 feature_names: Sequence[str] = FEATURE_NAMES, min_confidence: float = 0.6) -> None:
        self._est = estimator
        self._labels = list(labels)
        self._features = list(feature_names)
        self._min_conf = min_confidence

    def classify(self, features: Mapping[str, float], column_name: str) -> Optional[tuple[str, float]]:
        vec = [[float(features.get(f, 0.0)) for f in self._features]]
        proba = self._est.predict_proba(vec)[0]
        best = int(max(range(len(proba)), key=lambda i: proba[i]))
        conf = float(proba[best])
        if conf < self._min_conf:
            return None
        return self._labels[best], conf


# ------------------------------------------------------------------ pipeline


def infer_semantic_type(
    values: Sequence[Any],
    column_name: str,
    datatype: Optional[Datatype] = None,
    classifier: Optional[SemanticClassifier] = None,
    max_samples: int = 512,
) -> tuple[str, float]:
    strs = [display_str(v) for v in values if not is_null(v)][:max_samples]
    strs = [s for s in strs if s != ""]
    dtype = datatype if datatype is not None else infer_datatype(values)
    low_name = column_name.lower()

    best_label, best_conf = "", 0.0
    for rule in SEMANTIC_RULES:
        frac = rule.matcher(strs, column_name, dtype)
        if frac < rule.min_fraction:
            continue
        conf = frac * rule.prior
        if any(h in low_name for h in rule.name_hints):
            conf = min(0.99, conf + rule.hint_bonus)
        if conf > best_conf:
            best_label, best_conf = rule.name, conf
    if best_conf >= CONFIDENCE_FLOOR:
        return best_label, round(best_conf, 4)

    if classifier is not None and strs:
        hit = classifier.classify(extract_semantic_features(strs, column_name), column_name)
        if hit is not None:
            label, conf = hit
            if conf >= CONFIDENCE_FLOOR:
                return label, round(float(conf), 4)
    return "", 0.0
