"""M10 — the morphism ledger: the ontology's own provenance (§3.6 closure (iii)).

An append-only record of applied TEMPER operators (op type + params,
from_version -> to_version, migration stats, timestamp), persisted through the
M0 ledger as kind ``temper-op`` artifacts. ``replay`` reconstructs O^(t)
exactly from the base ontology; ``invert_record`` yields the inverse operator
for invertible ops, so a departing customer can replay OR invert the operator
path embedded in an AMBER snapshot.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Optional, Sequence

from ontoforge.contracts import Ontology, leaf, make_cell_atom, now_instant

from .ops import Operator, op_from_dict, op_to_dict


@dataclass(frozen=True)
class MorphismRecord:
    op_type: str
    params: dict[str, Any]
    from_version: int
    to_version: int
    stats: dict[str, Any]
    timestamp: int  # microseconds since epoch (Instant)

    def to_payload(self) -> str:
        return json.dumps(
            {
                "op_type": self.op_type,
                "params": self.params,
                "from_version": self.from_version,
                "to_version": self.to_version,
                "stats": self.stats,
                "timestamp": self.timestamp,
            },
            sort_keys=True,
            separators=(",", ":"),
        )

    @staticmethod
    def from_payload(payload: str) -> "MorphismRecord":
        d = json.loads(payload)
        return MorphismRecord(
            op_type=d["op_type"],
            params=d["params"],
            from_version=d["from_version"],
            to_version=d["to_version"],
            stats=d["stats"],
            timestamp=d["timestamp"],
        )

    def operator(self) -> Operator:
        return op_from_dict({"op_type": self.op_type, **self.params})


ARTIFACT_KIND = "temper-op"


class MorphismLedger:
    """In-memory append-only record, optionally written through to the M0
    ledger (kind 'temper-op'; each record's prov_ref is a real interned Leaf
    of a minted temper atom — constraint H holds for schema history too)."""

    def __init__(self, ledger=None) -> None:
        self._ledger = ledger
        self.records: list[MorphismRecord] = []

    def record(
        self,
        op: Operator,
        from_version: int,
        to_version: int,
        stats: dict[str, Any],
        *,
        now: Optional[int] = None,
    ) -> MorphismRecord:
        d = op_to_dict(op)
        rec = MorphismRecord(
            op_type=d.pop("op_type"),
            params=d,
            from_version=from_version,
            to_version=to_version,
            stats=stats,
            timestamp=now_instant() if now is None else now,
        )
        self.records.append(rec)
        if self._ledger is not None:
            payload = rec.to_payload()
            atom = make_cell_atom("temper", "morphism", str(to_version), "op", payload)
            self._ledger.register_atoms([atom])
            ref = self._ledger.intern(leaf(atom.atom_id))
            self._ledger.append_artifact(
                artifact_id=f"temper-op:{to_version:08d}", kind=ARTIFACT_KIND, payload=payload, prov_ref=ref
            )
        return rec


def load_morphisms(ledger) -> list[MorphismRecord]:
    """Read back every temper-op artifact from the M0 ledger in version order."""
    rows = ledger.connection.execute(
        "SELECT artifact_id, payload FROM artifact WHERE kind = ? ORDER BY artifact_id",
        (ARTIFACT_KIND,),
    ).fetchall()
    return [MorphismRecord.from_payload(p) for _aid, p in rows]


def replay(records: Sequence[MorphismRecord], base: Ontology) -> Ontology:
    """Reconstruct O^(t) exactly: re-apply every recorded ontology rewrite in
    order, asserting the version chain. Pure — no Hearth, no spine."""
    onto = base.clone()
    for rec in records:
        if rec.from_version != onto.version:
            raise ValueError(
                f"morphism chain broken: record expects from_version {rec.from_version}, have {onto.version}"
            )
        op = rec.operator()
        new = op.rewrite(onto)
        new.version = onto.version + 1
        if new.version != rec.to_version:
            raise ValueError(f"morphism chain broken at to_version {rec.to_version}")
        onto = new
    return onto


def invert_record(rec: MorphismRecord, pre: Ontology) -> Optional[Operator]:
    """Inverse operator of a recorded application, computed against the
    pre-application ontology (None when the op is not invertible)."""
    return rec.operator().invert(pre)
