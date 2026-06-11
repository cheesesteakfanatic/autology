"""Name-based correspondence between source columns and target properties.

Normalized-name equality, declared synonyms, then token containment / Jaccard.
Purely lexical — semantic escalation is T2/T3 (out of v0 scope, README).
"""

from __future__ import annotations

import re
from typing import Optional, Sequence

from ontoforge.contracts import PropertyDef

__all__ = ["normalize_name", "match_columns", "match_score"]

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def normalize_name(name: str) -> str:
    """'ALTITUDE.AGL.SINGLE VALUE' -> 'altitude_agl_single_value'."""
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", name)
    return _NON_ALNUM.sub("_", spaced.lower()).strip("_")


def _tokens(name: str) -> set[str]:
    return set(normalize_name(name).split("_")) - {""}


def match_score(prop: PropertyDef, column: str) -> float:
    cn = normalize_name(column)
    pn = normalize_name(prop.name)
    if cn == pn:
        return 1.0
    for syn in prop.synonyms:
        if normalize_name(syn) == cn:
            return 0.95
    ct, pt = _tokens(column), _tokens(prop.name)
    if not ct or not pt:
        return 0.0
    if ct == pt:
        return 0.9
    if pt <= ct:
        return 0.8
    if ct <= pt:
        return 0.75
    for syn in prop.synonyms:
        st = _tokens(syn)
        if st and (st <= ct or ct <= st):
            return 0.7
    j = len(ct & pt) / len(ct | pt)
    return 0.5 * j if j >= 0.5 else 0.0


def match_columns(
    props: Sequence[PropertyDef], columns: Sequence[str], floor: float = 0.6
) -> dict[str, Optional[str]]:
    """Greedy 1:1 assignment, highest score first; deterministic tie-breaks."""
    scored: list[tuple[float, str, str]] = []
    for p in props:
        for c in columns:
            s = match_score(p, c)
            if s >= floor:
                scored.append((s, p.name, c))
    scored.sort(key=lambda t: (-t[0], t[1], t[2]))
    out: dict[str, Optional[str]] = {p.name: None for p in props}
    used: set[str] = set()
    for s, pname, col in scored:
        if out[pname] is None and col not in used:
            out[pname] = col
            used.add(col)
    return out
