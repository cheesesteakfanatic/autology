"""M14 — AMBER import: bundle -> working (Hearth, Ontology, SqliteLedger).

Reconstruction order matters: the ledger first (atoms, then interned terms,
then artifacts/decisions — append_artifact enforces constraint H against the
freshly rebuilt term table), then the ontology (native JSON, order-exact),
then HEARTH via its own hash-verified canonical import.

Interned term identity is content-addressed (term_hash), so re-interning the
reconstructed term MUST reproduce the bundled prov_ref byte for byte — the
import asserts this for every term, which is the cryptographic seam between
the bundle's provenance extract and the new ledger.

Export-import idempotence (§11.2 M14): ledger ``created_at`` stamps are the
ONLY thing that differs in the rebuilt store, and snapshots never serialize
them — so ``snapshot(import_bundle(snapshot(X)))`` is manifest-equal (the
tests compare full manifests, hashes included).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ontoforge.contracts import Atom, DecisionResult, Ontology, Tier
from ontoforge.contracts.provenance import ProvTerm, leaf, prov_prod, prov_sum
from ontoforge.hearth import Hearth, import_canonical
from ontoforge.ledger import SqliteLedger

from . import ontology_json
from .errors import AmberError
from .verify import verify

_SLOT_PREFIX = "\x00slot:"  # the M0 interning slot namespace (ledger §4.2)


def _term_from_shape(obj: Any, leaf_ids: list[str]) -> ProvTerm:
    """Instantiate a §4.2 shape-dictionary JSON object with its leaf array,
    using only the PUBLIC contracts.provenance constructors (normal form is
    re-established by construction, so term_hash reproduces exactly)."""
    tag = obj[0]
    if tag == "L":
        slot = obj[1]
        if not slot.startswith(_SLOT_PREFIX):
            return leaf(slot)  # degenerate: a concrete leaf in the shape
        return leaf(leaf_ids[int(slot[len(_SLOT_PREFIX):])])
    if tag == "0":
        return prov_sum(())  # ZERO
    if tag == "1":
        return prov_prod(())  # ONE
    subs = (_term_from_shape(s, leaf_ids) for s in obj[1])
    return prov_sum(subs) if tag == "S" else prov_prod(subs)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def import_bundle(
    bundle_dir: str | Path, new_root: str | Path
) -> tuple[Hearth, Ontology, SqliteLedger]:
    """Reconstruct a WORKING store from a verified bundle.

    Returns (hearth, ontology, ledger): a live Hearth rooted at
    ``new_root/hearth`` (monotone clock restored, indexes rebuilt), the exact
    ontology, and a durable SqliteLedger at ``new_root/ledger.sqlite``
    containing every atom, interned term, transform, morphism record, and
    decision the bundle carried.
    """
    bundle = Path(bundle_dir)
    report = verify(bundle)
    if not report["ok"]:
        raise AmberError(f"bundle failed verification: {report['errors'][:5]}")

    root = Path(new_root)
    root.mkdir(parents=True, exist_ok=True)
    if any(root.iterdir()):
        raise AmberError(f"import target {root} is not empty")

    ledger = SqliteLedger(str(root / "ledger.sqlite"))

    # ---- atoms (identity preserved: stored atom_id wins over recomputation)
    atoms = _read_jsonl(bundle / "provenance" / "atoms.jsonl")
    ledger.register_atoms(
        [
            Atom(
                uri=a["uri"],
                value=json.loads(a["value_json"]) if a["value_json"] is not None else a["value_repr"],
                atom_id=a["atom_id"],
            )
            for a in atoms
        ]
    )

    # ---- interned terms; content addressing must reproduce every prov_ref
    shapes = {s["shape_hash"]: s for s in _read_jsonl(bundle / "provenance" / "prov_shapes.jsonl")}
    for t in _read_jsonl(bundle / "provenance" / "prov_terms.jsonl"):
        shape_obj = json.loads(shapes[t["shape_hash"]]["shape_json"])
        term = _term_from_shape(shape_obj, t["leaf_ids"])
        ref = ledger.intern(term)
        if ref != t["prov_ref"]:
            raise AmberError(
                f"interning mismatch on import: bundle says {t['prov_ref']}, "
                f"reconstruction hashes to {ref}"
            )

    # ---- artifacts: transforms then morphisms, in original seq order
    tdir = bundle / "transforms"
    metas = []
    for meta_path in sorted(tdir.glob("*.meta.json")) if tdir.is_dir() else []:
        metas.append(json.loads(meta_path.read_text()))
    metas.sort(key=lambda m: m["artifact_id"])
    for m in metas:
        ledger.append_artifact(m["artifact_id"], "transform", m["payload"], m["prov_ref"])
    for m in _read_jsonl(bundle / "morphisms" / "morphisms.jsonl"):
        ledger.append_artifact(m["artifact_id"], "temper-op", m["payload"], m["prov_ref"])

    # ---- decisions
    for d in _read_jsonl(bundle / "decisions" / "decisions.jsonl"):
        ledger.append_decision(
            DecisionResult(
                decision_id=d["decision_id"],
                outcome=d["outcome"],
                confidence=d["confidence"],
                conformal_set=tuple(d["conformal_set"]),
                tier=Tier(d["tier"]),
                cost_tokens=d["cost_tokens"],
                deferred_to_human=d["deferred_to_human"],
                quarantined=d["quarantined"],
                rationale=d["rationale"],
            ),
            prov_atoms=tuple(d["prov_atoms"]),
        )

    # ---- ontology (exact, order-preserving)
    ontology = ontology_json.loads((bundle / "ontology" / "ontology.json").read_text())

    # ---- HEARTH (its own content-hash verification runs inside)
    hearth = import_canonical(bundle / "data", root / "hearth", ledger, ontology)
    return hearth, ontology, ledger
