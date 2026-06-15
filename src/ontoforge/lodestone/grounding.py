"""LODESTONE stage 1 — grounding (whitepaper §6.2; M12 step 2).

ground(question, ontology, value_index) -> GroundingResult

Hybrid LEXICAL retrieval over the induced ontology (AMD-0002: deterministic,
no embeddings): class names + camel-split tokens + definition acronyms,
property names/synonyms/link names, literal-value probes against a HEARTH
value index, unit tokens (profiling §3.2 alias table + currency words), time
expressions, aggregation/comparison cues, and structured/unstructured cues
(textJoin verbs + their object phrase).

Coverage is computed over the *interrogative sentence only* (questions often
carry note sentences) and counts STRONG bindings: a token that merely appears
in some class definition is weak evidence and never licenses an answer —
abstention on low strong-coverage is §6.2's 'the system knows when it is
wrong'.
"""

from __future__ import annotations

import re
from typing import Optional

from ontoforge.contracts.ontology import Datatype, Ontology
from ontoforge.contracts.oqir import CmpOp
from ontoforge.contracts.temporal import to_instant
from ontoforge.profiling.units_table import ALIASES

from .model import Binding, GroundingResult, all_props

# extra unit words on top of the profiling alias table
EXTRA_UNIT_WORDS = {
    "dollars": "USD", "dollar": "USD", "usd": "USD", "$": "USD",
    "euros": "EUR", "euro": "EUR",
    "feet": "ft", "foot": "ft", "ft": "ft",
    "meters": "m", "metres": "m", "meter": "m", "metre": "m",
    "hours": "h", "hour": "h",
}

# Aggregation cues. Ordered longest-first so 'total number' (count) wins over
# 'total' (sum). DETERMINISTIC NL hardening: a richer surface lexicon so common
# paraphrases ('tally', 'on average', 'peak') route to the same intent.
AGG_CUES: list[tuple[tuple[str, ...], str]] = [
    (("total", "number", "of"), "count"),
    (("total", "number"), "count"),
    (("how", "many"), "count"),
    (("number", "of"), "count"),
    (("no", "of"), "count"),
    (("count", "of"), "count"),
    (("counting",), "count"),
    (("count",), "count"),
    (("tally",), "count"),
    (("tallied",), "count"),
    (("how", "much"), "sum"),
    (("sum", "total"), "sum"),
    (("grand", "total"), "sum"),
    (("add", "up"), "sum"),
    (("added", "up"), "sum"),
    (("adding", "up"), "sum"),
    (("summed",), "sum"),
    (("summing",), "sum"),
    (("combined",), "sum"),
    (("aggregate",), "sum"),
    (("total",), "sum"),
    (("sum",), "sum"),
    (("on", "average"), "avg"),
    (("average",), "avg"),
    (("avg",), "avg"),
    (("mean",), "avg"),
    (("typical",), "avg"),
    (("lowest",), "min"),
    (("minimum",), "min"),
    (("smallest",), "min"),
    (("least",), "min"),
    (("min",), "min"),
    (("bottom",), "min"),
    (("highest",), "max"),
    (("maximum",), "max"),
    (("largest",), "max"),
    (("greatest",), "max"),
    (("biggest",), "max"),
    (("peak",), "max"),
    (("max",), "max"),
]

# Comparison cues, ordered longest-first. Additive: synonymous comparison
# phrasings ('in excess of', 'north of', 'no more than') beyond the originals.
CMP_CUES: list[tuple[tuple[str, ...], CmpOp]] = [
    (("no", "less", "than"), CmpOp.GE),
    (("no", "fewer", "than"), CmpOp.GE),
    (("no", "more", "than"), CmpOp.LE),
    (("greater", "than", "or", "equal", "to"), CmpOp.GE),
    (("less", "than", "or", "equal", "to"), CmpOp.LE),
    (("in", "excess", "of"), CmpOp.GT),
    (("north", "of"), CmpOp.GT),
    (("south", "of"), CmpOp.LT),
    (("less", "than"), CmpOp.LT),
    (("fewer", "than"), CmpOp.LT),
    (("greater", "than"), CmpOp.GT),
    (("more", "than"), CmpOp.GT),
    (("at", "least"), CmpOp.GE),
    (("at", "most"), CmpOp.LE),
    (("below",), CmpOp.LT),
    (("under",), CmpOp.LT),
    (("beneath",), CmpOp.LT),
    (("above",), CmpOp.GT),
    (("over",), CmpOp.GT),
    (("exceeding",), CmpOp.GT),
    (("beyond",), CmpOp.GT),
    (("after",), CmpOp.GT),
    (("before",), CmpOp.LT),
    (("since",), CmpOp.GE),
    (("until",), CmpOp.LE),
    (("by",), CmpOp.LE),
]

TEXTJOIN_CUES = {
    "describe", "describes", "describing", "described",
    "mention", "mentions", "mentioning", "mentioned",
    "cite", "cites", "citing", "cited",
    "reference", "references", "referencing", "referenced",
    "matching", "containing", "contain", "contains",
    "talk", "talks", "talking", "discuss", "discusses", "discussing",
    "about", "regarding", "concerning", "involving", "involve", "involves",
    "note", "notes", "noting", "report", "reports", "reporting",
    "say", "says", "saying", "state", "states", "stating",
}

# Closed, high-precision abbreviation table (whitepaper §6.2 deterministic
# schema linking). Applied BOTH directions at index time; matches here keep
# strong=True because the mapping is curated (high precision). Open stemming
# heuristics are kept separate and graded weaker.
ABBREV: dict[str, str] = {
    "qty": "quantity", "amt": "amount", "amount": "amt",
    "num": "number", "nbr": "number", "nmbr": "number",
    "dt": "date", "ts": "timestamp", "yr": "year", "yrs": "year",
    "mfr": "manufacturer", "mfg": "manufacturer", "maker": "manufacturer",
    "built": "manufacturer", "made": "manufacturer", "make": "manufacturer",
    "wt": "weight", "pct": "percent", "addr": "address",
    "desc": "description", "id": "identifier", "ident": "identifier",
    "acft": "aircraft", "plane": "aircraft", "airplane": "aircraft",
    "regn": "registration", "reg": "registration",
    "tail": "tail_number", "nnumber": "tail_number", "callsign": "tail_number",
    "serial": "serial_number", "mech": "mechanic",
    "ata": "ata_chapter", "alt": "altitude", "mdl": "model",
    "narr": "narrative", "cause": "cause_narrative",
    "deaths": "fatalities", "fatality": "fatalities", "killed": "fatalities",
    "labour": "labor", "hrs": "hours",
}

# Reverse abbreviation map: schema token -> abbreviations that expand TO it.
# Built so that when a schema token ('manufacturer') is indexed we ALSO register
# its short forms ('mfr', 'maker') pointing at the same target (both directions,
# whitepaper §6.2). Multi-word expansions ('tail_number') key on each word.
ABBREV_REVERSE: dict[str, list[str]] = {}
for _abbr, _exp in ABBREV.items():
    for _w in _exp.replace("_", " ").split():
        ABBREV_REVERSE.setdefault(_w, []).append(_abbr)

STOPWORDS = {
    "the", "a", "an", "of", "in", "on", "at", "to", "for", "by", "and", "or",
    "is", "are", "was", "were", "did", "does", "do", "be", "been", "its", "it",
    "that", "this", "these", "those", "with", "as", "all", "across", "per",
    "what", "which", "who", "whose", "when", "where", "whom", "there", "have",
    "has", "had", "from", "into", "their", "her", "his", "not", "no", "but",
    "both", "if", "than", "then", "each", "any", "between", "while", "during",
    "record", "records", "note", "together", "involved", "appear", "appears",
    "refer", "refers", "referred", "carry", "carries", "belong", "belongs",
    "when", "does", "do", "its",
    # conversational / politeness / imperative filler: a paraphrase that wraps
    # the real ask in chrome ('could you tell me ...') must not have that chrome
    # inflate the coverage denominator (whitepaper §6.2 honest-coverage fix).
    "could", "would", "should", "can", "will", "please", "kindly",
    "tell", "know", "knowing", "wondering", "wonder", "curious", "like",
    "show", "showing", "give", "giving", "list", "listing", "find", "finding",
    "want", "need", "needed", "get", "getting", "see", "look", "looking",
    "me", "us", "you", "i", "we", "my", "our", "your", "back", "up", "out",
    "some", "many", "much", "such", "here", "about", "really", "just", "also",
    "please", "kindly", "say", "says", "regarding", "concerning",
}

ROUND_CUE = re.compile(
    r"rounded?\s+to\s+the\s+nearest|to\s+the\s+nearest\b",
    re.IGNORECASE,
)
# the source lexical unit a measure was recorded in. Accepts the canonical
# 'recorded/measured in X' and "'X' suffix" plus paraphrase forms
# 'X-recorded', 'X recorded/measured', 'recorded as X', 'logged in X'.
# NB: deliberately excludes 'expressed in X' — that is an OUTPUT-unit request
# ('lowest altitude expressed in feet'), handled by the 'in <unit>' block, not a
# source-unit filter. The hyphenated 'X-recorded' form requires a HYPHEN so it
# cannot swallow the auxiliary verb in 'was recorded in X'.
RECORDED_UNIT = re.compile(
    r"(?:recorded|measured|captured|logged|stored)\s+(?:in|as)\s+([a-z]+)"
    r"|'([a-z]+)'\s+suffix"
    r"|\b([a-z]+)-(?:recorded|measured|logged|denominated)\b",
    re.IGNORECASE,
)
MORE_THAN_ONE = re.compile(r"more\s+than\s+one", re.IGNORECASE)
AS_OF = re.compile(r"as\s+of\s+(\d{4}-\d{2}-\d{2}|\d{8}|[A-Za-z]+\s+\d{4})", re.IGNORECASE)
ISO_DATE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
NUMBER = re.compile(r"\b\d{1,3}(?:,\d{3})+(?:\.\d+)?\b|\b\d+\.\d+\b|\b\d+\b")
QUOTED = re.compile(r"'([^']+)'|\"([^\"]+)\"|‘([^’]+)’|“([^”]+)”")
CODEISH = re.compile(r"^[A-Z]{1,4}-?\d[\dA-Z-]*$|^N\d[\dA-Z]+$|^\d{3,}$|^[A-Z]{2,}\d+[A-Z\d]*$")
TOPK = re.compile(r"\btop\s+(\d+)\b", re.IGNORECASE)
MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}

CUE_WORDS = (
    {w for cue, _ in AGG_CUES for w in cue}
    | {w for cue, _ in CMP_CUES for w in cue}
    | TEXTJOIN_CUES
    | {"as", "of", "recorded", "expressed", "measured", "rounded", "nearest", "suffix",
       "top", "opened", "closed", "folded", "one", "fold", "folded", "spelling",
       "spellings", "spelled", "variants", "form", "forms", "normalized",
       "associated", "built", "made", "names", "named", "naming", "worth",
       "during", "throughout", "amongst", "among", "whilst", "within"}
)


def _tok(text: str) -> list[str]:
    return re.findall(r"[A-Za-z0-9_.,'/$-]+", text)


def _norm(tok: str) -> str:
    return tok.strip(".,;:!?'\"()").lower()


def _singular(word: str) -> str:
    if word.endswith("ies") and len(word) > 4:
        return word[:-3] + "y"
    if word.endswith("s") and not word.endswith("ss") and len(word) > 3:
        return word[:-1]
    return word


def _camel_split(name: str) -> list[str]:
    return [p.lower() for p in re.findall(r"[A-Z][a-z0-9]*|[a-z0-9]+", name)]


def _stem(w: str) -> str:
    """Open morphological stem (drop common inflections). Heuristic and lossy —
    callers grade stem-derived bindings strong=False."""
    for suf in ("ing", "ed", "es", "s"):
        if w.endswith(suf) and len(w) - len(suf) >= 3:
            return w[: -len(suf)]
    return w


# --- pure-python Jaro-Winkler (deterministic, no new dependency) ------------


def jaro(s1: str, s2: str) -> float:
    if s1 == s2:
        return 1.0
    if not s1 or not s2:
        return 0.0
    match_dist = max(len(s1), len(s2)) // 2 - 1
    if match_dist < 0:
        match_dist = 0
    s1_matches = [False] * len(s1)
    s2_matches = [False] * len(s2)
    matches = 0
    for i, c1 in enumerate(s1):
        lo = max(0, i - match_dist)
        hi = min(i + match_dist + 1, len(s2))
        for j in range(lo, hi):
            if not s2_matches[j] and s2[j] == c1:
                s1_matches[i] = True
                s2_matches[j] = True
                matches += 1
                break
    if matches == 0:
        return 0.0
    t = 0
    k = 0
    for i in range(len(s1)):
        if s1_matches[i]:
            while not s2_matches[k]:
                k += 1
            if s1[i] != s2[k]:
                t += 1
            k += 1
    t //= 2
    return (matches / len(s1) + matches / len(s2) + (matches - t) / matches) / 3.0


def jaro_winkler(s1: str, s2: str, p: float = 0.1) -> float:
    """Prefix-weighted Jaro similarity — ideal for short schema identifiers that
    share stems ('manufactured' ~ 'manufacturer')."""
    j = jaro(s1, s2)
    prefix = 0
    for c1, c2 in zip(s1, s2):
        if c1 == c2:
            prefix += 1
        else:
            break
        if prefix == 4:
            break
    return j + prefix * p * (1 - j)


def _trigrams(token: str) -> set[str]:
    """Padded character trigrams for blocking ('alt' -> {'  a',' al','alt',..})."""
    s = f"  {token} "
    return {s[i : i + 3] for i in range(len(s) - 2)}


# fuzzy thresholds (calibrated high: the single most important confidently-wrong
# guard — anything below STRONG cannot alone clear the coverage floor)
JW_STRONG = 0.92      # strong-eligible single-token fuzzy hit
JW_EVIDENCE = 0.88    # evidence-only (strong=False) fuzzy hit; never below this

# written-number table for numeric phrase parsing (additive, deterministic)
WRITTEN_NUMBERS: dict[str, float] = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
    "twelve": 12, "dozen": 12, "hundred": 100, "thousand": 1000,
    "million": 1_000_000, "billion": 1_000_000_000,
}
MAGNITUDE: dict[str, float] = {
    "k": 1_000, "m": 1_000_000, "mn": 1_000_000, "mm": 1_000_000,
    "bn": 1_000_000_000, "b": 1_000_000_000,
}
CURRENCY_PREFIX: dict[str, str] = {"$": "USD", "£": "GBP", "€": "EUR"}
# magnitude/currency-bearing literal: '$1M', '10k', '£1.5bn', '$1,234.56'
MONEY_MAG = re.compile(
    r"([$£€])?\s*(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)\s*(k|m|mn|mm|bn|b)?\b",
    re.IGNORECASE,
)


def parse_date_token(text: str) -> Optional[str]:
    """ISO 'YYYY-MM-DD' from ISO / YYYYMMDD / 'March 2024'."""
    t = text.strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", t):
        return t
    if re.fullmatch(r"\d{8}", t):
        return f"{t[:4]}-{t[4:6]}-{t[6:]}"
    m = re.fullmatch(r"([A-Za-z]+)\s+(\d{4})", t)
    if m and m.group(1).lower() in MONTHS:
        return f"{m.group(2)}-{MONTHS[m.group(1).lower()]:02d}-01"
    return None


def iso_to_instant(iso: str) -> int:
    from datetime import datetime, timezone

    return to_instant(datetime.strptime(iso, "%Y-%m-%d").replace(tzinfo=timezone.utc))


# ------------------------------------------------------------------ lexicon


class Lexicon:
    """Deterministic surface-form index over one ontology.

    Beyond verbatim names / camel-snake splits / authored synonyms, the index
    registers (whitepaper §6.2 deterministic schema linking):

      * closed-table abbreviation expansions in BOTH directions (qty<->quantity,
        mfr<->manufacturer, ...) as STRONG keys (curated, high precision);
      * open morphological stems (drop ing/ed/es/s) as WEAK keys (heuristic);
      * plural<->singular variants.

    Verbatim and authored-synonym hits keep strong=True/score=1.0; the greedy
    longest-first matcher in ``ground`` still hits the exact key first, so the
    canonical phrasings are untouched — these are purely *additional* fallbacks
    for paraphrases. Fuzzy lookup is gated behind a char-trigram blocking index
    so it scans only keys that share a trigram with the query token.
    """

    def __init__(self, onto: Ontology) -> None:
        self.onto = onto
        # phrase -> [(kind, target, score, strong)]
        self.phrases: dict[tuple[str, ...], list[tuple[str, str, float, bool]]] = {}
        # trigram -> set of single-token phrase keys (fuzzy blocking)
        self.trigram_index: dict[str, set[str]] = {}
        # exact single-token phrase keys present (so fuzzy never overrides exact)
        self._single_keys: set[str] = set()
        for c_uri in sorted(onto.classes):
            c = onto.classes[c_uri]
            name_toks = tuple(_camel_split(c.name))
            self._add(name_toks, "class", c_uri, 1.0, True)
            if len(name_toks) > 1:
                self._add((name_toks[-1],), "class", c_uri, 0.6, True)
            # forward abbrev only on a single-word class name; multi-token class
            # names get reverse/stem variants per fragment (forward=False)
            for ti, tok in enumerate(name_toks):
                self._add_variants((tok,), "class", c_uri, forward=(len(name_toks) == 1))
            # definition acronyms (ASRS, NTSB, FAA, ERP...) are distinctive
            for acro in set(re.findall(r"\b[A-Z]{2,6}\b", c.definition)):
                self._add((acro.lower(),), "class", c_uri, 0.8, True)
            # other definition content words are weak evidence only
            for w in set(_tok(c.definition)):
                wn = _norm(w)
                if wn and wn not in STOPWORDS and len(wn) > 2 and not w.isupper():
                    self._add((_singular(wn),), "class", c_uri, 0.2, False)
            for pname, p in sorted(all_props(onto, c_uri).items()):
                target = f"{c_uri}::{pname}"
                ptoks = tuple(pname.lower().replace("_", " ").split())
                self._add(ptoks, "prop", target, 1.0, True)
                self._add((pname.lower(),), "prop", target, 1.0, True)  # verbatim name
                if len(ptoks) > 1:
                    self._add((ptoks[0],), "prop", target, 0.7, True)   # head token
                    # tail token is WEAK evidence only: the last word of a
                    # compound name ('aircraft' in 'type_aircraft') does not
                    # identify the whole column — registering it strong would
                    # let 'aircraft' falsely project type_aircraft (induced-
                    # schema collision). Keep it weak so it informs ranking but
                    # never alone licenses a projection.
                    self._add((ptoks[-1],), "prop", target, 0.4, False)
                # forward abbrev expansion is only safe on a single-word property
                # name (a fragment 'mfr' of 'mfr_mdl_code' must NOT forward-expand
                # to 'manufacturer'); fragments get reverse/stem variants only
                for tok in ptoks:
                    self._add_variants((tok,), "prop", target, forward=(len(ptoks) == 1))
                for syn in p.synonyms:
                    syn_toks = tuple(syn.lower().split())
                    self._add(syn_toks, "prop", target, 1.0, True)
                    self._add(tuple(syn.lower().replace("-", " ").split()), "prop", target, 1.0, True)
                    for tok in syn_toks:
                        self._add_variants((tok,), "prop", target, forward=(len(syn_toks) == 1))
        # build the trigram blocking index over all single-token keys
        for key in self.phrases:
            if len(key) == 1 and key[0] and key[0].isalpha():
                self._single_keys.add(key[0])
                for tg in _trigrams(key[0]):
                    self.trigram_index.setdefault(tg, set()).add(key[0])

    def _add(self, phrase: tuple[str, ...], kind: str, target: str, score: float, strong: bool) -> None:
        if phrase and all(phrase):
            existing = self.phrases.setdefault(phrase, [])
            for i, (k, t, s, st) in enumerate(existing):
                if k == kind and t == target:
                    # keep the strongest registration for an identical target
                    if (st, s) >= (strong, score):
                        return
                    existing[i] = (kind, target, score, strong)
                    return
            existing.append((kind, target, score, strong))

    def _add_variants(
        self, phrase: tuple[str, ...], kind: str, target: str, *, forward: bool = True
    ) -> None:
        """Register abbreviation (closed-table, STRONG) and stem (open, WEAK)
        variants of a single SCHEMA index token.

        Both directions of the closed table are registered, but FORWARD
        expansion (the token IS an abbreviation key, e.g. 'mfr' -> 'manufacturer')
        is only safe on a WHOLE single-word name — forward-expanding a FRAGMENT
        of a compound name ('mfr' inside 'mfr_mdl_code') would falsely equate the
        whole column with the expansion. Callers pass forward=False for compound
        fragments. REVERSE expansion (the token is the expansion of an
        abbreviation, e.g. 'manufacturer' -> register 'maker'/'mfr') is always
        safe because it keys on the full meaningful word. Stopwords and 1-char
        tokens are never expanded (avoids 'in'=inch, 'no'=number leakage)."""
        if len(phrase) != 1:
            return
        tok = phrase[0]
        if not tok or len(tok) <= 1:
            return
        if forward:
            exp = ABBREV.get(tok)
            if exp and exp != tok and exp not in STOPWORDS:
                for variant in exp.lower().replace("_", " ").split():
                    if variant not in STOPWORDS and len(variant) > 1:
                        self._add((variant,), kind, target, 0.95, True)
        # reverse: this schema token is the expansion of known abbreviations,
        # so the question may use the short form ('maker' for 'manufacturer')
        for abbr in ABBREV_REVERSE.get(tok, ()):
            if abbr not in STOPWORDS and len(abbr) > 1 and abbr != tok:
                self._add((abbr,), kind, target, 0.95, True)
        # open morphological stem, WEAK (heuristic; cannot alone clear the floor)
        st = _stem(tok)
        if st != tok and len(st) >= 3 and st not in STOPWORDS:
            self._add((st,), kind, target, 0.5, False)
        sg = _singular(tok)
        if sg != tok and len(sg) >= 3 and sg not in STOPWORDS:
            self._add((sg,), kind, target, 0.7, True)

    def lookup(self, phrase: tuple[str, ...]) -> list[tuple[str, str, float, bool]]:
        hits = list(self.phrases.get(phrase, []))
        if not hits and phrase:
            alt = phrase[:-1] + (_singular(phrase[-1]),)
            if alt != phrase:
                hits = list(self.phrases.get(alt, []))
        if not hits and phrase:
            stemmed = phrase[:-1] + (_stem(phrase[-1]),)
            if stemmed != phrase:
                hits = list(self.phrases.get(stemmed, []))
        # trigram-blocked Jaro-Winkler fuzzy fallback for single tokens.
        # NEVER fires when the token already exact-matches a schema key (greedy
        # longest-first + exact match always wins); only scans candidate keys
        # that share >=1 trigram (BRIDGE/IRNet-style blocking).
        if not hits and len(phrase) == 1 and len(phrase[0]) >= 4:
            w = phrase[0]
            if w not in self._single_keys and w.isalpha():
                hits = self._fuzzy_lookup(w)
        return sorted(hits, key=lambda h: (-h[2], h[0], h[1]))

    def _fuzzy_lookup(self, w: str) -> list[tuple[str, str, float, bool]]:
        candidates: set[str] = set()
        for tg in _trigrams(w):
            candidates |= self.trigram_index.get(tg, set())
        out: list[tuple[str, str, float, bool]] = []
        for k in sorted(candidates):
            if len(k) < 4 or k == w:
                continue
            jw = jaro_winkler(w, k)
            if jw < JW_EVIDENCE:
                continue
            # STRONG-eligible only at/above the calibrated high threshold; the
            # evidence band (0.88..0.92) is forced strong=False so it can rank /
            # clarify but never alone satisfy the coverage floor.
            strong_eligible = jw >= JW_STRONG
            penalty = 0.9 if strong_eligible else 0.6
            for kind, target, score, strong in self.phrases[(k,)]:
                out.append((kind, target, round(score * penalty, 4),
                            strong and strong_eligible))
        return out


# --------------------------------------------------------------- value index


class ValueIndex:
    """Normalized literal -> [(class_uri, prop, count)] over HEARTH string
    values (§6.2 'value-index probes for literals')."""

    def __init__(self) -> None:
        self.exact: dict[str, dict[tuple[str, str], int]] = {}

    @staticmethod
    def norm(v: str) -> str:
        return re.sub(r"\s+", " ", str(v).strip()).upper()

    def add(self, class_uri: str, prop: str, value: object) -> None:
        if not isinstance(value, str) or not value.strip() or len(value) > 80:
            return
        d = self.exact.setdefault(self.norm(value), {})
        key = (class_uri, prop)
        d[key] = d.get(key, 0) + 1

    def probe(self, text: str) -> list[tuple[str, str, int]]:
        d = self.exact.get(self.norm(text), {})
        return sorted(((c, p, n) for (c, p), n in d.items()), key=lambda x: (-x[2], x[0], x[1]))

    def probe_contains(self, text: str) -> list[tuple[str, str, int]]:
        """Substring probe for partial proper nouns ('Rockwell')."""
        needle = self.norm(text)
        if len(needle) < 4:
            return []
        agg: dict[tuple[str, str], int] = {}
        for val, locs in self.exact.items():
            if needle in val:
                for key, n in locs.items():
                    agg[key] = agg.get(key, 0) + n
        return sorted(((c, p, n) for (c, p), n in agg.items()), key=lambda x: (-x[2], x[0], x[1]))


def _categorical_props(onto: Ontology, value_index: ValueIndex) -> set[tuple[str, str]]:
    """(class_uri, prop) pairs that hold a CLOSED / low-cardinality value set —
    safe targets for lowercase-content value probing. A property qualifies when
    it declares an `in_values` shape OR carries few distinct string values in the
    index relative to its row count (a true category, not an identifier). High
    cardinality identifier columns (serial/tail numbers) are excluded, so a
    stray content word can never latch onto them."""
    cats: set[tuple[str, str]] = set()
    for c_uri in onto.classes:
        c = onto.classes[c_uri]
        for sh in c.shapes:
            if getattr(sh, "in_values", None):
                cats.add((c_uri, sh.prop))
    # distinct-value-count heuristic over the index
    counts: dict[tuple[str, str], set[str]] = {}
    totals: dict[tuple[str, str], int] = {}
    for val, locs in value_index.exact.items():
        for (c_uri, prop), n in locs.items():
            counts.setdefault((c_uri, prop), set()).add(val)
            totals[(c_uri, prop)] = totals.get((c_uri, prop), 0) + n
    for key, distinct in counts.items():
        tot = totals.get(key, 0)
        # categorical iff a small absolute number of distinct values AND each
        # value recurs (distinct/total well below 1.0). Functional identifier
        # columns have distinct ~= total and are rejected.
        if len(distinct) <= 24 and tot >= 2 * max(1, len(distinct)):
            cats.add(key)
    return cats


def build_value_index(hearth, onto: Ontology) -> ValueIndex:
    """Index system-open string values of non-TEXT datatype properties."""
    from ontoforge.contracts import Layer

    idx = ValueIndex()
    text_props = {
        (c_uri, p.name)
        for c_uri in onto.classes
        for p in all_props(onto, c_uri).values()
        if p.datatype is Datatype.TEXT
    }
    for shard in hearth.value_shard_items():
        if shard.layer is not Layer.ENTITY:
            continue
        for cell in shard.cells:
            if not cell.system.open:
                continue
            if (shard.class_uri, cell.prop) in text_props:
                continue
            idx.add(shard.class_uri, cell.prop, cell.value)
    return idx


# ---------------------------------------------------------------- grounding


def strip_notes(question: str) -> str:
    """Normalize the question for binding: drop advisory 'Note: ...' sentences
    (they describe data warts, not what is asked; context sentences are kept —
    they may carry referents) and split lowercase compound hyphens
    ('operator-name' -> 'operator name'; codes like WO-100001 are untouched)."""
    parts = re.split(r"(?<=[.?])\s+", question.strip())
    kept = [p for p in parts if not re.match(r"\(?\s*note\b", p, re.IGNORECASE)]
    out = " ".join(kept) if kept else question
    return re.sub(r"(?<=[a-z])-(?=[a-z])", " ", out)


def question_sentence(question: str) -> str:
    """The interrogative sentence(s) — coverage scope; note sentences excluded."""
    parts = re.split(r"(?<=[.?])\s+", strip_notes(question))
    qs = [p for p in parts if p.rstrip().endswith("?")]
    return " ".join(qs) if qs else (parts[0] if parts else question)


def ground(question: str, onto: Ontology, value_index: ValueIndex) -> GroundingResult:
    lex = Lexicon(onto)
    res = GroundingResult()
    question = strip_notes(question)
    sentence = question_sentence(question)
    raw_tokens = _tok(question)
    tokens = [_norm(t) for t in raw_tokens]
    consumed: set[int] = set()

    def find_phrase(span: tuple[str, ...], start: int = 0) -> Optional[int]:
        for i in range(start, len(tokens) - len(span) + 1):
            if tuple(tokens[i : i + len(span)]) == span:
                return i
        return None

    def mark_words(words: set[str]) -> None:
        for i, t in enumerate(tokens):
            if t in words:
                consumed.add(i)

    def add(b: Binding) -> None:
        if b.pos < 0:
            p = find_phrase(b.span)
            if p is not None:
                b = Binding(b.kind, b.span, b.target, b.value, b.score, b.strong, p)
        res.bindings.append(b)

    # ---- quoted literals (highest-precision probes)
    for m in QUOTED.finditer(question):
        lit = next(g for g in m.groups() if g)
        span = tuple(_norm(t) for t in _tok(lit))
        hits = value_index.probe(lit)
        if hits:
            c_uri, prop, _n = hits[0]
            add(Binding("value", span, target=f"{c_uri}::{prop}", value=lit.strip()))
            idx = find_phrase(span)
            if idx is not None:
                consumed.update(range(idx, idx + len(span)))
            else:
                mark_words(set(span))

    # ---- structural cues
    if ROUND_CUE.search(question):
        add(Binding("round", ("rounded", "nearest"), value=0))
        mark_words({"rounded", "nearest"})
    for m in RECORDED_UNIT.finditer(question):
        word = (m.group(1) or m.group(2) or m.group(3) or "").lower()
        sym = EXTRA_UNIT_WORDS.get(word) or (ALIASES[word].symbol if word in ALIASES else None)
        if sym and not any(b.kind == "recorded_unit" and b.target == sym for b in res.bindings):
            add(Binding("recorded_unit", (word,), target=sym))
            mark_words({word, "recorded", "measured", "logged", "suffix"})
    if MORE_THAN_ONE.search(question):
        add(Binding("having_gt1", ("more", "than", "one")))
        mark_words({"more", "than", "one"})
    m = TOPK.search(question)
    if m:
        add(Binding("topk", ("top",), value=int(m.group(1))))
        mark_words({"top", m.group(1)})

    # ---- time expressions
    asof = AS_OF.search(question)
    if asof:
        iso = parse_date_token(asof.group(1))
        if iso:
            add(Binding("time", ("as", "of"), target="as_of", value=iso))
            mark_words({"as", "of"} | {_norm(t) for t in _tok(asof.group(1))})
    for m in ISO_DATE.finditer(sentence):
        iso = m.group(1)
        if asof and parse_date_token(asof.group(1)) == iso:
            continue
        prefix = sentence[: m.start()].lower()
        op: Optional[CmpOp] = None
        for cue, c_op in CMP_CUES:
            if cue[0] in ("after", "before", "since") and re.search(
                r"\b" + cue[0] + r"\b(?:\s+\S+){0,6}\s*$", prefix
            ):
                op = c_op
                break
        anchor = ""
        am = re.search(r"(\w+)\s+(?:after|before|since)\b[^.?]*$", prefix)
        if am:
            anchor = am.group(1).lower()
        add(
            Binding(
                "date_cond", (iso,), target=anchor,
                value=((op.value if op else CmpOp.EQ.value), iso),
            )
        )
        mark_words({iso, "after", "before", "since", "opened", "closed"})

    # ---- aggregation cues (in token order)
    for cue, agg in AGG_CUES:
        start = 0
        while True:
            idx = find_phrase(cue, start)
            if idx is None:
                break
            add(Binding("agg", cue, target=agg, pos=idx))
            consumed.update(range(idx, idx + len(cue)))
            start = idx + len(cue)

    # ---- comparison + number (+ unit) triples
    #
    # Numeric phrase parsing handles currency prefixes ('$1M'), magnitude
    # suffixes ('10k'), and digit groups. The magnitude/currency reading is
    # attached ONLY when a comparison cue is adjacent (the loop requires it);
    # the unit-dimension check in candidates.py + the type checker still reject
    # a dimension-incompatible literal ('$1M' vs a length property), so a
    # trick-unit can never be silently coerced.
    seen_num_pos: set[int] = set()
    for m in MONEY_MAG.finditer(sentence):
        if not m.group(2):
            continue
        cur_sym, digits, mag = m.group(1), m.group(2), (m.group(3) or "").lower()
        # plain integer/decimal with no currency/magnitude is handled below by
        # the canonical NUMBER pass (keeps date/code exclusion identical)
        if cur_sym is None and not mag:
            continue
        ctx = sentence[max(0, m.start() - 1) : m.end() + 1]
        if "-" in ctx:
            continue  # part of a date or code
        val = float(digits.replace(",", ""))
        if mag in MAGNITUDE:
            val *= MAGNITUDE[mag]
        unit_sym: Optional[str] = CURRENCY_PREFIX.get(cur_sym or "")
        if unit_sym is None and mag and mag != "m":
            # 'm' alone is ambiguous (meters vs million); only treat the
            # currency-prefixed or non-'m' magnitudes here
            pass
        prefix = sentence[: m.start()].lower().rstrip()
        op = None
        for cue, c_op in CMP_CUES:
            if re.search(r"\b" + r"\s+".join(cue) + r"\s*$", prefix):
                op = c_op
                break
        if op is not None:
            span_txt = sentence[m.start():m.end()].strip().lower()
            add(Binding("number_cond", (span_txt,), target=unit_sym or "",
                        value=(op.value, val)))
            for t in _tok(span_txt):
                mark_words({_norm(t)})
            seen_num_pos.update(range(m.start(), m.end()))

    for m in NUMBER.finditer(sentence):
        if m.start() in seen_num_pos:
            continue
        numtxt = m.group(0)
        ctx = sentence[max(0, m.start() - 1) : m.end() + 1]
        if "-" in ctx:
            continue  # part of a date or code
        val = float(numtxt.replace(",", ""))
        after = sentence[m.end():].lstrip()
        unit_word = _norm(after.split()[0]) if after.split() else ""
        unit_sym = EXTRA_UNIT_WORDS.get(unit_word) or (
            ALIASES[unit_word].symbol
            if unit_word in ALIASES and ALIASES[unit_word].confidence >= 0.7
            else None
        )
        prefix = sentence[: m.start()].lower().rstrip()
        op = None
        for cue, c_op in CMP_CUES:
            if re.search(r"\b" + r"\s+".join(cue) + r"\s*$", prefix):
                op = c_op
                break
        if op is not None:
            add(Binding("number_cond", (numtxt.lower(),), target=unit_sym or "", value=(op.value, val)))
            mark_words({numtxt.lower()} | ({unit_word} if unit_sym else set()))

    # ---- 'in <unit>' output-unit requests ('cost in USD', 'altitude in dollars')
    for m in re.finditer(r"\b(?:in|expressed\s+in)\s+([A-Za-z$]+)\b", sentence):
        w = m.group(1).lower()
        sym = EXTRA_UNIT_WORDS.get(w) or (
            ALIASES[w].symbol if w in ALIASES and ALIASES[w].confidence >= 0.7 else None
        )
        if sym and not any(b.kind == "recorded_unit" and b.span == (w,) for b in res.bindings):
            add(Binding("unit", (w,), target=sym))
            mark_words({w})

    # ---- lexicon phrase matching (greedy longest-first)
    n = len(tokens)
    i = 0
    while i < n:
        matched = False
        for ln in (4, 3, 2, 1):
            if i + ln > n:
                continue
            phrase = tuple(tokens[i : i + ln])
            if any(not p for p in phrase):
                continue
            if ln == 1 and (len(phrase[0]) <= 1 or phrase[0] in STOPWORDS or phrase[0] in CUE_WORDS):
                continue
            hits = lex.lookup(phrase)
            if not hits:
                continue
            if (
                ln == 1
                and i + 1 < n
                and tokens[i + 1] in ("a", "an", "the")
                and all(k == "prop" for k, _t, _s, _st in hits)
            ):
                continue  # verb position ('names a ...'), not a property mention
            strong_any = False
            seen_ht: set[tuple[str, str]] = set()
            n_added = 0
            for k2, t2, s2, st2 in hits:
                if (k2, t2) in seen_ht or n_added >= 6:
                    continue
                seen_ht.add((k2, t2))
                n_added += 1
                add(Binding(k2, phrase, target=t2, score=s2, strong=st2, pos=i))
                strong_any = strong_any or st2
            if strong_any:
                consumed.update(range(i, i + ln))
            i += ln
            matched = True
            break
        if not matched:
            i += 1

    # ---- value probes for residual proper-noun/code tokens
    i = 0
    while i < len(raw_tokens):
        raw = raw_tokens[i].strip(".,;:!?'\"()")
        tnorm = tokens[i]
        if i in consumed or not tnorm or tnorm in STOPWORDS:
            i += 1
            continue
        if raw.isupper() and len(raw) > 1 and not raw.replace("-", "").replace("_", "").isdigit():
            words = []
            j = i
            while j < len(raw_tokens):
                w = raw_tokens[j].strip(".,;:!?'\"()")
                if w.isupper() and len(w) > 1:
                    words.append(w)
                    j += 1
                else:
                    break
            # probe every prefix length; distinct (class, prop) hits become
            # ALTERNATIVE value bindings at the same start position (the
            # generator fans them into separate candidates; execution-guided
            # re-ranking settles which population actually holds the value)
            seen_targets: set[str] = set()
            best_ln = 0
            for ln in range(len(words), 0, -1):
                span_txt = " ".join(words[:ln])
                for c_uri, prop, _cnt in value_index.probe(span_txt)[:2]:
                    tgt = f"{c_uri}::{prop}"
                    if tgt in seen_targets:
                        continue
                    seen_targets.add(tgt)
                    score = 1.0 if not best_ln else 0.9
                    add(Binding("value", tuple(t.lower() for t in words[:ln]),
                                target=tgt, value=span_txt, score=score, pos=i))
                    best_ln = max(best_ln, ln)
            if best_ln:
                consumed.update(range(i, i + best_ln))
                i += best_ln
            else:
                i += 1
            continue
        if CODEISH.match(raw):
            hits = value_index.probe(raw)
            if hits:
                c_uri, prop, _cnt = hits[0]
                add(Binding("value", (tnorm,), target=f"{c_uri}::{prop}", value=raw, pos=i))
                consumed.add(i)
            elif re.fullmatch(r"N\d[\dA-Z]+", raw):
                # recognized tail-number surface form with no probe hit: the
                # entity reference is understood; data absence surfaces later
                add(Binding("value", (tnorm,), target="", value=raw, score=0.4, pos=i))
                consumed.add(i)
        elif raw[:1].isupper() and len(raw) > 3:
            # collect the contiguous title-case run ('Delta Air Lines') and probe
            # longest-first, so a multi-word proper noun resolves as ONE value
            # rather than being split into coincidental single-token hits
            words = []
            j = i
            while j < len(raw_tokens) and len(words) < 5:
                w = raw_tokens[j].strip(".,;:!?'\"()")
                wn = _norm(w)
                if w[:1].isupper() and len(w) > 1 and wn not in STOPWORDS:
                    words.append(w)
                    j += 1
                elif wn in ("air", "and", "of") and words:
                    words.append(w)  # connective inside a name ('Delta Air Lines')
                    j += 1
                else:
                    break
            best_ln = 0
            best_tgt: Optional[tuple[str, str]] = None
            for ln in range(len(words), 0, -1):
                span_txt = " ".join(words[:ln])
                probe = value_index.probe(span_txt)
                if probe:
                    best_tgt = (probe[0][0], probe[0][1])
                    best_ln = ln
                    break
            if best_ln and best_tgt is not None:
                add(Binding("value", tuple(_norm(w) for w in words[:best_ln]),
                            target=f"{best_tgt[0]}::{best_tgt[1]}",
                            value=" ".join(words[:best_ln]), score=0.9, pos=i))
                consumed.update(range(i, i + best_ln))
                i += best_ln
                continue
            hits = value_index.probe(raw) or value_index.probe_contains(raw)
            if hits:
                c_uri, prop, _cnt = hits[0]
                add(Binding("value_contains", (tnorm,), target=f"{c_uri}::{prop}",
                            value=raw, score=0.8, pos=i))
                consumed.add(i)
        i += 1

    # ---- categorical value probes for residual LOWERCASE content tokens
    #
    # 'reports during descent', 'landing gear work orders': the categorical
    # value ('Descent', 'LANDING GEAR') is written lowercase in a paraphrase and
    # so is not caught by the proper-noun heuristic above. Probe contiguous
    # unconsumed content tokens (longest-first, up to 3) against the value index,
    # but ACCEPT only hits on CATEGORICAL properties (those with a small closed
    # value set — declared `in_values` shapes, or low distinct-value counts in
    # the index). This is gated so an open identifier column (serial numbers,
    # tail numbers) can never latch onto a stray content word.
    cat_props = _categorical_props(onto, value_index)
    i = 0
    while i < len(tokens):
        tnorm = tokens[i]
        if i in consumed or not tnorm or tnorm in STOPWORDS or tnorm in CUE_WORDS \
                or len(tnorm) <= 2 or not tnorm.isalpha():
            i += 1
            continue
        # prefer the LONGEST exact value-index hit (a 2-word value like
        # 'landing gear' beats a coincidental 1-word categorical 'landing').
        # A hit on a categorical prop becomes a STRONG value binding; a hit on a
        # non-categorical (open) prop becomes a weaker value_contains alternative
        # reading (execution-guided Γ re-ranking settles which population holds
        # it — the substring-latching guard from the design brief).
        chosen_ln = 0
        chosen: Optional[tuple[str, str, str, bool]] = None
        for ln in (3, 2, 1):
            if i + ln > len(tokens):
                continue
            if any((i + k) in consumed for k in range(ln)):
                continue
            span_toks = tokens[i : i + ln]
            if any(t in STOPWORDS or not t.isalpha() for t in span_toks):
                continue
            span_txt = " ".join(span_toks)
            probes = value_index.probe(span_txt)
            if not probes:
                continue
            # within a span length, a categorical hit wins over an open one
            cat_hit = next(((c, p) for c, p, _n in probes if (c, p) in cat_props), None)
            if cat_hit is not None:
                chosen = (cat_hit[0], cat_hit[1], span_txt, True)
            else:
                c_uri, prop, _n = probes[0]
                chosen = (c_uri, prop, span_txt, False)
            chosen_ln = ln
            break
        if chosen_ln and chosen is not None:
            c_uri, prop, span_txt, is_cat = chosen
            if is_cat:
                add(Binding("value", tuple(tokens[i : i + chosen_ln]),
                            target=f"{c_uri}::{prop}", value=span_txt, score=0.85, pos=i))
                consumed.update(range(i, i + chosen_ln))
            else:
                # multi-token open value (e.g. component name): contributes as an
                # alternative reading; only consume when >1 token (single open
                # tokens are too ambiguous to strongly consume)
                add(Binding("value_contains", tuple(tokens[i : i + chosen_ln]),
                            target=f"{c_uri}::{prop}", value=span_txt, score=0.75, pos=i))
                if chosen_ln > 1:
                    consumed.update(range(i, i + chosen_ln))
            i += chosen_ln
        else:
            i += 1

    # ---- textJoin cue + object phrase
    #
    # The object of a narrative predicate may be separated from the cue by
    # connective filler ('talk ABOUT birds', 'reports THAT mention ...'). Skip a
    # leading run of articles/connectives, then collect content words (skipping
    # interior filler) until a strong schema binding or a clause boundary. The
    # object is the search PATTERN over a TEXT property — never a binding the
    # lexicon/value-index produced, so it cannot fabricate an answer.
    CONNECTIVES = {"a", "an", "the", "about", "that", "which", "who",
                   "regarding", "concerning", "involving", "of", "on", "to",
                   "any", "some", "for", "with"}
    for i, t in enumerate(tokens):
        if t in TEXTJOIN_CUES:
            j = i + 1
            # skip the leading connective run
            while j < len(tokens) and (tokens[j] in CONNECTIVES
                                       or tokens[j] in TEXTJOIN_CUES):
                j += 1
            phrase_words: list[str] = []
            skipped: list[int] = []
            while j < len(tokens) and len(phrase_words) < 5:
                w = tokens[j]
                if not w or not re.fullmatch(r"[a-z]+", w):
                    break
                if any(
                    w in b.span and b.strong and b.kind in ("class", "prop")
                    for b in res.bindings
                ):
                    break
                if w in STOPWORDS:
                    # allow a single interior connective ('birds hitting THE
                    # planes') but stop at a hard clause boundary
                    if w in ("a", "an", "the") and phrase_words:
                        skipped.append(j)
                        j += 1
                        continue
                    break
                phrase_words.append(w)
                j += 1
            if phrase_words:
                add(Binding("textjoin", tuple([t] + phrase_words),
                            value=" ".join(phrase_words), pos=i))
                mark_words(set([t] + phrase_words))
            break

    # ---- coverage over the interrogative sentence, strong bindings only
    strong_spans: set[str] = set()
    for b in res.bindings:
        if b.strong:
            strong_spans.update(b.span)
    content, covered = [], []
    for t in (_norm(x) for x in _tok(sentence)):
        if not t or t in STOPWORDS or len(t) <= 1:
            continue
        content.append(t)
        if t in strong_spans or t in CUE_WORDS or parse_date_token(t):
            covered.append(t)
    res.content_words = tuple(content)
    res.consumed = tuple(sorted(set(covered)))
    res.unconsumed = tuple(sorted(set(content) - set(covered)))
    res.coverage = (len(set(covered)) / len(set(content))) if content else 0.0
    return res
