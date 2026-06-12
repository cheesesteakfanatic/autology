"""Cross-system overlap floors (the entity-graph-first design under test).

Research targets: >= 60% of purchasing suppliers appear in >= 2 systems; every
hero entity (top suppliers, hero DC, hero model) appears in 3+ tables; orphans
are deliberate and bounded (PO-only tail vendors, quality-only suppliers, 3PL
sites without leases).
"""

from __future__ import annotations

from meridian_helpers import pad_vendor


def test_supplier_overlap_floor(frames):
    po_vendors = {pad_vendor(v) for v in frames["purchase_order_lines"]["VENDOR_ID"]}
    contracted = set(frames["supplier_contracts"]["SUPPLIER_ID"])
    in_two_systems = po_vendors & contracted
    assert len(po_vendors) >= 180
    share = len(in_two_systems) / len(po_vendors)
    assert share >= 0.60, f"only {share:.0%} of purchasing suppliers appear in 2+ systems"
    # the tail is deliberate, not dominant
    assert 5 <= len(po_vendors - contracted) <= 40


def test_top_suppliers_appear_in_three_plus_systems(frames):
    contracts = frames["supplier_contracts"]
    top = contracts.loc[contracts["CONTRACT_TYPE"] == "AMENDMENT", "SUPPLIER_ID"].drop_duplicates()
    top10 = list(top[:10])
    assert len(top10) == 10
    po_vendors = {pad_vendor(v) for v in frames["purchase_order_lines"]["VENDOR_ID"]}
    qn_names = " | ".join(frames["quality_notifications"]["SUPPLIER"]).casefold()
    name_of = dict(zip(contracts["SUPPLIER_ID"], contracts["SUPPLIER_LEGAL_NAME"]))
    for sid in top10:
        assert sid in po_vendors, f"top supplier {sid} missing from purchasing"
        first_word = name_of[sid].split()[0].casefold()
        assert first_word in qn_names, f"top supplier {sid} ({first_word}) missing from quality"


def test_quality_orphans_are_deliberate_and_bounded(frames):
    """Some quality suppliers must resolve to no contract/PO (explainable
    orphans) — but only a handful."""
    legal = {n.casefold() for n in frames["supplier_contracts"]["SUPPLIER_LEGAL_NAME"]}
    qn_first_words = {
        n.strip().casefold().split()[0]
        for n in frames["quality_notifications"]["SUPPLIER"]
        if n.strip()
    }
    contracted_first_words = {n.split()[0] for n in legal}
    orphan_words = qn_first_words - contracted_first_words
    assert 1 <= len(orphan_words) <= 15


def test_hero_facility_in_three_plus_systems(frames):
    code = "MER-DC-NL-01"
    assert (frames["leases"]["FACILITY_CODE"] == code).any()
    assert (frames["shipments"]["DESTINATION"] == code).any()
    assert (frames["site_headcount"]["FACILITY_CODE"] == code).any()
    assert (frames["quality_notifications"]["INSPECTION_SITE"] == code).any()
    assert (frames["purchase_order_lines"]["PLANT_CODE"] == code).any()


def test_hero_model_in_three_plus_systems(frames):
    assert (frames["products"]["MODEL_CODE"] == "PLS9P").any()
    assert (frames["bom_components"]["PARENT_MODEL_CODE"] == "PLS9P").any()
    serials = frames["support_tickets"]["SERIAL_NUMBER"]
    assert serials.str.startswith("PLS9P").any()  # the discoverable serial-prefix bridge


def test_facilities_without_leases_are_the_3pl_sites(frames):
    leased = set(frames["leases"]["FACILITY_CODE"])
    staffed = set(frames["site_headcount"]["FACILITY_CODE"])
    unleased = sorted(staffed - leased)
    assert 1 <= len(unleased) <= 5
    assert all(code.startswith("MER-3PL-") for code in unleased)


def test_text_bridge_battery_swelling_cluster(frames, gold):
    """The flagship text question's planted cluster: the exact phrase appears
    only on hero-model tickets, matching the pinned gold count."""
    tickets = frames["support_tickets"]
    hits = tickets[tickets["DESCRIPTION"].str.casefold().str.contains("battery swelling", regex=False)]
    pinned = next(q for q in gold["questions"] if q["id"] == "MQ-01")["answer"]
    assert len(hits) == pinned >= 20
    assert (hits["SERIAL_NUMBER"].str.startswith("PLS9P")).all()
    assert (hits["ISSUE_CATEGORY"] == "BATTERY").all()
    # ... and its battery part has OPEN quality notifications (the G5 trace)
    bom = frames["bom_components"]
    battery = bom[(bom["PARENT_MODEL_CODE"] == "PLS9P") & (bom["COMPONENT_CATEGORY"] == "BATTERY")]
    assert not battery.empty
    part = battery["COMPONENT_PART_NUMBER"].iloc[0]
    qn = frames["quality_notifications"]
    open_qns = qn[(qn["PART_NUMBER"] == part) & (qn["STATUS"] == "OPEN")]
    assert len(open_qns) >= 3
