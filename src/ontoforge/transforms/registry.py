"""M7 transform registry (whitepaper §5.1): transforms are declarative,
versioned, content-fingerprinted ledger artifacts.

`register(tdef)` validates the DSL body, computes the contract fingerprint,
and appends the serialized def to the ledger as an artifact of kind
"transform". Provenance (constraint H): a human-authored transform gets a
ONE-leaf term over a synthetic authorship atom minted here; a synthesized
transform passes the synthesizer's own interned term via `prov_ref`.

The registry keeps every registered version; `active()` returns the latest
def per transform *name* (re-registering a changed body under the same name
is how a transform is "changed" — the new content fingerprint is what flips
the virtual-environment memo key downstream).
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from ontoforge.contracts import leaf, make_cell_atom
from ontoforge.contracts.ledger import Ledger
from ontoforge.contracts.transforms import Layer, TransformDef

from .dsl import validate_sql

__all__ = ["RegisteredTransform", "TransformRegistry", "serialize_def", "deserialize_def"]


def serialize_def(tdef: TransformDef) -> str:
    return json.dumps(
        {
            "name": tdef.name,
            "inputs": list(tdef.inputs),
            "output": tdef.output,
            "sql": tdef.sql,
            "output_layer": tdef.output_layer.value,
            "description": tdef.description,
            "synthesized_by": tdef.synthesized_by,
            "version": tdef.version,
        },
        sort_keys=True,
    )


def deserialize_def(payload: str) -> TransformDef:
    d = json.loads(payload)
    return TransformDef(
        name=d["name"],
        inputs=tuple(d["inputs"]),
        output=d["output"],
        sql=d["sql"],
        output_layer=Layer(d["output_layer"]),
        description=d.get("description", ""),
        synthesized_by=d.get("synthesized_by", ""),
        version=d.get("version", 1),
    )


@dataclass(frozen=True, slots=True)
class RegisteredTransform:
    tdef: TransformDef
    fingerprint: str
    prov_ref: str


class TransformRegistry:
    def __init__(self, ledger: Ledger) -> None:
        self.ledger = ledger
        self._by_fp: dict[str, RegisteredTransform] = {}
        self._latest_fp_by_name: dict[str, str] = {}

    def register(self, tdef: TransformDef, *, prov_ref: str = "") -> str:
        """Validate + fingerprint + persist. Idempotent on identical content.

        prov_ref: interned term of the synthesizer's derivation for
        synthesized transforms (required when tdef.synthesized_by is set);
        human-authored defs get a ONE-leaf synthetic authorship atom.
        """
        validate_sql(tdef.sql)
        fp = tdef.fingerprint
        if fp in self._by_fp:
            self._latest_fp_by_name[tdef.name] = fp
            return fp
        if tdef.synthesized_by and not prov_ref:
            raise ValueError(
                f"synthesized transform {tdef.name!r} ({tdef.synthesized_by}) must "
                "carry the synthesizer's interned provenance term"
            )
        if not prov_ref:
            # ONE-leaf synthetic atom for human authorship: the transform text
            # itself is the evidence cell.
            atom = make_cell_atom(
                "human-author", "transforms", tdef.name, f"v{tdef.version}", tdef.sql
            )
            self.ledger.register_atoms([atom])
            prov_ref = self.ledger.intern(leaf(atom.atom_id))
        self.ledger.append_artifact(
            f"transform:{fp}", "transform", serialize_def(tdef), prov_ref
        )
        reg = RegisteredTransform(tdef=tdef, fingerprint=fp, prov_ref=prov_ref)
        self._by_fp[fp] = reg
        self._latest_fp_by_name[tdef.name] = fp
        return fp

    def get(self, fingerprint: str) -> RegisteredTransform:
        return self._by_fp[fingerprint]

    def by_name(self, name: str) -> RegisteredTransform:
        return self._by_fp[self._latest_fp_by_name[name]]

    def active(self) -> list[RegisteredTransform]:
        """Latest registered version per transform name, registration order."""
        return [self._by_fp[fp] for fp in self._latest_fp_by_name.values()]
