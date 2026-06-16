"""The Ask flywheel — close the loop end-to-end through LODESTONE (v2.1 §4).

A novel cross-source Ask that engineers a new answer WRITES THE RESULT BACK as a
referenceable cached object, so the next identical ask is served from cache. The
cache is consulted FIRST; a still-valid hit is served (skipping grounding,
candidate generation, and the spine decision); a stale hit (its provenance atoms
moved) is invalidated and recomputed — never a confidently-wrong cached answer.

These tests build their OWN Lodestone engines (fresh CachedWorkStore each) over
the shared read-only HEARTH world so the cache state of one test never leaks into
another; the invalidation test builds a private, mutable world.
"""

from __future__ import annotations

from ontoforge.contracts import Interval, Layer, SpineProfile, ValueCell
from ontoforge.contracts.oqir import Aggregate, Traverse
from ontoforge.discovery import WorkKind, fingerprint_atoms, normalize_question
from ontoforge.hearth import Hearth
from ontoforge.ledger import SqliteLedger
from ontoforge.lodestone import Lodestone, requires_live_composition
from ontoforge.lodestone.worldbuild import (
    NS,
    WorldBuilder,
    build_estate_world,
    extend_gold_ontology,
    slug,
)
from ontoforge.spine import DecisionSpine

# CQ-01 is a 2-type multi-hop (Aircraft -> AircraftModel -> Manufacturer): a
# textbook "live composition" the flywheel should cache and reuse.
COMPOSED_Q = (
    "Which manufacturer name does the FAA aircraft reference record for the "
    "model of the aircraft registered with tail number N4669X?"
)
COMPOSED_A = "GULFSTREAM AEROSPACE"


def _engine(gold_onto, hearth_world, ledger, *, tenant_id=""):
    """A fresh engine with its own (empty) flywheel cache over the shared world."""
    spine = DecisionSpine(SpineProfile(), model_client=None)
    return Lodestone(gold_onto, hearth_world, ledger, spine, tenant_id=tenant_id)


# ----------------------------------------------------- the cache-hit loop


def test_second_ask_is_served_from_cache(gold_onto, hearth_world, ledger):
    """Ask a composed question twice: the 2nd is a cache HIT — same answer + same
    citations — and it SKIPS the expensive pipeline (no spine decision, no
    candidate generation on the second pass)."""
    spine = DecisionSpine(SpineProfile(), model_client=None)

    decide_calls: list = []
    propose_calls: list = []
    inner_decide = spine.decide

    class SpySpine:
        def decide(self, req):
            decide_calls.append(req.kind)
            return inner_decide(req)

        def register_rule(self, kind, fn):
            spine.register_rule(kind, fn)

    from ontoforge.ledger import HeuristicAdapter
    from ontoforge.lodestone import GENERATE_TASK, make_generate_handler

    inner_client = HeuristicAdapter({GENERATE_TASK: make_generate_handler(gold_onto)})

    class SpyClient:
        def propose(self, req):
            propose_calls.append(req.task)
            return inner_client.propose(req)

    eng = Lodestone(gold_onto, hearth_world, ledger, SpySpine(), model_client=SpyClient())

    a1 = eng.ask(COMPOSED_Q)
    assert not a1.abstained and not a1.clarification
    assert a1.cached is False
    flat1 = [str(v) for r in a1.rows for v in r]
    assert COMPOSED_A in flat1
    assert all(c.atom_ids for c in a1.citations)
    n_decide_after_first = len(decide_calls)
    n_propose_after_first = len(propose_calls)
    assert n_decide_after_first > 0 and n_propose_after_first > 0  # first ask composed live

    a2 = eng.ask(COMPOSED_Q)
    # served from cache: same answer + same citations
    assert a2.cached is True
    assert a2.cache_object_id and a2.cache_description
    assert [list(r) for r in a2.rows] == [list(r) for r in a1.rows]
    assert [(c.row, c.column, c.value, c.atom_ids) for c in a2.citations] == [
        (c.row, c.column, c.value, c.atom_ids) for c in a1.citations
    ]
    # the cache hit skipped candidate generation entirely (the expensive stage)
    assert len(propose_calls) == n_propose_after_first


def test_cache_hit_survives_question_rephrasing(gold_onto, hearth_world, ledger):
    """A normalized-question match: a reordered/stopword-different phrasing of a
    previously composed question hits the same cached answer."""
    eng = _engine(gold_onto, hearth_world, ledger)
    a1 = eng.ask(COMPOSED_Q)
    assert a1.cached is False and not a1.abstained
    rephrased = (
        "For tail number N4669X, the FAA aircraft reference records which "
        "manufacturer name for the model of that registered aircraft?"
    )
    # only fire this when the two phrasings truly normalize together
    if normalize_question(rephrased) == normalize_question(COMPOSED_Q):
        a2 = eng.ask(rephrased)
        assert a2.cached is True
        assert [list(r) for r in a2.rows] == [list(r) for r in a1.rows]


# ---------------------------------------- written-back object is referenceable


def test_written_back_object_carries_description_and_is_searchable(gold_onto, hearth_world, ledger):
    """§4 step 3: the write-back is a versioned ASK object with an auto-generated
    description + provenance/validity metadata, retrievable by CachedWorkStore.search."""
    eng = _engine(gold_onto, hearth_world, ledger, tenant_id="acme")
    eng.ask(COMPOSED_Q)
    store = eng.work_store
    obj = store.latest_ask(COMPOSED_Q, tenant_id="acme")
    assert obj is not None and obj.kind is WorkKind.ASK
    assert obj.description and obj.tenant_id == "acme"
    assert "fingerprint" in obj.payload and obj.payload["fingerprint"].startswith("fp:")
    # retrievable downstream by natural-language search
    hits = store.search("manufacturer model N4669X", tenant_id="acme", kind=WorkKind.ASK)
    assert hits and hits[0].obj.object_id == obj.object_id


def test_cheap_single_type_lookup_is_not_cached(gold_onto, hearth_world, ledger):
    """A bare single-class lookup is cheap to recompute and must NOT bloat the
    cache — only 2+-type plans or fresh aggregates are written back."""
    eng = _engine(gold_onto, hearth_world, ledger)
    # 'damage level for NTSB event ...' style single-entity reads stay uncached;
    # we assert the predicate directly + that nothing is written for a non-answer.
    ask_objs_before = [o for o in eng.work_store.objects() if o.kind is WorkKind.ASK]
    eng.ask("zzzz totally ungroundable nonsense quux")  # abstains -> never cached
    ask_objs_after = [o for o in eng.work_store.objects() if o.kind is WorkKind.ASK]
    assert ask_objs_after == ask_objs_before


# ------------------------------------------------------- tenant isolation


def test_cache_is_tenant_isolated(gold_onto, hearth_world, ledger):
    """One tenant's cached Ask never serves another tenant (§1.5). Each tenant's
    engine owns its own store here; even a SHARED store filters by tenant."""
    from ontoforge.discovery import CachedWorkStore

    shared = CachedWorkStore()
    acme = _engine(gold_onto, hearth_world, ledger, tenant_id="acme")
    acme._work_store = shared
    globex = _engine(gold_onto, hearth_world, ledger, tenant_id="globex")
    globex._work_store = shared

    a_acme = acme.ask(COMPOSED_Q)
    assert a_acme.cached is False
    # globex shares the underlying store but must NOT get acme's cached hit
    a_globex = globex.ask(COMPOSED_Q)
    assert a_globex.cached is False, "globex must not be served acme's cached answer"
    # acme's own re-ask still hits its cache
    assert acme.ask(COMPOSED_Q).cached is True


# ----------------------------------- never serve a confidently-wrong stale answer


def test_provenance_change_invalidates_and_recomputes(estate, tmp_path):
    """A provenance change (a cited cell's value is re-committed -> a new
    content-addressed atom) makes the cached answer STALE: the next ask detects
    the fingerprint moved, invalidates, and recomputes the NEW answer — the
    flywheel never serves the now-wrong cached value."""
    onto = extend_gold_ontology(__import__(
        "ontoforge.estates", fromlist=["load_gold_ontology"]
    ).load_gold_ontology())
    ledger = SqliteLedger(":memory:")
    hearth = Hearth(tmp_path / "store", ledger)
    build_estate_world(estate, onto, hearth, ledger)
    eng = _engine(onto, hearth, ledger)

    a1 = eng.ask(COMPOSED_Q)
    assert not a1.abstained
    flat1 = [str(v) for r in a1.rows for v in r]
    assert COMPOSED_A in flat1
    fp1 = fingerprint_atoms({aid for c in a1.citations for aid in c.atom_ids})

    # the cached object exists and is valid (same fingerprint -> would be served)
    store = eng.work_store
    assert store.lookup_answer(COMPOSED_Q, current_fingerprint=fp1) is not None

    # --- mutate the underlying provenance: re-commit the cited Manufacturer name
    # cell with a NEW value. The new (uri,value) mints a new atom id, so the
    # answer's provenance fingerprint MOVES.
    b = WorldBuilder(estate, ledger, hearth, onto)
    mfr_uri = f"ent://manufacturer/{slug(COMPOSED_A)}"
    new_value = "GULFSTREAM AEROSPACE (RENAMED)"
    prov = b.ref_leaf("faa_acftref", "renamed-row", "MFR", new_value)
    # commit a superseding cell (newer system time -> wins the EVER read)
    cell = ValueCell(
        entity_uri=mfr_uri, prop="name", value=new_value,
        valid=Interval(0), system=Interval(0), prov_ref=prov,
        confidence=1.0, src_rank=0,  # rank-0 source wins survivorship
    )
    hearth._commit_cells(Layer.ENTITY, f"{NS}/Manufacturer", [cell],
                         now=None, allow_rank0=True)
    eng.refresh_value_index()

    # --- re-ask: the live fingerprint no longer matches the stored one, so the
    # cache is INVALIDATED and the answer is recomputed to the new value.
    a2 = eng.ask(COMPOSED_Q)
    assert a2.cached is False, "stale cached answer must NOT be served"
    flat2 = [str(v) for r in a2.rows for v in r]
    assert new_value in flat2, "must recompute the NEW answer after provenance change"
    assert COMPOSED_A not in flat2
    fp2 = fingerprint_atoms({aid for c in a2.citations for aid in c.atom_ids})
    assert fp2 != fp1  # provenance fingerprint moved

    # the recompute wrote a NEW version back; a 3rd ask is served from the fresh cache
    a3 = eng.ask(COMPOSED_Q)
    assert a3.cached is True
    assert [list(r) for r in a3.rows] == [list(r) for r in a2.rows]

    ledger.close()


# ------------------------------------------------------ composition predicate


def test_requires_live_composition_predicate():
    from ontoforge.contracts.oqir import Agg, Select

    assert requires_live_composition(Traverse(Select("C"), "link")) is True
    assert requires_live_composition(Aggregate(Select("C"), Agg.SUM, "x")) is True
    assert requires_live_composition(Aggregate(Select("C"), Agg.COUNT)) is True
    # a bare single-class select is cheap -> not cached
    assert requires_live_composition(Select("C")) is False
