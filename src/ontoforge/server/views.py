"""POST /api/view — natural-language (or shelf-driven) single chart, executed + cited.

The Tableau-shelf sibling of ``/api/dashboards``: instead of proposing a whole
multi-chart dashboard from a vague utterance, ``/api/view`` parses ONE explicit
view request — *measure, break down by, filter, chart type* — executes it over
the materialized world, and returns the rows, a ready-to-render Vega-Lite spec,
per-cell source citations, and a plain-English restatement.

The NL → ViewSpec parser is a thin, DETERMINISTIC cue+slot grounder that reuses
the SAME surfaces the rest of the analytic stack grounds against — it does not
re-derive any vocabulary:

  * measure + breakdown ground onto VISTA's derived metric layer
    (``ground_metric`` / ``ground_dim`` over ``derive_metric_layer`` /
    ``candidate_dims``);
  * the aggregate verb is read from LODESTONE's ``AGG_CUES`` table;
  * filters are read from LODESTONE's ``CMP_CUES`` table + a trailing number.

The OQIR term is then assembled exactly as ``vista.compose`` assembles its KPI /
breakdown terms (``Aggregate`` + ``Select`` + optional ``TopK``), the Vega-Lite
spec comes from ``vista.vega.chart_spec`` verbatim, and execution + citations run
through the world's existing executor + extract seams.

CONFIDENTLY-WRONG DISCIPLINE: when the measure does not ground (or two measures
tie), the parser returns a CLARIFICATION — one question + concrete options —
instead of guessing. Keyless / offline / deterministic; never raises into the
handler (any failure degrades to an honest abstention).
"""

from __future__ import annotations

import re
from typing import Any, Optional

from ontoforge.contracts.oqir import (
    Agg,
    Aggregate,
    Condition,
    CmpOp,
    OQIRTerm,
    Select,
    TopK,
)
from ontoforge.lodestone.grounding import AGG_CUES, CMP_CUES
from ontoforge.vista.compose import ground_dim, ground_metric, tokenize
from ontoforge.vista.metrics import DimRef, MetricDef, candidate_dims, derive_metric_layer
from ontoforge.vista.vega import chart_spec, value_field

from . import schemas as S

#: a measure is "clearly best" only if it beats the runner-up by this margin;
#: a tighter race is ambiguous → clarify rather than guess (confidently-wrong).
_TIE_MARGIN = 0.5
#: a measure must ground at least this well to be answerable at all.
_MIN_MEASURE_SCORE = 0.7
#: bonus added to a metric whose own aggregate matches an explicit agg verb in
#: the utterance — strong enough (≥ one lexical hit) to lift a count metric the
#: verb clearly named above measures that merely share a token.
_AGG_CUE_BONUS = 1.0
_NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")
_CMP_OP_TO_STR = {
    CmpOp.EQ: "==", CmpOp.NE: "!=", CmpOp.LT: "<",
    CmpOp.LE: "<=", CmpOp.GT: ">", CmpOp.GE: ">=", CmpOp.CONTAINS: "contains",
}


# ---------------------------------------------------------------- agg / filters


def _detect_agg(text_low: str) -> Optional[Agg]:
    """First AGG_CUES phrase present (cues are ordered longest-first)."""
    for cue, agg in AGG_CUES:
        if all(w in text_low for w in cue):
            try:
                return Agg(agg)
            except ValueError:
                continue
    return None


def _detect_filters(text: str, metric: MetricDef, onto: Any) -> list[S.ViewFilter]:
    """Numeric comparison filters: a CMP_CUES phrase + a trailing number, bound to
    the measure property (the only numeric prop a view-bar filter targets in v0).
    Deterministic and conservative — no filter unless BOTH a comparison cue and a
    number are present and the metric has a measure property to bind to."""
    if metric.measure_prop is None:
        return []
    low = text.lower()
    nums = _NUM_RE.findall(low)
    if not nums:
        return []
    for cue, op in CMP_CUES:
        if all(w in low for w in cue):
            try:
                value: Any = float(nums[-1])
                if value.is_integer():
                    value = int(value)
            except ValueError:
                continue
            return [S.ViewFilter(prop=metric.measure_prop.name, op=_CMP_OP_TO_STR[op], value=value)]
    return []


# ------------------------------------------------------------------- grounding


_CAMEL_RE = re.compile(r"[A-Z][a-z0-9]+|[a-z0-9]+")


def _class_word_bonus(tokens: list[str], class_name: str) -> float:
    """A small grounding lift when the utterance names the metric's CLASS.

    VISTA's ``_vocab`` lowercases a camel-cased class name into a SINGLE token
    ('AccidentEvent' → 'accidentevent'), so 'accidents' never matches it. We
    split the class name on camelCase boundaries here ('accident', 'event') and
    award a bonus for a substring/exact token hit — purely additive on top of
    ``ground_metric``, so it only ever breaks a count-class tie toward the class
    the user actually named. Never touches VISTA's deterministic lexicon."""
    parts = [w.lower() for w in _CAMEL_RE.findall(class_name)]
    if not parts:
        return 0.0
    hit = 0.0
    for t in tokens:
        for w in parts:
            if t == w or (len(t) >= 4 and (t in w or w in t)):
                hit = max(hit, 0.6)
    return hit


def _rank_measures(tokens: list[str], layer: list[MetricDef]) -> list[tuple[float, MetricDef]]:
    ranked = sorted(
        ((ground_metric(tokens, m) + _class_word_bonus(tokens, m.class_name), m) for m in layer),
        key=lambda t: (-t[0], t[1].name),
    )
    return ranked


#: explicit "break it down" cue words — a breakdown is only inferred when the
#: user ASKED for one (a Tableau 'Break down by' shelf intent), so a bare measure
#: query stays a single KPI instead of inventing a grouping.
_BREAKDOWN_CUES = frozenset({"by", "per", "across", "breakdown", "grouped", "group", "split"})


def _best_dim(text_low: str, tokens: list[str], metric: MetricDef) -> Optional[DimRef]:
    """The breakdown dimension the utterance grounds to on the chosen metric's
    class — only when the user actually asked to break the measure down.

    Requires BOTH an explicit breakdown cue ('by'/'per'/'across'/…) AND a real
    lexical hit on a candidate dim (score > 0.5, above ground_dim's priors), so
    'average altitude' stays a KPI but 'fatalities by month' grounds a bar/line.
    The cue gate is checked on the RAW text (VISTA's tokenizer drops 'by' as a
    stopword) and is what keeps a measure-only query from sprouting a surprise
    breakdown off an incidental token match."""
    if not metric.dims:
        return None
    raw_words = set(re.findall(r"[a-z]+", text_low))
    if not (raw_words & _BREAKDOWN_CUES):
        return None
    scored = sorted(
        ((ground_dim(tokens, d), d) for d in metric.dims),
        key=lambda t: (-t[0], t[1].name),
    )
    best_score, best = scored[0]
    return best if best_score > 0.5 else None


# ---------------------------------------------------------------- spec assembly


def _agg_for(metric: MetricDef, override: Optional[Agg]) -> Agg:
    """Honor an explicit aggregate verb when it is valid for the metric: COUNT
    needs no measure; SUM/AVG/MIN/MAX need a measure property. An override that
    is invalid for the metric is ignored (falls back to the metric's own agg)."""
    if override is None:
        return metric.agg
    if override is Agg.COUNT:
        return Agg.COUNT
    if metric.measure_prop is not None:
        return override
    return metric.agg


def _metric_with_agg(metric: MetricDef, agg: Agg) -> MetricDef:
    if agg is metric.agg:
        return metric
    # rebuild a MetricDef so value_field / chart_spec reflect the chosen agg.
    measure_prop = None if agg is Agg.COUNT else metric.measure_prop
    return MetricDef(
        name=f"{agg.value}_{metric.measure_name or metric.class_name.lower()}",
        class_uri=metric.class_uri,
        class_name=metric.class_name,
        agg=agg,
        measure_prop=measure_prop,
        dims=metric.dims,
        unit=metric.unit if measure_prop is not None else None,
    )


def _conditions(filters: list[S.ViewFilter]) -> tuple[Condition, ...]:
    str_to_op = {v: k for k, v in _CMP_OP_TO_STR.items()}
    out: list[Condition] = []
    for f in filters:
        op = str_to_op.get(f.op, CmpOp.EQ)
        out.append(Condition(prop=f.prop, op=op, value=f.value))
    return tuple(out)


def _build_term(metric: MetricDef, dim: Optional[DimRef], conds: tuple[Condition, ...]) -> OQIRTerm:
    """Assemble the OQIR exactly as vista.compose does (KPI vs breakdown), with
    any filters folded into the Select."""
    select = Select(class_uri=metric.class_uri, conditions=conds)
    agg = Aggregate(
        source=select,
        agg=metric.agg,
        measure_prop=metric.measure_name,
        group_by=(dim.name,) if dim is not None else (),
    )
    if dim is None:
        return agg
    if dim.kind == "temporal":
        return agg
    return TopK(source=agg, by=value_field(metric), k=10, descending=True)


def _viz(dim: Optional[DimRef]) -> str:
    if dim is None:
        return "kpi"
    return "line" if dim.kind == "temporal" else "bar"


def _plain_english(metric: MetricDef, dim: Optional[DimRef], filters: list[S.ViewFilter]) -> str:
    if metric.measure_prop is None:
        head = f"Count of {metric.class_name}"
    else:
        head = f"{metric.agg.value.capitalize()} of {metric.measure_prop.name.replace('_', ' ')}"
        if metric.unit:
            head += f" ({metric.unit})"
        head += f" across {metric.class_name}"
    if dim is not None:
        head += f", broken down by {dim.name.replace('_', ' ')}"
    if filters:
        clauses = ", ".join(f"{f.prop.replace('_', ' ')} {f.op} {f.value}" for f in filters)
        head += f", where {clauses}"
    return head + "."


def _spec_model(metric: MetricDef, dim: Optional[DimRef], filters: list[S.ViewFilter]) -> S.ViewSpec:
    return S.ViewSpec(
        class_uri=metric.class_uri,
        class_name=metric.class_name,
        measure=S.ViewMeasure(
            prop=metric.measure_name,
            agg=metric.agg.value,
            unit=metric.unit,
        ),
        breakdowns=[S.ViewBreakdown(prop=dim.name, kind=dim.kind)] if dim is not None else [],
        filters=list(filters),
        viz=_viz(dim),  # type: ignore[arg-type]
    )


# --------------------------------------------------------------------- parsing


def parse_view(text: str, onto: Any) -> dict[str, Any]:
    """NL → resolved (metric, dim, filters) or a clarification.

    Returns one of:
      * ``{"metric": MetricDef, "dim": DimRef|None, "filters": [ViewFilter]}``
      * ``{"clarification": str, "options": [str]}``
      * ``{"abstain": str}``  (nothing to ground onto)
    Deterministic; pure over the ontology + the cue tables."""
    layer = derive_metric_layer(onto)
    if not layer:
        return {"abstain": "no analytic fields in the current world yet"}

    tokens = tokenize(text)
    if not tokens:
        return {"clarification": "What would you like to measure?",
                "options": _measure_options(layer)}

    # the explicit aggregate verb ('how many'→count, 'average'→avg, …) biases the
    # ranking: a metric whose own agg matches the cue is preferred, so 'count of
    # occurrences' routes to a COUNT metric rather than an AVG one that merely
    # shares a token. This is the deterministic disambiguator LODESTONE uses too.
    agg_override = _detect_agg(text.lower())
    ranked = _rank_measures(tokens, layer)
    if agg_override is not None:
        biased = sorted(
            (
                (s + (_AGG_CUE_BONUS if m.agg is agg_override else 0.0), m)
                for s, m in ranked
            ),
            key=lambda t: (-t[0], t[1].name),
        )
        ranked = biased
    best_score, best = ranked[0]

    # answerability floor: a real lexical hit (or an explicit agg cue on a count
    # metric) must be present — otherwise we cannot tell which measure was meant.
    grounded = best_score >= _MIN_MEASURE_SCORE
    if not grounded:
        return {"clarification": "I could not match that to a measure. Which one did you mean?",
                "options": _measure_options(layer)}

    # ambiguity: a near-tie between two DIFFERENT measure surfaces → clarify
    # (never a confident guess). The agg-cue bias above already broke deliberate
    # 'count of …' vs 'average …' races, so a surviving tie is a genuine one.
    runner = next(
        ((s, m) for s, m in ranked[1:]
         if (m.class_uri, m.measure_name) != (best.class_uri, best.measure_name)),
        None,
    )
    if runner is not None and (best_score - runner[0]) < _TIE_MARGIN:
        opts = _dedup_keep_order([_measure_label(best), _measure_label(runner[1])])
        return {"clarification": "Which measure did you mean?", "options": opts}

    agg = _agg_for(best, agg_override)
    metric = _metric_with_agg(best, agg)
    dim = _best_dim(text.lower(), tokens, metric)
    filters = _detect_filters(text, metric, onto)
    return {"metric": metric, "dim": dim, "filters": filters}


def _measure_label(metric: MetricDef) -> str:
    if metric.measure_prop is None:
        return f"Count of {metric.class_name}"
    return f"{metric.agg.value.capitalize()} of {metric.measure_prop.name.replace('_', ' ')} ({metric.class_name})"


def _measure_options(layer: list[MetricDef], k: int = 6) -> list[str]:
    """A short, deterministic menu of the most distinct measures to offer."""
    out: list[str] = []
    for m in layer:
        label = _measure_label(m)
        if label not in out:
            out.append(label)
        if len(out) >= k:
            break
    return out


def _dedup_keep_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for it in items:
        if it not in seen:
            seen.add(it)
            out.append(it)
    return out


# ---------------------------------------------------------------- spec → ViewIn


def _metric_from_spec(spec: S.ViewSpec, onto: Any) -> Optional[MetricDef]:
    """Rebuild a MetricDef from an explicit (shelf-driven) ViewSpec, validated
    against the ontology so a hand-crafted spec cannot reference a phantom
    class/prop. Returns None when the class/measure does not exist."""
    cls = onto.get(spec.class_uri) if spec.class_uri else None
    if cls is None:
        return None
    try:
        agg = Agg(spec.measure.agg)
    except ValueError:
        agg = Agg.COUNT
    measure_prop = None
    unit = None
    if spec.measure.prop and agg is not Agg.COUNT:
        from ontoforge.lodestone.model import all_props

        props = all_props(onto, spec.class_uri)
        measure_prop = props.get(spec.measure.prop)
        if measure_prop is None:
            return None
        unit = measure_prop.unit
    return MetricDef(
        name=f"{agg.value}_{spec.measure.prop or cls.name.lower()}",
        class_uri=spec.class_uri,
        class_name=cls.name,
        agg=agg,
        measure_prop=measure_prop,
        dims=candidate_dims(cls, onto),
        unit=unit,
    )


def _dim_from_spec(spec: S.ViewSpec, metric: MetricDef) -> Optional[DimRef]:
    if not spec.breakdowns:
        return None
    want = spec.breakdowns[0].prop
    for d in metric.dims:
        if d.name == want:
            return d
    return None


# ------------------------------------------------------------------- the entry


def run_view(world: Any, body: S.ViewIn) -> S.ViewOut:
    """Resolve → execute → cite a single view. DEFENSIVE: any internal failure
    becomes an honest abstention, never a 500."""
    try:
        onto = world.ontology
    except Exception:
        onto = None
    if onto is None or not getattr(onto, "classes", None):
        return S.ViewOut(abstained=True, abstain_reason="no materialized world yet")

    # 1) resolve the (metric, dim, filters) — from an explicit spec or from text.
    metric: Optional[MetricDef]
    dim: Optional[DimRef]
    filters: list[S.ViewFilter]
    if body.spec is not None and body.spec.class_uri:
        metric = _metric_from_spec(body.spec, onto)
        if metric is None:
            return S.ViewOut(abstained=True, abstain_reason="the requested measure is not in this world")
        dim = _dim_from_spec(body.spec, metric)
        filters = list(body.spec.filters)
        # text may still supply filters/agg the spec omitted
        if body.text:
            if not filters:
                filters = _detect_filters(body.text, metric, onto)
            ov = _detect_agg(body.text.lower())
            if ov is not None:
                metric = _metric_with_agg(metric, _agg_for(metric, ov))
    else:
        parsed = parse_view(body.text or "", onto)
        if "clarification" in parsed:
            return S.ViewOut(clarification=parsed["clarification"], options=list(parsed["options"]))
        if "abstain" in parsed:
            return S.ViewOut(abstained=True, abstain_reason=parsed["abstain"])
        metric, dim, filters = parsed["metric"], parsed["dim"], parsed["filters"]

    # 2) build the OQIR term + Vega-Lite spec exactly as VISTA does.
    conds = _conditions(filters)
    term = _build_term(metric, dim, conds)
    title = _plain_english(metric, dim, filters)
    vega = chart_spec(title, metric, dim)

    # 3) execute over the materialized world (the same executor /api/dashboards
    #    uses) and fill the chart's data.
    rows_dicts: list[dict[str, Any]] = []
    try:
        executor = world.oqir_executor()
        rows_dicts = executor(term)
    except Exception:
        rows_dicts = []
    vega = {**vega, "data": {"values": [dict(r) for r in rows_dicts]}}

    # tabular form for the result grid: the dim column (if any) + the value col.
    val_col = value_field(metric)
    if dim is not None:
        columns = [dim.name, val_col]
        rows = [[r.get(dim.name), r.get(val_col)] for r in rows_dicts]
    else:
        columns = [val_col]
        rows = [[r.get(val_col)] for r in rows_dicts]

    # 4) cite SOURCE rows: read the underlying class extent (same filters) via the
    #    extract seam — value-level, per-cell atom evidence the chart rests on.
    citations = _citations(world, metric, dim, filters)

    spec = _spec_model(metric, dim, filters)
    return S.ViewOut(
        spec=spec,
        vega=vega,
        columns=columns,
        rows=rows,
        citations=citations,
        plain_english=title,
    )


def _citations(
    world: Any,
    metric: MetricDef,
    dim: Optional[DimRef],
    filters: list[S.ViewFilter],
) -> list[S.ExtractCitation]:
    """Per-cell source citations: the underlying class rows (with the same
    filters) for the measure + breakdown columns. Reuses ``world.extract`` so the
    evidence is the SAME value-level atom trail ``/api/extract`` and ``/api/lineage``
    expose. Capped + defensive — citations are evidence, never the payload."""
    cols: list[str] = []
    if metric.measure_prop is not None:
        cols.append(metric.measure_prop.name)
    if dim is not None and dim.kind != "link":
        cols.append(dim.name)
    if not cols:
        # a pure COUNT with a link breakdown has no literal cell to cite; skip.
        return []
    try:
        ex_filters = [{"prop": f.prop, "op": f.op, "value": f.value} for f in filters]
        payload = world.extract(metric.class_uri, ex_filters, cols, 200)
    except Exception:
        return []
    out: list[S.ExtractCitation] = []
    for c in payload.get("citations", []):
        if c.get("atom_ids"):
            out.append(S.ExtractCitation(**c))
    return out
