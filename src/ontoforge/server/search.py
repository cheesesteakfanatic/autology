"""Federated search over one project world (the OS shell's Cmd+K seam).

The frozen contract (the UI builds against it blind):

    GET /api/search?q=<query>&limit=20
    -> {"results": [{"kind": "class|entity|property|question|app",
                     "title": str, "subtitle": str, "ref": str, "score": float}]}

Kinds and their refs:

- ``class``     induced ontology classes, matched by name      ref = class uri
                (ClassDef carries no synonyms field in v0 — synonym matching
                applies where synonyms exist, i.e. properties)
- ``entity``    HEARTH entities, matched by key-ish/name-ish   ref = entity uri
                current cell values and by the entity uri itself
- ``property``  ontology properties by name + synonyms         ref = class_uri#prop
- ``question``  saved/recent asks from the ledger              ref = question text
- ``app``       the static app registry                        ref = app id

Ranking is a strict tier order — exact-prefix > word-prefix > substring >
fuzzy — encoded as DISJOINT score bands so kinds interleave purely by score:

    exact-prefix   (0.75, 1.00]   (query == text scores exactly 1.0)
    word-prefix    (0.55, 0.70]
    substring      (0.35, 0.45]
    fuzzy          [0.18, 0.30)   (difflib ratio >= 0.6, scaled)

Within a band, shorter matched text scores higher (len(q)/len(t) bonus).

The entity value index is built lazily from HEARTH current cells and is
memory-capped on purpose: only *key-ish/name-ish* properties are indexed —
functional (FD-backed key) properties plus properties whose name or synonyms
match the name/title/id/number/code/key word patterns — never every cell.
``ProjectWorld`` owns the index and drops it on /api/reload.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Iterable, Optional, Sequence

from ontoforge.contracts import Layer

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ontoforge.contracts.ontology import Ontology, PropertyDef
    from ontoforge.hearth import Hearth

# ------------------------------------------------------------------ registry

#: The static app registry the shell launches from (kind=app, ref=app id).
APP_REGISTRY: tuple[tuple[str, str, str], ...] = (
    ("ask", "Ask", "cited answers with abstention"),
    ("constellation", "Constellation", "the ontology class graph"),
    ("entities", "Entities", "entity inspector and time travel"),
    ("review", "Review", "human review queue and verdicts"),
    ("dashboards", "Dashboards", "proposed and saved dashboards"),
    ("status", "Status", "pipeline stages and ledger counters"),
    ("export", "Export", "AMBER snapshot bundles"),
)

# ------------------------------------------------------------------- scoring

_WORD_SPLIT = re.compile(r"[\s_\-./:#@]+")
_FUZZY_FLOOR = 0.6

# A property is key-ish/name-ish when it is functional (FD-backed key) or its
# name/synonyms contain one of these words between separators.
_KEYISH = re.compile(r"[\s_\-./](name|title|id|number|code|key)s?[\s_\-./]")

#: values longer than this are never indexed (memory cap; keys/names are short)
_MAX_INDEXED_VALUE = 200


def match_score(query: str, text: str) -> float:
    """One query against one text -> a score in the disjoint tier bands above.

    0.0 means no match. Deterministic; casefolded on both sides.
    """
    q = query.casefold().strip()
    t = (text or "").casefold().strip()
    if not q or not t:
        return 0.0
    ratio = min(len(q) / len(t), 1.0)
    if t.startswith(q):
        return 0.75 + 0.25 * ratio  # query == text -> exactly 1.0
    if any(w.startswith(q) for w in _WORD_SPLIT.split(t) if w):
        return 0.55 + 0.15 * ratio
    if q in t:
        return 0.35 + 0.10 * ratio
    fuzzy = difflib.SequenceMatcher(None, q, t).ratio()
    if fuzzy >= _FUZZY_FLOOR:
        return 0.30 * fuzzy
    return 0.0


def best_score(query: str, texts: Iterable[str]) -> float:
    return max((match_score(query, t) for t in texts), default=0.0)


def _is_keyish(prop: "PropertyDef") -> bool:
    if prop.functional:
        return True
    return any(_KEYISH.search(f" {t.casefold()} ") for t in (prop.name, *prop.synonyms))


def _uri_tail(uri: str) -> str:
    return uri.rstrip("/").rsplit("/", 1)[-1] or uri


# -------------------------------------------------------------- entity index


@dataclass(frozen=True, slots=True)
class IndexEntry:
    """One searchable string of one entity (a key-ish value or the uri itself)."""

    display: str      # the raw matched string (value or uri)
    prop: str         # property name, or "uri" for the uri entry
    uri: str          # entity uri (the search ref)
    class_name: str


@dataclass(slots=True)
class WorldIndex:
    """The lazy in-memory value index over HEARTH current cells."""

    entries: list[IndexEntry] = field(default_factory=list)
    labels: dict[str, str] = field(default_factory=dict)     # uri -> display label
    class_of: dict[str, str] = field(default_factory=dict)   # uri -> class name

    def label(self, uri: str) -> str:
        return self.labels.get(uri) or _uri_tail(uri)


def _label_rank(prop: str) -> int:
    """Preference order for the entity's display label."""
    p = prop.casefold()
    if p == "name":
        return 0
    if "name" in p:
        return 1
    if "title" in p:
        return 2
    return 3


def build_index(hearth: "Hearth", ontology: Optional["Ontology"]) -> WorldIndex:
    """Walk HEARTH entity-layer shards once; index only key-ish/name-ish
    cell values (string/int) plus every entity uri.

    'Current' here means CURRENT KNOWLEDGE (system-open cells): per (entity,
    prop) the currently-valid cell when one exists, else the latest system-open
    cell. A tail number whose registration window has closed is still the tail
    number you search the airframe by — valid-bounded facts stay findable."""
    idx = WorldIndex()
    label_best: dict[str, tuple[tuple[int, str], str]] = {}
    for shard in hearth.value_shard_items():
        if shard.layer is not Layer.ENTITY:
            continue
        cls = ontology.get(shard.class_uri) if ontology is not None else None
        class_name = cls.name if cls is not None else _uri_tail(shard.class_uri)
        keyish = {p.name for p in (cls.properties if cls is not None else ()) if _is_keyish(p)}
        for uri in sorted(shard.by_entity):
            idx.class_of.setdefault(uri, class_name)
            idx.entries.append(IndexEntry(display=uri, prop="uri", uri=uri, class_name=class_name))
        for (uri, prop), seqs in sorted(shard.open_by_key.items()):
            if prop not in keyish or not seqs:
                continue
            seq = shard.current.get((uri, prop), max(seqs))
            value = shard.cells[seq].value
            if isinstance(value, bool) or not isinstance(value, (str, int)):
                continue
            display = str(value).strip()
            if not display or len(display) > _MAX_INDEXED_VALUE:
                continue
            idx.entries.append(
                IndexEntry(display=display, prop=prop, uri=uri, class_name=class_name)
            )
            rank = (_label_rank(prop), prop)
            seen = label_best.get(uri)
            if seen is None or rank < seen[0]:
                label_best[uri] = (rank, display)
    idx.labels = {uri: text for uri, (_rank, text) in label_best.items()}
    return idx


# ------------------------------------------------------------ per-kind search


def search_apps(q: str) -> list[dict]:
    out = []
    for app_id, title, subtitle in APP_REGISTRY:
        s = best_score(q, (title, app_id))
        if s > 0.0:
            out.append({"kind": "app", "title": title, "subtitle": subtitle,
                        "ref": app_id, "score": s})
    return out


def search_classes(q: str, ontology: "Ontology") -> list[dict]:
    out = []
    for c in ontology.classes.values():
        s = match_score(q, c.name)
        if s > 0.0:
            subtitle = c.definition or f"{len(c.properties)} properties"
            out.append({"kind": "class", "title": c.name, "subtitle": subtitle,
                        "ref": c.uri, "score": s})
    return out


def search_properties(q: str, ontology: "Ontology") -> list[dict]:
    out = []
    for c in ontology.classes.values():
        for p in c.properties:
            s = best_score(q, (p.name, *p.synonyms))
            if s > 0.0:
                kind = f"link → {_uri_tail(p.range_class)}" if p.is_link and p.range_class \
                    else p.datatype.value
                out.append({
                    "kind": "property",
                    "title": p.name,
                    "subtitle": f"{c.name} · {kind}",
                    "ref": f"{c.uri}#{p.name}",
                    "score": s,
                })
    return out


def search_entities(q: str, index: WorldIndex) -> list[dict]:
    best: dict[str, dict] = {}
    for e in index.entries:
        s = match_score(q, e.display)
        if s <= 0.0:
            continue
        seen = best.get(e.uri)
        if seen is not None and seen["score"] >= s:
            continue
        subtitle = e.class_name if e.prop == "uri" else f"{e.class_name} · {e.prop}: {e.display}"
        best[e.uri] = {"kind": "entity", "title": index.label(e.uri),
                       "subtitle": subtitle, "ref": e.uri, "score": s}
    return list(best.values())


def search_questions(q: str, questions: Sequence[str]) -> list[dict]:
    out, seen = [], set()
    for text in questions:
        if text in seen:
            continue
        seen.add(text)
        s = match_score(q, text)
        if s > 0.0:
            out.append({"kind": "question", "title": text, "subtitle": "asked previously",
                        "ref": text, "score": s})
    return out


def run_search(
    q: str,
    limit: int,
    *,
    ontology: Optional["Ontology"] = None,
    index: Optional[WorldIndex] = None,
    questions: Sequence[str] = (),
) -> list[dict]:
    """Search every kind and interleave purely by score (then deterministic
    tie-breaks: kind, title, ref)."""
    results = search_apps(q)
    if ontology is not None:
        results += search_classes(q, ontology)
        results += search_properties(q, ontology)
    if index is not None:
        results += search_entities(q, index)
    results += search_questions(q, questions)
    results.sort(key=lambda r: (-r["score"], r["kind"], r["title"], r["ref"]))
    return results[: max(0, limit)]
