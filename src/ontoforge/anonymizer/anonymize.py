"""anonymize(tables, key, policy) → (anonymized_tables, encrypted_keymap) (v2.1 §7).

OPEN-SHELL (docs/IP_ARCHITECTURE.md). The customer-side entrypoint. Walks every
selected column, replaces each value with its deterministic token, and seals the
reverse map under the customer key. **NEVER writes a raw value anywhere except the
encrypted keymap** — :func:`scan_for_raw` is the test-grade proof of that.

Join preservation, concretely
------------------------------
* String / categorical tokens are a pure function of the value, so the same raw
  string is the same token in every table → string joins survive exactly.
* Numeric tokens use ONE shared, strictly-monotone keyed affine map by default
  (``numeric_dimension="*"``), so any two equal raw numbers map to equal tokens
  regardless of column name (an INTEGER FK still meets its DOUBLE PK, per the M3
  ``value_key`` normalization) while ordering + decile shape are preserved.
* Date tokens are monotone in the ordinal day/second, preserving temporal order.

A column the policy does NOT select is copied through verbatim.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from ._values_local import columns_of_local, is_null_local
from .keymap import EncryptedKeyMap, KeyMap, encrypt_keymap
from .tokenize import TOKEN_PREFIX, Policy, Sensitivity, Tokenizer

__all__ = [
    "AnonymizationResult",
    "anonymize",
    "anonymize_table",
    "anonymize_with_audit",
    "is_token",
    "scan_for_raw",
]


@dataclass(frozen=True, slots=True)
class AnonymizationResult:
    """What :func:`anonymize` returns alongside the encrypted keymap.

    ``tables`` is the anonymized corpus (same shape as the input). ``selected`` is
    the per-table list of tokenized columns with the sensitivity that selected
    them — the audit trail of WHAT was hidden and WHY."""

    tables: dict[str, dict[str, list]]
    keymap: EncryptedKeyMap
    selected: dict[str, list[tuple[str, Sensitivity]]]


def _is_intish(values: list) -> bool:
    """True when every non-null value is an integer (or integral float)."""
    saw = False
    for v in values:
        if is_null_local(v):
            continue
        saw = True
        if isinstance(v, bool):
            return False
        if isinstance(v, int):
            continue
        if isinstance(v, float) and float(v).is_integer():
            continue
        return False
    return saw


def _numeric_values(values: list) -> bool:
    saw = False
    for v in values:
        if is_null_local(v):
            continue
        saw = True
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            return False
    return saw


def _looks_date(values: list) -> bool:
    """True when every non-null sampled value is a date — a ``date``/``datetime``
    object OR an ISO date/datetime STRING.

    CSV-sourced columns arrive as strings (``dtype=str``), so a date column is a
    column of ISO strings, not datetime objects. Detecting them here routes the
    column through the keyed, order-preserving date map (valid, parseable,
    distribution-preserving) instead of format-preserving string tokenization
    (which yields unparseable garbage like month-62 and silently flips the
    engine's date-aware distribution signals)."""
    import datetime as _dt

    from .tokenize import _to_ordinal

    saw = False
    for v in values[:200]:
        if is_null_local(v):
            continue
        saw = True
        if isinstance(v, (_dt.date, _dt.datetime)):
            continue
        if isinstance(v, str) and _to_ordinal(v) is not None:
            continue
        return False
    return saw


def _tokenize_column(
    tok: Tokenizer,
    values: list,
    *,
    numeric_dimension: str,
) -> list:
    """Replace every non-null value with its token, preserving null positions."""
    if _looks_date(values):
        out: list = []
        for v in values:
            if is_null_local(v):
                out.append(None)
                continue
            t = tok.token_for_date(v)
            out.append(t if t is not None else tok.token_for_string(str(v)))
        return out
    if _numeric_values(values):
        as_int = _is_intish(values)
        return [
            None if is_null_local(v)
            else tok.token_for_number(v, dimension=numeric_dimension, as_int=as_int)
            for v in values
        ]
    # string / categorical / mixed → string token over the display form
    return [
        None if is_null_local(v) else tok.token_for_string(_disp(v))
        for v in values
    ]


def _disp(v: object) -> str:
    if isinstance(v, float) and float(v).is_integer():
        return str(int(v))
    return str(v)


def anonymize_table(
    tok: Tokenizer,
    table: str,
    data: Any,
    policy: Policy,
    *,
    numeric_dimension: str = "*",
) -> tuple[dict[str, list], list[tuple[str, Sensitivity]]]:
    """Anonymize one table against the SHARED tokenizer ``tok``.

    Sharing one tokenizer across tables is what makes cross-table joins survive:
    the forward map is global, so equal raw values collide on the same token in
    every table."""
    cols = columns_of_local(data)
    out: dict[str, list] = {}
    selected: list[tuple[str, Sensitivity]] = []
    for name, values in cols.items():
        take, why = policy.selects(table, name, values)
        if not take:
            out[name] = list(values)
            continue
        out[name] = _tokenize_column(tok, values, numeric_dimension=numeric_dimension)
        selected.append((name, why))
    return out, selected


def anonymize_with_audit(
    tables: dict[str, Any],
    key: bytes,
    policy: Optional[Policy] = None,
    *,
    numeric_dimension: str = "*",
    nonce: Optional[bytes] = None,
) -> AnonymizationResult:
    """Like :func:`anonymize` but returns the full :class:`AnonymizationResult`
    (tables + encrypted keymap + the per-table ``selected`` audit trail)."""
    if policy is None:
        policy = Policy()
    tok = Tokenizer(key=key, format_preserving=policy.format_preserving)

    anon: dict[str, dict[str, list]] = {}
    selected: dict[str, list[tuple[str, Sensitivity]]] = {}
    for name in sorted(tables):  # deterministic table order
        cols, sel = anonymize_table(tok, name, tables[name], policy, numeric_dimension=numeric_dimension)
        anon[name] = cols
        if sel:
            selected[name] = sel

    keymap = KeyMap(mapping=tok.reverse_map())
    enc = encrypt_keymap(keymap, key, nonce=nonce)
    return AnonymizationResult(tables=anon, keymap=enc, selected=selected)


def anonymize(
    tables: dict[str, Any],
    key: bytes,
    policy: Optional[Policy] = None,
    *,
    numeric_dimension: str = "*",
    nonce: Optional[bytes] = None,
) -> tuple[dict[str, dict[str, list]], EncryptedKeyMap]:
    """Anonymize a corpus of tables; return (anonymized_tables, encrypted_keymap).

    Parameters
    ----------
    tables : ``{table_name: DataFrame | pyarrow.Table | {col: values}}``.
    key : the CUSTOMER secret (bytes or passphrase str). Held only by the
        customer; we never see it.
    policy : what to tokenize (PII auto-detect + per-column allow/deny). Default
        :class:`Policy` auto-detects PII/quasi columns and preserves format.
    numeric_dimension : the keyed-affine map label shared by numeric columns.
        ``"*"`` (default) shares ONE monotone map across all numeric columns so
        numeric join keys still meet; pass a per-column scheme only to break a
        numeric join deliberately.
    nonce : keymap-encryption nonce; random by default (pass one only for repro
        tests).

    The returned tables contain ONLY tokens for selected columns and pass-through
    for the rest; the encrypted keymap is the only path back to raw. Use
    :func:`anonymize_with_audit` to also get the per-column selection trail.
    """
    result = anonymize_with_audit(
        tables, key, policy, numeric_dimension=numeric_dimension, nonce=nonce
    )
    return result.tables, result.keymap


# --------------------------------------------------------------------------
# Leak scan (THE PROOF: no raw sensitive value appears in the anonymized output)
# --------------------------------------------------------------------------


def scan_for_raw(
    anonymized: dict[str, dict[str, list]],
    raw_values: set[str],
    *,
    tokenized: Optional[dict[str, set[str]]] = None,
) -> list[tuple[str, str, str]]:
    """Scan the anonymized corpus for any RAW sensitive value (verbatim leak).

    Returns ``(table, column, raw_value)`` for every raw value found in a
    TOKENIZED cell — an empty list is the no-leak proof. Compares on the display
    string so an integer ``42`` and ``"42"`` are caught the same way.

    A leak only counts inside a column that was actually tokenized: a value that
    is sensitive in one column can be a legitimately-public value in a different,
    pass-through column (e.g. a hidden ``customer_id=1`` vs a public
    ``order_id=1`` that share the integer ``1``). Pass ``tokenized`` —
    ``{table: {column, ...}}`` of the columns that were tokenized (the
    ``selected`` map from :class:`AnonymizationResult`) — to scope the scan to
    those columns. When ``tokenized`` is ``None`` every column is scanned (the
    strictest reading; only safe when no pass-through column shares a value with a
    tokenized one)."""
    leaks: list[tuple[str, str, str]] = []
    needles = {r for r in raw_values if r}
    for table, cols in anonymized.items():
        scoped = None if tokenized is None else tokenized.get(table, set())
        for col, values in cols.items():
            if scoped is not None and col not in scoped:
                continue
            for v in values:
                if v is None:
                    continue
                s = _disp(v)
                if s in needles:
                    leaks.append((table, col, s))
    return leaks


def is_token(value: object) -> bool:
    """Heuristic: does a value look like an OntoForge string token?"""
    return isinstance(value, str) and TOKEN_PREFIX in value
