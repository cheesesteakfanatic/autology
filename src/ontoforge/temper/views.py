"""M10 — TEMPER backward views: query rewriting across ontology versions.

Discharges the §3.6 snapshot-queryability obligation in executable form:
a StructuredQuery authored against O^(s) is rewritten — by composing the
per-operator backward views of every operator applied since s — into an
equivalent Plan against O^(t), answering identically over the data HEARTH
holds now (on the slice the migrations moved).

Per-operator backward views (the §3.6 table, right column):

* AddClass / Retire* / Add/DropProperty / facets / renames — identity
  (URIs and storage keys are stable; renames are label-only).
* RetypeProperty — inverse conversion view: accessors compose the inverse
  conversion so values and filter comparisons happen in the QUERY's units.
* SplitClass — union view: the branch over c becomes branches over c1 ∪ c2.
* MergeClasses — discriminator-split view: branch over c_i becomes a branch
  over the merged class filtered on the RETAINED per-merge origin column.
* PromoteProperty — rejoin view: a Direct read of p becomes a Deref through
  the minted link into c_p's value property.
* DemoteClass — (a) queries reading through the link collapse Deref -> Direct
  (the value is inline again); (b) queries AGAINST c_p regroup: distinct
  owner values re-mint the same content-addressed entity URIs.
* Generalize / Specialize — identity (storage keys stable; extents are
  polymorphic at execution time).

The Plan is a closed, executable IR: branches (union semantics) of accessor
trees over the CURRENT ontology + HEARTH state. Execution is deterministic
(sorted extents, canonical-JSON value normalization).
"""

from __future__ import annotations

from dataclasses import dataclass, replace as dc_replace
from typing import Any, Callable, Optional, Union

from ontoforge.contracts import Layer, Ontology

from .ops import (
    Operator,
    DemoteClass,
    MergeClasses,
    PromoteProperty,
    RetypeProperty,
    SplitClass,
    compare,
    conversion,
    mint_entity_uri,
    resolve_prop,
    storage_key,
    subtree,
)

# ----------------------------------------------------------------- query IR


@dataclass(frozen=True)
class StructuredQuery:
    """The simple structured query of the M10 contract:
    {class_uri, property filters, projection} — properties by display name
    as of the authoring version."""

    class_uri: str
    filters: tuple[tuple[str, str, Any], ...] = ()   # (prop_name, cmp_op, value)
    projection: tuple[str, ...] = ()


@dataclass(frozen=True)
class Direct:
    """Read the entity's own cell under a stable storage key; `fn` maps the
    stored (current-schema) value back into the query version's terms."""

    key: str
    fn: Optional[Callable[[Any], Any]] = None


@dataclass(frozen=True)
class Deref:
    """Follow a link value (target entity URI) into one of `targets`' extents
    and evaluate `inner` on the target row (rejoin view)."""

    link_key: str
    targets: tuple[str, ...]
    inner: "Accessor"


Accessor = Union[Direct, Deref]


@dataclass(frozen=True)
class Regroup:
    """Branch extent synthesized by grouping owner_key values over the owner
    class extent and re-minting the content-addressed entity URIs (backward
    view of DemoteClass for queries against the demoted class)."""

    owner_class: str
    owner_key: str
    value_key: str


@dataclass(frozen=True)
class Branch:
    class_uri: str
    accessors: tuple[tuple[str, Accessor], ...]              # query prop name -> accessor
    extra_filters: tuple[tuple[Accessor, str, Any], ...] = ()
    regroup: Optional[Regroup] = None

    def accessor(self, name: str) -> Accessor:
        for n, a in self.accessors:
            if n == name:
                return a
        raise KeyError(name)


@dataclass(frozen=True)
class Plan:
    query: StructuredQuery
    branches: tuple[Branch, ...]


# --------------------------------------------------------------------- lift


def lift(query: StructuredQuery, onto: Ontology) -> Plan:
    """A query valid under `onto` becomes a single-branch Plan whose accessors
    bind each referenced property to its stable storage key."""
    if onto.get(query.class_uri) is None:
        raise ValueError(f"query class {query.class_uri!r} not in ontology version {onto.version}")
    names: list[str] = []
    for n in tuple(query.projection) + tuple(f[0] for f in query.filters):
        if n not in names:
            names.append(n)
    accessors: list[tuple[str, Accessor]] = []
    for n in names:
        r = resolve_prop(onto, query.class_uri, n)
        if r is None:
            raise ValueError(f"query property {n!r} not resolvable on {query.class_uri!r} at version {onto.version}")
        accessors.append((n, Direct(key=storage_key(r[1]))))
    return Plan(query=query, branches=(Branch(class_uri=query.class_uri, accessors=tuple(accessors)),))


# --------------------------------------------------------- accessor rewrite


def _map_accessors(
    branch: Branch, fn: Callable[[Accessor, tuple[str, ...]], Accessor]
) -> Branch:
    """Apply an accessor transform across the branch, threading the class
    context (the classes whose rows the accessor reads) through Derefs."""

    def walk(acc: Accessor, ctx: tuple[str, ...]) -> Accessor:
        if isinstance(acc, Deref):
            new_inner = walk(acc.inner, acc.targets)
            acc = Deref(link_key=acc.link_key, targets=acc.targets, inner=new_inner)
        return fn(acc, ctx)

    ctx0 = (branch.class_uri,)
    return dc_replace(
        branch,
        accessors=tuple((n, walk(a, ctx0)) for n, a in branch.accessors),
        extra_filters=tuple((walk(a, ctx0), op, v) for a, op, v in branch.extra_filters),
    )


def _compose(old_fn: Optional[Callable], inv: Callable) -> Callable:
    if old_fn is None:
        return inv
    return lambda v: old_fn(inv(v))


# ------------------------------------------------------- per-op rewriters


def op_rewriter(op: Operator, pre: Ontology, post: Ontology) -> Optional[Callable[[Plan], Plan]]:
    """Build the backward-view rewriter for one applied operator (None =
    identity). `pre`/`post` are the ontology versions around the application."""

    if isinstance(op, RetypeProperty):
        p = pre.classes[op.class_uri].prop(op.prop_name)
        key = storage_key(p)  # type: ignore[arg-type]
        scope = set(subtree(pre, op.class_uri))
        _, inv, _ = conversion(op.conversion_spec)

        def tx(acc: Accessor, ctx: tuple[str, ...]) -> Accessor:
            if isinstance(acc, Direct) and acc.key == key and any(c in scope for c in ctx):
                return Direct(key=acc.key, fn=_compose(acc.fn, inv))
            return acc

        def rw(plan: Plan) -> Plan:
            return Plan(plan.query, tuple(_map_accessors(b, tx) for b in plan.branches))

        return rw

    if isinstance(op, SplitClass):
        c = op.uri
        part_uris = tuple(u for u, _n in op.parts)

        def tx(acc: Accessor, ctx: tuple[str, ...]) -> Accessor:
            if isinstance(acc, Deref) and c in acc.targets:
                targets = tuple(t for t in acc.targets if t != c) + part_uris
                return Deref(link_key=acc.link_key, targets=targets, inner=acc.inner)
            return acc

        def rw(plan: Plan) -> Plan:
            out: list[Branch] = []
            for b in plan.branches:
                b = _map_accessors(b, tx)
                if b.class_uri == c:
                    out.extend(dc_replace(b, class_uri=u) for u in part_uris)
                else:
                    out.append(b)
            return Plan(plan.query, tuple(out))

        return rw

    if isinstance(op, MergeClasses):
        kmap = op.key_map_c2(pre)
        origin = op.origin_key
        c1, c2, u = op.c1_uri, op.c2_uri, op.new_uri

        def remap_c2(acc: Accessor, ctx: tuple[str, ...]) -> Accessor:
            if isinstance(acc, Direct) and acc.key in kmap:
                return Direct(key=kmap[acc.key], fn=acc.fn)
            return acc

        def retarget(acc: Accessor, ctx: tuple[str, ...]) -> Accessor:
            if isinstance(acc, Deref) and (c1 in acc.targets or c2 in acc.targets):
                targets = tuple(dict.fromkeys((u if t in (c1, c2) else t) for t in acc.targets))
                return Deref(link_key=acc.link_key, targets=targets, inner=acc.inner)
            return acc

        def rw(plan: Plan) -> Plan:
            out: list[Branch] = []
            for b in plan.branches:
                if b.class_uri == c1:
                    b = dc_replace(b, class_uri=u,
                                   extra_filters=b.extra_filters + ((Direct(key=origin), "==", c1),))
                elif b.class_uri == c2:
                    b = _map_accessors(b, remap_c2)
                    b = dc_replace(b, class_uri=u,
                                   extra_filters=b.extra_filters + ((Direct(key=origin), "==", c2),))
                out.append(_map_accessors(b, retarget))
            return Plan(plan.query, tuple(out))

        return rw

    if isinstance(op, PromoteProperty):
        p = pre.classes[op.class_uri].prop(op.prop_name)
        key = storage_key(p)  # type: ignore[arg-type]
        scope = set(subtree(pre, op.class_uri))
        cp, vkey = op.new_class_uri, op.value_prop

        def tx(acc: Accessor, ctx: tuple[str, ...]) -> Accessor:
            if isinstance(acc, Direct) and acc.key == key and any(c in scope for c in ctx):
                return Deref(link_key=key, targets=(cp,), inner=Direct(key=vkey, fn=acc.fn))
            return acc

        def rw(plan: Plan) -> Plan:
            return Plan(plan.query, tuple(_map_accessors(b, tx) for b in plan.branches))

        return rw

    if isinstance(op, DemoteClass):
        owner = pre.classes[op.owner_class_uri]
        key = storage_key(owner.prop(op.link_prop))  # type: ignore[arg-type]
        cp = op.class_uri
        vkey = storage_key(pre.classes[cp].prop(op.value_prop))  # type: ignore[arg-type]

        def tx(acc: Accessor, ctx: tuple[str, ...]) -> Accessor:
            if isinstance(acc, Deref) and acc.link_key == key and cp in acc.targets and isinstance(acc.inner, Direct):
                return Direct(key=key, fn=acc.inner.fn)
            return acc

        def rw(plan: Plan) -> Plan:
            out: list[Branch] = []
            for b in plan.branches:
                if b.class_uri == cp:
                    out.append(dc_replace(
                        b, class_uri=op.owner_class_uri,
                        regroup=Regroup(owner_class=op.owner_class_uri, owner_key=key, value_key=vkey),
                    ))
                else:
                    out.append(_map_accessors(b, tx))
            return Plan(plan.query, tuple(out))

        return rw

    return None  # identity: label/axiom-only, retire, generalize/specialize


# ------------------------------------------------------------------- chain


class RewriterChain:
    """Composition of applied operators' backward views. `rewrite(q, s)` lifts
    the query against the version-s snapshot and folds every later operator's
    rewriter over it, yielding a Plan valid against the current version."""

    def __init__(self) -> None:
        self._steps: list[tuple[int, Optional[Callable[[Plan], Plan]]]] = []  # (pre_version, fn)

    def add(self, pre_version: int, fn: Optional[Callable[[Plan], Plan]]) -> None:
        self._steps.append((pre_version, fn))

    def rewrite(self, query: StructuredQuery, from_version: int, snapshots: dict[int, Ontology]) -> Plan:
        if from_version not in snapshots:
            raise ValueError(f"no snapshot for version {from_version}")
        plan = lift(query, snapshots[from_version])
        for pre_version, fn in self._steps:
            if pre_version >= from_version and fn is not None:
                plan = fn(plan)
        return plan


# --------------------------------------------------------------- execution


class _Resolver:
    """Current-stance row access over HEARTH entity shards, cached per call.
    Reads only shards of classes present in the CURRENT ontology — superseded
    pre-migration shards (whose classes left O) are invisible, exactly the
    forward-migrated slice the snapshot-queryability theorem speaks about."""

    def __init__(self, hearth, onto: Ontology) -> None:
        self.h = hearth
        self.onto = onto
        self._rows: dict[str, dict[str, dict[str, Any]]] = {}

    def class_rows(self, class_uri: str) -> dict[str, dict[str, Any]]:
        """entity -> {storage_key: current value} for ONE class shard."""
        if class_uri not in self._rows:
            rows: dict[str, dict[str, Any]] = {}
            shard = self.h._shards.get((Layer.ENTITY, class_uri)) if self.h is not None else None
            if shard is not None:
                for (entity, prop), seq in shard.current.items():
                    rows.setdefault(entity, {})[prop] = shard.cells[seq].value
            self._rows[class_uri] = rows
        return self._rows[class_uri]

    def extent(self, class_uri: str) -> dict[str, dict[str, Any]]:
        """Polymorphic extent: the class + its descendants in the current O."""
        out: dict[str, dict[str, Any]] = {}
        for cu in subtree(self.onto, class_uri):
            for entity, row in self.class_rows(cu).items():
                out.setdefault(entity, row)
        return out

    def lookup(self, entity: str, targets: tuple[str, ...]) -> Optional[dict[str, Any]]:
        for t in targets:
            for cu in subtree(self.onto, t) if t in self.onto.classes else (t,):
                row = self.class_rows(cu).get(entity)
                if row is not None:
                    return row
        return None


def _eval(acc: Accessor, row: dict[str, Any], res: _Resolver) -> Any:
    if isinstance(acc, Direct):
        v = row.get(acc.key)
        if v is None:
            return None
        return acc.fn(v) if acc.fn is not None else v
    # Deref
    target = row.get(acc.link_key)
    if target is None:
        return None
    trow = res.lookup(target, acc.targets)
    if trow is None:
        return None
    return _eval(acc.inner, trow, res)


def _normalize(v: Any) -> Any:
    from ontoforge.hearth.store import encode_value

    return encode_value(v)


def execute(plan: Plan, hearth, onto: Ontology) -> frozenset:
    """Deterministic answer set: frozenset of (entity_uri, (canonical projected
    values...)) tuples, unioned across branches."""
    res = _Resolver(hearth, onto)
    q = plan.query
    out: set[tuple] = set()
    for b in plan.branches:
        if b.regroup is not None:
            owner_rows = res.extent(b.regroup.owner_class)
            seen: dict[str, dict[str, Any]] = {}
            for _e, row in sorted(owner_rows.items()):
                v = row.get(b.regroup.owner_key)
                if v is None:
                    continue
                # post-demote the owner holds the raw value again; re-minting
                # the content-addressed URI reproduces promote's entity ids
                uri = mint_entity_uri(plan.query.class_uri, _normalize(v))
                seen.setdefault(uri, {b.regroup.value_key: v})
            rows = seen
        elif b.class_uri in onto.classes:
            rows = res.extent(b.class_uri)
        else:
            rows = {}
        for entity, row in rows.items():
            ok = True
            for name, op, ref in q.filters:
                if not compare(_eval(b.accessor(name), row, res), op, ref):
                    ok = False
                    break
            if ok:
                for acc, op, ref in b.extra_filters:
                    if not compare(_eval(acc, row, res), op, ref):
                        ok = False
                        break
            if not ok:
                continue
            values = tuple(_normalize(_eval(b.accessor(n), row, res)) for n in q.projection)
            out.add((entity, values))
    return frozenset(out)
