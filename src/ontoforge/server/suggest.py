"""Grounded typeahead over one active world (the Ask command bar's seam).

The frozen contract (the Ask surface builds against it):

    GET /api/suggest?q=<partial>&limit=24
    -> {"measures":   [{label, measure, agg, unit, on_class, on_class_uri,
                        dataset, rows, criticality, question}],
        "entities":   [{cls, label, on_class_uri, dataset, records, fields,
                        criticality, question}],
        "questions":  [{text, kind}]}

Every ingredient is already in the active world; this module only COMPOSES
them — zero network, no key, deterministic over integer usage/ledger seqs:

* **measures / dimensions** ← the induced ontology's numeric / scalar properties
  (``world.ontology``). A measure carries a ``unit`` or a ``dimension`` (unit
  exponents) or matches the measure-word heuristic; everything else scalar and
  non-link is a group-by dimension. Each suggestion attaches its owning class's
  CRITICALITY score (the graph nodes are classes, so a measure inherits its
  class's score) and a runnable NL ``question`` to feed straight to ``/api/ask``.
* **entities** ← the ontology classes themselves, ranked by criticality.
* **questions** ← ``world.recent_questions()`` (ledger artifacts), filtered by q.

Ranking uses the same keyless deterministic ``match_score`` the federated
search uses, so a partial like ``frei`` surfaces ``freight_cost`` first. The
endpoint is DEFENSIVE end to end: an empty query or an unbuilt world yields
empty groups, never a 500 (mirrors ``/api/criticality``'s empty-safe contract).
"""

from __future__ import annotations

import re
from typing import Any

from .search import best_score, match_score

#: A property reads as a MEASURE (an aggregatable number) when it carries a
#: unit / a dimension, or its name matches one of these measure words. Mirrors
#: the heuristic the Ask surface already used so client + server agree.
_MEASURE_WORD = re.compile(
    r"cost|amount|price|delay|count|total|qty|quantity|spend|revenue|sales|"
    r"value|weight|volume|duration|score|rate|fee|tax|margin|profit|balance",
    re.IGNORECASE,
)
#: numeric scalar datatypes that can be summed / averaged
_NUMERIC = {"integer", "int", "float", "decimal", "number", "numeric", "double"}


def _uri_tail(uri: str) -> str:
    return uri.rstrip("/").rsplit("/", 1)[-1] or uri


def _humanize(name: str) -> str:
    return name.replace("_", " ").strip()


def _is_measure(prop: Any) -> bool:
    if getattr(prop, "is_link", False):
        return False
    if getattr(prop, "unit", None):
        return True
    if getattr(prop, "dimension", None) is not None:
        return True
    dt = str(getattr(getattr(prop, "datatype", None), "value", "") or "").lower()
    if dt in _NUMERIC and _MEASURE_WORD.search(prop.name or ""):
        return True
    return bool(_MEASURE_WORD.search(prop.name or ""))


def _agg_for(prop: Any) -> str:
    """How a measure most naturally rolls up — sum for additive money/counts,
    average for rates/scores. Deterministic and cosmetic (the engine decides)."""
    name = (getattr(prop, "name", "") or "").lower()
    if re.search(r"rate|score|margin|ratio|avg|average|percent|pct", name):
        return "avg"
    return "sum"


def _extent_counts(world: Any) -> dict[str, int]:
    """Cheap per-class entity-extent counts from HEARTH's already-built index
    (the ENTITY-layer ``by_entity`` map). Fully optional: any failure (no
    materialized HEARTH yet) yields ``{}`` and rows simply go unshown."""
    counts: dict[str, int] = {}
    try:
        from ontoforge.contracts import Layer

        hearth = world.hearth
        for shard in hearth.value_shard_items():
            if shard.layer is not Layer.ENTITY:
                continue
            counts[shard.class_uri] = counts.get(shard.class_uri, 0) + len(shard.by_entity)
    except Exception:
        return {}
    return counts


def _crit_map(world: Any, n: int) -> dict[str, float]:
    """{class_uri: criticality 0..1} for the top-n classes (empty-safe)."""
    try:
        from . import usage as _usage

        return {e["uri"]: float(e["score"]) for e in _usage.top_criticality(world, n)}
    except Exception:
        return {}


def build_suggestions(world: Any, q: str, limit: int) -> dict[str, list[dict[str, Any]]]:
    """Compose the grounded typeahead groups for the active world.

    Defensive: returns ``{"measures": [], "entities": [], "questions": []}``
    for an empty query or an unbuilt world, and never raises."""
    empty: dict[str, list[dict[str, Any]]] = {"measures": [], "entities": [], "questions": []}
    query = (q or "").strip()
    if not query:
        return empty

    try:
        onto = world.ontology
    except Exception:
        onto = None

    measures: list[dict[str, Any]] = []
    entities: list[dict[str, Any]] = []

    if onto is not None:
        try:
            crit = _crit_map(world, max(64, limit * 4))
            extent = _extent_counts(world)
            for uri, cls in onto.classes.items():
                cname = getattr(cls, "name", None) or _uri_tail(uri)
                score = float(crit.get(uri, 0.0))
                rows = extent.get(uri)
                dataset = _uri_tail(uri)
                # one ENTITY suggestion per class, matched by class name
                es = match_score(query, cname)
                if es > 0.0:
                    entities.append({
                        "cls": cname,
                        "label": cname,
                        "on_class_uri": uri,
                        "dataset": dataset,
                        "records": rows,
                        "fields": len(cls.properties),
                        "criticality": score,
                        "question": f"How many {cname} are there?",
                        "_score": es,
                    })
                # MEASURE / DIMENSION suggestions matched by property name
                for p in cls.properties:
                    ps = best_score(query, (p.name, *getattr(p, "synonyms", ())))
                    if ps <= 0.0:
                        continue
                    measure = _is_measure(p)
                    if measure:
                        agg = _agg_for(p)
                        verb = "average" if agg == "avg" else "total"
                        question = f"What was the {verb} {_humanize(p.name)} for {cname}?"
                    elif not getattr(p, "is_link", False):
                        agg = "group"
                        question = f"How many {cname} by {_humanize(p.name)}?"
                    else:
                        # a link property is a join path, not an askable measure
                        continue
                    measures.append({
                        "label": p.name,
                        "measure": p.name,
                        "agg": agg,
                        "unit": getattr(p, "unit", None),
                        "on_class": cname,
                        "on_class_uri": uri,
                        "dataset": dataset,
                        "rows": rows,
                        "criticality": score,
                        "question": question,
                        "_measure": measure,
                        "_score": ps,
                    })
        except Exception:
            measures, entities = [], []

    # QUESTIONS — recent/saved asks the ledger persisted, matched by q.
    questions: list[dict[str, Any]] = []
    try:
        recents = world.recent_questions()
    except Exception:
        recents = []
    seen: set[str] = set()
    for text in recents:
        if text in seen:
            continue
        seen.add(text)
        s = match_score(query, text)
        if s > 0.0:
            questions.append({"text": text, "kind": "asked previously", "_score": s})

    # rank each group: criticality first (measures/entities), then match score;
    # then a deterministic label tiebreak. Drop the private keys before return.
    def _finish(rows: list[dict[str, Any]], by_crit: bool, n: int) -> list[dict[str, Any]]:
        if by_crit:
            rows.sort(key=lambda r: (-r["criticality"], -r["_score"], r["label"]))
        else:
            rows.sort(key=lambda r: (-r["_score"], r["text"]))
        out = rows[: max(0, n)]
        for r in out:
            r.pop("_score", None)
            r.pop("_measure", None)
        return out

    return {
        "measures": _finish(measures, True, limit),
        "entities": _finish(entities, True, max(4, limit // 3)),
        "questions": _finish(questions, False, max(4, limit // 3)),
    }
