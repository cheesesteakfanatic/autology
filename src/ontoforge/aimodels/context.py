"""Schema linking — fit a large induced ontology into a token budget.

docs/AI_NATIVE_AND_UI_PLAN.md §B: a huge induced ontology cannot be injected
whole into a model's context. We prune EXTRACTIVELY (select existing elements;
never generate) given a focus (a class + the columns/props the task mentions) and
a budget. The selection is deterministic — lexical + structural relevance only,
no embeddings, no network — and **bidirectional**:

* **forward** relevance: elements lexically similar to the focus terms (the
  class/columns the task names), plus the focus class's own properties;
* **backward** relevance: elements reachable through the schema graph — the
  ranges of the focus class's link properties, and classes/props that link INTO
  the focus class (inbound ranges). Following links in both directions is what
  recovers needed-but-not-named elements (e.g. a join target's key column), which
  is where naive forward-only lexical pruning loses recall.

Relevance is scored, elements are taken in descending score until the budget
(approx. property count) is filled, and the focus class + its identity-ish
properties are always kept. The research target (validated on the synthetic
200-property ontology in the tests): **>=90% recall of a known-needed set at
>=70% column pruning**.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

__all__ = [
    "LinkedSchema",
    "SchemaElement",
    "link_schema",
    "render_grounding",
]

_TOK = re.compile(r"[a-z0-9]+")


def _tokens(s: str) -> set[str]:
    return set(_TOK.findall(str(s).lower()))


def _lex_sim(a: set[str], b: set[str]) -> float:
    """Token Jaccard with a substring bonus (so 'cust' ~ 'customer')."""
    if not a or not b:
        return 0.0
    inter = len(a & b)
    jac = inter / len(a | b)
    bonus = 0.0
    for x in a:
        for y in b:
            if x != y and (x in y or y in x) and min(len(x), len(y)) >= 3:
                bonus = max(bonus, 0.5 * min(len(x), len(y)) / max(len(x), len(y)))
    return min(1.0, jac + bonus)


@dataclass(frozen=True, slots=True)
class SchemaElement:
    """One selectable schema element: a property on a class (or the class node
    itself when ``prop`` is None)."""

    class_uri: str
    class_name: str
    prop: Optional[str] = None
    datatype: str = ""
    is_link: bool = False
    range_class: Optional[str] = None
    score: float = 0.0

    @property
    def key(self) -> tuple[str, Optional[str]]:
        return (self.class_uri, self.prop)


@dataclass(slots=True)
class LinkedSchema:
    """The pruned subset plus accounting: ``kept`` elements, the ``total`` element
    count, and ``pruning`` = fraction removed."""

    focus_class: str
    kept: list[SchemaElement] = field(default_factory=list)
    total: int = 0

    @property
    def pruning(self) -> float:
        return 0.0 if self.total == 0 else 1.0 - (len(self.kept) / self.total)

    @property
    def kept_keys(self) -> set[tuple[str, Optional[str]]]:
        return {e.key for e in self.kept}

    def recall(self, needed: Iterable[tuple[str, Optional[str]]]) -> float:
        need = set(needed)
        if not need:
            return 1.0
        return len(need & self.kept_keys) / len(need)


def _identityish(prop: str) -> bool:
    n = prop.lower()
    return n.endswith("_id") or n == "id" or n.endswith("id") or "key" in n or "code" in n


def _all_props(ontology: Any, class_uri: str) -> list[Any]:
    c = ontology.get(class_uri)
    return list(c.properties) if c is not None else []


def link_schema(
    ontology: Any,
    focus_class: str,
    focus_columns: Iterable[str] = (),
    budget: int = 40,
) -> LinkedSchema:
    """Extractively select the schema subset relevant to ``focus_class`` and the
    ``focus_columns`` the task mentions, capped at ~``budget`` properties.

    Deterministic and bidirectional. The focus class and its identity-ish
    properties are always retained; the rest are ranked by a blend of lexical
    relevance to the focus terms and structural proximity (1-hop link in either
    direction = strong; the focus class's own props = strongest)."""
    classes = list(ontology.iter_classes())
    # full element universe (every property of every class)
    universe: list[SchemaElement] = []
    by_class: dict[str, list[SchemaElement]] = {}
    name_of: dict[str, str] = {}
    for c in classes:
        name_of[c.uri] = c.name
    for c in classes:
        for p in c.properties:
            el = SchemaElement(
                class_uri=c.uri,
                class_name=c.name,
                prop=p.name,
                datatype=getattr(getattr(p, "datatype", None), "value", "") or "",
                is_link=bool(getattr(p, "is_link", False)),
                range_class=getattr(p, "range_class", None),
            )
            universe.append(el)
            by_class.setdefault(c.uri, []).append(el)

    total = len(universe)
    focus_terms = _tokens(name_of.get(focus_class, focus_class))
    for col in focus_columns:
        focus_terms |= _tokens(col)

    # structural proximity: 1-hop neighbours of the focus class in BOTH directions
    fwd_targets: set[str] = set()  # ranges of the focus class's links (forward)
    for p in _all_props(ontology, focus_class):
        if getattr(p, "is_link", False) and getattr(p, "range_class", None):
            fwd_targets.add(p.range_class)
    bwd_sources: set[str] = set()  # classes whose links point AT the focus (backward)
    for c in classes:
        for p in c.properties:
            if getattr(p, "is_link", False) and getattr(p, "range_class", None) == focus_class:
                bwd_sources.add(c.uri)

    def proximity(el: SchemaElement) -> float:
        if el.class_uri == focus_class:
            return 1.0
        if el.class_uri in fwd_targets or el.class_uri in bwd_sources:
            return 0.7
        return 0.0

    scored: list[SchemaElement] = []
    for el in universe:
        lex = _lex_sim(focus_terms, _tokens(f"{el.class_name} {el.prop or ''}"))
        prox = proximity(el)
        # identity-ish columns on a structurally-near class are the join keys we
        # must not drop (backward recall): boost them.
        keyboost = 0.0
        if el.prop and _identityish(el.prop) and prox > 0.0:
            keyboost = 0.35
        # a link property on the focus class is a bridge — keep it (it names the edge)
        linkboost = 0.25 if (el.class_uri == focus_class and el.is_link) else 0.0
        score = 0.55 * prox + 0.45 * lex + keyboost + linkboost
        scored.append(
            SchemaElement(
                class_uri=el.class_uri, class_name=el.class_name, prop=el.prop,
                datatype=el.datatype, is_link=el.is_link, range_class=el.range_class,
                score=round(score, 6),
            )
        )

    # always-keep: focus class's own props + identity-ish props of near classes
    pinned: set[tuple[str, Optional[str]]] = set()
    for el in scored:
        if el.class_uri == focus_class:
            pinned.add(el.key)
        if el.prop and _identityish(el.prop) and proximity(el) >= 0.7:
            pinned.add(el.key)

    # deterministic order: score desc, then class_uri, then prop
    def sort_key(el: SchemaElement):
        return (-el.score, el.class_uri, el.prop or "")

    ranked = sorted(scored, key=sort_key)
    kept: list[SchemaElement] = []
    kept_keys: set[tuple[str, Optional[str]]] = set()
    # pin first (guaranteed in)
    for el in ranked:
        if el.key in pinned and el.key not in kept_keys:
            kept.append(el)
            kept_keys.add(el.key)
    # then fill by score up to the budget
    for el in ranked:
        if len(kept) >= budget:
            break
        if el.key not in kept_keys and el.score > 0.0:
            kept.append(el)
            kept_keys.add(el.key)

    return LinkedSchema(focus_class=focus_class, kept=kept, total=total)


def render_grounding(linked: LinkedSchema, ontology: Any) -> str:
    """Render the pruned subset as a compact grounding block for a prompt —
    grouped by class, deterministic ordering."""
    by_class: dict[str, list[SchemaElement]] = {}
    for el in linked.kept:
        by_class.setdefault(el.class_uri, []).append(el)
    lines: list[str] = []
    for uri in sorted(by_class):
        c = ontology.get(uri)
        cname = c.name if c is not None else uri
        props = sorted((e for e in by_class[uri] if e.prop), key=lambda e: e.prop or "")
        rendered = []
        for e in props:
            if e.is_link and e.range_class:
                tgt = ontology.get(e.range_class)
                rendered.append(f"{e.prop}->{tgt.name if tgt else e.range_class}")
            else:
                rendered.append(f"{e.prop}:{e.datatype}" if e.datatype else e.prop)
        lines.append(f"- {cname}({', '.join(rendered)})")
    return "\n".join(lines)
