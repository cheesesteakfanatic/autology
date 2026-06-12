"""Shared M11/M14 test world: a populated HEARTH built from the REAL aviation
estate fixtures with real provenance, the way tests/integration/
test_wave2_induction_seam.py does it — every committed cell's prov_ref is an
interned Leaf over an atom minted with make_cell_atom from the actual source
cell, in one shared SqliteLedger.

The §11.3 de-risking posture applies: the FROZEN gold mini-ontology stands in
for the induced one (same contracts.Ontology interface), evolved by one real
TEMPER operator so the morphism ledger is non-empty. Transforms and a decision
record are registered so every AMBER bundle section has real content.

Everything is deterministic: fixed `now` instants, sorted row selection,
fixed namespaces.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any

from ontoforge.contracts import (
    DecisionResult,
    Interval,
    Layer,
    LinkCell,
    Tier,
    TransformDef,
    ValueCell,
    leaf,
    make_cell_atom,
)
from ontoforge.contracts.transforms import Layer as TLayer
from ontoforge.estates import load_estate, load_gold_ontology
from ontoforge.hearth import Hearth
from ontoforge.ledger import SqliteLedger
from ontoforge.temper import AddProperty, MorphismLedger
from ontoforge.transforms import TransformRegistry

NS = "onto://gold/aviation"
AIRCRAFT = f"{NS}/Aircraft"
MODEL = f"{NS}/AircraftModel"
OPERATOR = f"{NS}/Operator"
AGENT = f"{NS}/Agent"

N_AIRCRAFT = 60
N_OPERATORS = 10

# fixed, strictly increasing system-time instants (µs since epoch)
T0 = 1_700_000_000_000_000
T_STEP = 1_000_000

_EPOCH = date(1970, 1, 1).toordinal()
_US_PER_DAY = 86_400 * 1_000_000


def yyyymmdd_to_instant(s: str) -> int:
    s = s.strip()
    d = date(int(s[:4]), int(s[4:6]), int(s[6:8]))
    return (d.toordinal() - _EPOCH) * _US_PER_DAY


def slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", text.strip()).strip("-")


def _cell(entity: str, prop: str, value: Any, prov: str, valid: Interval | None = None) -> ValueCell:
    return ValueCell(
        entity_uri=entity,
        prop=prop,
        value=value,
        valid=valid if valid is not None else Interval(0),
        system=Interval(0),
        prov_ref=prov,
        confidence=1.0,
        src_rank=1,
    )


def build_world(root: Path) -> dict[str, Any]:
    estate = load_estate()
    gold = load_gold_ontology()

    ledger = SqliteLedger(":memory:")

    # --- TEMPER: one real operator evolves gold v1 -> v2; the morphism ledger
    # records it as a 'temper-op' artifact (real provenance inside).
    op = AddProperty(class_uri=AIRCRAFT, name="notes", datatype="string")
    op.precondition(gold, None)
    ontology = op.rewrite(gold)
    ontology.version = gold.version + 1
    morphisms = MorphismLedger(ledger)
    morphism_record = morphisms.record(
        op, gold.version, ontology.version, {"cells_touched": 0}, now=T0
    )

    hearth = Hearth(root / "hearth", ledger, ontology)
    clock = [T0]

    def tick() -> int:
        clock[0] += T_STEP
        return clock[0]

    def mint(table: str, source_id: str, rkey: str, column: str, value: Any) -> str:
        atom = make_cell_atom(source_id, table, rkey, column, value)
        ledger.register_atoms([atom])
        return ledger.intern(leaf(atom.atom_id))

    faa_sid = estate["metadata"]["tables"]["faa_master"]["source_id"]
    ref_sid = estate["metadata"]["tables"]["faa_acftref"]["source_id"]

    master = estate["tables"]["faa_master"].to_dict("records")
    master.sort(key=lambda r: str(r["N-NUMBER"]).strip())
    rows = master[:N_AIRCRAFT]

    acftref = {str(r["CODE"]).strip(): r for r in estate["tables"]["faa_acftref"].to_dict("records")}

    # ---------------- AircraftModel entities (referenced models only)
    model_cells: list[ValueCell] = []
    model_uris: dict[str, str] = {}
    codes = sorted({str(r["MFR MDL CODE"]).strip() for r in rows} & set(acftref))
    for code in codes:
        r = acftref[code]
        uri = f"ent://model/{slug(code)}"
        model_uris[code] = uri
        rkey = code

        def m(col: str) -> str:
            return mint("faa_acftref", ref_sid, rkey, col, r[col])

        model_cells += [
            _cell(uri, "mfr_mdl_code", code, m("CODE")),
            _cell(uri, "model_name", str(r["MODEL"]).strip(), m("MODEL")),
            _cell(uri, "seats", int(str(r["NO-SEATS"]).strip()), m("NO-SEATS")),
            _cell(uri, "engine_count", int(str(r["NO-ENG"]).strip()), m("NO-ENG")),
            _cell(uri, "weight_class", str(r["AC-WEIGHT"]).strip(), m("AC-WEIGHT")),
            _cell(uri, "cruise_speed", float(str(r["SPEED"]).strip()), m("SPEED")),
            _cell(uri, "type_aircraft", str(r["TYPE-ACFT"]).strip(), m("TYPE-ACFT")),
        ]
    hearth.commit(Layer.ENTITY, MODEL, model_cells, now=tick())

    # ---------------- Aircraft entities
    aircraft_cells: list[ValueCell] = []
    links: list[LinkCell] = []
    aircraft_meta: dict[str, dict[str, Any]] = {}
    known_uri = None
    for r in rows:
        nnum = str(r["N-NUMBER"]).strip()
        serial = str(r["SERIAL NUMBER"]).strip()
        code = str(r["MFR MDL CODE"]).strip()
        rkey = f"{nnum}|{serial}"
        uri = f"ent://aircraft/{slug(nnum)}-{slug(serial)}"

        def a(col: str, value: Any = None) -> str:
            return mint("faa_master", faa_sid, rkey, col, r[col] if value is None else value)

        tail = f"N{nnum}"
        cells = [
            _cell(uri, "tail_number", tail, a("N-NUMBER")),
            _cell(uri, "serial_number", serial, a("SERIAL NUMBER")),
            _cell(uri, "mode_s_code", str(r["MODE S CODE"]).strip(), a("MODE S CODE")),
        ]
        year_raw = str(r["YEAR MFR"]).strip()
        if year_raw.isdigit():
            cells.append(_cell(uri, "year_mfr", int(year_raw), a("YEAR MFR")))
        registrant = str(r["REGISTRANT NAME"]).strip()
        cert, expire = str(r["CERT ISSUE DATE"]).strip(), str(r["EXPIRATION DATE"]).strip()
        meta: dict[str, Any] = {
            "row_key": rkey,
            "tail": tail,
            "serial": serial,
            "code": code,
            "year": int(year_raw) if year_raw.isdigit() else None,
            "registrant": registrant,
        }
        if (
            known_uri is None
            and registrant
            and len(cert) == 8
            and len(expire) == 8
            and cert.isdigit()
            and expire.isdigit()
            and yyyymmdd_to_instant(cert) < yyyymmdd_to_instant(expire)
            and code in model_uris
        ):
            known_uri = uri
            t1, t2 = yyyymmdd_to_instant(cert), yyyymmdd_to_instant(expire)
            # bitemporal registrant history: REGISTRANT held [t1, t2), then a
            # successor holds [t2, open)
            successor = f"{registrant} SUCCESSOR LLC"
            cells.append(
                _cell(uri, "registrant_name", registrant, a("REGISTRANT NAME"), Interval(t1, t2))
            )
            cells.append(
                _cell(
                    uri,
                    "registrant_name",
                    successor,
                    mint("faa_master", faa_sid, rkey, "REGISTRANT NAME", successor),
                    Interval(t2),
                )
            )
            meta.update(registrant_valid=Interval(t1, t2), successor=successor, t_mid=(t1 + t2) // 2)
        elif registrant:
            cells.append(_cell(uri, "registrant_name", registrant, a("REGISTRANT NAME")))
        aircraft_cells.extend(cells)
        if code in model_uris:
            links.append(
                LinkCell(
                    subject_uri=uri,
                    predicate="model",
                    object_uri=model_uris[code],
                    valid=Interval(0),
                    system=Interval(0),
                    prov_ref=a("MFR MDL CODE"),
                )
            )
        aircraft_meta[uri] = meta
    assert known_uri is not None, "no aircraft row with a usable registration window"
    hearth.commit(Layer.ENTITY, AIRCRAFT, aircraft_cells, now=tick())
    hearth.commit_links(AIRCRAFT, "model", links, now=tick())

    # a system-time supersession: corrected year for the known aircraft
    km = aircraft_meta[known_uri]
    t_before_correction = tick()
    if km["year"] is not None:
        corrected = km["year"] + 1
        prov = mint("faa_master", faa_sid, km["row_key"], "YEAR MFR", str(corrected))
        hearth.commit(
            Layer.ENTITY, AIRCRAFT, [_cell(known_uri, "year_mfr", corrected, prov)], now=tick()
        )
        km["year_corrected"] = corrected

    # ---------------- Operator entities (subsumption: Operator < Organization < Agent)
    operator_cells: list[ValueCell] = []
    operator_uris: list[str] = []
    seen: list[str] = []
    for r in rows:
        name = str(r["REGISTRANT NAME"]).strip()
        if name and name not in seen:
            seen.append(name)
        if len(seen) == N_OPERATORS:
            break
    for name in seen:
        src_row = next(r for r in rows if str(r["REGISTRANT NAME"]).strip() == name)
        rkey = f"{str(src_row['N-NUMBER']).strip()}|{str(src_row['SERIAL NUMBER']).strip()}"
        uri = f"ent://operator/{slug(name)}"
        operator_uris.append(uri)
        prov = mint("faa_master", faa_sid, rkey, "REGISTRANT NAME", name)
        operator_cells.append(_cell(uri, "name", name, prov))
        operator_cells.append(_cell(uri, "org_kind", "corporate", prov))
    hearth.commit(Layer.ENTITY, OPERATOR, operator_cells, now=tick())

    # ---------------- transforms: two real, readable, DSL-valid bodies
    registry = TransformRegistry(ledger)
    t_conform = TransformDef(
        name="conform_faa_master",
        inputs=("raw.faa_master",),
        output="conformed.aircraft",
        sql=(
            'SELECT TRIM("N-NUMBER") AS n_number, TRIM("SERIAL NUMBER") AS serial_number, '
            'UPPER(TRIM("REGISTRANT NAME")) AS registrant_name, '
            'CAST("YEAR MFR" AS INTEGER) AS year_mfr FROM raw.faa_master'
        ),
        output_layer=TLayer.CONFORMED,
        description="Strip FAA fixed-width padding; type the year column.",
    )
    t_models = TransformDef(
        name="conform_acftref_models",
        inputs=("raw.faa_acftref",),
        output="conformed.aircraft_model",
        sql=(
            'SELECT TRIM("CODE") AS mfr_mdl_code, TRIM("MODEL") AS model_name, '
            'CAST("NO-SEATS" AS INTEGER) AS seats, CAST("NO-ENG" AS INTEGER) AS engine_count '
            "FROM raw.faa_acftref"
        ),
        output_layer=TLayer.CONFORMED,
        description="Typed model reference table from FAA ACFTREF.",
    )
    fingerprints = [registry.register(t_conform), registry.register(t_models)]

    # ---------------- one ER decision record (the DECISION ledger extract)
    decision_atom = make_cell_atom(faa_sid, "faa_master", km["row_key"], "N-NUMBER", rows[0]["N-NUMBER"])
    ledger.register_atoms([decision_atom])
    ledger.append_decision(
        DecisionResult(
            decision_id=f"er:aircraft:{km['tail']}",
            outcome="match",
            confidence=0.97,
            conformal_set=("match",),
            tier=Tier.T1,
            cost_tokens=0,
            rationale="exact serial+model agreement",
        ),
        prov_atoms=(decision_atom.atom_id,),
    )

    return {
        "estate": estate,
        "ledger": ledger,
        "hearth": hearth,
        "ontology": ontology,
        "gold": gold,
        "morphism_record": morphism_record,
        "registry": registry,
        "transform_fingerprints": fingerprints,
        "aircraft_meta": aircraft_meta,
        "model_uris": model_uris,
        "operator_uris": operator_uris,
        "known_uri": known_uri,
        "known": km,
        "t_before_correction": t_before_correction,
        "n_aircraft": len(rows),
        "n_models": len(codes),
        "n_links": len(links),
        "n_operators": len(seen),
    }
