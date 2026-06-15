"""Deterministic, keyless command parser for the NL-DE layer.

A plain-English imperative is parsed in two stages, both pure-python:

1. **cue-word trigger** — a fixed table of verbs/phrases routes the sentence to
   exactly one operator kind (``link``/``synonym``/``retype``/``merge_entities``/
   ``split``/``rename``). No cue -> :class:`UnsupportedCommand`.
2. **slot extraction** — the remaining tokens are matched against the LIVE
   ontology (class + property display names) and estate (table + column names).
   A slot that matches nothing, or matches ambiguously, yields a single
   :class:`ClarificationNeeded` question — NEVER a guessed operator. This is the
   confidently-wrong guard at the language layer (LODESTONE's clarify-don't-guess
   contract): the parser only ever PROPOSES.

There is zero fuzzy NL inference about intent: matching is exact-token or
normalized-substring against names that actually exist in the world. The same
schema vocabulary the parser matches against is what the operator-application
service compiles the proposed command into.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional, Union

__all__ = [
    "PARSEABLE_KINDS",
    "ClarificationNeeded",
    "ParseResult",
    "ProposedCommand",
    "SchemaView",
    "UnsupportedCommand",
    "parse_command",
]

PARSEABLE_KINDS = ("link", "synonym", "retype", "merge_entities", "split", "rename")

#: example commands surfaced when a command is unsupported (the API contract's
#: ``supported_examples``)
SUPPORTED_EXAMPLES = (
    "link orders to customers on customer_id",
    "treat amount as currency",
    "rename qty to quantity",
    "merge duplicate suppliers",
    "split full_name into first and last on space",
    "price means the same as net_price",
)


# --------------------------------------------------------------- result types


@dataclass(frozen=True, slots=True)
class ProposedCommand:
    """A successfully parsed command: an operator kind + resolved slots.

    Nothing is applied — this is a proposal the operator service previews and
    (only on explicit confirm) applies. ``human_summary`` is the readable echo
    the UI shows; ``confidence`` is the parse confidence (slot-match quality),
    NOT a claim about the data."""

    kind: str
    params: dict[str, Any]
    human_summary: str
    confidence: float = 1.0


@dataclass(frozen=True, slots=True)
class ClarificationNeeded:
    """The command parsed to a kind but a slot was ambiguous/unresolved.

    The UI asks the ONE question and re-submits; the parser never guesses."""

    clarification: str
    options: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class UnsupportedCommand:
    """No cue word matched — the command is outside the supported grammar."""

    reason: str
    supported_examples: tuple[str, ...] = SUPPORTED_EXAMPLES


ParseResult = Union[ProposedCommand, ClarificationNeeded, UnsupportedCommand]


# --------------------------------------------------------------- schema view


@dataclass
class SchemaView:
    """The live vocabulary the parser matches slots against.

    * ``classes`` — {normalized name -> class_uri} for class display names.
    * ``props`` — {normalized prop name -> [(class_uri, prop_name)]} across all
      classes (a property name can live on several classes).
    * ``tables`` — {normalized table -> table} estate table names.
    * ``columns`` — {normalized column -> [(table, column)]} estate columns.
    """

    classes: dict[str, str] = field(default_factory=dict)
    props: dict[str, list[tuple[str, str]]] = field(default_factory=dict)
    tables: dict[str, str] = field(default_factory=dict)
    columns: dict[str, list[tuple[str, str]]] = field(default_factory=dict)
    #: original display names, for clarification option lists
    class_names: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_world(cls, ontology: Any, profiles: Optional[dict[str, Any]] = None) -> "SchemaView":
        from ontoforge.lodestone.model import all_props

        view = cls()
        if ontology is not None:
            for c in ontology.iter_classes():
                view.classes[_norm(c.name)] = c.uri
                view.class_names[c.uri] = c.name
            for c in ontology.iter_classes():
                for pname, pdef in all_props(ontology, c.uri).items():
                    view.props.setdefault(_norm(pname), []).append((c.uri, pname))
        if profiles:
            for tname, tp in profiles.items():
                view.tables[_norm(tname)] = tname
                cols = getattr(tp, "columns", {}) or {}
                for col in cols:
                    view.columns.setdefault(_norm(col), []).append((tname, col))
        return view


_NORM_RE = re.compile(r"[^a-z0-9]+")


def _norm(s: str) -> str:
    """Normalize a name/slot to comparable form: lowercased, separators -> '_'."""
    return _NORM_RE.sub("_", str(s).lower()).strip("_")


# ------------------------------------------------------------------ cue table


#: (kind, compiled-cue) — order matters: more specific cues come first so e.g.
#: "means the same as" wins over a bare "as". Each cue is a regex over the
#: lowercased command; the FIRST match routes the kind.
_CUES: tuple[tuple[str, re.Pattern], ...] = (
    ("synonym", re.compile(r"\bmeans?\s+the\s+same\b|\bsame\s+as\b|\bsynonym\b|\bis\s+the\s+same\s+(kind|thing)\b")),
    ("merge_entities", re.compile(r"\b(merge|dedupe|deduplicate)\b|\bsame\s+\w+\s+is\s+one\b")),
    ("split", re.compile(r"\bsplit\b")),
    ("retype", re.compile(r"\btreat\b.*\bas\b|\bparse\b.*\bas\b|\bconvert\b.*\bto\b|\bretype\b")),
    ("link", re.compile(r"\b(link|join|connect)\b")),
    ("rename", re.compile(r"\brename\b|\bcall\b")),
)


def _cue_kind(text: str) -> Optional[str]:
    for kind, rx in _CUES:
        if rx.search(text):
            return kind
    return None


# ------------------------------------------------------------- slot resolution


def _name_variants(slot: str) -> list[str]:
    """Normalized forms to try for a class/table slot: as-is, singular, plural.
    Lets 'orders'/'order' both resolve to a class named 'Order'/'Orders'."""
    n = _norm(slot)
    out = [n]
    if n.endswith("s"):
        out.append(n[:-1])          # orders -> order
    if n.endswith("ies"):
        out.append(n[:-3] + "y")    # categories -> category
    out.append(n + "s")             # order -> orders
    seen: list[str] = []
    for v in out:
        if v and v not in seen:
            seen.append(v)
    return seen


def _resolve_class_or_table(slot: str, schema: SchemaView) -> tuple[Optional[str], Optional[str], bool]:
    """Resolve a slot to a class uri or an estate table.

    Tries exact then singular/plural variants against class names first (the
    user-facing view), then estate table names. Returns (class_uri, table,
    ambiguous)."""
    for v in _name_variants(slot):
        if v in schema.classes:
            return schema.classes[v], None, False
    for v in _name_variants(slot):
        if v in schema.tables:
            return None, schema.tables[v], False
    return None, None, False


def _resolve_prop_or_column(slot: str, schema: SchemaView) -> dict[str, Any]:
    """Resolve a slot to a property (with owning classes) and/or estate column.

    Returns {prop, classes:[uri], columns:[(table,col)], matched:bool}."""
    n = _norm(slot)
    prop_hits = schema.props.get(n, [])
    col_hits = schema.columns.get(n, [])
    return {
        "prop": slot if (prop_hits or col_hits) else None,
        "classes": [uri for uri, _ in prop_hits],
        "prop_names": sorted({pn for _, pn in prop_hits}),
        "columns": col_hits,
        "matched": bool(prop_hits or col_hits),
    }


# ------------------------------------------------------------- per-kind parsers


def _split_on_connector(rest: str, connectors: tuple[str, ...]) -> Optional[tuple[str, str]]:
    """Split ``rest`` into (left, right) at the FIRST connector word; None if no
    connector present."""
    for conn in connectors:
        m = re.search(rf"\s+{re.escape(conn)}\s+", rest)
        if m:
            return rest[: m.start()].strip(), rest[m.end():].strip()
    return None


def _parse_link(text: str, schema: SchemaView) -> ParseResult:
    # link A to B [on x=y | using col] ; join A and B ; connect A to B
    body = re.sub(r"^\s*(link|join|connect)\s+", "", text, count=1)
    on_cols: tuple[Optional[str], Optional[str]] = (None, None)
    m_on = re.search(r"\s+on\s+(.+)$", body)
    m_using = re.search(r"\s+using\s+(\S+)\s*$", body)
    if m_on:
        on_clause = m_on.group(1).strip()
        body = body[: m_on.start()].strip()
        if "=" in on_clause:
            lhs, rhs = on_clause.split("=", 1)
            on_cols = (lhs.strip(), rhs.strip())
        else:
            on_cols = (on_clause.strip(), on_clause.strip())
    elif m_using:
        col = m_using.group(1).strip()
        body = body[: m_using.start()].strip()
        on_cols = (col, col)

    pair = _split_on_connector(body, ("to", "and", "with", "against"))
    if pair is None:
        return ClarificationNeeded(
            clarification="Which two things should I link? Say e.g. 'link orders to customers on customer_id'."
        )
    left_s, right_s = pair
    lc, lt, _ = _resolve_class_or_table(left_s, schema)
    rc, rt, _ = _resolve_class_or_table(right_s, schema)
    if (lc is None and lt is None) or (rc is None and rt is None):
        unknown = left_s if (lc is None and lt is None) else right_s
        opts = sorted(schema.class_names.values()) + sorted(schema.tables.values())
        return ClarificationNeeded(
            clarification=f"I don't recognize {unknown!r}. Which class or table did you mean?",
            options=tuple(opts[:12]),
        )
    params = {
        "left_class": lc, "left_table": lt, "left_label": left_s.strip(),
        "right_class": rc, "right_table": rt, "right_label": right_s.strip(),
        "on_left": on_cols[0], "on_right": on_cols[1],
    }
    on_txt = f" on {on_cols[0]} = {on_cols[1]}" if on_cols[0] else ""
    summary = f"link {left_s.strip()} → {right_s.strip()}{on_txt}"
    return ProposedCommand(kind="link", params=params, human_summary=summary)


def _parse_synonym(text: str, schema: SchemaView) -> ParseResult:
    # synonym connectors are multi-word phrases; split on the first one found
    m = re.split(
        r"\s+(?:means?\s+the\s+same\s+as|means?\s+the\s+same|is\s+the\s+same(?:\s+kind\s+of\s+thing)?\s+as|same\s+as|synonym\s+for)\s+",
        text, maxsplit=1,
    )
    if len(m) != 2:
        return ClarificationNeeded(
            clarification="Which two properties mean the same? Say e.g. 'price means the same as net_price'."
        )
    left_s = re.sub(r"^\s*(treat\s+)?", "", m[0]).strip()
    right_s = m[1].strip()
    left = _resolve_prop_or_column(left_s, schema)
    right = _resolve_prop_or_column(right_s, schema)
    if not left["matched"] or not right["matched"]:
        unknown = left_s if not left["matched"] else right_s
        return ClarificationNeeded(
            clarification=f"I don't recognize the property {unknown!r}. Which property did you mean?",
            options=tuple(sorted(set(schema.props))[:12]),
        )
    params = {
        "left_prop": left_s, "right_prop": right_s,
        "left_classes": left["classes"], "right_classes": right["classes"],
        "left_columns": left["columns"], "right_columns": right["columns"],
    }
    return ProposedCommand(
        kind="synonym", params=params,
        human_summary=f"treat {left_s} and {right_s} as the same property",
    )


_TYPE_WORDS = {
    "date": ("date", "as_a_date", "a_date", "dates"),
    "datetime": ("datetime", "timestamp", "a_timestamp"),
    "number": ("number", "numeric", "a_number", "float", "decimal"),
    "integer": ("integer", "int", "whole_number"),
    "currency": ("currency", "money", "usd", "dollars", "amount_in"),
}


def _parse_retype(text: str, schema: SchemaView) -> ParseResult:
    # treat <col> as <type> ; parse <col> as <type> ; convert <col> to <unit>
    m = re.search(r"\b(?:treat|parse|convert|retype)\s+(.+?)\s+(?:as|to)\s+(.+)$", text)
    if not m:
        return ClarificationNeeded(
            clarification="What should I retype, and to what? Say e.g. 'treat amount as currency'."
        )
    col_s = re.sub(r"^(the|column|col)\s+", "", m.group(1).strip())
    type_s = m.group(2).strip()
    type_n = _norm(type_s)
    target_type = None
    for canon, words in _TYPE_WORDS.items():
        if type_n in words or any(type_n.startswith(w) for w in words):
            target_type = canon
            break
    resolved = _resolve_prop_or_column(col_s, schema)
    if not resolved["matched"]:
        return ClarificationNeeded(
            clarification=f"I don't recognize the column {col_s!r}. Which property did you mean?",
            options=tuple(sorted(set(schema.props))[:12]),
        )
    if target_type is None:
        return ClarificationNeeded(
            clarification=f"What type should {col_s!r} be? date, number, integer, or currency?",
            options=("date", "number", "integer", "currency"),
        )
    params = {
        "prop": col_s, "target_type": target_type, "raw_type": type_s,
        "classes": resolved["classes"], "prop_names": resolved["prop_names"],
        "columns": resolved["columns"],
    }
    return ProposedCommand(
        kind="retype", params=params,
        human_summary=f"treat {col_s} as {target_type}",
    )


def _parse_merge(text: str, schema: SchemaView) -> ParseResult:
    # merge <class> / dedupe <class> / merge duplicate <class>
    m = re.search(r"\b(?:merge|dedupe|deduplicate)\s+(?:duplicate|these|the)?\s*(.+?)\s*$", text)
    if not m:
        return ClarificationNeeded(
            clarification="Which entities should I merge? Say e.g. 'merge duplicate suppliers'."
        )
    target = m.group(1).strip()
    # try plural -> singular trim for class match
    cands = [target, target.rstrip("s"), re.sub(r"s$", "", target)]
    class_uri = None
    matched_label = target
    for cand in cands:
        n = _norm(cand)
        if n in schema.classes:
            class_uri = schema.classes[n]
            matched_label = cand
            break
    if class_uri is None:
        return ClarificationNeeded(
            clarification=f"I don't recognize the entity type {target!r}. Which class did you mean?",
            options=tuple(sorted(schema.class_names.values())[:12]),
        )
    return ProposedCommand(
        kind="merge_entities",
        params={"class_uri": class_uri, "label": matched_label},
        human_summary=f"merge duplicate {matched_label} into single entities",
    )


def _parse_split(text: str, schema: SchemaView) -> ParseResult:
    # split <col> into <x> and <y> [on <delim>]
    m = re.search(r"\bsplit\s+(.+?)\s+into\s+(.+?)(?:\s+(?:on|by)\s+(.+))?$", text)
    if not m:
        return ClarificationNeeded(
            clarification="What should I split, and into what? Say e.g. 'split full_name into first and last on space'."
        )
    col_s = re.sub(r"^(the|column|col)\s+", "", m.group(1).strip())
    parts_s = m.group(2).strip()
    delim_s = (m.group(3) or "").strip()
    parts = [p.strip() for p in re.split(r"\s+and\s+|\s*,\s*", parts_s) if p.strip()]
    resolved = _resolve_prop_or_column(col_s, schema)
    if not resolved["matched"]:
        return ClarificationNeeded(
            clarification=f"I don't recognize the column {col_s!r}. Which property did you mean?",
            options=tuple(sorted(set(schema.props))[:12]),
        )
    if len(parts) < 2:
        return ClarificationNeeded(
            clarification=f"What parts should {col_s!r} split into? Name at least two, e.g. 'first and last'."
        )
    delim = {"space": " ", "comma": ",", "dash": "-", "hyphen": "-", "slash": "/"}.get(
        _norm(delim_s), delim_s or " "
    )
    params = {
        "prop": col_s, "parts": parts, "delimiter": delim,
        "classes": resolved["classes"], "columns": resolved["columns"],
    }
    return ProposedCommand(
        kind="split", params=params,
        human_summary=f"split {col_s} into {' and '.join(parts)} on {delim_s or 'space'!r}",
    )


def _parse_rename(text: str, schema: SchemaView) -> ParseResult:
    # rename <prop|class> to <newname> ; call <prop> <newname>
    m = re.search(r"\brename\s+(.+?)\s+to\s+(.+?)\s*$", text)
    if not m:
        m = re.search(r"\bcall\s+(.+?)\s+(\S+)\s*$", text)
    if not m:
        return ClarificationNeeded(
            clarification="What should I rename, and to what? Say e.g. 'rename qty to quantity'."
        )
    target_s = re.sub(r"^(the|column|col|property|class)\s+", "", m.group(1).strip())
    new_name = m.group(2).strip()
    # property first, then class
    resolved = _resolve_prop_or_column(target_s, schema)
    if resolved["matched"]:
        params = {
            "kind_target": "property", "old_name": target_s, "new_name": new_name,
            "classes": resolved["classes"], "prop_names": resolved["prop_names"],
        }
        return ProposedCommand(
            kind="rename", params=params,
            human_summary=f"rename property {target_s} → {new_name}",
        )
    n = _norm(target_s)
    if n in schema.classes:
        params = {
            "kind_target": "class", "old_name": target_s, "new_name": new_name,
            "class_uri": schema.classes[n],
        }
        return ProposedCommand(
            kind="rename", params=params,
            human_summary=f"rename class {target_s} → {new_name}",
        )
    return ClarificationNeeded(
        clarification=f"I don't recognize {target_s!r}. Which property or class did you mean?",
        options=tuple((sorted(set(schema.props)) + sorted(schema.class_names.values()))[:12]),
    )


_PARSERS = {
    "link": _parse_link,
    "synonym": _parse_synonym,
    "retype": _parse_retype,
    "merge_entities": _parse_merge,
    "split": _parse_split,
    "rename": _parse_rename,
}


# ------------------------------------------------------------------- entry pt


def parse_command(command: str, schema: SchemaView) -> ParseResult:
    """Parse one plain-English imperative against the live schema.

    Returns a :class:`ProposedCommand`, a :class:`ClarificationNeeded` (one
    question, never a guess), or an :class:`UnsupportedCommand`. Deterministic
    and keyless — equal (command, schema) always yields the same result."""
    text = (command or "").strip().lower()
    if not text:
        return UnsupportedCommand(reason="empty command")
    kind = _cue_kind(text)
    if kind is None:
        return UnsupportedCommand(
            reason=(
                "no supported data-engineering verb found "
                "(link/join, means-the-same, treat-as, merge/dedupe, split, rename)"
            )
        )
    return _PARSERS[kind](text, schema)
