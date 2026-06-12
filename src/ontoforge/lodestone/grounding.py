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

AGG_CUES: list[tuple[tuple[str, ...], str]] = [
    (("how", "many"), "count"),
    (("number", "of"), "count"),
    (("counting",), "count"),
    (("count",), "count"),
    (("total",), "sum"),
    (("sum",), "sum"),
    (("average",), "avg"),
    (("mean",), "avg"),
    (("lowest",), "min"),
    (("minimum",), "min"),
    (("smallest",), "min"),
    (("highest",), "max"),
    (("maximum",), "max"),
    (("largest",), "max"),
]

CMP_CUES: list[tuple[tuple[str, ...], CmpOp]] = [
    (("less", "than"), CmpOp.LT),
    (("fewer", "than"), CmpOp.LT),
    (("below",), CmpOp.LT),
    (("under",), CmpOp.LT),
    (("greater", "than"), CmpOp.GT),
    (("more", "than"), CmpOp.GT),
    (("above",), CmpOp.GT),
    (("over",), CmpOp.GT),
    (("at", "least"), CmpOp.GE),
    (("at", "most"), CmpOp.LE),
    (("after",), CmpOp.GT),
    (("before",), CmpOp.LT),
    (("since",), CmpOp.GE),
]

TEXTJOIN_CUES = {
    "describe", "describes", "describing", "described",
    "mention", "mentions", "mentioning", "mentioned",
    "cite", "cites", "citing", "cited",
    "reference", "references", "referencing",
    "matching", "containing",
}

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
}

ROUND_CUE = re.compile(r"rounded?\s+to\s+the\s+nearest", re.IGNORECASE)
RECORDED_UNIT = re.compile(
    r"(?:recorded|measured)\s+in\s+([a-z]+)|'([a-z]+)'\s+suffix", re.IGNORECASE
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
    | {"as", "of", "recorded", "expressed", "measured", "rounded", "nearest", "suffix",
       "top", "opened", "closed", "folded", "one"}
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
    """Deterministic surface-form index over one ontology."""

    def __init__(self, onto: Ontology) -> None:
        self.onto = onto
        # phrase -> [(kind, target, score, strong)]
        self.phrases: dict[tuple[str, ...], list[tuple[str, str, float, bool]]] = {}
        for c_uri in sorted(onto.classes):
            c = onto.classes[c_uri]
            name_toks = tuple(_camel_split(c.name))
            self._add(name_toks, "class", c_uri, 1.0, True)
            if len(name_toks) > 1:
                self._add((name_toks[-1],), "class", c_uri, 0.6, True)
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
                for syn in p.synonyms:
                    self._add(tuple(syn.lower().split()), "prop", target, 1.0, True)
                    self._add(tuple(syn.lower().replace("-", " ").split()), "prop", target, 1.0, True)

    def _add(self, phrase: tuple[str, ...], kind: str, target: str, score: float, strong: bool) -> None:
        if phrase and all(phrase):
            self.phrases.setdefault(phrase, []).append((kind, target, score, strong))

    def lookup(self, phrase: tuple[str, ...]) -> list[tuple[str, str, float, bool]]:
        hits = list(self.phrases.get(phrase, []))
        if not hits and phrase:
            alt = phrase[:-1] + (_singular(phrase[-1]),)
            if alt != phrase:
                hits = list(self.phrases.get(alt, []))
        # fuzzy fallback for single inflected tokens ('manufactured' ~ 'manufacturer')
        if not hits and len(phrase) == 1 and len(phrase[0]) >= 6:
            w = phrase[0]
            for key in sorted(self.phrases):
                if len(key) != 1:
                    continue
                k = key[0]
                if len(k) < 6:
                    continue
                common = 0
                for a, b in zip(w, k):
                    if a != b:
                        break
                    common += 1
                if common >= max(len(w), len(k)) - 2 and common >= 6:
                    hits.extend(
                        (kind, target, score * 0.85, strong)
                        for kind, target, score, strong in self.phrases[key]
                    )
        return sorted(hits, key=lambda h: (-h[2], h[0], h[1]))


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
        word = (m.group(1) or m.group(2) or "").lower()
        sym = EXTRA_UNIT_WORDS.get(word) or (ALIASES[word].symbol if word in ALIASES else None)
        if sym and not any(b.kind == "recorded_unit" and b.target == sym for b in res.bindings):
            add(Binding("recorded_unit", (word,), target=sym))
            mark_words({word, "recorded", "measured", "suffix"})
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
    for m in NUMBER.finditer(sentence):
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
            hits = value_index.probe(raw) or value_index.probe_contains(raw)
            if hits:
                c_uri, prop, _cnt = hits[0]
                add(Binding("value_contains", (tnorm,), target=f"{c_uri}::{prop}",
                            value=raw, score=0.8, pos=i))
                consumed.add(i)
        i += 1

    # ---- textJoin cue + object phrase
    for i, t in enumerate(tokens):
        if t in TEXTJOIN_CUES:
            phrase_words: list[str] = []
            j = i + 1
            while j < len(tokens) and len(phrase_words) < 4:
                w = tokens[j]
                if w in ("a", "an", "the"):
                    j += 1
                    continue
                if not w or w in STOPWORDS or not re.fullmatch(r"[a-z]+", w):
                    break
                if any(
                    w in b.span and b.strong and b.kind in ("class", "prop")
                    for b in res.bindings
                ):
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
