"""M14 — AMBER snapshot: the freeze-frame bundle (whitepaper §7, §11.2 M14).

Bundle layout (§7 items 1–8, at AMD-0008 substrate — plain Parquet, no Iceberg):

    manifest.json                   signed manifest: per-file sha256, counts,
                                    constants, capability-loss declaration L
    ontology/ontology.ttl           O^(t) as OWL 2 + SHACL (sorted Turtle, M11)
    ontology/ontology.json          native exact serialization (order-preserving)
    data/...                        HEARTH export_canonical: every value shard
                                    (bi-temporal history materialized) + link
                                    shards + the hearth content-hash manifest
    rdf/data_current.ttl            current-stance entity graph + provenance
                                    annotations (M11 data_to_rdf) for the
                                    SPARQL leg of the reference stack
    transforms/<fp>.sql|.meta.json  every ledger 'transform' artifact as a
                                    readable SQL file + verbatim payload
    decisions/decisions.jsonl       DECISION ledger extract
    morphisms/morphisms.jsonl       TEMPER morphism ledger ('temper-op' artifacts)
    provenance/prov_terms.jsonl     interned term table (refs used in the bundle)
    provenance/prov_shapes.jsonl    the §4.2 shape dictionary rows they cite
    provenance/atoms.jsonl          every leaf atom (id, uri, value)
    docs/README.md                  generated documentation (classes, counts)

Determinism: NO timestamps anywhere in the bundle (ledger created_at columns
are dropped from the extracts — §7's "modulo timestamps" exclusion); files are
written sorted; the manifest hashes file bytes. Snapshotting the same logical
state twice yields byte-identical bundles, which is what the export-import
idempotence gate compares.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from ontoforge.contracts import FOREVER, Ontology
from ontoforge.export import data_to_rdf, ontology_graph, sorted_turtle

from . import ontology_json
from .errors import AmberError

MANIFEST_NAME = "manifest.json"
FORMAT = "amber-bundle"
FORMAT_VERSION = 1

#: §7's capability-loss set L — exactly these, nothing else (the negative test
#: in tests/m14 enforces that the bundle-side answerer needs nothing beyond
#: duckdb/rdflib/pyoxigraph/pyarrow/stdlib).
CAPABILITY_LOSS = (
    "live autonomous induction/update (STRATA, ER, ANVIL, WARDEN runtimes)",
    "trained T2 specialists and calibration state (OntoForge runtime assets, not customer data)",
    "serving indexes and their latency profiles (derived structures; rebuild from the Parquet)",
    "LODESTONE/VISTA natural-language front-ends (compiled artifacts are included; the NL layer is not)",
)

#: The survivorship total order, declared in the manifest so a bundle-only
#: consumer can reproduce reads without OntoForge source.
SURVIVORSHIP_ORDER = "src_rank ASC, confidence DESC, created_at DESC, seq DESC"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _jsonl(rows: list[dict[str, Any]]) -> str:
    return "".join(json.dumps(r, sort_keys=True, separators=(",", ":")) + "\n" for r in rows)


def _chunks(items: list[str], size: int = 400):
    for i in range(0, len(items), size):
        yield items[i : i + size]


def snapshot(
    out_dir: str | Path,
    hearth: Any,
    ontology: Ontology,
    ledger: Any,
    *,
    scope: str = "full",
) -> Path:
    """Write a complete AMBER bundle; returns the manifest path.

    `ledger` must expose the SqliteLedger surface (``connection`` for the
    append-only table extracts). `scope` is 'full' in v0 (RAW-on-request and
    class-scoped bundles are §7 options deferred with M13/M15, AMD-0007).
    """
    if scope != "full":
        raise AmberError(f"unsupported snapshot scope {scope!r} (v0 supports 'full')")
    out = Path(out_dir)
    if out.exists() and any(out.iterdir()):
        raise AmberError(f"snapshot target {out} is not empty")
    out.mkdir(parents=True, exist_ok=True)

    # ---- (2)(3) data: canonical Parquet incl. bi-temporal history + links
    hearth.export_canonical(out / "data")

    # ---- (1) ontology, twice
    _write(out / "ontology" / "ontology.ttl", sorted_turtle(ontology_graph(ontology)))
    _write(out / "ontology" / "ontology.json", ontology_json.dumps(ontology))

    # ---- (3) current-stance RDF graph (values + links + prov annotations)
    _write(out / "rdf" / "data_current.ttl", sorted_turtle(data_to_rdf(hearth, ontology)))

    # ---- (4) transforms: readable SQL + verbatim payload
    conn = ledger.connection
    transform_rows = conn.execute(
        "SELECT seq, artifact_id, payload, prov_ref FROM artifact "
        "WHERE kind = 'transform' ORDER BY seq"
    ).fetchall()
    artifact_refs: list[str] = []
    for _seq, artifact_id, payload, prov_ref in transform_rows:
        d = json.loads(payload)
        fp = artifact_id.split(":", 1)[1] if ":" in artifact_id else artifact_id
        header = (
            f"-- transform: {d['name']} (version {d['version']})\n"
            f"-- fingerprint: {fp}\n"
            f"-- inputs: {', '.join(d['inputs'])}\n"
            f"-- output: {d['output']} [{d['output_layer']}]\n"
        )
        if d.get("description"):
            header += f"-- {d['description']}\n"
        _write(out / "transforms" / f"{fp}.sql", header + d["sql"] + "\n")
        _write(
            out / "transforms" / f"{fp}.meta.json",
            json.dumps(
                {
                    "artifact_id": artifact_id,
                    "fingerprint": fp,
                    "prov_ref": prov_ref,
                    "payload": payload,  # verbatim ledger payload (re-import fidelity)
                    "def": d,
                },
                sort_keys=True,
                indent=1,
            )
            + "\n",
        )
        artifact_refs.append(prov_ref)

    # ---- (5b) ER/spine decision records
    decision_rows = conn.execute(
        "SELECT seq, decision_id, outcome, confidence, conformal_set, tier, cost_tokens, "
        "deferred_to_human, quarantined, rationale, prov_atoms FROM decision ORDER BY seq"
    ).fetchall()
    decisions = [
        {
            "seq": r[0],
            "decision_id": r[1],
            "outcome": r[2],
            "confidence": r[3],
            "conformal_set": json.loads(r[4]),
            "tier": r[5],
            "cost_tokens": r[6],
            "deferred_to_human": bool(r[7]),
            "quarantined": bool(r[8]),
            "rationale": r[9],
            "prov_atoms": json.loads(r[10]),
        }
        for r in decision_rows
    ]
    _write(out / "decisions" / "decisions.jsonl", _jsonl(decisions))

    # ---- (5a) morphism ledger (TEMPER history)
    morphism_rows = conn.execute(
        "SELECT seq, artifact_id, payload, prov_ref FROM artifact "
        "WHERE kind = 'temper-op' ORDER BY seq"
    ).fetchall()
    # NOTE: the artifact-table seq is NOT serialized — it interleaves transform
    # and temper-op rows in ledger-arrival order, which a rebuilt ledger cannot
    # (and need not) reproduce; morphism order is the version chain itself.
    morphisms = [
        {"artifact_id": r[1], "payload": r[2], "prov_ref": r[3]} for r in morphism_rows
    ]
    _write(out / "morphisms" / "morphisms.jsonl", _jsonl(morphisms))
    artifact_refs.extend(r[3] for r in morphism_rows)

    # ---- (6) provenance extract: every prov_ref reachable from the bundle
    refs = set(artifact_refs)
    for shard in hearth.value_shard_items():
        refs.update(c.prov_ref for c in shard.cells)
    for lshard in hearth.links.link_shard_items():
        refs.update(c.prov_ref for c in lshard.cells)
    term_rows: list[tuple[str, str, str]] = []
    for chunk in _chunks(sorted(refs)):
        marks = ",".join("?" * len(chunk))
        term_rows += conn.execute(
            f"SELECT prov_ref, shape_hash, leaf_ids FROM prov_term WHERE prov_ref IN ({marks})",
            chunk,
        ).fetchall()
    found = {r[0] for r in term_rows}
    missing = refs - found
    if missing:
        raise AmberError(f"prov_refs unknown to the ledger: {sorted(missing)[:5]} ...")
    terms = [
        {"prov_ref": r[0], "shape_hash": r[1], "leaf_ids": json.loads(r[2])}
        for r in sorted(term_rows)
    ]
    shape_hashes = sorted({t["shape_hash"] for t in terms})
    shape_rows: list[tuple[str, str, int]] = []
    for chunk in _chunks(shape_hashes):
        marks = ",".join("?" * len(chunk))
        shape_rows += conn.execute(
            f"SELECT shape_hash, shape_json, n_slots FROM prov_shape WHERE shape_hash IN ({marks})",
            chunk,
        ).fetchall()
    shapes = [
        {"shape_hash": r[0], "shape_json": r[1], "n_slots": r[2]} for r in sorted(shape_rows)
    ]
    atom_ids = sorted({aid for t in terms for aid in t["leaf_ids"]})
    # decision evidence atoms ride along when registered
    decision_atom_ids = sorted({a for d in decisions for a in d["prov_atoms"]})
    atom_rows: list[tuple[str, str, str, Any]] = []
    for chunk in _chunks(sorted(set(atom_ids) | set(decision_atom_ids))):
        marks = ",".join("?" * len(chunk))
        atom_rows += conn.execute(
            f"SELECT atom_id, uri, value_repr, value_json FROM atom WHERE atom_id IN ({marks})",
            chunk,
        ).fetchall()
    atoms_found = {r[0] for r in atom_rows}
    missing_atoms = set(atom_ids) - atoms_found
    if missing_atoms:
        raise AmberError(f"leaf atoms missing from ledger: {sorted(missing_atoms)[:5]} ...")
    atoms = [
        {"atom_id": r[0], "uri": r[1], "value_repr": r[2], "value_json": r[3]}
        for r in sorted(atom_rows)
    ]
    _write(out / "provenance" / "prov_terms.jsonl", _jsonl(terms))
    _write(out / "provenance" / "prov_shapes.jsonl", _jsonl(shapes))
    _write(out / "provenance" / "atoms.jsonl", _jsonl(atoms))

    # ---- (7) generated documentation
    counts = {
        "classes": len(ontology.classes),
        "value_shards": sum(1 for _ in hearth.value_shard_items()),
        "value_cells": sum(len(s.cells) for s in hearth.value_shard_items()),
        "link_shards": sum(1 for _ in hearth.links.link_shard_items()),
        "link_cells": sum(len(s.cells) for s in hearth.links.link_shard_items()),
        "transforms": len(transform_rows),
        "decisions": len(decisions),
        "morphisms": len(morphisms),
        "prov_terms": len(terms),
        "prov_shapes": len(shapes),
        "atoms": len(atoms),
    }
    _write(out / "docs" / "README.md", _docs(ontology, hearth, counts))

    # ---- (8) the manifest, hashed over everything above
    files = {}
    for path in sorted(out.rglob("*")):
        if path.is_file():
            rel = path.relative_to(out).as_posix()
            files[rel] = {"sha256": _sha256(path), "bytes": path.stat().st_size}
    manifest = {
        "format": FORMAT,
        "format_version": FORMAT_VERSION,
        "scope": scope,
        "ontology_version": ontology.version,
        "capability_loss": list(CAPABILITY_LOSS),
        "constants": {"forever": FOREVER, "survivorship_order": SURVIVORSHIP_ORDER},
        "counts": counts,
        "files": files,
    }
    manifest_path = out / MANIFEST_NAME
    manifest_path.write_text(json.dumps(manifest, sort_keys=True, indent=1) + "\n")
    return manifest_path


def _docs(ontology: Ontology, hearth: Any, counts: dict[str, int]) -> str:
    lines = [
        "# AMBER bundle",
        "",
        "Self-contained freeze-frame of an OntoForge semantic estate (whitepaper §7).",
        "Everything here opens with standard tools: Parquet (DuckDB or any Arrow",
        "reader), Turtle (any SPARQL 1.1 store), JSON/JSONL.",
        "",
        "## How to read it",
        "",
        "* `data/` — bi-temporal value/link cells, plain Parquet. A cell is CURRENT",
        f"  when `valid_to` and `expired_at` both equal the open sentinel ({FOREVER}).",
        "  As-of t: `expired_at = open AND valid_from <= t < valid_to`. When several",
        "  cells survive a predicate, take the first under the survivorship order",
        f"  `{SURVIVORSHIP_ORDER}` per (entity_uri, prop).",
        "* `rdf/data_current.ttl` + `ontology/ontology.ttl` — the current-stance graph",
        "  with provenance annotations; load both into one SPARQL store.",
        "* `provenance/` — resolve any `prov_ref` via prov_terms.jsonl: its `leaf_ids`",
        "  are the citation atom ids; atoms.jsonl has each atom's source URI and value.",
        "* `transforms/`, `morphisms/`, `decisions/` — the customer's logic and the",
        "  estate's full decision/evolution history, human-readable.",
        "",
        "## Capability loss (declared, finite — §7)",
        "",
    ]
    lines += [f"* {item}" for item in CAPABILITY_LOSS]
    lines += ["", "## Counts", ""]
    lines += [f"* {k}: {v}" for k, v in sorted(counts.items())]
    lines += ["", "## Classes", ""]
    for uri in sorted(ontology.classes):
        c = ontology.classes[uri]
        parents = f" < {', '.join(sorted(c.parents))}" if c.parents else ""
        lines.append(f"### {c.name}{parents}")
        lines.append("")
        lines.append(f"`{uri}`  ")
        if c.definition:
            lines.append(c.definition)
        lines.append("")
        for p in c.properties:
            kind = f"link -> {p.range_class}" if p.is_link else p.datatype.value
            unit = f" [{p.unit}]" if p.unit else ""
            lines.append(f"* `{p.name}`: {kind}{unit}")
        if c.shapes:
            lines.append(f"* constraints: {len(c.shapes)} SHACL shape(s)")
        lines.append("")
    return "\n".join(lines) + "\n"
