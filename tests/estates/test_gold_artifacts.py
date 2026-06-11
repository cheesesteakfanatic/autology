"""Gold-artifact validity tests (whitepaper §17.4 Tier-2 artifacts).

Covers: the frozen gold mini-ontology (loads into contracts.Ontology with valid
parent/range refs, correct event flags and unit dimensions), the ER gold pairs
(every cited record exists; pairs respect the temporal-identity reuse trap), and
the competency-question suite (18 questions, every cited cell exists, answers
independently recomputed from the fixtures).
"""

from __future__ import annotations

import re
from datetime import date

import pytest

from ontoforge.contracts import Datatype
from ontoforge.contracts.units import dim
from ontoforge.estates import aviation

from conftest import FIXTURES

FT_PER_M = 3.28084


@pytest.fixture(scope="module")
def onto():
    return aviation.load_gold_ontology(FIXTURES)


@pytest.fixture(scope="module")
def cq():
    return aviation.load_competency_questions(FIXTURES)


@pytest.fixture(scope="module")
def pairs():
    return aviation.load_er_gold_pairs(FIXTURES)


# ------------------------------------------------------------- mini-ontology

def test_ontology_loads_with_class_budget(onto):
    n = len(onto.classes)
    assert 16 <= n <= 20, f"§17.4 scaled budget is ~16-20 classes, got {n}"


def test_hierarchy_and_event_flags(onto):
    ns = "onto://gold/aviation"
    safety = f"{ns}/SafetyEvent"
    for leafname in ("IncidentReport", "AccidentEvent"):
        uri = f"{ns}/{leafname}"
        assert onto.subsumes(safety, uri), f"{leafname} must specialize SafetyEvent"
        assert onto.get(uri).is_event
    assert onto.subsumes(f"{ns}/Place", f"{ns}/Airport")
    assert onto.subsumes(f"{ns}/Agent", f"{ns}/Manufacturer")  # via Organization
    assert onto.get(f"{ns}/WorkOrder").is_event
    assert onto.get(f"{ns}/Registration").is_event
    assert not onto.get(f"{ns}/Aircraft").is_event


def test_parent_and_range_refs_resolve(onto):
    for cls in onto.iter_classes():
        for parent in cls.parents:
            assert onto.get(parent) is not None
        for p in cls.properties:
            if p.is_link:
                assert onto.get(p.range_class) is not None, f"{cls.name}.{p.name}"
            else:
                assert p.range_class is None
        assert cls.prov_ref, f"{cls.name} missing provenance (constraint H)"
        assert cls.intent_hash


def test_united_properties_carry_dimensions(onto):
    ns = "onto://gold/aviation"
    alt = onto.get(f"{ns}/IncidentReport").prop("altitude_agl")
    assert alt.datatype == Datatype.FLOAT
    assert alt.dimension == dim(m=1)
    assert alt.unit == "ft"
    cost = onto.get(f"{ns}/WorkOrder").prop("cost")
    assert cost.dimension == dim(currency=1)
    assert cost.unit == "USD"
    hours = onto.get(f"{ns}/WorkOrder").prop("labor_hours")
    assert hours.dimension == dim(s=1)
    assert hours.unit == "h"
    speed = onto.get(f"{ns}/AircraftModel").prop("cruise_speed")
    assert speed.dimension == dim(m=1, s=-1)
    # altitude (length) and cost (currency) are deliberately incomparable —
    # the CQ-18 trick-unit question relies on this
    assert alt.dimension != cost.dimension


def test_shape_constraints_are_wellformed(onto):
    n_shapes = 0
    for cls in onto.iter_classes():
        prop_names = {p.name for p in cls.properties}
        for s in cls.shapes:
            n_shapes += 1
            assert s.prop in prop_names
            if s.pattern:
                re.compile(s.pattern)
            if s.min_value is not None and s.max_value is not None:
                assert s.min_value <= s.max_value
    assert n_shapes >= 15, "gold ontology should carry a real SHACL-ish load"


def test_link_property_coverage(onto):
    """The graph must connect: Aircraft->Model->Manufacturer, events->Aircraft."""
    ns = "onto://gold/aviation"
    ac = onto.get(f"{ns}/Aircraft")
    assert ac.prop("model").range_class == f"{ns}/AircraftModel"
    assert onto.get(f"{ns}/AircraftModel").prop("manufacturer").range_class == f"{ns}/Manufacturer"
    assert onto.get(f"{ns}/SafetyEvent").prop("aircraft").range_class == f"{ns}/Aircraft"
    assert onto.get(f"{ns}/WorkOrder").prop("aircraft").range_class == f"{ns}/Aircraft"


# ------------------------------------------------------------- ER gold pairs

def test_every_pair_endpoint_exists(pairs, row_index):
    for rec in pairs.to_dict(orient="records"):
        for side in ("LEFT", "RIGHT"):
            table, key = rec[f"{side}_TABLE"], rec[f"{side}_KEY"]
            assert table in row_index, table
            assert key in row_index[table], f"{table}:{key}"
        assert rec["ENTITY_TYPE"] in ("aircraft", "operator")
        assert rec["ENTITY_ID"]


def test_pairs_cover_all_cross_source_tables(pairs):
    right = set(zip(pairs["ENTITY_TYPE"], pairs["RIGHT_TABLE"]))
    for table in ("asrs_reports", "ntsb_events", "maintenance_erp"):
        assert ("aircraft", table) in right
        assert ("operator", table) in right
    assert ("operator", "faa_master") in right  # registry spelling variants


def test_aircraft_pairs_respect_registration_windows(pairs, row_index):
    """Event dates of paired records must fall inside the master row's
    [CERT ISSUE, EXPIRATION] window — this is what makes the reuse trap fair."""
    for rec in pairs.to_dict(orient="records"):
        if rec["ENTITY_TYPE"] != "aircraft":
            continue
        m = row_index["faa_master"][rec["LEFT_KEY"]]
        cert = m["CERT ISSUE DATE"].strip()
        exp = m["EXPIRATION DATE"].strip()
        r = row_index[rec["RIGHT_TABLE"]][rec["RIGHT_KEY"]]
        if rec["RIGHT_TABLE"] == "asrs_reports":
            ym = int(r["DATE"])
            assert int(cert[:6]) <= ym <= int(exp[:6]), rec
        elif rec["RIGHT_TABLE"] == "ntsb_events":
            d = r["EV_DATE"].replace("-", "")
            assert cert <= d <= exp, rec
        elif rec["RIGHT_TABLE"] == "maintenance_erp":
            d = r["OPEN_DATE"].replace("-", "")
            assert cert <= d <= exp, rec


def test_temporal_reuse_trap_is_exercised(pairs, row_index):
    notes = list(pairs["NOTE"])
    old = [n for n in notes if n == "temporal_reuse_trap:old"]
    new = [n for n in notes if n == "temporal_reuse_trap:new"]
    assert len(old) >= 2, "need events pinned to the OLD airframe of a reused tail"
    assert len(new) >= 1, "need an event pinned to the NEW airframe of a reused tail"
    for rec in pairs.to_dict(orient="records"):
        if rec["NOTE"] == "temporal_reuse_trap:old":
            m = row_index["faa_master"][rec["LEFT_KEY"]]
            assert m["STATUS CODE"].strip() == "D"


def test_no_pair_merges_two_airframes_of_a_reused_tail(pairs, row_index):
    """The two master rows sharing a tail are DIFFERENT entities: no aircraft
    gold pair may identify them with each other, and their ENTITY_IDs differ."""
    eid_by_master: dict[str, set[str]] = {}
    for rec in pairs.to_dict(orient="records"):
        if rec["ENTITY_TYPE"] == "aircraft":
            eid_by_master.setdefault(rec["LEFT_KEY"], set()).add(rec["ENTITY_ID"])
            assert rec["RIGHT_TABLE"] != "faa_master"
    by_tail: dict[str, set[str]] = {}
    for key in eid_by_master:
        tail = key.split("|")[0]
        by_tail.setdefault(tail, set()).update(eid_by_master[key])
    for key, eids in eid_by_master.items():
        assert len(eids) == 1, f"master row {key} mapped to several entity ids"


# ----------------------------------------------------- competency questions

def test_question_suite_shape(cq):
    qs = cq["questions"]
    assert len(qs) == 18
    ids = [q["id"] for q in qs]
    assert len(set(ids)) == 18
    unanswerable = [q for q in qs if not q["answerable"]]
    abstain = [q for q in unanswerable if q["expected_behavior"] == "abstain"]
    trick = [q for q in unanswerable if q["expected_behavior"] == "reject_unit_mismatch"]
    assert len(abstain) == 2, "exactly 2 abstention targets required"
    assert len(trick) == 1, "exactly 1 trick-unit question required"
    kinds = {k for q in qs for k in q["kinds"]}
    assert {"multi_hop", "temporal_as_of", "unit_sensitive",
            "structured_unstructured", "aggregation", "unanswerable",
            "trick_unit"} <= kinds


def test_every_citation_resolves_to_a_real_cell(cq, row_index):
    for q in cq["questions"]:
        if q["answerable"]:
            assert q["citations"], f"{q['id']} has no citations"
        else:
            assert q["citations"] == []
        for c in q["citations"]:
            row = row_index[c["table"]].get(c["row_key"])
            assert row is not None, f"{q['id']}: missing row {c['table']}:{c['row_key']}"
            assert c["column"] in row, f"{q['id']}: missing column {c['column']}"


def _q(cq, qid):
    return next(q for q in cq["questions"] if q["id"] == qid)


def test_cq05_unit_threshold_recomputed(cq, estate):
    """Independent recomputation of the meters-wart threshold count."""
    asrs = estate["tables"]["asrs_reports"]
    count = 0
    for rec in asrs.to_dict(orient="records"):
        if rec["FLIGHT PHASE"] != "Descent":
            continue
        a = rec["ALTITUDE.AGL.SINGLE VALUE"].strip()
        if not a:
            continue
        ft = float(a[:-1]) * FT_PER_M if a.endswith("m") else float(a)
        if ft < 10000.0:
            count += 1
    q = _q(cq, "CQ-05")
    assert q["answer"] == count
    assert len(q["citations"]) == count
    # the naive (suffix-ignoring) count must differ, else the trap is dead
    naive = 0
    for rec in asrs.to_dict(orient="records"):
        if rec["FLIGHT PHASE"] != "Descent":
            continue
        a = rec["ALTITUDE.AGL.SINGLE VALUE"].strip().rstrip("m")
        if a and float(a) < 10000.0:
            naive += 1
    assert naive != count, "meters wart does not change the answer — trap is toothless"


def test_cq12_reuse_list_recomputed(cq, estate):
    master = estate["tables"]["faa_master"]
    by_tail: dict[str, set[str]] = {}
    for rec in master.to_dict(orient="records"):
        by_tail.setdefault("N" + rec["N-NUMBER"].strip(), set()).add(
            rec["SERIAL NUMBER"].strip())
    reused = sorted(t for t, s in by_tail.items() if len(s) > 1)
    assert _q(cq, "CQ-12")["answer"] == reused


def test_cq10_labor_hours_recomputed(cq, estate):
    erp = estate["tables"]["maintenance_erp"]
    total = round(sum(float(r["LABOR_HOURS"]) for r in erp.to_dict(orient="records")
                      if r["COMPONENT"] == "LANDING GEAR"), 1)
    assert _q(cq, "CQ-10")["answer"] == f"{total:.1f}"


def test_cq09_mixed_format_cost_recomputed(cq, estate, generator):
    q = _q(cq, "CQ-09")
    ntsb_cit = next(c for c in q["citations"] if c["table"] == "ntsb_events")
    ntsb = estate["tables"]["ntsb_events"]
    ev = next(r for r in ntsb.to_dict(orient="records") if r["EV_ID"] == ntsb_cit["row_key"])
    ev_d = date.fromisoformat(ev["EV_DATE"])
    tail = re.search(r"N[0-9A-Z]+", q["question"]).group(0)
    erp = estate["tables"]["maintenance_erp"]
    total = 0.0
    styles = set()
    for r in erp.to_dict(orient="records"):
        if r["TAIL_NUMBER"] == tail and date.fromisoformat(r["OPEN_DATE"]) > ev_d:
            styles.add("styled" if r["COST"].startswith("USD") else "bare")
            total += float(r["COST"].replace("USD", "").replace(",", "").strip())
    assert f"{round(total, 2):.2f}" == q["answer"]
    assert styles == {"styled", "bare"}, "CQ-09 must span both cost lexical forms"


def test_cq07_bird_strike_join_recomputed(cq, estate):
    tails_in_registry = {"N" + n.strip()
                         for n in estate["tables"]["faa_master"]["N-NUMBER"]}
    asrs = estate["tables"]["asrs_reports"]
    found = set()
    for rec in asrs.to_dict(orient="records"):
        if "bird strike" in rec["NARRATIVE"].lower():
            for t in re.findall(r"N[0-9]{2,5}[A-Z]{0,2}", rec["NARRATIVE"]):
                if t in tails_in_registry:
                    found.add(t)
    assert _q(cq, "CQ-07")["answer"] == sorted(found)


def test_trick_unit_question_is_dimensionally_ill_typed(cq, onto):
    q = _q(cq, "CQ-18")
    assert "dollars" in q["question"] and "altitude" in q["question"]
    ns = "onto://gold/aviation"
    alt = onto.get(f"{ns}/IncidentReport").prop("altitude_agl")
    cost = onto.get(f"{ns}/WorkOrder").prop("cost")
    assert alt.dimension != cost.dimension  # rejection is type-level, not data-level
