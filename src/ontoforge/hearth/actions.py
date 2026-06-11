"""M6 — HEARTH Actions: typed, validated user write-back (whitepaper §4.3.2).

Following the OSv2 lesson, user edits NEVER touch storage directly. An Action is
a declared operation — SetProperty | Link | Unlink | CreateObject — that is:

1. SHACL pre-validated against the class's ShapeConstraints (the gold ontology
   or any contracts.Ontology supplied at Hearth construction);
2. recorded as evidence: a synthetic atom of kind `human-edit` (actor, op
   payload, timestamp) registered in the M0 ledger, its Leaf interned as the
   cell's prov_ref, and a `human-edit` artifact appended (constraint H holds
   for human writes exactly as for pipeline writes);
3. written as a versioned cell with src_rank = 0 — the reserved top
   survivorship rank, so a human override beats pipeline values, and a
   subsequent pipeline write at lower precedence lands dead-on-arrival in
   history instead of clobbering the action value.
"""

from __future__ import annotations

import json
import re as _re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Mapping, Optional, Union

from ontoforge.contracts import (
    Atom,
    ClassDef,
    Datatype,
    Instant,
    Interval,
    LinkCell,
    Layer,
    ShapeConstraint,
    ValueCell,
    leaf,
    now_instant,
)

from .errors import ActionValidationError

if TYPE_CHECKING:  # pragma: no cover
    from .store import Hearth

HUMAN_EDIT_KIND = "human-edit"
HUMAN_RANK = 0

# ------------------------------------------------------------------ op types


@dataclass(frozen=True)
class SetProperty:
    class_uri: str
    entity_uri: str
    prop: str
    value: Any
    valid: Optional[Interval] = None  # None -> [now, FOREVER)


@dataclass(frozen=True)
class Link:
    class_uri: str
    subject_uri: str
    predicate: str
    object_uri: str


@dataclass(frozen=True)
class Unlink:
    class_uri: str
    subject_uri: str
    predicate: str
    object_uri: str


@dataclass(frozen=True)
class CreateObject:
    class_uri: str
    entity_uri: str
    props: Mapping[str, Any] = field(default_factory=dict)


Op = Union[SetProperty, Link, Unlink, CreateObject]


@dataclass(frozen=True)
class ActionReceipt:
    actor: str
    op: Op
    prov_ref: str
    at: Instant
    cells_written: int
    links_written: int


# --------------------------------------------------------------- validation

_DATATYPE_OK = {
    Datatype.STRING: lambda v: isinstance(v, str),
    Datatype.TEXT: lambda v: isinstance(v, str),
    Datatype.INTEGER: lambda v: isinstance(v, int) and not isinstance(v, bool),
    Datatype.FLOAT: lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    Datatype.BOOLEAN: lambda v: isinstance(v, bool),
    Datatype.DATE: lambda v: isinstance(v, str),
    Datatype.DATETIME: lambda v: isinstance(v, str),
}


def _check_shape(cls: ClassDef, shape: ShapeConstraint, value: Any) -> None:
    where = f"{cls.name}.{shape.prop}"
    if shape.datatype is not None and not _DATATYPE_OK[shape.datatype](value):
        raise ActionValidationError(
            f"{where}: expected {shape.datatype.value}, got {type(value).__name__} ({value!r})"
        )
    if shape.in_values is not None and str(value) not in shape.in_values:
        raise ActionValidationError(f"{where}: {value!r} not in allowed values {shape.in_values}")
    if shape.pattern is not None:
        if not isinstance(value, str) or _re.search(shape.pattern, value) is None:
            raise ActionValidationError(f"{where}: {value!r} does not match pattern {shape.pattern!r}")
    if shape.min_value is not None or shape.max_value is not None:
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise ActionValidationError(f"{where}: range-constrained value must be numeric, got {value!r}")
        if shape.min_value is not None and value < shape.min_value:
            raise ActionValidationError(f"{where}: {value!r} < min_value {shape.min_value}")
        if shape.max_value is not None and value > shape.max_value:
            raise ActionValidationError(f"{where}: {value!r} > max_value {shape.max_value}")


def _resolve_class(store: "Hearth", class_uri: str) -> Optional[ClassDef]:
    if store.ontology is None:
        return None  # no ontology supplied: shape validation is a no-op (documented)
    cls = store.ontology.get(class_uri)
    if cls is None:
        raise ActionValidationError(f"unknown class {class_uri!r} (not in the supplied ontology)")
    return cls


def _validate_property(cls: Optional[ClassDef], prop: str, value: Any) -> None:
    if cls is None:
        return
    if value is None:
        raise ActionValidationError(f"{cls.name}.{prop}: Actions may not set None; use intervals instead")
    pd = cls.prop(prop)
    if pd is not None and pd.is_link:
        raise ActionValidationError(
            f"{cls.name}.{prop} is an object property; use the Link op, not SetProperty"
        )
    if pd is not None:
        # PropertyDef datatype check (stricter than shapes alone).
        if not _DATATYPE_OK[pd.datatype](value):
            raise ActionValidationError(
                f"{cls.name}.{prop}: expected {pd.datatype.value}, got {type(value).__name__} ({value!r})"
            )
    for shape in cls.shapes:
        if shape.prop == prop:
            _check_shape(cls, shape, value)


def validate_op(store: "Hearth", op: Op) -> None:
    """SHACL-style pre-validation (§4.3.2). Raises ActionValidationError."""
    if isinstance(op, SetProperty):
        cls = _resolve_class(store, op.class_uri)
        _validate_property(cls, op.prop, op.value)
    elif isinstance(op, CreateObject):
        cls = _resolve_class(store, op.class_uri)
        if cls is not None:
            for shape in cls.shapes:
                if shape.min_count >= 1 and op.props.get(shape.prop) is None:
                    raise ActionValidationError(
                        f"{cls.name}: required property {shape.prop!r} (min_count="
                        f"{shape.min_count}) missing from CreateObject"
                    )
        for prop, value in op.props.items():
            _validate_property(cls, prop, value)
    elif isinstance(op, (Link, Unlink)):
        cls = _resolve_class(store, op.class_uri)
        if cls is not None:
            pd = cls.prop(op.predicate)
            if pd is None or not pd.is_link:
                raise ActionValidationError(
                    f"{cls.name}: {op.predicate!r} is not a declared object property"
                )
    else:
        raise ActionValidationError(f"unknown Action op type: {type(op).__name__}")


# ------------------------------------------------------------------ perform


def _op_payload(actor: str, op: Op, at: Instant) -> dict[str, Any]:
    body = {k: getattr(op, k) for k in op.__dataclass_fields__}
    if isinstance(op, SetProperty) and body["valid"] is not None:
        body["valid"] = [body["valid"].start, body["valid"].end]
    if isinstance(op, CreateObject):
        body["props"] = dict(sorted(body["props"].items()))
    return {"actor": actor, "op": type(op).__name__, "at": at, "body": body}


def _human_edit_prov(store: "Hearth", actor: str, op: Op, at: Instant) -> str:
    """Register the synthetic human-edit atom + artifact in the ledger and
    return the interned prov_ref of its Leaf (constraint H for human writes)."""
    payload = _op_payload(actor, op, at)
    payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    subject = getattr(op, "entity_uri", None) or getattr(op, "subject_uri", "")
    fragment = getattr(op, "prop", None) or getattr(op, "predicate", "object")
    uri = f"atom://{HUMAN_EDIT_KIND}/{actor}/{type(op).__name__}/{subject.replace('://', '/')}#{fragment}"
    atom = Atom(uri=uri, value=payload_json)
    store.ledger.register_atoms([atom])
    prov_ref = store.ledger.intern(leaf(atom.atom_id))
    store.ledger.append_artifact(
        artifact_id=f"action/{actor}/{at}/{atom.atom_id}",
        kind=HUMAN_EDIT_KIND,
        payload=payload_json,
        prov_ref=prov_ref,
    )
    return prov_ref


def perform(store: "Hearth", actor: str, op: Op, *, now: Optional[Instant] = None) -> ActionReceipt:
    """Validate -> evidence -> rank-0 versioned write (§4.3.2)."""
    if not actor:
        raise ActionValidationError("Actions require a non-empty actor identity")
    validate_op(store, op)
    at = now_instant() if now is None else now
    prov_ref = _human_edit_prov(store, actor, op, at)
    cells = 0
    links = 0
    if isinstance(op, SetProperty):
        cell = ValueCell(
            entity_uri=op.entity_uri,
            prop=op.prop,
            value=op.value,
            valid=op.valid if op.valid is not None else Interval(at),
            system=Interval(at),
            prov_ref=prov_ref,
            confidence=1.0,
            src_rank=HUMAN_RANK,
        )
        cells = store._commit_cells(Layer.ENTITY, op.class_uri, [cell], now=at, allow_rank0=True)
    elif isinstance(op, CreateObject):
        batch = [
            ValueCell(
                entity_uri=op.entity_uri,
                prop=prop,
                value=value,
                valid=Interval(at),
                system=Interval(at),
                prov_ref=prov_ref,
                confidence=1.0,
                src_rank=HUMAN_RANK,
            )
            for prop, value in sorted(op.props.items())
        ]
        cells = store._commit_cells(Layer.ENTITY, op.class_uri, batch, now=at, allow_rank0=True)
    elif isinstance(op, Link):
        link = LinkCell(
            subject_uri=op.subject_uri,
            predicate=op.predicate,
            object_uri=op.object_uri,
            valid=Interval(at),
            system=Interval(at),
            prov_ref=prov_ref,
            confidence=1.0,
        )
        links = store.links.commit(op.class_uri, op.predicate, [link], now=at)
    elif isinstance(op, Unlink):
        done = store.links.unlink(
            op.class_uri, op.predicate, op.subject_uri, op.object_uri, prov_ref, now=at
        )
        if not done:
            raise ActionValidationError(
                f"Unlink: no current link ({op.subject_uri!r} -{op.predicate}-> {op.object_uri!r})"
            )
        links = 1
    return ActionReceipt(
        actor=actor, op=op, prov_ref=prov_ref, at=at, cells_written=cells, links_written=links
    )
