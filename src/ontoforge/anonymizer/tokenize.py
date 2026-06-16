"""Deterministic, join- and structure-preserving tokenization (v2.1 §7).

OPEN-SHELL (docs/IP_ARCHITECTURE.md): runs on the customer machine, stdlib crypto
only. Equal raw value → equal token via HMAC-SHA256 keyed by the customer secret,
so a value that joins across tables STILL JOINS after anonymization. This module
decides WHAT to tokenize (the PII/sensitivity policy) and HOW (string token,
order-preserving numeric map, keyed date map), never WHERE the keymap lives.

Three token families, each chosen to keep a different engine signal alive:

* **String / categorical** — HMAC token. Optionally *format-preserving* (keep the
  source length + per-character class) so the profiler format-signature and any
  shape-sensitive engine logic still behave. Relabeling is injective, so value
  overlap / containment / MinHash-Jaccard and the value-frequency distribution
  (JSD, entropy) are preserved exactly.
* **Numeric** — a keyed, strictly **monotone** affine map per (column) dimension.
  Order and the decile/quantile *shape* survive (so quantile-divergence still
  fires), the literal magnitudes are scrambled, and — because the map is a pure
  function of the value — equal numbers still collide, so numeric joins survive.
* **Date / datetime** — the date is reduced to an ordinal day/second count and run
  through the same monotone numeric map, then re-rendered, so temporal ordering
  and spacing-shape survive.

Everything is deterministic for a fixed key and zero-network.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import hmac
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

# Reuse the established PII patterns from the secure layer (data minimization is
# already implemented there); the sensitivity policy is the same vocabulary.
from ontoforge.aimodels.secure import PII_GAZETTEER

__all__ = [
    "Sensitivity",
    "Policy",
    "Tokenizer",
    "classify_column",
    "classify_value",
    "derive_subkey",
    "monotone_numeric_map",
    "TOKEN_PREFIX",
]

# Token marker. A token is structurally recognizable so :func:`decipher` and the
# leak-scan can find it, and so the profiler never mistakes a token for raw PII.
TOKEN_PREFIX = "OFX"

_EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_PHONE = re.compile(r"(?<!\d)(?:\+?\d{1,3}[\s.-]?)?(?:\(?\d{2,4}\)?[\s.-]?){2,4}\d{2,4}(?!\d)")
_SSN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_CARD = re.compile(r"\b(?:\d[ -]?){13,16}\b")

# column-name hints that mark a column sensitive regardless of value shape
_NAME_HINTS = (
    "email", "mail", "phone", "tel", "mobile", "ssn", "social", "card", "ccn",
    "name", "fname", "lname", "first", "last", "surname", "address", "addr",
    "street", "dob", "birth", "passport", "license", "account", "acct", "iban",
    "patient", "customer_name", "user_name", "username", "full_name",
)


class Sensitivity(str, Enum):
    """Why a column was selected (or not) for tokenization."""

    PII = "pii"                # matched an email/phone/SSN/card/name signal
    IDENTIFIER = "identifier"  # join-key-shaped id (tokenize to keep joins private)
    QUASI = "quasi"            # quasi-identifier by column-name hint
    NONE = "none"              # not sensitive — passes through untouched


def derive_subkey(key: bytes, label: str) -> bytes:
    """HKDF-style domain separation: a 32-byte subkey for ``label`` under ``key``.

    Each use (token derivation, numeric mapping, keymap encryption) draws an
    INDEPENDENT subkey so that, e.g., a token can never be confused with a cipher
    keystream byte. Pure HMAC-SHA256, deterministic."""
    if isinstance(key, str):  # be forgiving — accept a passphrase
        key = key.encode("utf-8")
    return hmac.new(key, b"ontoforge/anonymizer/v1/" + label.encode("utf-8"), hashlib.sha256).digest()


# --------------------------------------------------------------------------
# Sensitivity classification (reuse aimodels.secure patterns)
# --------------------------------------------------------------------------

#: a value matches PII when it looks like an email/phone/SSN/card or is a
#: gazetteer name. Mirrors ``aimodels.secure.redact_pii`` so the toolkit and the
#: prompt-time redactor agree on what "sensitive" means.
_GAZETTEER = {n.lower() for n in PII_GAZETTEER}


def classify_value(value: str) -> bool:
    """True if a single string value looks like PII (email/phone/SSN/card/name)."""
    if not value:
        return False
    if _EMAIL.search(value) or _SSN.search(value) or _CARD.search(value) or _PHONE.search(value):
        return True
    stripped = value.strip().lower()
    return stripped in _GAZETTEER


def _name_hint(column: str) -> bool:
    c = column.lower()
    return any(h in c for h in _NAME_HINTS)


def classify_column(
    column: str,
    values: list,
    *,
    pii_fraction: float = 0.3,
    sample_cap: int = 500,
) -> Sensitivity:
    """Decide a column's sensitivity from its NAME and a small value sample.

    Deterministic. PII when a name hint matches *or* ≥ ``pii_fraction`` of the
    sampled non-null values match a PII pattern. A column whose name hints at
    PII but whose values do not match a pattern is still flagged QUASI (e.g. a
    free-text ``address``). Everything else is NONE (passed through)."""
    name_pii = _name_hint(column)
    seen = matched = 0
    for v in values[:sample_cap]:
        if v is None:
            continue
        s = _display(v)
        if not s:
            continue
        seen += 1
        if classify_value(s):
            matched += 1
    frac = (matched / seen) if seen else 0.0
    if frac >= pii_fraction:
        return Sensitivity.PII
    if name_pii:
        return Sensitivity.QUASI
    return Sensitivity.NONE


# --------------------------------------------------------------------------
# Policy: explicit per-column allow / deny over the classifier
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Policy:
    """What to tokenize. The classifier proposes; the allow/deny *decides*.

    * ``deny`` — never tokenize these columns (pass through), even if PII.
    * ``allow`` — always tokenize these columns, even if the classifier says NONE
      (the canonical case: join-KEY ids that are not "PII" but are the linkage we
      must keep private while still letting joins survive).
    * ``auto`` — when True (default), classify everything else and tokenize the
      PII / QUASI columns; when False, tokenize ONLY the ``allow`` set.
    * ``format_preserving`` — keep length + character class on string tokens.

    Column names are matched case-insensitively, by bare name or ``table.column``.
    """

    allow: frozenset[str] = field(default_factory=frozenset)
    deny: frozenset[str] = field(default_factory=frozenset)
    auto: bool = True
    format_preserving: bool = True
    pii_fraction: float = 0.3

    @staticmethod
    def _norm(name: str) -> str:
        return name.strip().lower()

    def _in(self, names: frozenset[str], table: str, column: str) -> bool:
        n = {self._norm(column), self._norm(f"{table}.{column}")}
        return any(self._norm(x) in n for x in names)

    def selects(self, table: str, column: str, values: list) -> tuple[bool, Sensitivity]:
        """Return (tokenize?, why). Deny wins over allow wins over auto."""
        if self._in(self.deny, table, column):
            return False, Sensitivity.NONE
        if self._in(self.allow, table, column):
            return True, Sensitivity.IDENTIFIER
        if not self.auto:
            return False, Sensitivity.NONE
        sens = classify_column(column, values, pii_fraction=self.pii_fraction)
        return (sens in (Sensitivity.PII, Sensitivity.QUASI)), sens


# --------------------------------------------------------------------------
# Display + numeric helpers
# --------------------------------------------------------------------------


def _display(v: object) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def _char_class_token(raw: str, digest: bytes) -> str:
    """Format-preserving token: same length, same per-character class.

    Digit→digit, upper→upper, lower→lower, everything else passes through. Keeps
    the profiler's format-signature stable. Driven by ``digest`` bytes so it is
    deterministic in the key, and we make it injective-enough by appending a short
    keyed suffix collision-guard only when the raw is too short to be unique. The
    keymap (not the format) is the source of truth for invertibility."""
    out: list[str] = []
    di = 0
    n = len(digest)
    for ch in raw:
        b = digest[di % n]
        di += 1
        if ch.isdigit():
            out.append(str(b % 10))
        elif ch.isupper():
            out.append(chr(ord("A") + (b % 26)))
        elif ch.islower():
            out.append(chr(ord("a") + (b % 26)))
        else:
            out.append(ch)  # punctuation/space structure preserved verbatim
    return "".join(out)


# --------------------------------------------------------------------------
# Order-preserving numeric map
# --------------------------------------------------------------------------


def monotone_numeric_map(key: bytes, dimension: str, *, integer: bool = False) -> tuple[float, float]:
    """A keyed strictly-increasing affine map ``x -> a*x + b`` for a numeric column.

    The slope ``a > 0`` (strictly positive ⇒ order-preserving: deciles map to
    deciles, ordering survives) and the offset ``b`` are derived from the customer
    key + a column ``dimension`` label, so the literal magnitudes are scrambled
    but ordering survives, and equal raw numbers collide identically (numeric
    joins survive). Distribution-divergence is preserved because two columns
    sharing a ``dimension`` share the SAME affine.

    For ``integer=True`` the slope is an INTEGER ≥ 1 and the offset an integer, so
    the map is **injective on the integers**: distinct integer keys never collapse
    onto the same token (which would both corrupt the keymap and destroy
    key-uniqueness, demoting a real FK). Floats use a real slope in ``[0.5, 2.5)``.

    Determinism: a pure function of (key, dimension, integer)."""
    sub = derive_subkey(key, f"numeric/{'i' if integer else 'f'}/{dimension}")
    a_raw = int.from_bytes(sub[:8], "big") / 2**64        # [0,1)
    b_raw = int.from_bytes(sub[8:16], "big") / 2**64      # [0,1)
    if integer:
        a = float(1 + int(a_raw * 8))                     # integer slope in 1..8
        b = float(int((b_raw - 0.5) * 2_000_000))         # integer offset
        return a, b
    a = 0.5 + 2.0 * a_raw                                 # strictly positive
    b = (b_raw - 0.5) * 2000.0
    return round(a, 12), round(b, 12)


_EPOCH = _dt.date(1970, 1, 1)
# the representable day-window for ``date``: [date.min, date.max] as ordinals
# from the 1970 epoch. The keyed date shift is wrapped into this window so a
# rendered token is ALWAYS a valid ISO date — including the ubiquitous ERP
# "forever" sentinel 9999-12-31, which sits at the very top of the range and
# would overflow under any naive positive shift.
_DAY_MIN = float((_dt.date.min - _EPOCH).days)        # year 1
_DAY_MAX = float((_dt.date.max - _EPOCH).days)        # year 9999
_DAY_SPAN = _DAY_MAX - _DAY_MIN + 1.0                 # inclusive day-ring size
# the representable second-window for ``datetime``: we build the rendered value
# as ``datetime.min + timedelta(seconds=...)`` (NOT ``fromtimestamp``, which has
# platform-specific 1970/epoch lower bounds), so the wrap window is the FULL
# [datetime.min, datetime.max] span. Anchoring at datetime.min and wrapping there
# guarantees a valid datetime for any input, including the year-9999 sentinel.
_DT_MIN = _dt.datetime.min
_SEC_SPAN = (_dt.datetime.max - _DT_MIN).total_seconds()


def _to_ordinal(value: object) -> Optional[tuple[float, str]]:
    """Map a date/datetime (or ISO string) to (ordinal_number, render_kind).

    Date ordinals are days from the 1970 epoch; datetime ordinals are SECONDS
    from ``datetime.min`` (a naive, platform-independent count — not a POSIX
    timestamp — so the year-9999 sentinel is representable)."""
    if isinstance(value, _dt.datetime):
        return (value - _DT_MIN).total_seconds(), "datetime"
    if isinstance(value, _dt.date):
        return float((value - _EPOCH).days), "date"
    if isinstance(value, str):
        s = value.strip()
        for fmt, kind in (("%Y-%m-%dT%H:%M:%S", "datetime"), ("%Y-%m-%d %H:%M:%S", "datetime"),
                          ("%Y-%m-%d", "date")):
            try:
                dtv = _dt.datetime.strptime(s, fmt)
            except ValueError:
                continue
            if kind == "date":
                return float((dtv.date() - _EPOCH).days), "date"
            return (dtv - _DT_MIN).total_seconds(), "datetime"
    return None


def _wrap_into(ordinal: float, lo: float, span: float) -> float:
    """Map ``ordinal`` into ``[lo, lo+span)`` by modular wrap.

    A pure translation on the date ordinal can run past ``date.max`` (e.g. the
    9999-12-31 sentinel shifted forward). Wrapping into the representable window
    keeps the rendered token a VALID date and is still **injective** (the wrap is
    a bijection on the integer day-ring) — so distinct raw dates stay distinct
    tokens and the value-frequency distribution is preserved. Order is preserved
    for every input that does not straddle the single wrap seam, which the keymap
    backstops regardless (decipher reads the keymap, never re-derives the date)."""
    return lo + ((ordinal - lo) % span)


def _render_ordinal(ordinal: float, kind: str) -> str:
    if kind == "date":
        day = _wrap_into(ordinal, _DAY_MIN, _DAY_SPAN)
        return (_EPOCH + _dt.timedelta(days=int(round(day)))).isoformat()
    sec = _wrap_into(ordinal, 0.0, _SEC_SPAN)
    return (_DT_MIN + _dt.timedelta(seconds=sec)).isoformat(sep="T")


# --------------------------------------------------------------------------
# The tokenizer
# --------------------------------------------------------------------------


@dataclass(slots=True)
class Tokenizer:
    """Deterministic value→token map under a customer key.

    Holds the per-run forward map (raw→token), so the same raw value always gets
    the same token within a run and across tables (join preservation), and so the
    reverse map can be sealed into the encrypted keymap. Holds NO raw plaintext
    beyond the forward map needed to build the keymap — and the caller seals that
    map and discards it.
    """

    key: bytes
    format_preserving: bool = True
    # raw -> token, partitioned by token family so a numeric 5 and a string "5"
    # never alias. Key is (family, dimension, raw_display).
    _forward: dict[tuple[str, str, str], str] = field(default_factory=dict)
    _numeric_maps: dict[str, tuple[float, float]] = field(default_factory=dict)
    _tok_key: bytes = b""

    def __post_init__(self) -> None:
        if isinstance(self.key, str):
            self.key = self.key.encode("utf-8")
        self._tok_key = derive_subkey(self.key, "token")

    # ---- string / categorical -------------------------------------------------

    def token_for_string(self, raw: str) -> str:
        cache = ("s", "", raw)
        hit = self._forward.get(cache)
        if hit is not None:
            return hit
        digest = hmac.new(self._tok_key, raw.encode("utf-8"), hashlib.sha256).digest()
        if self.format_preserving and raw:
            body = _char_class_token(raw, digest)
            # disambiguate: a short keyed tag keeps it injective without changing
            # the visible char-class shape's *type* (still alnum). Hex tag stays
            # within the STRING datatype band (never all-digits-only).
            tag = digest.hex()[:6]
            token = f"{body}~{TOKEN_PREFIX}{tag}"
        else:
            token = f"{TOKEN_PREFIX}_{digest.hex()[:24]}"
        self._forward[cache] = token
        return token

    # ---- numeric --------------------------------------------------------------

    def _numeric_map(self, dimension: str, *, integer: bool) -> tuple[float, float]:
        cache = f"{'i' if integer else 'f'}/{dimension}"
        m = self._numeric_maps.get(cache)
        if m is None:
            m = monotone_numeric_map(self.key, dimension, integer=integer)
            self._numeric_maps[cache] = m
        return m

    def token_for_number(self, raw: float, *, dimension: str = "default", as_int: bool = False) -> object:
        a, b = self._numeric_map(dimension, integer=as_int)
        if as_int:
            out: object = int(a) * int(raw) + int(b)   # injective on integers
        else:
            out = round(a * float(raw) + b, 6)
        cache = ("n", f"{'i' if as_int else 'f'}/{dimension}", _display(raw))
        self._forward.setdefault(cache, _display(out))
        return out

    # ---- date / datetime ------------------------------------------------------

    def token_for_date(self, raw: object, *, dimension: str = "date") -> Optional[str]:
        parsed = _to_ordinal(raw)
        if parsed is None:
            return None
        ordinal, kind = parsed
        # A keyed, order-preserving, INJECTIVE shift on the ordinal: slope 1
        # (a pure translation) keeps temporal spacing exact — so quantile/decile
        # shape and ordering survive — while the absolute dates are scrambled.
        sub = derive_subkey(self.key, f"date/{dimension}")
        shift = int.from_bytes(sub[:6], "big") % (4000 * 365)  # < ~4000 years of days/secs
        mapped = ordinal + float(shift)
        rendered = _render_ordinal(mapped, kind)
        self._forward[("d", dimension, _display(raw))] = rendered
        return rendered

    # ---- reverse map ----------------------------------------------------------

    def reverse_map(self) -> dict[str, str]:
        """token (as displayed string) → raw (display string). The seed for the
        encrypted keymap. One entry per distinct (family,dim,raw); a token string
        is globally unique across families because numeric/date tokens are still
        passed through the keymap by their rendered string and string tokens
        carry the ``OFX`` marker."""
        rev: dict[str, str] = {}
        for (_family, _dim, raw), token in self._forward.items():
            rev.setdefault(str(token), raw)
        return rev
