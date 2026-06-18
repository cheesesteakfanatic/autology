"""GET /api/fields — faceted, criticality-RANKED field enumeration (the scale story).

The Build-mode left pane used to derive measures/dimensions CLIENT-SIDE with a
flat regex chip list. This is the server-side replacement: enumerate every
analytic field across the WHOLE ontology once, server-side, tag each with its
owning class (the "dataset"/type), the class's materialized extent size, and the
class's criticality score, then RANK by a deterministic grounding-score search
(VISTA's lexicon) blended with criticality, and return a paginated top-N plus
facet COUNTS — never a flat dump, so it scales to thousands of datasets.

Reuses, verbatim:
  * VISTA :func:`derive_metric_layer` — every (class × measure × agg) MetricDef
    and every per-class COUNT, with units; the §3.2 measure rule.
  * VISTA :func:`candidate_dims` — every categorical/temporal/link dimension,
    inherited props included, identifier-junk dropped.
  * VISTA grounding (:func:`tokenize`/:func:`ground_metric`/:func:`ground_dim`)
    for the free-text search score.
  * the process-local criticality bridge (:mod:`.usage`) for per-class ranking.

Keyless / offline / deterministic: pure functions of the ontology + accrued
usage. Fully DEFENSIVE — an unbuilt world yields empty fields + empty facets and
never raises.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from ontoforge.contracts import Layer
from ontoforge.vista.compose import ground_dim, ground_metric, tokenize
from ontoforge.vista.metrics import DimRef, MetricDef, candidate_dims, derive_metric_layer

from . import schemas as S
from . import usage as criticality_usage

#: default page size — a ranked window, not the whole estate.
DEFAULT_LIMIT = 60

#: a real lexical match contributes ≥0.7 (substring) per token; VISTA's grounding
#: priors (class/measure/temporal bonuses) top out at 0.15. A field whose score
#: is at or below this floor matched on PRIORS ONLY — not the query — so it is
#: dropped from a text search.
_MATCH_FLOOR = 0.2


def _domain_of(is_event: bool) -> str:
    """The deterministic semantic bucket a class belongs to.

    The induced ontology carries no free 'domain' label, but it DOES carry the
    §3.5 event/entity distinction — the one universal, keyless grouping. Events
    (the streams dashboards monitor) vs Entities (the things they describe)."""
    return "events" if is_event else "entities"


def _extent_counts(world: Any) -> dict[str, int]:
    """class_uri → materialized entity count, read from the LOADED entity shards.

    Uses the shards Hearth already has open (``value_shard_items``) so it never
    materializes a phantom shard. Defensive: any failure yields {}."""
    counts: dict[str, int] = {}
    try:
        hearth = world.hearth
        for shard in hearth.value_shard_items():
            if shard.layer is Layer.ENTITY:
                counts[shard.class_uri] = counts.get(shard.class_uri, 0) + len(shard.by_entity)
    except Exception:
        return {}
    return counts


def _measure_field(
    m: MetricDef,
    *,
    is_event: bool,
    rows: int,
    crit: float,
    search_score: float,
) -> S.FieldOut:
    label = f"{m.agg.value} of {m.measure_prop.name.replace('_', ' ')}" if m.measure_prop else "count"
    return S.FieldOut(
        key=f"measure:{m.class_uri}:{m.measure_name or '*'}:{m.agg.value}",
        kind="measure",
        label=label,
        prop=m.measure_name or "",
        on_class=m.class_uri,
        dataset=m.class_name,
        domain=_domain_of(is_event),
        agg=m.agg.value,
        unit=m.unit,
        rows=rows,
        criticality=round(crit, 6),
        score=round(search_score, 6),
    )


def _dim_field(
    class_uri: str,
    class_name: str,
    dim: DimRef,
    *,
    is_event: bool,
    rows: int,
    crit: float,
    search_score: float,
) -> S.FieldOut:
    return S.FieldOut(
        key=f"dim:{class_uri}:{dim.name}",
        kind="dimension",
        label=dim.name.replace("_", " "),
        prop=dim.name,
        on_class=class_uri,
        dataset=class_name,
        domain=_domain_of(is_event),
        dim_kind=dim.kind,
        link_target=dim.target or None,
        rows=rows,
        criticality=round(crit, 6),
        score=round(search_score, 6),
    )


def _all_fields(world: Any) -> list[S.FieldOut]:
    """Enumerate every measure + dimension across the active ontology.

    One pass: VISTA's metric layer gives the measures (each agg) + per-class
    counts; ``candidate_dims`` gives the dimensions. Each field is tagged with
    its owning class's extent size + criticality. ``score`` starts equal to
    ``criticality`` (the q-empty ranking); search overrides it per request."""
    try:
        onto = world.ontology
    except Exception:
        return []
    if onto is None or not getattr(onto, "classes", None):
        return []

    crit = criticality_usage.scores_by_class(world)
    extents = _extent_counts(world)

    fields: list[S.FieldOut] = []

    # measures (+ counts): straight off the derived metric layer.
    for m in derive_metric_layer(onto):
        cls = onto.get(m.class_uri)
        is_event = bool(getattr(cls, "is_event", False)) if cls else False
        c = float(crit.get(m.class_uri, 0.0))
        fields.append(
            _measure_field(
                m,
                is_event=is_event,
                rows=extents.get(m.class_uri, 0),
                crit=c,
                search_score=c,
            )
        )

    # dimensions: per class, dedup the (class, dim.name) pairs (a count metric
    # and its measures all share the same dim set — emit each dim once).
    for uri in sorted(onto.classes):
        cls = onto.classes[uri]
        is_event = bool(getattr(cls, "is_event", False))
        c = float(crit.get(uri, 0.0))
        rows = extents.get(uri, 0)
        for dim in candidate_dims(cls, onto):
            fields.append(
                _dim_field(
                    uri,
                    cls.name,
                    dim,
                    is_event=is_event,
                    rows=rows,
                    crit=c,
                    search_score=c,
                )
            )
    return fields


def _facet_counts(fields: list[S.FieldOut], attr: str, labeler=None) -> list[S.FacetCount]:
    counter: Counter[str] = Counter()
    for f in fields:
        val = getattr(f, attr, None)
        if val:
            counter[str(val)] += 1
    out = [
        S.FacetCount(value=v, label=(labeler(v) if labeler else v), count=n)
        for v, n in counter.items()
    ]
    # count desc then value asc — deterministic.
    out.sort(key=lambda fc: (-fc.count, fc.value))
    return out


def search_fields(
    world: Any,
    *,
    q: str = "",
    type_: str = "",
    domain: str = "",
    dataset: str = "",
    limit: int = DEFAULT_LIMIT,
) -> S.FieldsOut:
    """The faceted, ranked field search.

    Filters (``type`` = measure|dimension, ``domain``, ``dataset`` = owning class
    name OR uri) narrow the set; ``q`` re-scores it by VISTA grounding × class
    criticality so the most relevant + most important fields surface first.
    Facets COUNT across the whole filtered match set; ``fields`` is the ranked
    top-``limit`` window. Empty on an unbuilt world."""
    fields = _all_fields(world)

    # facet filters (applied before search so facet counts reflect the active
    # filters the user already chose — the standard faceted-search contract).
    if type_:
        t = type_.strip().lower()
        fields = [f for f in fields if f.kind == t]
    if domain:
        d = domain.strip().lower()
        fields = [f for f in fields if f.domain.lower() == d]
    if dataset:
        ds = dataset.strip().lower()
        fields = [f for f in fields if f.dataset.lower() == ds or f.on_class.lower() == ds]

    # search ranking: grounding score (lexical+fuzzy over VISTA's vocab) folded
    # with the class criticality so important fields win ties; q-empty leaves the
    # pure-criticality ranking. Ground each field against the query tokens using
    # the SAME ground_metric/ground_dim VISTA uses, keyed by field.key.
    tokens = tokenize(q) if q.strip() else []
    if tokens:
        ground = _ground_against_query(world, tokens)
        kept: list[S.FieldOut] = []
        for f in fields:
            g = ground.get(f.key, 0.0)
            if g <= _MATCH_FLOOR:
                continue
            # relevance dominates; criticality is the tie-breaker / prior.
            f.score = round(g + 0.15 * f.criticality, 6)
            kept.append(f)
        fields = kept
    # rank: score desc, then criticality desc, then dataset/label asc (stable).
    fields.sort(key=lambda f: (-f.score, -f.criticality, f.dataset, f.label, f.key))

    facets = S.FieldFacets(
        kind=_facet_counts(fields, "kind"),
        domain=_facet_counts(fields, "domain"),
        dataset=_facet_counts(fields, "dataset"),
        unit=_facet_counts(fields, "unit"),
        dim_kind=_facet_counts(fields, "dim_kind"),
    )
    total = len(fields)
    lim = max(1, int(limit)) if limit else DEFAULT_LIMIT
    page = fields[:lim]
    return S.FieldsOut(fields=page, facets=facets, total=total, returned=len(page))


def _ground_against_query(world: Any, tokens: list[str]) -> dict[str, float]:
    """field.key → VISTA grounding score for the query ``tokens``.

    Re-derives the metric layer + dims (deterministic, cheap) and scores each
    measure with :func:`ground_metric` and each dimension with :func:`ground_dim`
    — the exact lexicon LODESTONE/VISTA ground against, so the panel search
    agrees with the rest of the analytic surface. Returns {} on failure (the
    no-results path), so the search degrades to an empty list, never a 500."""
    out: dict[str, float] = {}
    try:
        onto = world.ontology
    except Exception:
        return out
    if onto is None or not getattr(onto, "classes", None):
        return out
    try:
        for m in derive_metric_layer(onto):
            key = f"measure:{m.class_uri}:{m.measure_name or '*'}:{m.agg.value}"
            out[key] = ground_metric(tokens, m)
        for uri in sorted(onto.classes):
            cls = onto.classes[uri]
            for dim in candidate_dims(cls, onto):
                out[f"dim:{uri}:{dim.name}"] = ground_dim(tokens, dim)
    except Exception:
        return {}
    return out
