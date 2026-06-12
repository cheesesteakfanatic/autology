"""M13 — composition search: vague utterance -> 3 ranked, valid dashboards."""

from __future__ import annotations

import pytest

from ontoforge.contracts.oqir import Aggregate, Select, TopK
from ontoforge.estates import load_gold_ontology
from ontoforge.vista import VEGA_LITE_SCHEMA, propose, render_with_data
from ontoforge.vista.compose import MAX_BREAKDOWNS, MIN_BREAKDOWNS

VAGUE = "supplier risk"   # the §6.3 canonical vague utterance


@pytest.fixture(scope="module")
def gold():
    return load_gold_ontology()


@pytest.fixture(scope="module")
def proposals(gold):
    return propose(VAGUE, gold)


def _validate_vega(spec: dict) -> None:
    """Programmatic Vega-Lite v5 structure validation (required keys/types)."""
    assert spec["$schema"] == VEGA_LITE_SCHEMA
    assert isinstance(spec["data"], dict)
    assert isinstance(spec["data"]["values"], list)
    assert "mark" in spec and isinstance(spec["mark"], (str, dict))
    enc = spec["encoding"]
    assert isinstance(enc, dict) and enc
    for channel, e in enc.items():
        assert isinstance(e, dict), channel
        assert isinstance(e["field"], str) and e["field"]
        assert e["type"] in ("quantitative", "nominal", "ordinal", "temporal")


def test_three_ranked_dashboards(proposals):
    assert len(proposals) == 3
    scores = [d.score for d in proposals]
    assert scores == sorted(scores, reverse=True)
    assert len({d.title for d in proposals}) == 3


def test_dashboard_shape_kpi_plus_breakdowns(proposals):
    for d in proposals:
        kpi, *breakdowns = d.charts
        assert isinstance(kpi.oqir, Aggregate)
        assert kpi.oqir.group_by == ()                      # scalar KPI
        assert isinstance(kpi.oqir.source, Select)
        assert MIN_BREAKDOWNS <= len(breakdowns) <= MAX_BREAKDOWNS
        for c in breakdowns:
            assert isinstance(c.oqir, (Aggregate, TopK))
            agg = c.oqir.source if isinstance(c.oqir, TopK) else c.oqir
            assert isinstance(agg, Aggregate)
            assert len(agg.group_by) == 1                   # one breakdown dim each


def test_no_redundant_grain_and_dimension_diversity(proposals):
    for d in proposals:
        grains = [c.grain for c in d.charts]
        assert len(set(grains)) == len(grains), f"duplicate grain in {d.title}"
        dims = [c.grain[-1] for c in d.charts[1:]]
        assert len(set(dims)) == len(dims), f"repeated dimension in {d.title}"


def test_vega_specs_are_valid_v5(proposals):
    for d in proposals:
        for c in d.charts:
            _validate_vega(c.vega)


def test_grounded_terms_reference_real_ontology(gold, proposals):
    from ontoforge.vista.metrics import effective_properties

    for d in proposals:
        for c in d.charts:
            agg = c.oqir.source if isinstance(c.oqir, TopK) else c.oqir
            cls = gold.get(agg.source.class_uri)
            assert cls is not None
            if agg.measure_prop is not None:
                assert cls.prop(agg.measure_prop) is not None
            names = {p.name for p in effective_properties(cls, gold)}
            for g in agg.group_by:
                assert g in names  # own or inherited via subsumption


def test_specific_utterance_grounds_to_named_measure(gold):
    """When intent IS specific, grounding should pick it up (nvBench direction)."""
    dashboards = propose("average altitude by flight phase", gold)
    top = dashboards[0]
    assert "altitude" in top.title.lower()
    fields = {c.grain[-1] for c in top.charts[1:]}
    assert "flight_phase" in fields


def test_deterministic(gold):
    a = propose(VAGUE, gold)
    b = propose(VAGUE, gold)
    assert [d.title for d in a] == [d.title for d in b]
    assert [d.score for d in a] == [d.score for d in b]
    assert [repr(c.oqir) for d in a for c in d.charts] == [
        repr(c.oqir) for d in b for c in d.charts
    ]
    assert [c.vega for d in a for c in d.charts] == [c.vega for d in b for c in d.charts]


def test_render_with_data_executor_hook(proposals):
    """Any callable(oqir) -> rows fills data.values — no LODESTONE import."""
    d = propose(VAGUE, load_gold_ontology())[0]   # fresh copy, don't mutate fixture
    calls = []

    def executor(term):
        calls.append(term)
        return [{"x": 1, "value": 2.5}]

    out = render_with_data(d, executor)
    assert len(calls) == len(d.charts)
    for c in out.charts:
        assert c.vega["data"]["values"] == [{"x": 1, "value": 2.5}]
