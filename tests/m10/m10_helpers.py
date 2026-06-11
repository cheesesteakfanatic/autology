"""M10 test helpers: deterministic base HEARTH store over the gold aviation
ontology (~207 entities), the fixed snapshot-queryability battery, and the
seeded random-valid-operator generator for the replay harness."""

from __future__ import annotations

import random
from typing import Any, Optional

from ontoforge.contracts import (
    Datatype,
    DecisionKind,
    Interval,
    Layer,
    Ontology,
    SpineProfile,
    TierScore,
    ValueCell,
    leaf,
    make_cell_atom,
)
from ontoforge.hearth import Hearth
from ontoforge.ledger import SqliteLedger
from ontoforge.spine import DecisionSpine
from ontoforge.temper import (
    AddClass,
    AddFacet,
    AddProperty,
    DemoteClass,
    Generalize,
    MergeClasses,
    PromoteProperty,
    RenameClass,
    RenameProperty,
    RetireClass,
    RetireFacet,
    RetypeProperty,
    Specialize,
    SplitClass,
    StructuredQuery,
    facet_params,
    is_retired,
    storage_key,
)

G = "onto://gold/aviation"
BASE_T = 1_000_000  # base-store commit epoch (µs)


def mint_prov(ledger: SqliteLedger, key: str = "m10-base") -> str:
    atom = make_cell_atom("m10-test", "table", key, "col", "evidence")
    ledger.register_atoms([atom])
    return ledger.intern(leaf(atom.atom_id))


def ent(cls: str, i: int) -> str:
    return f"ent://aviation/{cls}/{i}"


def _cell(entity: str, prop: str, value: Any, prov: str) -> ValueCell:
    return ValueCell(entity_uri=entity, prop=prop, value=value, valid=Interval(0),
                     system=Interval(0), prov_ref=prov, confidence=1.0, src_rank=1)


def build_base_store(root, ledger: SqliteLedger) -> Hearth:
    """Deterministic ~207-entity ENTITY-layer store over the gold classes.
    Link properties hold the target entity URI as the cell value."""
    h = Hearth(root, ledger)
    prov = mint_prov(ledger)
    t = [BASE_T]

    def commit(cls_uri: str, rows: list[tuple[str, dict[str, Any]]]) -> None:
        cells = [_cell(e, k, v, prov) for e, row in rows for k, v in sorted(row.items())]
        t[0] += 1
        h.commit(Layer.ENTITY, cls_uri, cells, now=t[0])

    phases = ["CLIMB", "CRUISE", "DESCENT", "TAXI"]
    actions = ["REPLACE", "INSPECT", "REPAIR"]
    damage = ["MINOR", "SUBST", "DEST"]
    weights = ["CLASS 1", "CLASS 2", "CLASS 3"]

    commit(f"{G}/Manufacturer", [
        (ent("mfr", i), {"name": f"Mfr {i}", "name_variants": f"MFR-{i}", "org_kind": "manufacturer"})
        for i in range(6)
    ])
    commit(f"{G}/Operator", [
        (ent("op", i), {"name": f"Operator {i}", "org_kind": "carrier"}) for i in range(8)
    ])
    commit(f"{G}/AircraftModel", [
        (ent("model", i), {
            "model_name": f"Model-{i}", "mfr_mdl_code": f"C{i:03d}",
            "manufacturer": ent("mfr", i % 6), "seats": 50 + i * 15, "engine_count": 2,
            "weight_class": weights[i % 3], "cruise_speed": float(200 + i * 25),
            "type_aircraft": "fixed" if i % 4 else "rotor",
        }) for i in range(12)
    ])
    commit(f"{G}/Aircraft", [
        (ent("ac", i), {
            "serial_number": f"SN{i:04d}", "tail_number": f"N{100 + i}",
            "year_mfr": 1990 + i % 30, "mode_s_code": f"A{i:05d}",
            "model": ent("model", i % 12), "registrant": ent("op", i % 8),
        }) for i in range(40)
    ])
    commit(f"{G}/IncidentReport", [
        (ent("ir", i), {
            "acn": f"ACN{i:04d}", "altitude_agl": 250.0 * ((i * 7) % 40 + 1),
            "flight_phase": phases[i % 4], "narrative": f"narrative {i}",
            "synopsis": f"synopsis {i}", "event_date": f"2024-{(i % 12) + 1:02d}-15",
            "aircraft": ent("ac", i % 40), "operator": ent("op", i % 8),
        }) for i in range(40)
    ])
    commit(f"{G}/AccidentEvent", [
        (ent("ae", i), {
            "ntsb_number": f"NTSB{i:04d}", "ev_type": "ACC" if i % 2 else "INC",
            "damage": damage[i % 3], "fatalities": i % 4,
            "cause_narrative": f"cause {i}", "event_date": f"2023-{(i % 12) + 1:02d}-20",
            "aircraft": ent("ac", (i * 3) % 40),
        }) for i in range(25)
    ])
    commit(f"{G}/WorkOrder", [
        (ent("wo", i), {
            "work_order_id": f"WO{i:04d}", "aircraft": ent("ac", i % 40),
            "action": actions[i % 3], "labor_hours": 0.5 * ((i % 16) + 1),
            "cost": 0.25 * (100 + i * 13), "open_date": f"2024-{(i % 12) + 1:02d}-01",
        }) for i in range(50)
    ])
    commit(f"{G}/Registration", [
        (ent("reg", i), {
            "aircraft": ent("ac", i), "status_code": "V" if i % 5 else "D",
            "cert_issue_date": f"20{10 + i % 15}-06-01",
        }) for i in range(15)
    ])
    commit(f"{G}/Airport", [
        (ent("apt", i), {"iata": f"X{i}A", "place_name": f"Airport {i}", "city": f"City {i}", "state": "TX"})
        for i in range(6)
    ])
    commit(f"{G}/Component", [
        (ent("cmp", i), {"component_name": f"Component {i}", "ata_chapter": f"{20 + i}"})
        for i in range(5)
    ])
    return h


# ----------------------------------------------------------------- battery

BATTERY: tuple[StructuredQuery, ...] = (
    StructuredQuery(f"{G}/IncidentReport", filters=(("altitude_agl", ">", 5000.0),), projection=("acn", "altitude_agl")),
    StructuredQuery(f"{G}/WorkOrder", filters=(("cost", "<=", 50.0),), projection=("work_order_id", "cost")),
    StructuredQuery(f"{G}/Aircraft", projection=("tail_number", "serial_number")),
    StructuredQuery(f"{G}/SafetyEvent", projection=("event_date",)),
    StructuredQuery(f"{G}/AccidentEvent", filters=(("fatalities", ">=", 1),), projection=("ntsb_number", "fatalities")),
    StructuredQuery(f"{G}/AircraftModel", filters=(("seats", ">", 120),), projection=("model_name", "seats")),
    StructuredQuery(f"{G}/WorkOrder", filters=(("action", "==", "REPLACE"),), projection=("work_order_id", "labor_hours")),
    StructuredQuery(f"{G}/Registration", filters=(("status_code", "==", "V"),), projection=("aircraft",)),
    StructuredQuery(f"{G}/Operator", projection=("name",)),
    StructuredQuery(f"{G}/IncidentReport", filters=(("flight_phase", "in", ("CRUISE", "CLIMB")),), projection=("acn", "flight_phase")),
)


def auto_accept_spine() -> DecisionSpine:
    """A spine whose T0 rule deterministically auto-accepts TEMPER's gated
    structural ops (P(yes)=1.0 clears tau_high even at maximal impact widening,
    which is capped at 1-1e-4)."""
    spine = DecisionSpine(SpineProfile())
    spine.register_rule(DecisionKind.SM, lambda req: TierScore(scores={"no": 0.0, "yes": 1.0}))
    return spine


# ----------------------------------------------- random valid-op generation

LINEAR_FACTORS = ["linear:2.0:0.0", "linear:0.5:0.0", "linear:4.0:0.0", "linear:0.25:0.0"]


def _own_props(c, *, links: Optional[bool] = None):
    out = []
    for p in c.properties:
        if p.name.startswith("__temper"):
            continue
        if links is not None and p.is_link != links:
            continue
        out.append(p)
    return out


def candidate_ops(onto: Ontology, adapter, rng: random.Random, n: int) -> list:
    """Deterministic candidate operator list for one step (preconditions are
    mostly satisfied by construction; the harness re-checks via apply)."""
    cands: list = []
    live = [c for c in sorted(onto.iter_classes(), key=lambda c: c.uri) if not is_retired(c)]
    live_uris = {c.uri for c in live}
    from ontoforge.temper.ops import children_of, inbound_ranges

    leafy = [c for c in live if not children_of(onto, c.uri) and not inbound_ranges(onto, c.uri)]

    for c in live:
        cands.append(RenameClass(uri=c.uri, new_name=f"{c.name}_r{n}"))
        props = _own_props(c)
        if props:
            p = props[n % len(props)]
            cands.append(RenameProperty(class_uri=c.uri, prop_name=p.name, new_name=f"{p.name}_r{n}"))
            cands.append(AddFacet(class_uri=c.uri, shape=facet_params(_mk_shape(p.name))))
        if c.shapes:
            cands.append(RetireFacet(class_uri=c.uri, shape=facet_params(c.shapes[n % len(c.shapes)])))
        cands.append(AddProperty(class_uri=c.uri, name=f"extra_{n}"))
        # retype: floats -> linear (exactly-representable factors), ints -> cast
        for p in _own_props(c, links=False):
            if p.datatype is Datatype.FLOAT:
                cands.append(RetypeProperty(class_uri=c.uri, prop_name=p.name, new_datatype="float",
                                            conversion_spec=LINEAR_FACTORS[n % len(LINEAR_FACTORS)],
                                            new_unit=p.unit))
            elif p.datatype is Datatype.INTEGER:
                cands.append(RetypeProperty(class_uri=c.uri, prop_name=p.name, new_datatype="float",
                                            conversion_spec="int_to_float", new_unit=p.unit))
        # generalize own non-link props to a parent that lacks them
        for parent_uri in c.parents:
            if parent_uri not in live_uris:
                continue
            for p in _own_props(c, links=False):
                from ontoforge.temper import resolve_prop
                if resolve_prop(onto, parent_uri, p.name) is None:
                    cands.append(Generalize(class_uri=c.uri, parent_uri=parent_uri, prop_name=p.name))
        # specialize: only when there are NO violators (queryability-preserving)
        if adapter is not None:
            for child_uri in children_of(onto, c.uri):
                if child_uri not in live_uris:
                    continue
                for p in _own_props(c, links=False):
                    op = Specialize(parent_uri=c.uri, child_uri=child_uri, prop_name=p.name)
                    try:
                        if not op.violators(onto, adapter):
                            cands.append(op)
                    except Exception:
                        pass

    cands.append(AddClass(uri=f"onto://temper/cls/{n}", name=f"Cls{n}",
                          parent=rng.choice([None] + sorted(live_uris))))

    if adapter is not None:
        for c in leafy:
            extent = adapter.extent_own(c.uri)
            if not extent:
                continue
            # split: total non-link discriminator
            for p in _own_props(c, links=False):
                key = storage_key(p)
                values = [row[key].value for row in extent.values() if key in row]
                if len(values) != len(extent):
                    continue
                if all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in values):
                    pivot: Any = sorted(values)[len(values) // 2]
                elif all(isinstance(v, str) for v in values):
                    pivot = sorted(values)[len(values) // 2]
                else:
                    continue
                cands.append(SplitClass(
                    uri=c.uri,
                    parts=((f"{c.uri}~p{n}a", f"{c.name}_A{n}"), (f"{c.uri}~p{n}b", f"{c.name}_B{n}")),
                    discriminator=(p.name, "<=", pivot),
                ))
                break
            # promote: string-valued own prop
            for p in _own_props(c, links=False):
                if p.datatype in (Datatype.STRING, Datatype.TEXT):
                    cands.append(PromoteProperty(class_uri=c.uri, prop_name=p.name,
                                                 new_class_uri=f"onto://temper/promoted/{n}",
                                                 new_class_name=f"Promoted{n}"))
                    break
        # merge: leaf pairs with identical parent sets and disjoint extents
        pairs = []
        for i, c1 in enumerate(leafy):
            for c2 in leafy[i + 1:]:
                if set(c1.parents) == set(c2.parents):
                    pairs.append((c1, c2))
        if pairs:
            c1, c2 = pairs[n % len(pairs)]
            cands.append(MergeClasses(c1_uri=c1.uri, c2_uri=c2.uri,
                                      new_uri=f"onto://temper/merged/{n}", new_name=f"Merged{n}",
                                      origin_key=f"__temper_origin@{n}"))
        # demote: previously promoted classes (link from owner into onto://temper/promoted/)
        for c in live:
            for p in _own_props(c, links=True):
                if p.range_class and p.range_class.startswith("onto://temper/promoted/"):
                    cands.append(DemoteClass(owner_class_uri=c.uri, link_prop=p.name,
                                             class_uri=p.range_class))
    # retire (kept rare-ish: tail of the list; never retire a class some other
    # candidate this step depends on — the engine precondition arbitrates)
    for c in live:
        cands.append(RetireClass(uri=c.uri))
    return cands


def _mk_shape(prop_name: str):
    from ontoforge.contracts import ShapeConstraint

    return ShapeConstraint(prop=prop_name, min_count=0)


LABEL_ONLY_KINDS = ("RenameClass", "RenameProperty", "AddClass", "AddProperty", "AddFacet", "RetireFacet", "RetireClass")


def run_sequence(engine, rng: random.Random, length: int, *, label_only: bool = False) -> list:
    """Apply `length` random valid operators; returns the MigrationReports."""
    from ontoforge.temper import OperatorDeferred, PreconditionError

    reports = []
    for step in range(length):
        n = rng.randrange(10_000)
        cands = candidate_ops(engine.ontology, engine.adapter, rng, n)
        if label_only:
            cands = [op for op in cands if op.op_type in LABEL_ONLY_KINDS]
        rng.shuffle(cands)
        for op in cands:
            try:
                reports.append(engine.apply(op, now=BASE_T + 10_000 + step))
                break
            except (PreconditionError, OperatorDeferred):
                continue
        else:  # pragma: no cover - candidate pool exhausted
            raise AssertionError("no valid operator found")
    return reports
