"""M13 VISTA — composition search over the derived metric layer (§6.3, AMD-0007).

Vagueness as a *ranking* problem: ``propose(utterance, ontology)`` grounds the
utterance tokens onto M^(t) (lexical + fuzzy, the same surface LODESTONE
grounds against), enumerates small dashboards (one primary KPI + 2–4
complementary breakdowns) under composition constraints — no redundant grain,
dimension diversity — and scores each candidate by

    score = grounding weight + diversity bonus − simplicity penalty.

Usage priors and WARDEN health priors from the full §6.3 algorithm are out of
scope per AMD-0007 (no query history exists in v0). Everything is
deterministic: ties break on names.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from typing import Any, Optional

from ontoforge.contracts import Ontology
from ontoforge.contracts.oqir import Aggregate, OQIRTerm, Select, TopK

from .metrics import DimRef, MetricDef, derive_metric_layer
from .vega import chart_spec, value_field

_STOPWORDS = frozenset(
    "a an and by for from give in me my of on or our over per please show some "
    "the to us with dashboard dashboards chart charts report view".split()
)
_TOKEN_RE = re.compile(r"[a-z0-9]+")

#: composition constraints (§6.3 step 2)
MIN_BREAKDOWNS = 2
MAX_BREAKDOWNS = 4
TOP_K_DEFAULT = 10


@dataclass(slots=True)
class Chart:
    """One dashboard tile: a title, the OQIR analytical term, its Vega-Lite spec."""

    title: str
    oqir: OQIRTerm
    vega: dict[str, Any]
    grain: tuple[str, ...] = ()        # (class_uri, measure|count, *group_by) — redundancy key


@dataclass(slots=True)
class Dashboard:
    title: str
    score: float
    charts: list[Chart] = field(default_factory=list)
    rationale: str = ""


# ----------------------------------------------------------- grounding


def tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN_RE.findall(text.lower()) if t not in _STOPWORDS]


def _word_sim(a: str, b: str) -> float:
    """Lexical+fuzzy word match: exact > affix/substring > difflib ratio."""
    if a == b:
        return 1.0
    if len(a) >= 3 and (a in b or b in a):
        return 0.7
    r = difflib.SequenceMatcher(None, a, b).ratio()
    return r if r >= 0.75 else 0.0


def _vocab(metric: MetricDef) -> list[str]:
    words = set(tokenize(metric.name.replace("_", " ")))
    words.update(tokenize(metric.class_name))
    p = metric.measure_prop
    if p is not None:
        words.update(tokenize(p.name.replace("_", " ")))
        for s in p.synonyms:
            words.update(tokenize(s.replace("_", " ")))
    return sorted(words)


def ground_metric(tokens: list[str], metric: MetricDef) -> float:
    """Grounding weight of one metric against the utterance tokens."""
    vocab = _vocab(metric)
    score = sum(max((_word_sim(t, w) for w in vocab), default=0.0) for t in tokens)
    # priors: dashboards monitor event streams; physical measures beat raw counts.
    cls_bonus = 0.10 if metric.dims else 0.0
    measure_bonus = 0.05 if metric.measure_prop is not None else 0.0
    return score + cls_bonus + measure_bonus


def ground_dim(tokens: list[str], dim: DimRef) -> float:
    words = sorted(set(tokenize(dim.name.replace("_", " ")) + tokenize(dim.target.replace("_", " "))))
    score = sum(max((_word_sim(t, w) for w in words), default=0.0) for t in tokens)
    return score + (0.05 if dim.kind == "temporal" else 0.0)  # trends are dashboard-default


# ----------------------------------------------------------- term building


def _kpi_term(metric: MetricDef) -> OQIRTerm:
    return Aggregate(
        source=Select(class_uri=metric.class_uri),
        agg=metric.agg,
        measure_prop=metric.measure_name,
    )


def _breakdown_term(metric: MetricDef, dim: DimRef) -> OQIRTerm:
    agg = Aggregate(
        source=Select(class_uri=metric.class_uri),
        agg=metric.agg,
        measure_prop=metric.measure_name,
        group_by=(dim.name,),
    )
    if dim.kind == "temporal":
        return agg                      # full series, no TopK truncation
    return TopK(source=agg, by=value_field(metric), k=TOP_K_DEFAULT, descending=True)


def _grain(metric: MetricDef, dim: Optional[DimRef]) -> tuple[str, ...]:
    return (
        metric.class_uri,
        metric.measure_name or "count",
        *(() if dim is None else (dim.name,)),
    )


def _measure_label(metric: MetricDef) -> str:
    if metric.measure_prop is None:
        return metric.agg.value  # "count"
    return f"{metric.agg.value} {metric.measure_prop.name.replace('_', ' ')}"


def _chart(metric: MetricDef, dim: Optional[DimRef]) -> Chart:
    label = _measure_label(metric).upper()
    if dim is None:
        title = f"{label} — {metric.class_name}"
    else:
        title = f"{label} by {dim.name.replace('_', ' ')} — {metric.class_name}"
    return Chart(
        title=title,
        oqir=_breakdown_term(metric, dim) if dim is not None else _kpi_term(metric),
        vega=chart_spec(title, metric, dim),
        grain=_grain(metric, dim),
    )


# ----------------------------------------------------------- composition


def _candidate_breakdowns(
    primary: MetricDef,
    ranked_metrics: list[tuple[float, MetricDef]],
    tokens: list[str],
) -> list[tuple[float, MetricDef, DimRef]]:
    """Breakdown pool: primary metric over each of its dims, plus the best dim
    of other grounded metrics (complementary context tiles)."""
    pool: list[tuple[float, MetricDef, DimRef]] = []
    for dim in primary.dims:
        pool.append((1.0 + ground_dim(tokens, dim), primary, dim))
    for w, m in ranked_metrics:
        if m.class_uri == primary.class_uri and m.measure_name == primary.measure_name:
            continue
        for dim in m.dims:
            pool.append((0.5 * w + ground_dim(tokens, dim), m, dim))
    pool.sort(key=lambda t: (-t[0], t[1].name, t[2].name))
    return pool


def _compose(primary_w: float, primary: MetricDef, pool: list[tuple[float, MetricDef, DimRef]]) -> Dashboard:
    """One dashboard: KPI + up to MAX_BREAKDOWNS tiles, constrained to unique
    grain and pairwise-distinct dimensions (dimension diversity)."""
    charts = [_chart(primary, None)]
    grains = {charts[0].grain}
    dims_used: set[str] = set()
    bd_score = 0.0
    n_dims = 0
    for w, m, dim in pool:
        if len(charts) - 1 >= MAX_BREAKDOWNS:
            break
        c = _chart(m, dim)
        if c.grain in grains or dim.name in dims_used:
            continue
        grains.add(c.grain)
        dims_used.add(dim.name)
        charts.append(c)
        bd_score += w
        n_dims += 1

    diversity = 0.2 * len({d for d in dims_used})
    simplicity = 0.05 * len(charts)
    score = round(2.0 * primary_w + bd_score + diversity - simplicity, 6)
    title = f"{primary.class_name}: {_measure_label(primary)} overview"
    rationale = (
        f"primary metric {primary.name!r} (grounding {primary_w:.2f}); "
        f"{n_dims} breakdowns over dims {sorted(dims_used)}"
    )
    return Dashboard(title=title, score=score, charts=charts, rationale=rationale)


def propose(utterance: str, ontology: Ontology, k: int = 3) -> list[Dashboard]:
    """Top-k ranked dashboard candidates for a (possibly vague) utterance."""
    tokens = tokenize(utterance)
    layer = derive_metric_layer(ontology)
    ranked = sorted(
        ((ground_metric(tokens, m), m) for m in layer),
        key=lambda t: (-t[0], t[1].name),
    )
    # primaries: best-grounded metrics with at least MIN_BREAKDOWNS dims available
    primaries = [(w, m) for w, m in ranked if len(m.dims) >= MIN_BREAKDOWNS]
    if not primaries:
        primaries = ranked
    dashboards = [
        _compose(w, m, _candidate_breakdowns(m, ranked[:12], tokens))
        for w, m in primaries[: max(k * 2, 6)]
    ]
    dashboards.sort(key=lambda d: (-d.score, d.title))
    return dashboards[:k]


__all__ = ["Chart", "Dashboard", "propose", "tokenize", "ground_metric", "ground_dim"]
