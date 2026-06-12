"""Full-estate world building: the aviation estate committed into a REAL
HEARTH under the frozen gold mini-ontology (§11.3 de-risking slice), with every
cell's prov_ref interned over atoms minted from the ACTUAL source cells
(constraint H).

This module plays the role of the Wave-2/3 pipeline at gold-ontology fidelity:
conformance (currency lexical forms, meter-suffixed altitudes, operator-name
folding, leading-N normalization) happens HERE, with the source lexical unit
recorded alongside the conformed measure (altitude_agl_unit) — exactly what
ANVIL-conformed entity shards would contain. LODESTONE itself never sees the
CSVs.

Extracted from the M12 competency-suite fixture builder (tests/m12/conftest.py)
so the product CLI materializes the SAME world the competency gates prove.
Public surface:

    build_estate_world(estate, ontology, hearth, ledger, *, limit=None) -> stats
    extend_gold_ontology(onto) -> onto   # adds the altitude_agl_unit annotation
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from typing import Any, Optional

from ontoforge.contracts import (
    Interval,
    Layer,
    LinkCell,
    ValueCell,
    leaf,
    make_cell_atom,
    to_instant,
)
from ontoforge.contracts.ontology import Datatype, Ontology, PropertyDef, property_uri
from ontoforge.contracts.provenance import prov_prod, prov_sum

from .lower import normalize_name

NS = "onto://gold/aviation"
FT_PER_M = 3.28084
TAIL_RE = re.compile(r"N\d[\dA-Z]+")

__all__ = ["build_estate_world", "extend_gold_ontology", "NS"]


def _curi(name: str) -> str:
    return f"{NS}/{name}"


def slug(s: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9]+", "-", s.strip()).strip("-").lower()[:48]
    h = hashlib.sha1(s.strip().encode()).hexdigest()[:8]
    return f"{safe}-{h}" if safe else h


def parse_yyyymmdd(s: str) -> Optional[int]:
    s = s.strip()
    if not re.fullmatch(r"\d{8}", s):
        return None
    try:
        return to_instant(datetime.strptime(s, "%Y%m%d").replace(tzinfo=timezone.utc))
    except ValueError:
        return None


def parse_iso(s: str) -> Optional[int]:
    s = s.strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return None
    return to_instant(datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc))


def window(start: Optional[int], end: Optional[int]) -> Interval:
    if start is not None and end is not None and start < end:
        return Interval(start, end)
    if start is not None:
        return Interval(start)
    return Interval(0)


def parse_cost(raw: str) -> Optional[float]:
    s = re.sub(r"[^0-9.]", "", raw)
    return float(s) if s else None


class WorldBuilder:
    """Mints atoms from source cells, interns provenance, batches commits."""

    def __init__(self, estate: dict, ledger, hearth, ontology: Optional[Ontology] = None) -> None:
        self.estate = estate
        self.ledger = ledger
        self.hearth = hearth
        self.ontology = ontology
        self.cells: dict[str, list[ValueCell]] = {}     # class name -> cells
        self.links: dict[tuple[str, str], list[LinkCell]] = {}
        self._atom_cache: dict[tuple, str] = {}

    def class_uri(self, cls: str) -> str:
        if self.ontology is not None:
            c = self.ontology.by_name(cls)
            if c is not None:
                return c.uri
        return _curi(cls)

    def source_id(self, table: str) -> str:
        return self.estate["metadata"]["tables"][table]["source_id"]

    def atom(self, table: str, row_key: str, column: str, value: Any) -> str:
        key = (table, row_key, column)
        if key not in self._atom_cache:
            a = make_cell_atom(self.source_id(table), table, row_key, column, value)
            self.ledger.register_atoms([a])
            self._atom_cache[key] = a.atom_id
        return self._atom_cache[key]

    def ref_leaf(self, table: str, row_key: str, column: str, value: Any) -> str:
        return self.ledger.intern(leaf(self.atom(table, row_key, column, value)))

    def ref_prod(self, atoms: list[str]) -> str:
        return self.ledger.intern(prov_prod([leaf(a) for a in atoms]))

    def ref_sum(self, atoms: list[str]) -> str:
        return self.ledger.intern(prov_sum([leaf(a) for a in sorted(set(atoms))]))

    def put(self, cls: str, uri: str, prop: str, value: Any, prov: str,
            valid: Interval = Interval(0)) -> None:
        self.cells.setdefault(cls, []).append(
            ValueCell(entity_uri=uri, prop=prop, value=value, valid=valid,
                      system=Interval(0), prov_ref=prov, confidence=1.0, src_rank=1)
        )

    def link(self, cls: str, pred: str, subj: str, obj: str, prov: str,
             valid: Interval = Interval(0)) -> None:
        self.links.setdefault((cls, pred), []).append(
            LinkCell(subject_uri=subj, predicate=pred, object_uri=obj, valid=valid,
                     system=Interval(0), prov_ref=prov)
        )

    def commit_all(self) -> dict[str, Any]:
        n_cells = 0
        n_links = 0
        entities: set[str] = set()
        per_class: dict[str, int] = {}
        for cls in sorted(self.cells):
            self.hearth.commit(Layer.ENTITY, self.class_uri(cls), self.cells[cls])
            n_cells += len(self.cells[cls])
            uris = {c.entity_uri for c in self.cells[cls]}
            entities |= uris
            per_class[cls] = len(uris)
        for (cls, pred) in sorted(self.links):
            self.hearth.commit_links(self.class_uri(cls), pred, self.links[(cls, pred)])
            n_links += len(self.links[(cls, pred)])
        return {
            "entities": len(entities),
            "cells": n_cells,
            "links": n_links,
            "classes": per_class,
        }


def build_estate_world(
    estate: dict[str, Any],
    ontology: Optional[Ontology],
    hearth,
    ledger,
    *,
    limit: Optional[int] = None,
) -> dict[str, Any]:
    """Commit ALL estate tables (aircraft, models, operators, incident reports,
    accident events, work orders + links) into HEARTH with constraint-H
    provenance. Returns stats: {entities, cells, links, classes: {name: n}}.

    ``limit`` optionally caps each table to its first N rows (the CLI usually
    pre-limits via its sticky --limit subsample instead).
    """
    b = WorldBuilder(estate, ledger, hearth, ontology)
    t = estate["tables"]
    if limit is not None:
        t = {name: df.head(limit) for name, df in t.items()}

    # ---------------- Manufacturer + AircraftModel (faa_acftref)
    mfr_atoms: dict[str, list[str]] = {}
    model_uri: dict[str, str] = {}
    for r in t["faa_acftref"].to_dict("records"):
        code = r["CODE"].strip()
        rk = code
        mfr = r["MFR"].strip()
        mfr_atoms.setdefault(mfr, []).append(b.atom("faa_acftref", rk, "MFR", r["MFR"]))
        uri = f"ent://model/{slug(code)}"
        model_uri[code] = uri
        b.put("AircraftModel", uri, "mfr_mdl_code", code, b.ref_leaf("faa_acftref", rk, "CODE", r["CODE"]))
        b.put("AircraftModel", uri, "model_name", r["MODEL"].strip(), b.ref_leaf("faa_acftref", rk, "MODEL", r["MODEL"]))
        if r["NO-SEATS"].strip().isdigit():
            b.put("AircraftModel", uri, "seats", int(r["NO-SEATS"]), b.ref_leaf("faa_acftref", rk, "NO-SEATS", r["NO-SEATS"]))
        if r["NO-ENG"].strip().isdigit():
            b.put("AircraftModel", uri, "engine_count", int(r["NO-ENG"]), b.ref_leaf("faa_acftref", rk, "NO-ENG", r["NO-ENG"]))
        try:
            b.put("AircraftModel", uri, "cruise_speed", float(r["SPEED"]), b.ref_leaf("faa_acftref", rk, "SPEED", r["SPEED"]))
        except ValueError:
            pass
        b.put("AircraftModel", uri, "weight_class", r["AC-WEIGHT"].strip(), b.ref_leaf("faa_acftref", rk, "AC-WEIGHT", r["AC-WEIGHT"]))
        b.put("AircraftModel", uri, "type_aircraft", r["TYPE-ACFT"].strip(), b.ref_leaf("faa_acftref", rk, "TYPE-ACFT", r["TYPE-ACFT"]))
        b.link("AircraftModel", "manufacturer", uri, f"ent://manufacturer/{slug(mfr)}",
               b.ref_leaf("faa_acftref", rk, "MFR", r["MFR"]))
    for mfr, atoms in sorted(mfr_atoms.items()):
        b.put("Manufacturer", f"ent://manufacturer/{slug(mfr)}", "name", mfr, b.ref_sum(atoms))

    # ---------------- Aircraft + Agent registrants (faa_master)
    agent_atoms: dict[str, list[str]] = {}
    nnum_rows: dict[str, list[dict]] = {}
    aircraft_uri: dict[str, str] = {}
    for r in t["faa_master"].to_dict("records"):
        nnum = r["N-NUMBER"].strip()
        serial = r["SERIAL NUMBER"].strip()
        rk = f"{nnum}|{serial}"
        uri = f"ent://aircraft/{slug(rk)}"
        aircraft_uri[rk] = uri
        cert = parse_yyyymmdd(r["CERT ISSUE DATE"])
        exp = parse_yyyymmdd(r["EXPIRATION DATE"])
        reg_window = window(cert, exp)
        nnum_rows.setdefault(nnum, []).append(
            {"rk": rk, "uri": uri, "cert": cert or 0, "window": reg_window}
        )
        tail_prov = b.ref_prod([
            b.atom("faa_master", rk, "N-NUMBER", r["N-NUMBER"]),
            b.atom("faa_master", rk, "CERT ISSUE DATE", r["CERT ISSUE DATE"]),
            b.atom("faa_master", rk, "EXPIRATION DATE", r["EXPIRATION DATE"]),
        ])
        b.put("Aircraft", uri, "tail_number", f"N{nnum}", tail_prov, valid=reg_window)
        b.put("Aircraft", uri, "serial_number", serial,
              b.ref_leaf("faa_master", rk, "SERIAL NUMBER", r["SERIAL NUMBER"]))
        if r["YEAR MFR"].strip().isdigit():
            b.put("Aircraft", uri, "year_mfr", int(r["YEAR MFR"]),
                  b.ref_leaf("faa_master", rk, "YEAR MFR", r["YEAR MFR"]))
        code = r["MFR MDL CODE"].strip()
        if code in model_uri:
            b.link("Aircraft", "model", uri, model_uri[code],
                   b.ref_leaf("faa_master", rk, "MFR MDL CODE", r["MFR MDL CODE"]))
        reg = r["REGISTRANT NAME"].strip()
        if reg:
            agent_atoms.setdefault(reg, []).append(
                b.atom("faa_master", rk, "REGISTRANT NAME", r["REGISTRANT NAME"])
            )
            reg_prov = b.ref_prod([
                b.atom("faa_master", rk, "REGISTRANT NAME", r["REGISTRANT NAME"]),
                b.atom("faa_master", rk, "CERT ISSUE DATE", r["CERT ISSUE DATE"]),
                b.atom("faa_master", rk, "EXPIRATION DATE", r["EXPIRATION DATE"]),
            ])
            b.link("Aircraft", "registrant", uri, f"ent://agent/{slug(reg)}", reg_prov,
                   valid=reg_window)
    for reg, atoms in sorted(agent_atoms.items()):
        b.put("Agent", f"ent://agent/{slug(reg)}", "name", reg, b.ref_sum(atoms))

    def aircraft_for(nnum: str, at: Optional[int]) -> Optional[str]:
        """Resolve a bare N-number to the airframe registered at `at` (else the
        latest registration) — the ER/temporal binding the pipeline performs."""
        rows = nnum_rows.get(nnum.lstrip("N").strip() if nnum.startswith("N") else nnum)
        if rows is None:
            rows = nnum_rows.get(nnum)
        if not rows:
            return None
        if at is not None:
            for row in rows:
                if row["window"].contains(at):
                    return row["uri"]
        return max(rows, key=lambda x: x["cert"])["uri"]

    # ---------------- Operators (folded across ERP / NTSB / ASRS spellings)
    op_atoms: dict[str, list[str]] = {}

    def operator_uri(raw_name: str, table: str, rk: str, column: str, raw_value: Any) -> Optional[str]:
        name = raw_name.strip()
        if not name:
            return None
        key = normalize_name(name)
        if not key:
            return None
        op_atoms.setdefault(key, []).append(b.atom(table, rk, column, raw_value))
        return f"ent://operator/{slug(key)}"

    # ---------------- IncidentReport (asrs_reports)
    for r in t["asrs_reports"].to_dict("records"):
        acn = r["ACN"].strip()
        rk = acn
        uri = f"ent://asrs/{slug(acn)}"
        b.put("IncidentReport", uri, "acn", acn, b.ref_leaf("asrs_reports", rk, "ACN", r["ACN"]))
        phase = r["FLIGHT PHASE"].strip()
        if phase:
            b.put("IncidentReport", uri, "flight_phase", phase,
                  b.ref_leaf("asrs_reports", rk, "FLIGHT PHASE", r["FLIGHT PHASE"]))
        alt_raw = r["ALTITUDE.AGL.SINGLE VALUE"].strip()
        if alt_raw:
            col = "ALTITUDE.AGL.SINGLE VALUE"
            prov = b.ref_leaf("asrs_reports", rk, col, r[col])
            if alt_raw.endswith("m"):
                b.put("IncidentReport", uri, "altitude_agl", float(alt_raw[:-1]) * FT_PER_M, prov)
                b.put("IncidentReport", uri, "altitude_agl_unit", "m", prov)
            else:
                b.put("IncidentReport", uri, "altitude_agl", float(alt_raw), prov)
                b.put("IncidentReport", uri, "altitude_agl_unit", "ft", prov)
        narrative = r["NARRATIVE"]
        b.put("IncidentReport", uri, "narrative", narrative,
              b.ref_leaf("asrs_reports", rk, "NARRATIVE", narrative))
        b.put("IncidentReport", uri, "synopsis", r["SYNOPSIS"],
              b.ref_leaf("asrs_reports", rk, "SYNOPSIS", r["SYNOPSIS"]))
        # narrative entity mentions -> aircraft links (extraction-grounded)
        narr_prov = b.ref_leaf("asrs_reports", rk, "NARRATIVE", narrative)
        for tail in sorted(set(TAIL_RE.findall(str(narrative)))):
            target = aircraft_for(tail, None)
            if target is not None:
                b.link("IncidentReport", "aircraft", uri, target, narr_prov)
        op = operator_uri(r["AIRCRAFT 1 OPERATOR"], "asrs_reports", rk,
                          "AIRCRAFT 1 OPERATOR", r["AIRCRAFT 1 OPERATOR"])
        if op:
            b.link("IncidentReport", "operator", uri, op,
                   b.ref_leaf("asrs_reports", rk, "AIRCRAFT 1 OPERATOR", r["AIRCRAFT 1 OPERATOR"]))

    # ---------------- AccidentEvent + Place (ntsb_events)
    place_atoms: dict[tuple[str, str], dict[str, list[str]]] = {}
    for r in t["ntsb_events"].to_dict("records"):
        ev = r["EV_ID"].strip()
        rk = ev
        uri = f"ent://ntsb/{slug(ev)}"
        b.put("AccidentEvent", uri, "ntsb_number", r["NTSB_NO"].strip(),
              b.ref_leaf("ntsb_events", rk, "NTSB_NO", r["NTSB_NO"]))
        b.put("AccidentEvent", uri, "ev_type", r["EV_TYPE"].strip(),
              b.ref_leaf("ntsb_events", rk, "EV_TYPE", r["EV_TYPE"]))
        b.put("AccidentEvent", uri, "damage", r["DAMAGE"].strip(),
              b.ref_leaf("ntsb_events", rk, "DAMAGE", r["DAMAGE"]))
        if r["INJ_TOT_F"].strip() != "":
            b.put("AccidentEvent", uri, "fatalities", int(r["INJ_TOT_F"]),
                  b.ref_leaf("ntsb_events", rk, "INJ_TOT_F", r["INJ_TOT_F"]))
        b.put("AccidentEvent", uri, "cause_narrative", r["NARR_CAUSE"],
              b.ref_leaf("ntsb_events", rk, "NARR_CAUSE", r["NARR_CAUSE"]))
        b.put("AccidentEvent", uri, "event_date", r["EV_DATE"].strip(),
              b.ref_leaf("ntsb_events", rk, "EV_DATE", r["EV_DATE"]))
        regn = r["ACFT_REGIST_NMBR"].strip()
        if regn:
            target = aircraft_for(regn, parse_iso(r["EV_DATE"]))
            if target is not None:
                b.link("AccidentEvent", "aircraft", uri, target,
                       b.ref_leaf("ntsb_events", rk, "ACFT_REGIST_NMBR", r["ACFT_REGIST_NMBR"]))
        op = operator_uri(r["OPERATOR"], "ntsb_events", rk, "OPERATOR", r["OPERATOR"])
        if op:
            b.link("AccidentEvent", "operator", uri, op,
                   b.ref_leaf("ntsb_events", rk, "OPERATOR", r["OPERATOR"]))
        city, state = r["EV_CITY"].strip(), r["EV_STATE"].strip()
        if state:
            pk = (city, state)
            pa = place_atoms.setdefault(pk, {"city": [], "state": []})
            pa["city"].append(b.atom("ntsb_events", rk, "EV_CITY", r["EV_CITY"]))
            pa["state"].append(b.atom("ntsb_events", rk, "EV_STATE", r["EV_STATE"]))
            b.link("AccidentEvent", "place", uri, f"ent://place/{slug(city + '|' + state)}",
                   b.ref_prod([pa["city"][-1], pa["state"][-1]]))
    for (city, state), atoms in sorted(place_atoms.items()):
        puri = f"ent://place/{slug(city + '|' + state)}"
        b.put("Place", puri, "city", city, b.ref_sum(atoms["city"]))
        b.put("Place", puri, "state", state, b.ref_sum(atoms["state"]))
        b.put("Place", puri, "place_name", city, b.ref_sum(atoms["city"]))

    # ---------------- WorkOrder + Component (maintenance_erp)
    comp_atoms: dict[tuple[str, str], list[str]] = {}
    for r in t["maintenance_erp"].to_dict("records"):
        wo = r["WORK_ORDER_ID"].strip()
        rk = wo
        uri = f"ent://wo/{slug(wo)}"
        b.put("WorkOrder", uri, "work_order_id", wo,
              b.ref_leaf("maintenance_erp", rk, "WORK_ORDER_ID", r["WORK_ORDER_ID"]))
        b.put("WorkOrder", uri, "action", r["ACTION"].strip(),
              b.ref_leaf("maintenance_erp", rk, "ACTION", r["ACTION"]))
        if r["LABOR_HOURS"].strip():
            b.put("WorkOrder", uri, "labor_hours", float(r["LABOR_HOURS"]),
                  b.ref_leaf("maintenance_erp", rk, "LABOR_HOURS", r["LABOR_HOURS"]))
        cost = parse_cost(r["COST"])
        if cost is not None:
            b.put("WorkOrder", uri, "cost", cost,
                  b.ref_leaf("maintenance_erp", rk, "COST", r["COST"]))
        for col, prop in (("OPEN_DATE", "open_date"), ("CLOSE_DATE", "close_date")):
            if r[col].strip():
                b.put("WorkOrder", uri, prop, r[col].strip(),
                      b.ref_leaf("maintenance_erp", rk, col, r[col]))
        target = aircraft_for(r["TAIL_NUMBER"].strip(), parse_iso(r["OPEN_DATE"]))
        if target is not None:
            b.link("WorkOrder", "aircraft", uri, target,
                   b.ref_leaf("maintenance_erp", rk, "TAIL_NUMBER", r["TAIL_NUMBER"]))
        comp, ata = r["COMPONENT"].strip(), r["ATA_CHAPTER"].strip()
        if comp:
            ck = (comp, ata)
            comp_atoms.setdefault(ck, []).append(
                b.atom("maintenance_erp", rk, "COMPONENT", r["COMPONENT"])
            )
            b.link("WorkOrder", "component", uri, f"ent://component/{slug(comp + '|' + ata)}",
                   b.ref_leaf("maintenance_erp", rk, "COMPONENT", r["COMPONENT"]))
        op = operator_uri(r["OPERATOR_NAME"], "maintenance_erp", rk,
                          "OPERATOR_NAME", r["OPERATOR_NAME"])
        if op:
            b.link("WorkOrder", "operator", uri, op,
                   b.ref_leaf("maintenance_erp", rk, "OPERATOR_NAME", r["OPERATOR_NAME"]))
    for (comp, ata), atoms in sorted(comp_atoms.items()):
        comp_uri = f"ent://component/{slug(comp + '|' + ata)}"
        b.put("Component", comp_uri, "component_name", comp, b.ref_sum(atoms))
        b.put("Component", comp_uri, "ata_chapter", ata, b.ref_sum(atoms))

    for key, atoms in sorted(op_atoms.items()):
        b.put("Operator", f"ent://operator/{slug(key)}", "name", key, b.ref_sum(atoms))

    return b.commit_all()


def extend_gold_ontology(onto: Ontology) -> Ontology:
    """Add the pipeline-recorded source-unit annotation property
    (altitude_agl_unit) to IncidentReport — what unit/dimension induction
    (§3.2) records when it conforms a mixed-unit column."""
    import dataclasses

    c = onto.by_name("IncidentReport")
    if c is None:
        raise ValueError("ontology has no IncidentReport class to extend")
    if any(p.name == "altitude_agl_unit" for p in c.properties):
        return onto
    extra = PropertyDef(
        uri=property_uri(c.uri, "altitude_agl_unit"),
        name="altitude_agl_unit",
        datatype=Datatype.STRING,
        definition="source lexical unit the altitude was recorded in ('ft' | 'm')",
    )
    onto.replace_class(dataclasses.replace(c, properties=c.properties + (extra,)))
    return onto
