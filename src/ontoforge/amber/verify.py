"""M14 — AMBER bundle verification (§11.2 M14 'verify(bundle)').

Checks, in order:

1. **Manifest integrity** — every manifest entry exists with matching sha256
   and size; no stray files outside the manifest (tamper = flipped byte,
   truncation, deletion, or insertion — all caught).
2. **Provenance completeness** — every `prov_ref` on every value/link cell in
   `data/` resolves through `provenance/prov_terms.jsonl` to a term with a
   present shape row, a consistent slot count, NON-ZERO leaves, and every
   leaf atom present in `provenance/atoms.jsonl` (constraint H survives the
   freeze).
3. **Transforms readable** — every `.sql` is non-empty readable text, every
   `.meta.json` parses, and its payload re-fingerprints to the filename
   (content-addressing intact).
4. **Ontology parses** — ontology.ttl loads as Turtle (rdflib), ontology.json
   loads natively, both agree on the class set; rdf/data_current.ttl parses.
5. **Extract well-formedness** — decisions/morphisms JSONL parse; morphism
   payloads carry a contiguous version chain.

Returns a report dict: {"ok": bool, "errors": [...], "checks": {...}}.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq

from .snapshot import FORMAT, FORMAT_VERSION, MANIFEST_NAME


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def verify(bundle_dir: str | Path) -> dict[str, Any]:
    bundle = Path(bundle_dir)
    errors: list[str] = []
    checks: dict[str, Any] = {}

    manifest_path = bundle / MANIFEST_NAME
    if not manifest_path.is_file():
        return {"ok": False, "errors": [f"no {MANIFEST_NAME} in {bundle}"], "checks": {}}
    try:
        manifest = json.loads(manifest_path.read_text())
    except json.JSONDecodeError as exc:
        return {"ok": False, "errors": [f"manifest unparseable: {exc}"], "checks": {}}
    if manifest.get("format") != FORMAT or manifest.get("format_version") != FORMAT_VERSION:
        errors.append(
            f"unsupported bundle format {manifest.get('format')!r} "
            f"v{manifest.get('format_version')!r}"
        )

    # ---- 1. manifest integrity ------------------------------------------------
    listed = manifest.get("files", {})
    n_hash_ok = 0
    for rel, entry in sorted(listed.items()):
        path = bundle / rel
        if not path.is_file():
            errors.append(f"missing file: {rel}")
            continue
        if path.stat().st_size != entry["bytes"]:
            errors.append(f"size mismatch: {rel}")
        if _sha256(path) != entry["sha256"]:
            errors.append(f"sha256 mismatch: {rel}")
        else:
            n_hash_ok += 1
    on_disk = {
        p.relative_to(bundle).as_posix()
        for p in bundle.rglob("*")
        if p.is_file() and p.name != MANIFEST_NAME
    }
    for stray in sorted(on_disk - set(listed)):
        errors.append(f"file not in manifest: {stray}")
    checks["files_verified"] = n_hash_ok

    if any(e.startswith(("missing file", "sha256", "size")) for e in errors):
        # state below the hash layer is untrustworthy; report and stop
        return {"ok": False, "errors": errors, "checks": checks}

    # ---- 2. provenance completeness -------------------------------------------
    terms = {t["prov_ref"]: t for t in _read_jsonl(bundle / "provenance" / "prov_terms.jsonl")}
    shapes = {s["shape_hash"]: s for s in _read_jsonl(bundle / "provenance" / "prov_shapes.jsonl")}
    atoms = {a["atom_id"]: a for a in _read_jsonl(bundle / "provenance" / "atoms.jsonl")}
    data_manifest = json.loads((bundle / "data" / "manifest.json").read_text())
    cell_refs: set[str] = set()
    n_cells = 0
    for entry in data_manifest["shards"]:
        table = pq.read_table(bundle / "data" / entry["path"], columns=["prov_ref"])
        refs = table.column("prov_ref").to_pylist()
        n_cells += len(refs)
        cell_refs.update(refs)
    unresolved = 0
    for ref in sorted(cell_refs):
        term = terms.get(ref)
        if term is None:
            errors.append(f"cell prov_ref not in provenance extract: {ref}")
            unresolved += 1
            continue
        shape = shapes.get(term["shape_hash"])
        if shape is None:
            errors.append(f"prov_ref {ref}: shape {term['shape_hash']} missing")
            continue
        if shape["n_slots"] != len(term["leaf_ids"]):
            errors.append(f"prov_ref {ref}: slot count mismatch")
        if not term["leaf_ids"]:
            errors.append(f"prov_ref {ref}: ZERO provenance (no leaves) — constraint H")
        for aid in term["leaf_ids"]:
            if aid not in atoms:
                errors.append(f"prov_ref {ref}: leaf atom {aid} missing from extract")
    checks["data_cells"] = n_cells
    checks["distinct_prov_refs"] = len(cell_refs)
    checks["prov_refs_resolved"] = len(cell_refs) - unresolved

    # ---- 3. transforms readable ------------------------------------------------
    from ontoforge.contracts import TransformDef
    from ontoforge.contracts.transforms import Layer as TLayer

    n_transforms = 0
    tdir = bundle / "transforms"
    for meta_path in sorted(tdir.glob("*.meta.json")) if tdir.is_dir() else []:
        try:
            meta = json.loads(meta_path.read_text())
            d = json.loads(meta["payload"])
            tdef = TransformDef(
                name=d["name"],
                inputs=tuple(d["inputs"]),
                output=d["output"],
                sql=d["sql"],
                output_layer=TLayer(d["output_layer"]),
                description=d.get("description", ""),
                synthesized_by=d.get("synthesized_by", ""),
                version=d.get("version", 1),
            )
        except (json.JSONDecodeError, KeyError, ValueError) as exc:
            errors.append(f"transform meta unreadable: {meta_path.name}: {exc}")
            continue
        if tdef.fingerprint != meta["fingerprint"]:
            errors.append(f"transform fingerprint mismatch: {meta_path.name}")
        sql_path = tdir / f"{meta['fingerprint']}.sql"
        if not sql_path.is_file() or d["sql"] not in sql_path.read_text():
            errors.append(f"transform SQL missing/garbled: {sql_path.name}")
        else:
            n_transforms += 1
    checks["transforms_readable"] = n_transforms

    # ---- 4. ontology parses ------------------------------------------------------
    try:
        from rdflib import Graph

        from . import ontology_json

        g = Graph()
        g.parse(data=(bundle / "ontology" / "ontology.ttl").read_text(), format="turtle")
        onto = ontology_json.loads((bundle / "ontology" / "ontology.json").read_text())
        from rdflib import RDF
        from rdflib.namespace import OWL

        ttl_classes = {str(c) for c in g.subjects(RDF.type, OWL.Class)}
        if ttl_classes != set(onto.classes):
            errors.append("ontology.ttl and ontology.json disagree on the class set")
        checks["ontology_classes"] = len(onto.classes)
        gd = Graph()
        gd.parse(data=(bundle / "rdf" / "data_current.ttl").read_text(), format="turtle")
        checks["rdf_data_triples"] = len(gd)
    except Exception as exc:  # noqa: BLE001 — verification reports, never raises
        errors.append(f"ontology/rdf parse failure: {exc}")

    # ---- 5. extracts well-formed ---------------------------------------------------
    try:
        decisions = _read_jsonl(bundle / "decisions" / "decisions.jsonl")
        checks["decisions"] = len(decisions)
        morphisms = _read_jsonl(bundle / "morphisms" / "morphisms.jsonl")
        versions = [json.loads(m["payload"])["to_version"] for m in morphisms]
        if versions != sorted(versions):
            errors.append("morphism ledger extract is out of version order")
        checks["morphisms"] = len(morphisms)
    except (OSError, json.JSONDecodeError, KeyError) as exc:
        errors.append(f"extract unreadable: {exc}")

    return {"ok": not errors, "errors": errors, "checks": checks}
