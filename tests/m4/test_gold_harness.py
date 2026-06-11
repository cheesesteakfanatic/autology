"""Gold harness: induce from the REAL aviation estate, compare to the gold
mini-ontology (whitepaper §11.2 M4 "hero gold-ontology comparator").

HARD GATES: class precision >= 0.70, class recall >= 0.60.
Hierarchy-edge P/R are computed on matched classes and reported (with sanity
floors well below the class gates — the §12 0.85 bar is a design target for
the full cascade, not this keyless heuristic build).
"""

from __future__ import annotations

from m4_helpers import compare_to_gold

CLASS_PRECISION_GATE = 0.70
CLASS_RECALL_GATE = 0.60


def test_class_precision_recall_gates(induction, gold, profiles):
    _, result = induction
    cmpr = compare_to_gold(result, gold, profiles)
    print("\n" + cmpr.report)
    assert cmpr.precision >= CLASS_PRECISION_GATE, (
        f"class precision {cmpr.precision:.3f} < {CLASS_PRECISION_GATE}\n{cmpr.report}"
    )
    assert cmpr.recall >= CLASS_RECALL_GATE, (
        f"class recall {cmpr.recall:.3f} < {CLASS_RECALL_GATE}\n{cmpr.report}"
    )


def test_hierarchy_edges_reported(induction, gold, profiles):
    _, result = induction
    cmpr = compare_to_gold(result, gold, profiles)
    print(
        f"\nhierarchy-edge precision {cmpr.hierarchy_precision:.3f} "
        f"({cmpr.n_induced_edges} induced edges scored), "
        f"recall {cmpr.hierarchy_recall:.3f} ({cmpr.n_gold_edges} gold edges scored)"
    )
    assert cmpr.n_gold_edges > 0, "no gold edges had both endpoints matched"
    assert cmpr.hierarchy_recall >= 0.5
    assert cmpr.hierarchy_precision >= 0.30


def test_anchor_classes_are_matched(induction, gold, profiles):
    """The estate's four unambiguous one-table types must individually match
    their gold counterparts — coarse aggregate scores can hide their loss."""
    _, result = induction
    cmpr = compare_to_gold(result, gold, profiles)
    for anchor in ("WorkOrder", "IncidentReport", "AircraftModel", "AccidentEvent"):
        assert anchor in cmpr.gold_matches, f"gold {anchor} unmatched\n{cmpr.report}"


def test_event_sources_drive_event_candidates(induction):
    """All five tables yield G-table candidates; the three FD-cluster latent
    types of faa_master (registrant / city / zip) are found by G-decomp."""
    _, result = induction
    cids = {c.cid for c in result.candidates}
    for table in ("faa_master", "faa_acftref", "asrs_reports", "ntsb_events", "maintenance_erp"):
        assert f"g-table:{table}" in cids
    assert "g-decomp:faa_master:registrant_name" in cids
    assert "g-decomp:faa_master:city" in cids
    assert "g-decomp:maintenance_erp:component" in cids
    assert "g-decomp:faa_master:engine_manufacturer_model" in cids


def test_hub_review_keeps_state_discards_numeric_coincidence(induction):
    """§3.4 failure-mode (b): the shared US-state domain survives spine review;
    the NO-SEATS/TYPE-ACFT numeric value-range coincidence hub is discarded."""
    _, result = induction
    assert result.hub_reviews["g-join:asrs_reports.state_reference"].outcome == "admit"
    assert result.hub_reviews["g-join:faa_acftref.no_seats"].outcome == "discard"
    cids = {c.cid for c in result.candidates}
    assert "g-join:asrs_reports.state_reference" in cids
    assert "g-join:faa_acftref.no_seats" not in cids


def test_every_admission_went_through_the_spine(induction):
    """Spine-gated admission: every non-root lattice concept carries a
    DecisionResult of kind ADMIT; admitted+merged+discarded partition them."""
    _, result = induction
    adm = result.admission
    all_hashes = set(result.lattice.concepts)
    routed = set(adm.decisions) | set(adm.discarded)
    assert routed == all_hashes
    assert set(adm.admitted).isdisjoint(adm.merged)
    for ih, d in adm.decisions.items():
        assert d.decision_id == f"strata:admit:{ih}"  # DecisionKind.ADMIT route
        assert d.outcome in ("admit", "merge", "discard")
    # merges always land on an admitted target or are explicitly discarded
    for ih, target in adm.merged.items():
        assert target is None or target in adm.admitted


def test_emitted_ontology_is_well_formed(induction):
    _, result = induction
    onto = result.ontology
    assert len(onto.classes) >= 10
    for uri, c in onto.classes.items():
        assert uri == f"onto://class/{c.intent_hash}"
        for p in c.parents:
            assert p in onto.classes and p != uri
        # parents are transitively reduced: no parent is an ancestor of another
        for p in c.parents:
            others = set(c.parents) - {p}
            assert not (others & onto.ancestors(p))
        for prop in c.properties:
            if prop.is_link:
                assert prop.range_class in onto.classes
        shape_props = {s.prop for s in c.shapes}
        assert shape_props <= {p.name for p in c.properties}
        assert c.prov_ref
