"""String-similarity primitives for the ER cascade (whitepaper §11.2 M5).

Everything here is implemented from scratch on the stdlib (HARD RULE: no new
dependencies): Jaro, Jaro-Winkler, character n-grams, token sets and a
fuzzy token-overlap measure used by both the Fellegi-Sunter comparators
(fs.py) and the deterministic T2 adjudicator (heuristics.py).

All functions are pure and deterministic.
"""

from __future__ import annotations

__all__ = [
    "jaro",
    "jaro_winkler",
    "char_ngrams",
    "token_jaccard",
    "fuzzy_token_jaccard",
    "fuzzy_token_containment",
    "tokens_match",
    "is_abbreviation",
]


def jaro(s1: str, s2: str) -> float:
    """Jaro similarity (Jaro 1989). Range [0, 1]; 1.0 for two empty strings."""
    if s1 == s2:
        return 1.0
    n1, n2 = len(s1), len(s2)
    if n1 == 0 or n2 == 0:
        return 0.0
    window = max(n1, n2) // 2 - 1
    if window < 0:
        window = 0
    match1 = [False] * n1
    match2 = [False] * n2
    matches = 0
    for i, c in enumerate(s1):
        lo = max(0, i - window)
        hi = min(n2, i + window + 1)
        for j in range(lo, hi):
            if not match2[j] and s2[j] == c:
                match1[i] = True
                match2[j] = True
                matches += 1
                break
    if matches == 0:
        return 0.0
    # transpositions: compare matched characters in order
    k = 0
    transpositions = 0
    for i in range(n1):
        if match1[i]:
            while not match2[k]:
                k += 1
            if s1[i] != s2[k]:
                transpositions += 1
            k += 1
    t = transpositions / 2.0
    m = float(matches)
    return (m / n1 + m / n2 + (m - t) / m) / 3.0


def jaro_winkler(s1: str, s2: str, prefix_scale: float = 0.1, max_prefix: int = 4) -> float:
    """Jaro-Winkler similarity (Winkler 1990): Jaro + common-prefix boost.

    Uses the original unconditional prefix boost (no 0.7 boost threshold),
    standard scaling p=0.1 capped at a 4-character prefix, so the canonical
    reference values hold: JW(MARTHA, MARHTA)=0.9611, JW(DIXON, DICKSONX)=0.8133.
    """
    j = jaro(s1, s2)
    prefix = 0
    for c1, c2 in zip(s1, s2):
        if c1 != c2 or prefix >= max_prefix:
            break
        prefix += 1
    return j + prefix * prefix_scale * (1.0 - j)


def char_ngrams(s: str, n: int = 3, pad: str = " ") -> set[str]:
    """Padded character n-gram set ('  AB' style edges included via one pad char)."""
    if not s:
        return set()
    padded = pad + s + pad
    if len(padded) < n:
        return {padded}
    return {padded[i : i + n] for i in range(len(padded) - n + 1)}


def token_jaccard(t1: set[str], t2: set[str]) -> float:
    """Plain Jaccard over two token sets; 1.0 when both empty."""
    if not t1 and not t2:
        return 1.0
    if not t1 or not t2:
        return 0.0
    return len(t1 & t2) / len(t1 | t2)


def is_abbreviation(short: str, long: str) -> bool:
    """True when `short` (>=3 chars) is a first-char-anchored subsequence of
    `long` — e.g. DEPT ~ DEPARTMENT, INTL ~ INTERNATIONAL. Generic, no tables."""
    if len(short) < 3 or len(short) >= len(long):
        return False
    if short[0] != long[0]:
        return False
    it = iter(long)
    return all(c in it for c in short)


def tokens_match(a: str, b: str, jw_threshold: float = 0.92) -> bool:
    """Fuzzy token equality: exact, abbreviation-subsequence, or high Jaro-Winkler."""
    if a == b:
        return True
    if is_abbreviation(a, b) or is_abbreviation(b, a):
        return True
    return jaro_winkler(a, b) >= jw_threshold


def _greedy_token_alignment(t1: list[str], t2: list[str]) -> int:
    """Number of greedily fuzzy-matched token pairs (each token used once)."""
    used = [False] * len(t2)
    matched = 0
    for a in t1:
        for j, b in enumerate(t2):
            if not used[j] and tokens_match(a, b):
                used[j] = True
                matched += 1
                break
    return matched


def fuzzy_token_jaccard(t1: list[str], t2: list[str]) -> float:
    """Jaccard with fuzzy token equality (greedy one-to-one alignment)."""
    if not t1 and not t2:
        return 1.0
    if not t1 or not t2:
        return 0.0
    m = _greedy_token_alignment(t1, t2)
    return m / (len(t1) + len(t2) - m)


def fuzzy_token_containment(t1: list[str], t2: list[str]) -> bool:
    """True when every token of the SHORTER list fuzzy-matches into the longer.

    Captures legal-suffix / qualifier dropping: REPUBLIC AIRWAYS is contained in
    REPUBLIC AIRWAYS HOLDINGS. Empty-vs-nonempty is NOT containment.
    """
    if not t1 or not t2:
        return False
    short, long_ = (t1, t2) if len(t1) <= len(t2) else (t2, t1)
    return _greedy_token_alignment(short, long_) == len(short)
