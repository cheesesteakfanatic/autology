"""M10 — TEMPER operator set (whitepaper §3.6 operator table).

Each operator is a frozen dataclass implementing the §3.6 triple:

* ``precondition(onto, data)``  — typed validity check; raises PreconditionError.
* ``rewrite(onto)``             — pure ontology rewrite returning a NEW Ontology
                                  (untouched ClassDef objects are shared, hence
                                  bit-identical — URI stability by construction).
* ``migrate(pre, post, data)``  — forward migration over HEARTH entity cells via
                                  the engine's DataAdapter (commit path); returns
                                  a stats dict. Label/axiom-only operators are
                                  no-ops here (ZERO Hearth commits — §3.6 target).
* ``invert(pre)``               — the inverse operator where one exists (Rename,
                                  Add/Retire pairs, Split<->Merge with retained
                                  discriminator, Promote<->Demote, Retype with
                                  inverse conversion, Generalize<->Specialize).

Backward views (the snapshot-queryability obligation) are constructed per
applied operator in ``views.op_rewriter`` — see views.py.

Storage-key convention
----------------------
HEARTH cells key on the property URI's stable tail (``storage_key``), not on
the display name. RenameProperty therefore changes labels only: the URI — and
hence the cell key — never moves, which is what makes renames zero-migration
by construction rather than by promise.

Provenance of migrated cells
----------------------------
Forward migrations REUSE the original cells' interned prov_refs (the migrated
value is derivable from exactly the atoms the original was; tagging as
Prod(original) would intern an equivalent term — we reuse the refs directly,
documented per AMD note in the M10 task). System-time bitemporality preserves
every pre-migration cell: superseded cells close their system interval, moved
extents leave the old shard intact (its class simply leaves the ontology), so
``as_known_at`` reads reconstruct any pre-migration state.
"""

from __future__ import annotations

from dataclasses import dataclass, fields, replace
from typing import TYPE_CHECKING, Any, Callable, Optional

import xxhash

from ontoforge.contracts import (
    ClassDef,
    Datatype,
    Interval,
    Ontology,
    PropertyDef,
    ShapeConstraint,
    ValueCell,
    property_uri,
)

if TYPE_CHECKING:  # pragma: no cover
    from .apply import DataAdapter

# ---------------------------------------------------------------- constants

RETIRED_MARK = "[temper:retired]"
QUARANTINE_PROP = "__temper_quarantine"
ORIGIN_PREFIX = "__temper_origin"
INTERNAL_PREFIXES = ("__temper",)


class PreconditionError(Exception):
    """Operator precondition violated; nothing was changed."""


# ------------------------------------------------------------------ helpers


def storage_key(p: PropertyDef) -> str:
    """The stable cell key for a property: the URI tail (URI-stable under
    renames). Falls back to the name for non-canonical URIs."""
    if "/prop/" in p.uri:
        return p.uri.rsplit("/prop/", 1)[-1]
    return p.name


def is_retired(c: ClassDef) -> bool:
    return RETIRED_MARK in c.definition


def resolve_prop(onto: Ontology, class_uri: str, name: str) -> Optional[tuple[str, PropertyDef]]:
    """Resolve a property by display name on a class, own props first, then
    ancestors (deterministic sorted order). Returns (owner_uri, PropertyDef)."""
    c = onto.get(class_uri)
    if c is None:
        return None
    p = c.prop(name)
    if p is not None:
        return class_uri, p
    for anc in sorted(onto.ancestors(class_uri)):
        ac = onto.get(anc)
        if ac is None:
            continue
        p = ac.prop(name)
        if p is not None:
            return anc, p
    return None


def children_of(onto: Ontology, uri: str) -> list[str]:
    return sorted(u for u, c in onto.classes.items() if uri in c.parents)


def inbound_ranges(onto: Ontology, uri: str) -> list[tuple[str, str]]:
    """(class_uri, prop_name) pairs whose link range targets `uri`."""
    out = []
    for c in onto.iter_classes():
        for p in c.properties:
            if p.is_link and p.range_class == uri:
                out.append((c.uri, p.name))
    return sorted(out)


def subtree(onto: Ontology, uri: str) -> list[str]:
    """uri + descendants, restricted to classes present in `onto`, sorted."""
    out = {uri} | onto.descendants(uri)
    return sorted(u for u in out if u in onto.classes)


def mint_entity_uri(class_uri: str, canonical_value: str) -> str:
    """Deterministic entity URI for a promoted value (content-addressed: equal
    canonical values deduplicate — the §3.6 'through the ER cascade' obligation
    discharged by exact-value identity at fixture scale)."""
    h = xxhash.xxh3_64(canonical_value.encode()).hexdigest()
    return f"{class_uri}/e/{h}"


def _canon(value: Any) -> str:
    from ontoforge.hearth.store import encode_value

    return encode_value(value)


def _require(cond: bool, msg: str) -> None:
    if not cond:
        raise PreconditionError(msg)


def _live_class(onto: Ontology, uri: str, ctx: str) -> ClassDef:
    c = onto.get(uri)
    _require(c is not None, f"{ctx}: class {uri!r} not in ontology")
    _require(not is_retired(c), f"{ctx}: class {uri!r} is retired (extent is read-only)")  # type: ignore[arg-type]
    return c  # type: ignore[return-value]


def compare(value: Any, op: str, ref: Any) -> bool:
    """The shared predicate evaluator (discriminators and query filters)."""
    if value is None:
        return False
    if op == "==":
        return value == ref
    if op == "!=":
        return value != ref
    if op == "in":
        return value in ref
    try:
        if op == "<":
            return value < ref
        if op == "<=":
            return value <= ref
        if op == ">":
            return value > ref
        if op == ">=":
            return value >= ref
    except TypeError:
        return False
    raise ValueError(f"unknown comparison op {op!r}")


# ------------------------------------------------------- value conversions


def conversion(spec: str) -> tuple[Callable[[Any], Any], Callable[[Any], Any], str]:
    """Named, deterministic value conversions for RetypeProperty.

    Returns (forward, inverse, inverse_spec). ``linear:a:b`` maps v -> a*v+b
    (unit rescales); ``int_to_float``/``float_to_int`` are the datatype casts.
    """
    if spec == "int_to_float":
        return (lambda v: float(v), lambda v: int(round(v)), "float_to_int")
    if spec == "float_to_int":
        return (lambda v: int(round(v)), lambda v: float(v), "int_to_float")
    if spec.startswith("linear:"):
        parts = spec.split(":")
        if len(parts) != 3:
            raise PreconditionError(f"malformed linear conversion {spec!r}")
        a, b = float(parts[1]), float(parts[2])
        if a == 0.0:
            raise PreconditionError("linear conversion with a=0 is not invertible")
        inv = f"linear:{1.0 / a!r}:{-b / a!r}"
        return (lambda v: a * v + b, lambda v: (v - b) / a, inv)
    raise PreconditionError(f"unknown conversion {spec!r}")


# ------------------------------------------------------------ migration kit


def _copy_cell(c: ValueCell, *, key: Optional[str] = None, value: Any = None, has_value: bool = False) -> ValueCell:
    """A fresh commit-ready cell carrying the ORIGINAL prov_ref/confidence and
    valid interval; system interval open (store-stamped on commit)."""
    return ValueCell(
        entity_uri=c.entity_uri,
        prop=key if key is not None else c.prop,
        value=value if has_value else c.value,
        valid=c.valid,
        system=Interval(0),
        prov_ref=c.prov_ref,
        confidence=c.confidence,
        src_rank=max(c.src_rank, 1),
    )


def _insert_prop(props: tuple, p, index: Optional[int]) -> tuple:
    lst = list(props)
    lst.insert(len(lst) if index is None else min(index, len(lst)), p)
    return tuple(lst)


# =========================================================================
# Operator base
# =========================================================================


@dataclass(frozen=True)
class Operator:
    @property
    def op_type(self) -> str:
        return type(self).__name__

    def precondition(self, onto: Ontology, data: Optional["DataAdapter"]) -> None:
        raise NotImplementedError

    def rewrite(self, onto: Ontology) -> Ontology:
        raise NotImplementedError

    def migrate(self, pre: Ontology, post: Ontology, data: "DataAdapter") -> dict[str, Any]:
        return {}

    def invert(self, pre: Ontology) -> Optional["Operator"]:
        return None

    # ---- serialization (morphism ledger payloads) ----
    def params(self) -> dict[str, Any]:
        return {f.name: getattr(self, f.name) for f in fields(self)}


def _tupled(v: Any) -> Any:
    if isinstance(v, list):
        return tuple(_tupled(x) for x in v)
    if isinstance(v, tuple):
        return tuple(_tupled(x) for x in v)
    return v


def op_from_dict(d: dict[str, Any]) -> Operator:
    cls = OP_REGISTRY[d["op_type"]]
    kwargs = {k: _tupled(v) for k, v in d.items() if k != "op_type"}
    return cls(**kwargs)


def op_to_dict(op: Operator) -> dict[str, Any]:
    out: dict[str, Any] = {"op_type": op.op_type}
    for k, v in op.params().items():
        out[k] = list(v) if isinstance(v, tuple) else v
    # tuples nested inside tuples (e.g. parts) need list-ification for JSON
    def jsonify(v: Any) -> Any:
        if isinstance(v, (tuple, list)):
            return [jsonify(x) for x in v]
        return v

    return {k: jsonify(v) for k, v in out.items()}


# =========================================================================
# Label / axiom-only operators (zero migration)
# =========================================================================


@dataclass(frozen=True)
class AddClass(Operator):
    uri: str
    name: str
    parent: Optional[str] = None

    def precondition(self, onto, data) -> None:
        _require(onto.get(self.uri) is None, f"AddClass: {self.uri!r} already exists")
        if self.parent is not None:
            _live_class(onto, self.parent, "AddClass(parent)")

    def rewrite(self, onto: Ontology) -> Ontology:
        o = onto.clone()
        o.add(ClassDef(uri=self.uri, name=self.name, parents=(self.parent,) if self.parent else ()))
        return o

    def invert(self, pre) -> Operator:
        return DropClass(uri=self.uri)


@dataclass(frozen=True)
class DropClass(Operator):
    """Categorical inverse of AddClass: removable only while untouched (empty
    extent, no children, no inbound ranges)."""

    uri: str

    def precondition(self, onto, data) -> None:
        _require(onto.get(self.uri) is not None, f"DropClass: {self.uri!r} not in ontology")
        _require(not children_of(onto, self.uri), f"DropClass: {self.uri!r} has subclasses")
        _require(not inbound_ranges(onto, self.uri), f"DropClass: {self.uri!r} is a link range")
        if data is not None:
            _require(not data.extent_own(self.uri), f"DropClass: {self.uri!r} has a populated extent")

    def rewrite(self, onto: Ontology) -> Ontology:
        o = onto.clone()
        del o.classes[self.uri]
        return o


@dataclass(frozen=True)
class RenameClass(Operator):
    uri: str
    new_name: str

    def precondition(self, onto, data) -> None:
        _live_class(onto, self.uri, "RenameClass")

    def rewrite(self, onto: Ontology) -> Ontology:
        o = onto.clone()
        o.replace_class(replace(o.classes[self.uri], name=self.new_name))
        return o

    def invert(self, pre) -> Operator:
        return RenameClass(uri=self.uri, new_name=pre.classes[self.uri].name)


@dataclass(frozen=True)
class RenameProperty(Operator):
    """Label-only: the property URI (and hence the HEARTH cell key) is stable."""

    class_uri: str
    prop_name: str
    new_name: str

    def precondition(self, onto, data) -> None:
        c = _live_class(onto, self.class_uri, "RenameProperty")
        _require(c.prop(self.prop_name) is not None, f"RenameProperty: {self.prop_name!r} is not an own property of {self.class_uri!r}")
        _require(c.prop(self.new_name) is None, f"RenameProperty: {self.new_name!r} already exists on {self.class_uri!r}")

    def rewrite(self, onto: Ontology) -> Ontology:
        o = onto.clone()
        c = o.classes[self.class_uri]
        props = tuple(replace(p, name=self.new_name) if p.name == self.prop_name else p for p in c.properties)
        shapes = tuple(replace(s, prop=self.new_name) if s.prop == self.prop_name else s for s in c.shapes)
        o.replace_class(replace(c, properties=props, shapes=shapes))
        return o

    def invert(self, pre) -> Operator:
        return RenameProperty(class_uri=self.class_uri, prop_name=self.new_name, new_name=self.prop_name)


@dataclass(frozen=True)
class RetireClass(Operator):
    """Tombstone: the class stays in O (so its frozen extent stays readable);
    TEMPER refuses every subsequent operator that would touch it."""

    uri: str

    def precondition(self, onto, data) -> None:
        _live_class(onto, self.uri, "RetireClass")

    def rewrite(self, onto: Ontology) -> Ontology:
        o = onto.clone()
        c = o.classes[self.uri]
        o.replace_class(replace(c, definition=c.definition + RETIRED_MARK))
        return o

    def invert(self, pre) -> Operator:
        return UnretireClass(uri=self.uri)


@dataclass(frozen=True)
class UnretireClass(Operator):
    uri: str

    def precondition(self, onto, data) -> None:
        c = onto.get(self.uri)
        _require(c is not None, f"UnretireClass: {self.uri!r} not in ontology")
        _require(is_retired(c), f"UnretireClass: {self.uri!r} is not retired")  # type: ignore[arg-type]

    def rewrite(self, onto: Ontology) -> Ontology:
        o = onto.clone()
        c = o.classes[self.uri]
        o.replace_class(replace(c, definition=c.definition.replace(RETIRED_MARK, "")))
        return o

    def invert(self, pre) -> Operator:
        return RetireClass(uri=self.uri)


@dataclass(frozen=True)
class AddProperty(Operator):
    class_uri: str
    name: str
    datatype: str = "string"
    unit: Optional[str] = None
    range_class: Optional[str] = None  # non-None => link property
    cardinality: str = "one"

    def precondition(self, onto, data) -> None:
        c = _live_class(onto, self.class_uri, "AddProperty")
        _require(resolve_prop(onto, self.class_uri, self.name) is None,
                 f"AddProperty: {self.name!r} already resolvable on {self.class_uri!r}")
        Datatype(self.datatype)
        if self.range_class is not None:
            _require(onto.get(self.range_class) is not None,
                     f"AddProperty: unknown range class {self.range_class!r}")

    def rewrite(self, onto: Ontology) -> Ontology:
        o = onto.clone()
        c = o.classes[self.class_uri]
        p = PropertyDef(
            uri=property_uri(self.class_uri, self.name),
            name=self.name,
            datatype=Datatype(self.datatype),
            is_link=self.range_class is not None,
            range_class=self.range_class,
            unit=self.unit,
            cardinality=self.cardinality,
        )
        o.replace_class(replace(c, properties=c.properties + (p,)))
        return o

    def invert(self, pre) -> Operator:
        return DropProperty(class_uri=self.class_uri, name=self.name)


@dataclass(frozen=True)
class DropProperty(Operator):
    """Inverse of AddProperty: only while no data exists under the key."""

    class_uri: str
    name: str

    def precondition(self, onto, data) -> None:
        c = _live_class(onto, self.class_uri, "DropProperty")
        p = c.prop(self.name)
        _require(p is not None, f"DropProperty: {self.name!r} is not an own property of {self.class_uri!r}")
        if data is not None:
            key = storage_key(p)  # type: ignore[arg-type]
            for cu in subtree(onto, self.class_uri):
                for row in data.extent_own(cu).values():
                    _require(key not in row, f"DropProperty: {self.name!r} has data in extent of {cu!r}")

    def rewrite(self, onto: Ontology) -> Ontology:
        o = onto.clone()
        c = o.classes[self.class_uri]
        props = tuple(p for p in c.properties if p.name != self.name)
        shapes = tuple(s for s in c.shapes if s.prop != self.name)
        o.replace_class(replace(c, properties=props, shapes=shapes))
        return o


def _shape_from_params(d: dict[str, Any]) -> ShapeConstraint:
    d = dict(d)
    if d.get("datatype") is not None:
        d["datatype"] = Datatype(d["datatype"])
    if d.get("in_values") is not None:
        d["in_values"] = tuple(d["in_values"])
    return ShapeConstraint(**d)


def _shape_to_params(s: ShapeConstraint) -> dict[str, Any]:
    return {
        "prop": s.prop,
        "min_count": s.min_count,
        "max_count": s.max_count,
        "datatype": s.datatype.value if s.datatype is not None else None,
        "pattern": s.pattern,
        "in_values": list(s.in_values) if s.in_values is not None else None,
        "min_value": s.min_value,
        "max_value": s.max_value,
        "unit": s.unit,
    }


@dataclass(frozen=True)
class AddFacet(Operator):
    """SHACL shape change only (§3.6 table: WARDEN recompiles; no data move)."""

    class_uri: str
    shape: tuple[tuple[str, Any], ...]  # canonical (field, value) pairs
    index: Optional[int] = None         # reinsertion position (for inversion exactness)

    def _constraint(self) -> ShapeConstraint:
        return _shape_from_params(dict(self.shape))

    def precondition(self, onto, data) -> None:
        c = _live_class(onto, self.class_uri, "AddFacet")
        s = self._constraint()
        _require(resolve_prop(onto, self.class_uri, s.prop) is not None,
                 f"AddFacet: shape cites unknown property {s.prop!r}")

    def rewrite(self, onto: Ontology) -> Ontology:
        o = onto.clone()
        c = o.classes[self.class_uri]
        s = self._constraint()
        shapes = list(c.shapes)
        idx = len(shapes) if self.index is None else min(self.index, len(shapes))
        shapes.insert(idx, s)
        o.replace_class(replace(c, shapes=tuple(shapes)))
        return o

    def invert(self, pre) -> Operator:
        return RetireFacet(class_uri=self.class_uri, shape=self.shape)


@dataclass(frozen=True)
class RetireFacet(Operator):
    class_uri: str
    shape: tuple[tuple[str, Any], ...]

    def _constraint(self) -> ShapeConstraint:
        return _shape_from_params(dict(self.shape))

    def precondition(self, onto, data) -> None:
        c = _live_class(onto, self.class_uri, "RetireFacet")
        _require(self._constraint() in c.shapes,
                 f"RetireFacet: no matching shape on {self.class_uri!r}")

    def rewrite(self, onto: Ontology) -> Ontology:
        o = onto.clone()
        c = o.classes[self.class_uri]
        s = self._constraint()
        shapes = list(c.shapes)
        shapes.remove(s)  # first match
        o.replace_class(replace(c, shapes=tuple(shapes)))
        return o

    def invert(self, pre) -> Operator:
        c = pre.classes[self.class_uri]
        return AddFacet(class_uri=self.class_uri, shape=self.shape, index=c.shapes.index(self._constraint()))


def facet_params(s: ShapeConstraint) -> tuple[tuple[str, Any], ...]:
    """Canonical (field, value) tuple form of a ShapeConstraint for op params."""
    return tuple(sorted(_shape_to_params(s).items()))


# =========================================================================
# Data-touching operators
# =========================================================================


@dataclass(frozen=True)
class RetypeProperty(Operator):
    """Datatype/unit change. Forward = conversion plan over HEARTH cells
    (touched extent only); backward = inverse conversion view (views.py)."""

    class_uri: str
    prop_name: str
    new_datatype: str
    conversion_spec: str
    new_unit: Optional[str] = None

    def precondition(self, onto, data) -> None:
        c = _live_class(onto, self.class_uri, "RetypeProperty")
        p = c.prop(self.prop_name)
        _require(p is not None, f"RetypeProperty: {self.prop_name!r} is not an own property of {self.class_uri!r}")
        _require(not p.is_link, "RetypeProperty: cannot retype a link property")  # type: ignore[union-attr]
        Datatype(self.new_datatype)
        fwd, _, _ = conversion(self.conversion_spec)
        if self.conversion_spec == "int_to_float":
            _require(p.datatype is Datatype.INTEGER, "int_to_float requires an INTEGER property")  # type: ignore[union-attr]
        if self.conversion_spec.startswith("linear:"):
            _require(p.datatype in (Datatype.FLOAT, Datatype.INTEGER),  # type: ignore[union-attr]
                     "linear conversion requires a numeric property")

    def rewrite(self, onto: Ontology) -> Ontology:
        o = onto.clone()
        c = o.classes[self.class_uri]
        props = tuple(
            replace(p, datatype=Datatype(self.new_datatype), unit=self.new_unit)
            if p.name == self.prop_name else p
            for p in c.properties
        )
        o.replace_class(replace(c, properties=props))
        return o

    def migrate(self, pre, post, data) -> dict[str, Any]:
        fwd, _, _ = conversion(self.conversion_spec)
        key = storage_key(pre.classes[self.class_uri].prop(self.prop_name))  # type: ignore[arg-type]
        cells_written = 0
        entities = 0
        for cu in subtree(pre, self.class_uri):
            batch: list[ValueCell] = []
            for entity, row in sorted(data.extent_own(cu).items()):
                cell = row.get(key)
                if cell is None:
                    continue
                batch.append(_copy_cell(cell, value=fwd(cell.value), has_value=True))
                entities += 1
            if batch:
                data.commit_cells(cu, batch)
                cells_written += len(batch)
        return {"cells_written": cells_written, "entities_touched": entities}

    def invert(self, pre) -> Operator:
        p = pre.classes[self.class_uri].prop(self.prop_name)
        _, _, inv_spec = conversion(self.conversion_spec)
        return RetypeProperty(
            class_uri=self.class_uri,
            prop_name=self.prop_name,
            new_datatype=p.datatype.value,  # type: ignore[union-attr]
            conversion_spec=inv_spec,
            new_unit=p.unit,  # type: ignore[union-attr]
        )


@dataclass(frozen=True)
class Generalize(Operator):
    """Move property p from class c up to parent: WIDEN — no data move (§3.6)."""

    class_uri: str          # c (current owner)
    parent_uri: str
    prop_name: str
    index: Optional[int] = None   # insertion position on the parent (inversion exactness)

    def precondition(self, onto, data) -> None:
        c = _live_class(onto, self.class_uri, "Generalize")
        _live_class(onto, self.parent_uri, "Generalize(parent)")
        _require(self.parent_uri in c.parents, f"Generalize: {self.parent_uri!r} is not a parent of {self.class_uri!r}")
        _require(c.prop(self.prop_name) is not None,
                 f"Generalize: {self.prop_name!r} is not an own property of {self.class_uri!r}")
        _require(resolve_prop(onto, self.parent_uri, self.prop_name) is None,
                 f"Generalize: {self.prop_name!r} already resolvable on {self.parent_uri!r}")

    def rewrite(self, onto: Ontology) -> Ontology:
        o = onto.clone()
        c = o.classes[self.class_uri]
        p = c.prop(self.prop_name)
        o.replace_class(replace(c, properties=tuple(q for q in c.properties if q.name != self.prop_name)))
        parent = o.classes[self.parent_uri]
        o.replace_class(replace(parent, properties=_insert_prop(parent.properties, p, self.index)))
        return o

    def invert(self, pre) -> Operator:
        pos = [q.name for q in pre.classes[self.class_uri].properties].index(self.prop_name)
        return Specialize(parent_uri=self.parent_uri, child_uri=self.class_uri,
                          prop_name=self.prop_name, index=pos)


@dataclass(frozen=True)
class Specialize(Operator):
    """Move property p from parent down to child: NARROW — spine-gated instance
    check; instances of other subtrees holding p are quarantined (marker cell)."""

    parent_uri: str
    child_uri: str
    prop_name: str
    index: Optional[int] = None   # insertion position on the child (inversion exactness)

    def precondition(self, onto, data) -> None:
        parent = _live_class(onto, self.parent_uri, "Specialize(parent)")
        child = _live_class(onto, self.child_uri, "Specialize(child)")
        _require(self.parent_uri in child.parents, f"Specialize: {self.parent_uri!r} is not a parent of {self.child_uri!r}")
        p = parent.prop(self.prop_name)
        _require(p is not None, f"Specialize: {self.prop_name!r} is not an own property of {self.parent_uri!r}")

    def violators(self, onto: Ontology, data: "DataAdapter") -> list[tuple[str, str]]:
        """(class_uri, entity) pairs outside the child subtree that hold p."""
        p = onto.classes[self.parent_uri].prop(self.prop_name)
        key = storage_key(p)  # type: ignore[arg-type]
        keep = set(subtree(onto, self.child_uri))
        out: list[tuple[str, str]] = []
        for cu in subtree(onto, self.parent_uri):
            if cu in keep:
                continue
            for entity, row in sorted(data.extent_own(cu).items()):
                if key in row:
                    out.append((cu, entity))
        return out

    def rewrite(self, onto: Ontology) -> Ontology:
        o = onto.clone()
        parent = o.classes[self.parent_uri]
        p = parent.prop(self.prop_name)
        o.replace_class(replace(parent, properties=tuple(q for q in parent.properties if q.name != self.prop_name)))
        child = o.classes[self.child_uri]
        o.replace_class(replace(child, properties=_insert_prop(child.properties, p, self.index)))
        return o

    def migrate(self, pre, post, data) -> dict[str, Any]:
        viol = self.violators(pre, data)
        p = pre.classes[self.parent_uri].prop(self.prop_name)
        key = storage_key(p)  # type: ignore[arg-type]
        quarantined: list[str] = []
        by_class: dict[str, list[ValueCell]] = {}
        for cu, entity in viol:
            cell = data.extent_own(cu)[entity][key]
            marker = _copy_cell(cell, key=QUARANTINE_PROP, value=f"specialize:{self.prop_name}->{self.child_uri}", has_value=True)
            by_class.setdefault(cu, []).append(marker)
            quarantined.append(entity)
        cells = 0
        for cu in sorted(by_class):
            data.commit_cells(cu, by_class[cu])
            cells += len(by_class[cu])
        return {"cells_written": cells, "entities_touched": len(quarantined), "quarantined": tuple(quarantined)}

    def invert(self, pre) -> Operator:
        pos = [q.name for q in pre.classes[self.parent_uri].properties].index(self.prop_name)
        return Generalize(class_uri=self.child_uri, parent_uri=self.parent_uri,
                          prop_name=self.prop_name, index=pos)


@dataclass(frozen=True)
class SplitClass(Operator):
    """Replace c by (c1, c2); instances routed by a TOTAL discriminator
    predicate over property values. Backward = union view. Spine-gated on
    populated extents (§3.6 autonomy integration)."""

    uri: str
    parts: tuple[tuple[str, str], ...]          # ((uri1, name1), (uri2, name2))
    discriminator: tuple[str, str, Any]         # (prop_name, cmp_op, value): True -> part 1

    def precondition(self, onto, data) -> None:
        c = _live_class(onto, self.uri, "SplitClass")
        _require(len(self.parts) == 2, "SplitClass: exactly two parts required")
        for u, _n in self.parts:
            _require(onto.get(u) is None, f"SplitClass: part uri {u!r} already exists")
        _require(self.parts[0][0] != self.parts[1][0], "SplitClass: part uris must differ")
        _require(not children_of(onto, self.uri), f"SplitClass: {self.uri!r} has subclasses")
        _require(not inbound_ranges(onto, self.uri), f"SplitClass: {self.uri!r} is a link range")
        prop, op, _v = self.discriminator
        key = self._disc_key(onto)
        _require(key is not None, f"SplitClass: discriminator property {prop!r} not resolvable on {self.uri!r}")
        if data is not None:
            for entity, row in sorted(data.extent_own(self.uri).items()):
                _require(key in row, f"SplitClass: discriminator not TOTAL — {entity!r} lacks {prop!r}")
                compare(row[key].value, op, _v)  # raises on unknown op

    def rewrite(self, onto: Ontology) -> Ontology:
        o = onto.clone()
        c = o.classes[self.uri]
        del o.classes[self.uri]
        for u, n in self.parts:
            o.add(replace(c, uri=u, name=n))
        return o

    def _disc_key(self, onto: Ontology) -> Optional[str]:
        """Storage key of the discriminator: ontology property, or a retained
        TEMPER-internal column (e.g. a merge's origin key — Merge<->Split)."""
        prop = self.discriminator[0]
        if prop.startswith(ORIGIN_PREFIX):
            return prop
        r = resolve_prop(onto, self.uri, prop)
        return storage_key(r[1]) if r is not None else None

    def migrate(self, pre, post, data) -> dict[str, Any]:
        prop, op, v = self.discriminator
        key = self._disc_key(pre)
        routed = {self.parts[0][0]: 0, self.parts[1][0]: 0}
        batches: dict[str, list[ValueCell]] = {self.parts[0][0]: [], self.parts[1][0]: []}
        cells = 0
        for entity, row in sorted(data.extent_own(self.uri).items()):
            dest = self.parts[0][0] if compare(row[key].value, op, v) else self.parts[1][0]
            routed[dest] += 1
            for k in sorted(row):
                batches[dest].append(_copy_cell(row[k]))
                cells += 1
        for dest in sorted(batches):
            if batches[dest]:
                data.commit_cells(dest, batches[dest])
        return {"cells_written": cells, "entities_touched": sum(routed.values()), "routed": routed}

    def invert(self, pre) -> Operator:
        c = pre.classes[self.uri]
        tail = self.parts[0][0].rsplit("/", 1)[-1]
        return MergeClasses(
            c1_uri=self.parts[0][0],
            c2_uri=self.parts[1][0],
            new_uri=c.uri,
            new_name=c.name,
            alignment=(),
            origin_key=f"{ORIGIN_PREFIX}~{tail}",
        )


@dataclass(frozen=True)
class MergeClasses(Operator):
    """Union + property alignment map; a RETAINED per-merge provenance column
    (origin_key) makes the backward discriminator-split view exact. Spine-gated
    on populated extents."""

    c1_uri: str
    c2_uri: str
    new_uri: str
    new_name: str
    alignment: tuple[tuple[str, str], ...] = ()   # (c2 prop name -> c1 prop name)
    origin_key: str = ORIGIN_PREFIX

    def _fold_map(self, onto: Ontology) -> dict[str, str]:
        """c2 prop name -> c1 prop name (explicit alignment + implicit same-name)."""
        c1, c2 = onto.classes[self.c1_uri], onto.classes[self.c2_uri]
        explicit = dict(self.alignment)
        c1_names = {p.name for p in c1.properties}
        out: dict[str, str] = {}
        for p in c2.properties:
            if p.name in explicit:
                out[p.name] = explicit[p.name]
            elif p.name in c1_names:
                out[p.name] = p.name
        return out

    def precondition(self, onto, data) -> None:
        c1 = _live_class(onto, self.c1_uri, "MergeClasses(c1)")
        c2 = _live_class(onto, self.c2_uri, "MergeClasses(c2)")
        _require(self.c1_uri != self.c2_uri, "MergeClasses: c1 == c2")
        _require(onto.get(self.new_uri) is None,
                 f"MergeClasses: target uri {self.new_uri!r} already exists")
        _require(self.origin_key.startswith(ORIGIN_PREFIX), "MergeClasses: origin_key must be a __temper_origin key")
        for u in (self.c1_uri, self.c2_uri):
            _require(not children_of(onto, u), f"MergeClasses: {u!r} has subclasses")
            _require(not inbound_ranges(onto, u), f"MergeClasses: {u!r} is a link range")
        for src, dst in self.alignment:
            _require(c2.prop(src) is not None, f"MergeClasses: alignment source {src!r} not on c2")
            _require(c1.prop(dst) is not None, f"MergeClasses: alignment target {dst!r} not on c1")
        for src, dst in self._fold_map(onto).items():
            ps, pd = c2.prop(src), c1.prop(dst)
            _require(ps is not None and pd is not None and ps.datatype == pd.datatype and ps.is_link == pd.is_link,
                     f"MergeClasses: fold {src!r}->{dst!r} is not type-compatible")
        if data is not None:
            e1 = set(data.extent_own(self.c1_uri))
            e2 = set(data.extent_own(self.c2_uri))
            _require(not (e1 & e2), "MergeClasses: extents are not disjoint")

    def rewrite(self, onto: Ontology) -> Ontology:
        o = onto.clone()
        c1, c2 = o.classes[self.c1_uri], o.classes[self.c2_uri]
        fold = self._fold_map(o)
        merged_props = c1.properties + tuple(p for p in c2.properties if p.name not in fold)
        extra_shapes = tuple(s for s in c2.shapes if s.prop not in fold)
        parents = c1.parents if c1.parents == c2.parents else tuple(sorted(set(c1.parents) | set(c2.parents)))
        del o.classes[self.c1_uri]
        del o.classes[self.c2_uri]
        o.add(replace(c1, uri=self.new_uri, name=self.new_name, parents=parents,
                      properties=merged_props, shapes=c1.shapes + extra_shapes))
        return o

    def key_map_c2(self, pre: Ontology) -> dict[str, str]:
        """c2 storage key -> merged storage key for folded properties."""
        c1, c2 = pre.classes[self.c1_uri], pre.classes[self.c2_uri]
        fold = self._fold_map(pre)
        out: dict[str, str] = {}
        for src, dst in fold.items():
            k_src = storage_key(c2.prop(src))  # type: ignore[arg-type]
            k_dst = storage_key(c1.prop(dst))  # type: ignore[arg-type]
            if k_src != k_dst:
                out[k_src] = k_dst
        return out

    def migrate(self, pre, post, data) -> dict[str, Any]:
        kmap = self.key_map_c2(pre)
        cells = 0
        entities = 0
        for src_uri, remap in ((self.c1_uri, {}), (self.c2_uri, kmap)):
            batch: list[ValueCell] = []
            for entity, row in sorted(data.extent_own(src_uri).items()):
                entities += 1
                first_key = min(row)
                for k in sorted(row):
                    batch.append(_copy_cell(row[k], key=remap.get(k, k)))
                # retained discriminator column (per-merge key)
                batch.append(_copy_cell(row[first_key], key=self.origin_key, value=src_uri, has_value=True))
            if batch:
                data.commit_cells(self.new_uri, batch)
                cells += len(batch)
        return {"cells_written": cells, "entities_touched": entities}

    def invert(self, pre) -> Optional[Operator]:
        c2 = pre.classes[self.c2_uri]
        fold = self._fold_map(pre)
        if any(p.name not in fold for p in c2.properties):
            return None  # only total alignments are invertible (no residual props)
        c1 = pre.classes[self.c1_uri]
        return SplitClass(
            uri=self.new_uri,
            parts=((self.c1_uri, c1.name), (self.c2_uri, c2.name)),
            discriminator=(self.origin_key, "==", self.c1_uri),
        )


@dataclass(frozen=True)
class PromoteProperty(Operator):
    """p of c -> new class c_p + link. Forward: group-by p's current values,
    mint content-addressed entities (equal values DEDUPLICATE), rewrite p as a
    link. Backward = rejoin view. Inverse = DemoteClass."""

    class_uri: str
    prop_name: str
    new_class_uri: str
    new_class_name: str
    value_prop: str = "value"

    def precondition(self, onto, data) -> None:
        c = _live_class(onto, self.class_uri, "PromoteProperty")
        p = c.prop(self.prop_name)
        _require(p is not None, f"PromoteProperty: {self.prop_name!r} is not an own property of {self.class_uri!r}")
        _require(not p.is_link, "PromoteProperty: property is already a link")  # type: ignore[union-attr]
        _require(onto.get(self.new_class_uri) is None, f"PromoteProperty: {self.new_class_uri!r} already exists")

    def rewrite(self, onto: Ontology) -> Ontology:
        o = onto.clone()
        c = o.classes[self.class_uri]
        p = c.prop(self.prop_name)
        vp = PropertyDef(
            uri=property_uri(self.new_class_uri, self.value_prop),
            name=self.value_prop,
            datatype=p.datatype,  # type: ignore[union-attr]
            unit=p.unit,  # type: ignore[union-attr]
            dimension=p.dimension,  # type: ignore[union-attr]
        )
        o.add(ClassDef(uri=self.new_class_uri, name=self.new_class_name, properties=(vp,)))
        link = replace(p, is_link=True, range_class=self.new_class_uri,  # type: ignore[arg-type]
                       datatype=Datatype.STRING, unit=None, dimension=None)
        o.replace_class(replace(c, properties=tuple(link if q.name == self.prop_name else q for q in c.properties)))
        return o

    def migrate(self, pre, post, data) -> dict[str, Any]:
        p = pre.classes[self.class_uri].prop(self.prop_name)
        key = storage_key(p)  # type: ignore[arg-type]
        vkey = self.value_prop  # storage key of a freshly minted property == its name
        minted: dict[str, ValueCell] = {}      # minted uri -> representative source cell
        link_batches: dict[str, list[ValueCell]] = {}
        entities = 0
        for cu in subtree(pre, self.class_uri):
            for entity, row in sorted(data.extent_own(cu).items()):
                cell = row.get(key)
                if cell is None:
                    continue
                entities += 1
                uri = mint_entity_uri(self.new_class_uri, _canon(cell.value))
                if uri not in minted:
                    minted[uri] = cell
                link_batches.setdefault(cu, []).append(_copy_cell(cell, value=uri, has_value=True))
        cells = 0
        if minted:
            batch = [
                ValueCell(entity_uri=u, prop=vkey, value=minted[u].value, valid=Interval(0),
                          system=Interval(0), prov_ref=minted[u].prov_ref,
                          confidence=minted[u].confidence, src_rank=max(minted[u].src_rank, 1))
                for u in sorted(minted)
            ]
            data.commit_cells(self.new_class_uri, batch)
            cells += len(batch)
        for cu in sorted(link_batches):
            data.commit_cells(cu, link_batches[cu])
            cells += len(link_batches[cu])
        return {"cells_written": cells, "entities_touched": entities, "minted": len(minted)}

    def invert(self, pre) -> Operator:
        return DemoteClass(
            owner_class_uri=self.class_uri,
            link_prop=self.prop_name,
            class_uri=self.new_class_uri,
            value_prop=self.value_prop,
        )


@dataclass(frozen=True)
class DemoteClass(Operator):
    """Inverse of PromoteProperty: flatten c_p back into a direct property of
    the owner via join over the link. Backward = regroup view."""

    owner_class_uri: str
    link_prop: str
    class_uri: str            # c_p
    value_prop: str = "value"

    def precondition(self, onto, data) -> None:
        owner = _live_class(onto, self.owner_class_uri, "DemoteClass(owner)")
        cp = _live_class(onto, self.class_uri, "DemoteClass")
        p = owner.prop(self.link_prop)
        _require(p is not None and p.is_link and p.range_class == self.class_uri,
                 f"DemoteClass: {self.link_prop!r} is not a link from {self.owner_class_uri!r} to {self.class_uri!r}")
        _require(cp.prop(self.value_prop) is not None,
                 f"DemoteClass: {self.value_prop!r} not on {self.class_uri!r}")
        _require(not children_of(onto, self.class_uri), f"DemoteClass: {self.class_uri!r} has subclasses")
        refs = [r for r in inbound_ranges(onto, self.class_uri) if r != (self.owner_class_uri, self.link_prop)]
        _require(not refs, f"DemoteClass: {self.class_uri!r} is also a link range of {refs}")

    def rewrite(self, onto: Ontology) -> Ontology:
        o = onto.clone()
        cp = o.classes[self.class_uri]
        vp = cp.prop(self.value_prop)
        del o.classes[self.class_uri]
        owner = o.classes[self.owner_class_uri]
        restored = tuple(
            replace(q, is_link=False, range_class=None, datatype=vp.datatype, unit=vp.unit, dimension=vp.dimension)  # type: ignore[union-attr]
            if q.name == self.link_prop else q
            for q in owner.properties
        )
        o.replace_class(replace(owner, properties=restored))
        return o

    def migrate(self, pre, post, data) -> dict[str, Any]:
        owner = pre.classes[self.owner_class_uri]
        key = storage_key(owner.prop(self.link_prop))  # type: ignore[arg-type]
        cp = pre.classes[self.class_uri]
        vkey = storage_key(cp.prop(self.value_prop))  # type: ignore[arg-type]
        values = {e: row[vkey].value for e, row in data.extent_own(self.class_uri).items() if vkey in row}
        cells = 0
        entities = 0
        for cu in subtree(pre, self.owner_class_uri):
            batch: list[ValueCell] = []
            for entity, row in sorted(data.extent_own(cu).items()):
                cell = row.get(key)
                if cell is None or cell.value not in values:
                    continue
                batch.append(_copy_cell(cell, value=values[cell.value], has_value=True))
                entities += 1
            if batch:
                data.commit_cells(cu, batch)
                cells += len(batch)
        return {"cells_written": cells, "entities_touched": entities}

    def invert(self, pre) -> Operator:
        return PromoteProperty(
            class_uri=self.owner_class_uri,
            prop_name=self.link_prop,
            new_class_uri=self.class_uri,
            new_class_name=pre.classes[self.class_uri].name,
            value_prop=self.value_prop,
        )


# ----------------------------------------------------------------- registry

OP_REGISTRY: dict[str, type] = {
    cls.__name__: cls
    for cls in (
        AddClass, DropClass, RenameClass, RenameProperty, RetireClass, UnretireClass,
        AddProperty, DropProperty, AddFacet, RetireFacet,
        RetypeProperty, Generalize, Specialize,
        SplitClass, MergeClasses, PromoteProperty, DemoteClass,
    )
}

# Operators whose forward migration may touch HEARTH cells.
DATA_TOUCHING = frozenset({"RetypeProperty", "Specialize", "SplitClass", "MergeClasses", "PromoteProperty", "DemoteClass"})
# High-impact structural operators (spine-gated on populated extents, §3.6).
SPINE_GATED = frozenset({"SplitClass", "MergeClasses"})
