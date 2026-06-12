"""Shared CLI pipeline helpers (ontology JSON round-trip, gold matching,
HEARTH materialization) — the wave-2 seam approach packaged for the product CLI.

Ontology persistence format (documented per the CLI spec): a plain-JSON dump of
``contracts.Ontology`` — every ClassDef/PropertyDef/ShapeConstraint field, with
``Dimension`` as its exponent vector and enums by value. This is intentionally
*not* the gold-loader dialect: induced classes carry intent-hash URIs and
confidences that the gold dialect (name-resolved references, confidence 1.0)
cannot represent losslessly.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Optional

from ontoforge.contracts import (
    ClassDef,
    Datatype,
    Interval,
    Layer,
    Ontology,
    PropertyDef,
    ShapeConstraint,
    ValueCell,
    leaf,
    make_cell_atom,
)
from ontoforge.contracts.units import Dimension

# ----------------------------------------------------- ontology JSON round-trip


def _prop_to_obj(p: PropertyDef) -> dict[str, Any]:
    return {
        "uri": p.uri,
        "name": p.name,
        "datatype": p.datatype.value,
        "is_link": p.is_link,
        "range_class": p.range_class,
        "dimension": list(p.dimension.exps) if p.dimension is not None else None,
        "unit": p.unit,
        "cardinality": p.cardinality,
        "functional": p.functional,
        "synonyms": list(p.synonyms),
        "definition": p.definition,
    }


def _prop_from_obj(o: dict[str, Any]) -> PropertyDef:
    return PropertyDef(
        uri=o["uri"],
        name=o["name"],
        datatype=Datatype(o["datatype"]),
        is_link=o["is_link"],
        range_class=o["range_class"],
        dimension=Dimension(tuple(o["dimension"])) if o["dimension"] is not None else None,
        unit=o["unit"],
        cardinality=o["cardinality"],
        functional=o["functional"],
        synonyms=tuple(o["synonyms"]),
        definition=o["definition"],
    )


def _shape_to_obj(s: ShapeConstraint) -> dict[str, Any]:
    return {
        "prop": s.prop,
        "min_count": s.min_count,
        "max_count": s.max_count,
        "datatype": s.datatype.value if s.datatype else None,
        "pattern": s.pattern,
        "in_values": list(s.in_values) if s.in_values else None,
        "min_value": s.min_value,
        "max_value": s.max_value,
        "unit": s.unit,
    }


def _shape_from_obj(o: dict[str, Any]) -> ShapeConstraint:
    return ShapeConstraint(
        prop=o["prop"],
        min_count=o["min_count"],
        max_count=o["max_count"],
        datatype=Datatype(o["datatype"]) if o["datatype"] else None,
        pattern=o["pattern"],
        in_values=tuple(o["in_values"]) if o["in_values"] else None,
        min_value=o["min_value"],
        max_value=o["max_value"],
        unit=o["unit"],
    )


def ontology_to_json(onto: Ontology) -> dict[str, Any]:
    return {
        "format": "ontoforge.cli/ontology-v1",
        "version": onto.version,
        "classes": [
            {
                "uri": c.uri,
                "name": c.name,
                "parents": list(c.parents),
                "properties": [_prop_to_obj(p) for p in c.properties],
                "shapes": [_shape_to_obj(s) for s in c.shapes],
                "definition": c.definition,
                "intent_hash": c.intent_hash,
                "is_event": c.is_event,
                "confidence": c.confidence,
                "prov_ref": c.prov_ref,
                "disjoint_with": list(c.disjoint_with),
            }
            for _, c in sorted(onto.classes.items())
        ],
    }


def ontology_from_json(data: dict[str, Any]) -> Ontology:
    onto = Ontology(version=int(data.get("version", 0)))
    for c in data["classes"]:
        onto.add(
            ClassDef(
                uri=c["uri"],
                name=c["name"],
                parents=tuple(c["parents"]),
                properties=tuple(_prop_from_obj(p) for p in c["properties"]),
                shapes=tuple(_shape_from_obj(s) for s in c["shapes"]),
                definition=c["definition"],
                intent_hash=c["intent_hash"],
                is_event=c["is_event"],
                confidence=c["confidence"],
                prov_ref=c["prov_ref"],
                disjoint_with=tuple(c["disjoint_with"]),
            )
        )
    return onto


def save_ontology(onto: Ontology, path: Path) -> None:
    path.write_text(json.dumps(ontology_to_json(onto), indent=1, sort_keys=True), encoding="utf-8")


def load_ontology(path: Path) -> Ontology:
    return ontology_from_json(json.loads(path.read_text(encoding="utf-8")))


# ------------------------------------------------------- light gold matching

_SPLIT_RE = re.compile(r"[^a-z0-9]+")


def _tokens(name: str) -> frozenset[str]:
    return frozenset(t for t in _SPLIT_RE.split(name.lower()) if t)


def _class_tokens(c: ClassDef) -> frozenset[str]:
    toks: set[str] = set(_tokens(c.name))
    for p in c.properties:
        toks |= _tokens(p.name)
        for s in p.synonyms:
            toks |= _tokens(s)
    return frozenset(toks)


def match_to_gold(
    induced: Ontology, gold: Ontology, threshold: float = 0.25
) -> tuple[float, float, dict[str, str]]:
    """Greedy token-Jaccard matcher: gold class name -> best induced class name.

    Lighter than the tests/m4 comparator (which is test-harness code and cannot
    be imported from the product); used only for CLI reporting, not for gates.
    Returns (precision, recall, gold_name -> induced_name).
    """
    ind = [(c.name, _class_tokens(c)) for c in induced.iter_classes()]
    matches: dict[str, str] = {}
    used: set[str] = set()
    scored: list[tuple[float, str, str]] = []
    for g in gold.iter_classes():
        gt = _class_tokens(g)
        for iname, it in ind:
            inter = len(gt & it)
            union = len(gt | it) or 1
            sc = inter / union
            if sc >= threshold:
                scored.append((sc, g.name, iname))
    for sc, gname, iname in sorted(scored, key=lambda t: (-t[0], t[1], t[2])):
        if gname in matches or iname in used:
            continue
        matches[gname] = iname
        used.add(iname)
    precision = len(used) / len(ind) if ind else 0.0
    recall = len(matches) / len(gold.classes) if gold.classes else 0.0
    return precision, recall, matches


def find_aircraft_class_uri(onto: Ontology, gold: Optional[Ontology]) -> Optional[str]:
    """The induced Aircraft-like class URI (for HEARTH materialization)."""
    if gold is not None:
        gold_air = gold.by_name("Aircraft")
        if gold_air is not None:
            _, _, matches = match_to_gold(onto, gold)
            iname = matches.get("Aircraft")
            if iname is not None:
                hit = onto.by_name(iname)
                if hit is not None:
                    return hit.uri
    for c in onto.iter_classes():  # fallback: name heuristic
        if "aircraft" in c.name.lower():
            return c.uri
    return None


# ----------------------------------------------------- HEARTH materialization

#: canonical Aircraft property <- faa_master source column (wave-2 seam mapping)
AIRCRAFT_PROPS: dict[str, str] = {
    "tail": "N-NUMBER",
    "serial": "SERIAL NUMBER",
    "model": "MFR MDL CODE",
    "registrant": "REGISTRANT NAME",
}


def materialize_aircraft(
    estate: dict[str, Any],
    clusters: dict[str, list[str]],          # cluster uri -> mention_ids
    mentions_by_id: dict[str, Any],          # mention_id -> EntityMention
    class_uri: str,
    hearth: Any,
    ledger: Any,
    max_clusters: int = 40,
) -> tuple[int, int]:
    """Commit ER aircraft clusters with registry anchors into HEARTH.

    Every cell's prov_ref is an interned Leaf over an atom minted from the
    ACTUAL faa_master source cell (constraint H), exactly as the wave-2 seam
    does. Returns (entities_committed, cells_committed).
    """
    faa_meta = estate["metadata"]["tables"]["faa_master"]
    source_id = faa_meta["source_id"]
    raw_rows = {
        f"{str(r['N-NUMBER']).strip()}|{str(r['SERIAL NUMBER']).strip()}": r
        for r in estate["tables"]["faa_master"].to_dict("records")
    }

    selected: list[tuple[str, Any]] = []
    for uri in sorted(clusters):
        reg = sorted(
            mid
            for mid in clusters[uri]
            if mid in mentions_by_id and mentions_by_id[mid].table == "faa_master"
        )
        if reg and mentions_by_id[reg[0]].row_key in raw_rows:
            selected.append((uri, mentions_by_id[reg[0]]))
        if len(selected) >= max_clusters:
            break

    cells: list[ValueCell] = []
    for uri, m in selected:
        raw = raw_rows[m.row_key]
        for prop, col in AIRCRAFT_PROPS.items():
            atom = make_cell_atom(source_id, m.table, m.row_key, col, raw[col])
            ledger.register_atoms([atom])
            prov = ledger.intern(leaf(atom.atom_id))
            cells.append(
                ValueCell(
                    entity_uri=uri,
                    prop=prop,
                    value=str(raw[col]).strip(),
                    valid=Interval(0),
                    system=Interval(0),
                    prov_ref=prov,
                    confidence=1.0,
                    src_rank=1,
                )
            )
    n = hearth.commit(Layer.ENTITY, class_uri, cells)
    return len(selected), n
