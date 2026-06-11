"""Shared name-normalization utilities for STRATA (whitepaper §3.4.2).

Column and table names across estates use ad-hoc abbreviation conventions
("ACFT_REGIST_NMBR", "MFR MDL CODE", "faa_acftref"). STRATA normalizes names
into token tuples before any similarity computation:

1. lowercase, split on every non-alphabetic character (digits dropped:
   "AIRCRAFT 1 OPERATOR" -> aircraft/operator);
2. expand common *token-level* abbreviations via a small static dictionary
   (generic data-vocabulary abbreviations -- this is NOT a table-name map and
   contains no estate-specific identifiers, per the §18.3 anti-hardcoding rule);
3. unknown long tokens are greedily decomposed into two known abbreviations
   ("acftref" -> acft+ref -> aircraft/reference), a dictionary-driven
   decomposition that needs no per-source configuration.

The *cross-column synonym map* itself (n_number/tail_number/acft_regist_nmbr
-> tail_number) is NOT in this file: it is built from name-token Jaccard,
IND links, and value-overlap evidence in :mod:`ontoforge.strata.context`.
"""

from __future__ import annotations

import re

__all__ = [
    "ABBREV",
    "GENERIC_SUFFIX_TOKENS",
    "camel",
    "name_tokens",
    "normalize_name",
    "singularize",
    "token_jaccard",
]

#: Token-level abbreviation expansions (generic data vocabulary, no table names).
ABBREV: dict[str, str] = {
    "acft": "aircraft",
    "amt": "amount",
    "cert": "certificate",
    "dt": "date",
    "eng": "engine",
    "ev": "event",
    "inj": "injury",
    "loc": "location",
    "mdl": "model",
    "mfr": "manufacturer",
    "narr": "narrative",
    "nbr": "number",
    "nmbr": "number",
    "num": "number",
    "org": "organization",
    "qty": "quantity",
    "ref": "reference",
    "regist": "registration",
    "registr": "registration",
    "tot": "total",
}

#: Generic trailing tokens stripped when deriving a *type* name from a column
#: name ("REGISTRANT NAME" -> Registrant): these tokens describe the lexical
#: role of the column, not the entity it identifies.
GENERIC_SUFFIX_TOKENS = frozenset({"name", "id", "code", "number", "key"})

_ALPHA = re.compile(r"[a-z]+")


def _expand(token: str) -> tuple[str, ...]:
    """Expand one lowercase token via ABBREV, with greedy 2-way decomposition
    of unknown compound tokens whose halves are both known abbreviations."""
    if token in ABBREV:
        return (ABBREV[token],)
    if len(token) >= 6:
        for i in range(3, len(token) - 2):
            a, b = token[:i], token[i:]
            if a in ABBREV and b in ABBREV:
                return (ABBREV[a], ABBREV[b])
    return (token,)


def name_tokens(name: str) -> tuple[str, ...]:
    """Normalized token tuple of a column/table name (order preserved,
    duplicates kept; similarity functions work over the token *set*)."""
    out: list[str] = []
    for raw in _ALPHA.findall(name.lower()):
        out.extend(_expand(raw))
    return tuple(out)


def normalize_name(name: str) -> str:
    """Canonical snake_case form of a name ("ACFT_REGIST_NMBR" ->
    "aircraft_registration_number")."""
    return "_".join(name_tokens(name))


def token_jaccard(a: str | tuple[str, ...], b: str | tuple[str, ...]) -> float:
    """Jaccard similarity over normalized token sets."""
    ta = set(name_tokens(a)) if isinstance(a, str) else set(a)
    tb = set(name_tokens(b)) if isinstance(b, str) else set(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def singularize(token: str) -> str:
    """Light plural strip: 'reports' -> 'report' (never touches 'ss' endings)."""
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def camel(snake: str, *, singular_last: bool = True) -> str:
    """snake_case -> CamelCase; optionally singularizes the final token
    ('asrs_reports' -> 'AsrsReport')."""
    parts = [p for p in snake.split("_") if p]
    if not parts:
        return ""
    if singular_last:
        parts[-1] = singularize(parts[-1])
    return "".join(p.capitalize() for p in parts)
