"""Format-signature lattice (whitepaper §3.1: "format signatures — regex lattice over samples").

Pipeline
--------
1. Tokenize each sampled string over alphabet classes:
       D = digit, A = upper, a = lower; every other character is a LITERAL class
       of itself (punct, space, etc.); non-ASCII collapses to ANY.
   Adjacent equal classes run-length collapse: 'N123AB' -> [A, D{3}, A{2}].
2. Generalize bottom-up: signatures are folded pairwise through a global alignment
   (Needleman–Wunsch) whose substitution cost is the distance walked in the class
   lattice and whose gap cost makes a token optional (min_count -> 0):

       class lattice:   D   A   a   <literals>
                          \  | /
                       X <- L            L = letter (A ∨ a)
                        \   |            X = alnum  (L ∨ D)
                          ANY            ANY = top

   Aligned tokens join their classes and widen their count interval
   [min(lo), max(hi)]; gapped tokens become optional. Adjacent same-class tokens
   re-collapse by interval addition. The fold therefore yields a minimal covering
   signature in the lattice: every sampled value matches it (verified by
   `to_regex`), and counts/classes are only widened when forced by a sample.
3. Render: 'A D{2,4} A{0,2}' style; literal chars render as themselves (space as ␣).

The result is ColumnProfile.format_signature.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Sequence

from ._values import sample_evenly

__all__ = ["Tok", "tokenize", "merge_token_seqs", "generalize", "render", "to_regex", "format_signature"]

# class symbols (literals carry lit=True and cls == the character itself)
D, A, LOW, L, X, ANY = "D", "A", "a", "L", "X", "ANY"
_CLASS_ORDER = {D: 0, A: 0, LOW: 0, L: 1, X: 2, ANY: 3}


@dataclass(frozen=True, slots=True)
class Tok:
    cls: str
    lo: int
    hi: int
    lit: bool = False


def _char_class(ch: str) -> tuple[str, bool]:
    if "0" <= ch <= "9":
        return D, False
    if "A" <= ch <= "Z":
        return A, False
    if "a" <= ch <= "z":
        return LOW, False
    if ord(ch) < 128:
        return ch, True  # literal punct/space
    return ANY, False


def tokenize(s: str) -> list[Tok]:
    toks: list[Tok] = []
    for ch in s:
        cls, lit = _char_class(ch)
        if toks and toks[-1].cls == cls and toks[-1].lit == lit:
            last = toks[-1]
            toks[-1] = Tok(cls, last.lo + 1, last.hi + 1, lit)
        else:
            toks.append(Tok(cls, 1, 1, lit))
    return toks


def _join_cls(t1: Tok, t2: Tok) -> tuple[str, bool, int]:
    """Least upper bound of two token classes + the lattice distance (cost)."""
    if t1.cls == t2.cls and t1.lit == t2.lit:
        return t1.cls, t1.lit, 0
    c1, c2 = t1.cls, t2.cls
    if t1.lit or t2.lit:
        return ANY, False, 3
    pair = {c1, c2}
    if pair <= {A, LOW, L}:
        return L, False, 1
    if pair <= {A, LOW, L, D, X}:
        return X, False, 2
    return ANY, False, 3


_GAP_COST = 2


def merge_token_seqs(s1: Sequence[Tok], s2: Sequence[Tok]) -> list[Tok]:
    """Global alignment merge: returns the minimal covering sequence of s1 and s2."""
    n, m = len(s1), len(s2)
    # dp[i][j] = min cost aligning s1[:i] with s2[:j]
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    back = [[0] * (m + 1) for _ in range(n + 1)]  # 1=diag 2=up(gap in s2) 3=left(gap in s1)
    for i in range(1, n + 1):
        dp[i][0] = i * _GAP_COST
        back[i][0] = 2
    for j in range(1, m + 1):
        dp[0][j] = j * _GAP_COST
        back[0][j] = 3
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            _, _, sub = _join_cls(s1[i - 1], s2[j - 1])
            diag = dp[i - 1][j - 1] + sub
            up = dp[i - 1][j] + _GAP_COST
            left = dp[i][j - 1] + _GAP_COST
            best = min(diag, up, left)
            dp[i][j] = best
            back[i][j] = 1 if best == diag else (2 if best == up else 3)
    out: list[Tok] = []
    i, j = n, m
    while i > 0 or j > 0:
        move = back[i][j]
        if move == 1:
            t1, t2 = s1[i - 1], s2[j - 1]
            cls, lit, _ = _join_cls(t1, t2)
            out.append(Tok(cls, min(t1.lo, t2.lo), max(t1.hi, t2.hi), lit))
            i, j = i - 1, j - 1
        elif move == 2:
            t = s1[i - 1]
            out.append(Tok(t.cls, 0, t.hi, t.lit))
            i -= 1
        else:
            t = s2[j - 1]
            out.append(Tok(t.cls, 0, t.hi, t.lit))
            j -= 1
    out.reverse()
    return _recollapse(out)


def _recollapse(toks: list[Tok]) -> list[Tok]:
    """Combine adjacent same-class tokens by interval addition (D{1,2} D{0,1} -> D{1,3})."""
    out: list[Tok] = []
    for t in toks:
        if out and out[-1].cls == t.cls and out[-1].lit == t.lit:
            last = out[-1]
            out[-1] = Tok(t.cls, last.lo + t.lo, last.hi + t.hi, t.lit)
        else:
            out.append(t)
    return out


def generalize(values: Iterable[str], max_samples: int = 128) -> list[Tok]:
    """Minimal covering token sequence over a deterministic sample of the values."""
    strings = [s for s in values if s != ""]
    if not strings:
        return []
    sample = sample_evenly(strings, max_samples)
    seqs = sorted((tokenize(s) for s in sample), key=lambda seq: (len(seq), str(seq)))
    merged = seqs[0]
    for seq in seqs[1:]:
        merged = merge_token_seqs(merged, seq)
    return merged


def _render_tok(t: Tok) -> str:
    base = ("␣" if t.cls == " " else t.cls) if t.lit else t.cls
    if t.lo == t.hi:
        return base if t.lo == 1 else f"{base}{{{t.lo}}}"
    if t.lo == 0 and t.hi == 1:
        return f"{base}?"
    return f"{base}{{{t.lo},{t.hi}}}"


def render(toks: Sequence[Tok]) -> str:
    return " ".join(_render_tok(t) for t in toks)


_RX = {D: "[0-9]", A: "[A-Z]", LOW: "[a-z]", L: "[A-Za-z]", X: "[A-Za-z0-9]", ANY: "."}


def to_regex(toks: Sequence[Tok]) -> str:
    parts: list[str] = []
    for t in toks:
        atom = re.escape(t.cls) if t.lit else _RX[t.cls]
        if t.lo == t.hi:
            parts.append(atom if t.lo == 1 else f"{atom}{{{t.lo}}}")
        else:
            parts.append(f"{atom}{{{t.lo},{t.hi}}}")
    return "".join(parts)


def format_signature(values: Iterable[str], max_samples: int = 128) -> str:
    """The string emitted as ColumnProfile.format_signature."""
    return render(generalize(values, max_samples))
