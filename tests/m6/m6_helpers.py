"""M6 test helpers: provenance minting + cell construction shorthand.

Every committed cell needs a prov_ref that resolves in the ledger (constraint H),
so tests mint REAL atoms and intern REAL Leaf terms — no fake refs anywhere.
"""

from __future__ import annotations

from typing import Any

from ontoforge.contracts import (
    FOREVER,
    Interval,
    LinkCell,
    ValueCell,
    leaf,
    make_cell_atom,
)
from ontoforge.ledger import SqliteLedger


def mint_prov(ledger: SqliteLedger, *key: object, value: Any = "evidence") -> str:
    """Register a content-addressed atom for `key` and intern its Leaf."""
    row_key = "/".join(str(k) for k in key) or "row"
    atom = make_cell_atom("m6-test", "table", row_key, "col", value)
    ledger.register_atoms([atom])
    return ledger.intern(leaf(atom.atom_id))


def vc(
    entity: str,
    prop: str,
    value: Any,
    prov: str,
    *,
    valid_from: int = 0,
    valid_to: int = FOREVER,
    rank: int = 1,
    conf: float = 1.0,
) -> ValueCell:
    return ValueCell(
        entity_uri=entity,
        prop=prop,
        value=value,
        valid=Interval(valid_from, valid_to),
        system=Interval(0),  # store-stamped on commit
        prov_ref=prov,
        confidence=conf,
        src_rank=rank,
    )


def lc(
    subject: str,
    predicate: str,
    obj: str,
    prov: str,
    *,
    valid_from: int = 0,
    valid_to: int = FOREVER,
    conf: float = 1.0,
) -> LinkCell:
    return LinkCell(
        subject_uri=subject,
        predicate=predicate,
        object_uri=obj,
        valid=Interval(valid_from, valid_to),
        system=Interval(0),
        prov_ref=prov,
        confidence=conf,
    )


def stance(spec: tuple) -> "object":
    from ontoforge.contracts import Stance

    kind = spec[0]
    if kind == "current":
        return Stance()
    if kind == "as_of":
        return Stance("as_of", valid_at=spec[1])
    if kind == "as_known_at":
        return Stance("as_known_at", known_at=spec[1])
    if kind == "audit":
        return Stance("audit", valid_at=spec[1], known_at=spec[2])
    raise ValueError(spec)
