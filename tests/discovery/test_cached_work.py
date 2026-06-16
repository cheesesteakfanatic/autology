"""CachedWorkStore — versioned DE work + keyless semantic retrieval (v2.1 §5).

Deterministic, keyless, zero-network: a pure-python hashing TF-IDF index over
auto-generated descriptions serves human search and the model RAG bootstrap.
"""

from __future__ import annotations

from ontoforge.discovery import (
    CachedWorkStore,
    WorkKind,
    describe_work,
)


def _store() -> CachedWorkStore:
    s = CachedWorkStore()
    s.record_join("orders.customer_id", "customers.customer_id", "fk_join",
                  match_rate=0.99, fanout_avg=1.0, validated=True,
                  rationale="clean foreign key", tenant_id="acme")
    s.record_join("imports.region", "weather.region", "lookup_dimension",
                  match_rate=0.95, fanout_avg=1.0, validated=True,
                  rationale="region dimension lookup", tenant_id="acme")
    s.record("revenue_by_month", WorkKind.RESULT,
             {"question": "monthly revenue by region", "columns": ["month", "region", "rev"]},
             tenant_id="acme")
    return s


def test_auto_description_surfaces_query_terms() -> None:
    desc = describe_work(WorkKind.JOIN, {
        "left": "imports.region", "right": "weather.region",
        "rel_type": "lookup_dimension", "match_rate": 0.95, "validated": True,
    })
    assert "imports.region" in desc and "weather.region" in desc
    assert "lookup_dimension" in desc
    assert "match rate 95%" in desc
    assert "backward-validated" in desc


def test_search_ranks_relevant_work_first() -> None:
    s = _store()
    hits = s.search("weather region join", tenant_id="acme")
    assert hits, "expected at least one hit"
    top = hits[0].obj
    # the import/weather lookup join is the most relevant work for that query
    assert "weather" in top.description and "imports" in top.description
    assert hits[0].score > 0.0


def test_retrieve_for_model_returns_known_joins_for_context() -> None:
    """The RAG bootstrap: given a candidate context, return what we already KNOW
    about this kind of join (prior validated work) to seed an adjudicator."""
    s = _store()
    ctx = {"left": "weather.region", "right": "imports.region", "rel_type": "lookup_dimension"}
    hits = s.retrieve_for_model(ctx, tenant_id="acme")
    assert hits
    assert hits[0].obj.kind is WorkKind.JOIN
    assert "region" in hits[0].obj.description


def test_versioning_keeps_history_and_bumps_version() -> None:
    s = CachedWorkStore()
    a = s.record_join("a.id", "b.id", "fk_join", match_rate=0.8, tenant_id="t")
    b = s.record_join("a.id", "b.id", "fk_join", match_rate=0.99, tenant_id="t")
    assert a.version == 1 and b.version == 2
    # the latest object is the newest version; history retains both
    latest = s.objects(tenant_id="t")
    assert len(latest) == 1 and latest[0].version == 2
    hist = s.history(a.key)
    assert [o.version for o in hist] == [1, 2]
    assert hist[0].payload["match_rate"] == 0.8


def test_retrieval_is_tenant_scoped() -> None:
    """Per-tenant isolation (§1.5): one tenant's cached work never surfaces in
    another tenant's search/RAG retrieval."""
    s = CachedWorkStore()
    s.record_join("acme.cust", "acme.dim", "fk_join", validated=True, tenant_id="acme")
    s.record_join("globex.order", "globex.dim", "fk_join", validated=True, tenant_id="globex")
    acme_hits = s.search("fk join dim", tenant_id="acme")
    assert acme_hits and all(h.obj.tenant_id == "acme" for h in acme_hits)
    globex_hits = s.search("fk join dim", tenant_id="globex")
    assert globex_hits and all(h.obj.tenant_id == "globex" for h in globex_hits)


def test_search_is_deterministic() -> None:
    """Keyless + deterministic: identical stores yield byte-identical rankings."""
    h1 = [(r.obj.object_id, r.score) for r in _store().search("weather region", tenant_id="acme")]
    h2 = [(r.obj.object_id, r.score) for r in _store().search("weather region", tenant_id="acme")]
    assert h1 == h2


def test_unrelated_query_does_not_force_a_match() -> None:
    """An off-topic query produces no RELEVANT hit. Character-trigram features can
    yield faint (<0.1) overlaps, but nothing at real-relevance strength — so a
    sensible relevance floor returns empty rather than a confidently-wrong match."""
    s = _store()
    hits = s.search("zzzz totally unrelated quux", tenant_id="acme", min_score=0.1)
    assert hits == []
    # for contrast, an on-topic query clears the same floor comfortably
    on_topic = s.search("customer orders fk join", tenant_id="acme", min_score=0.1)
    assert on_topic and on_topic[0].score > 0.1
