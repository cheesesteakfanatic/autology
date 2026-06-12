"""OQIR static type checker (whitepaper §6.2 well-formedness; M12 step 1).

typecheck(term, ontology) -> OQIRType | TypeError_

Statically eliminates the dominant NL2SQL error classes BEFORE execution:

* phantom joins        — every Traverse must follow a real link property of the
                         source class (or a real reverse link), typed by range;
* unknown elements     — Select classes and Condition/Aggregate/TextJoin
                         properties must exist (inheritance-aware); Condition
                         props may be forward link PATHS ('operator.name');
* unit mixing          — a Condition literal expressed in a unit must be
                         CONVERTIBLE to the property's unit (same physical
                         dimension; the conversion is injected at lowering),
                         and a requested output unit (`expect_unit`) must be
                         convertible from the measure's unit. 'Total altitude
                         in dollars' is a TypeError_, never a coercion;
* wrong-grain agg      — SUM/AVG/MIN/MAX demand a numeric, dimension-consistent
                         measure; SUM over TEXT/STRING is rejected;
* TopK needs a Table;  AsOf demands a well-formed stance and wraps anything.

Reverse traversals may be class-ambiguous in isolation (several classes can
link to the same range); the checker propagates a UNION of possible targets
upward and lets enclosing operators (conditions, textJoin props, aggregate
measures) narrow it. A union that survives to the root unresolved is itself a
type error — the plan would not denote a single entity population.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Union

from ontoforge.contracts import Stance
from ontoforge.contracts.ontology import Datatype, Ontology, PropertyDef
from ontoforge.contracts.oqir import (
    Agg,
    Aggregate,
    AsOf,
    CmpOp,
    Condition,
    EntitySetT,
    OQIRTerm,
    OQIRType,
    Select,
    TableT,
    TextJoin,
    TopK,
    Traverse,
    TypeError_,
)
from ontoforge.profiling.units_table import UNITS

from .model import all_props, resolve_prop

NUMERIC = (Datatype.INTEGER, Datatype.FLOAT)
TEXTUAL = (Datatype.STRING, Datatype.TEXT)
DATELIKE = (Datatype.DATE, Datatype.DATETIME)


def _err(msg: str, term: object = None) -> TypeError_:
    return TypeError_(message=msg, term_repr=repr(term) if term is not None else "")


def unit_convertible(from_unit: str, to_unit: str) -> Union[bool, TypeError_]:
    """True iff both units are known and share a physical dimension (§3.2 unit
    algebra). Currencies only convert to themselves (FX is market data)."""
    fu, tu = UNITS.get(from_unit), UNITS.get(to_unit)
    if fu is None:
        return _err(f"unknown unit {from_unit!r}")
    if tu is None:
        return _err(f"unknown unit {to_unit!r}")
    if fu.dimension != tu.dimension:
        return _err(
            f"unit mismatch: cannot convert {from_unit!r} (dimension {fu.dimension}) "
            f"to {to_unit!r} (dimension {tu.dimension}) without a semantic conversion"
        )
    if fu.dimension == UNITS["USD"].dimension and fu.canonical != tu.canonical:
        return _err(
            f"cross-currency conversion {from_unit!r} -> {to_unit!r} requires a "
            f"time-varying FX rate, not a static unit conversion"
        )
    return True


def resolve_path(onto: Ontology, class_uri: str, path: str) -> Union[PropertyDef, TypeError_]:
    """Resolve a dotted forward link path ('model.manufacturer.name') from a
    class to its terminal datatype property."""
    parts = path.split(".")
    at = class_uri
    for i, part in enumerate(parts):
        p = resolve_prop(onto, at, part)
        if p is None:
            c = onto.get(at)
            return _err(f"unknown property {part!r} (path {path!r}) on class {c.name if c else at}")
        if i < len(parts) - 1:
            if not p.is_link or not p.range_class:
                return _err(f"path {path!r}: {part!r} is not a link property")
            at = p.range_class
        else:
            return p
    return _err(f"empty property path {path!r}")


def _check_condition(cond: Condition, class_uri: str, onto: Ontology) -> Optional[TypeError_]:
    p = resolve_path(onto, class_uri, cond.prop)
    if isinstance(p, TypeError_):
        return p
    if p.is_link:
        return _err(
            f"condition terminal {cond.prop!r} is a link property; conditions bind datatype values",
            cond,
        )
    ordered = cond.op in (CmpOp.LT, CmpOp.LE, CmpOp.GT, CmpOp.GE, CmpOp.BETWEEN)
    if ordered and p.datatype in TEXTUAL and not isinstance(cond.value, str):
        return _err(
            f"ordered comparison {cond.op.value!r} with non-string literal on "
            f"{p.datatype.value} property {cond.prop!r}",
            cond,
        )
    if p.datatype in NUMERIC and ordered and not isinstance(cond.value, (int, float)):
        return _err(
            f"numeric property {cond.prop!r} compared to non-numeric literal {cond.value!r}", cond
        )
    if cond.op is CmpOp.CONTAINS and p.datatype not in TEXTUAL:
        return _err(f"contains-match on non-textual property {cond.prop!r}", cond)
    if cond.unit is not None:
        if p.unit is None:
            return _err(
                f"literal carries unit {cond.unit!r} but property {cond.prop!r} has no unit", cond
            )
        ok = unit_convertible(cond.unit, p.unit)
        if ok is not True:
            return ok  # type: ignore[return-value]
    return None


def _check_stance(stance: Stance) -> Optional[TypeError_]:
    """A malformed stance is a static type error (the 'bad stance' class)."""
    if stance.kind not in ("current", "as_of", "as_known_at", "audit"):
        return _err(f"unknown stance kind {stance.kind!r}", stance)
    if stance.kind == "as_of" and stance.valid_at is None:
        return _err("as_of stance without valid_at", stance)
    if stance.kind == "as_known_at" and stance.known_at is None:
        return _err("as_known_at stance without known_at", stance)
    if stance.kind == "audit" and (stance.valid_at is None or stance.known_at is None):
        return _err("audit stance needs both valid_at and known_at", stance)
    return None


@dataclass(slots=True)
class Inference:
    """Full inference result: the public type plus per-node resolved entity
    classes (keyed by id(node)) that lowering uses to pick scan targets."""

    type: Union[OQIRType, TypeError_]
    targets: dict[int, tuple[str, ...]] = field(default_factory=dict)


def infer(term: OQIRTerm, onto: Ontology, *, expect_unit: Optional[str] = None) -> Inference:
    inf = Inference(type=EntitySetT(""))
    out = _infer(term, onto, inf)
    if isinstance(out, TypeError_):
        return Inference(type=out, targets=inf.targets)
    if isinstance(out, TableT):
        inf.type = out
    else:
        classes = sorted(out)
        if len(classes) == 1:
            inf.type = EntitySetT(classes[0])
        else:
            lca = _lca(onto, classes)
            if lca is None:
                names = [onto.get(c).name if onto.get(c) else c for c in classes]
                return Inference(
                    type=_err(
                        f"ambiguous traversal: result could be any of {names}; "
                        f"the plan does not denote a single entity population",
                        term,
                    ),
                    targets=inf.targets,
                )
            inf.type = EntitySetT(lca)
    if expect_unit is not None:
        e = _check_expect_unit(term, onto, expect_unit, inf)
        if e is not None:
            return Inference(type=e, targets=inf.targets)
    return inf


def typecheck(
    term: OQIRTerm, onto: Ontology, *, expect_unit: Optional[str] = None
) -> Union[OQIRType, TypeError_]:
    """Type the term against the induced ontology. `expect_unit` is the unit the
    caller wants the result expressed in; it must be convertible from the
    result measure's declared unit."""
    return infer(term, onto, expect_unit=expect_unit).type


def _lca(onto: Ontology, classes: list[str]) -> Optional[str]:
    common: Optional[set[str]] = None
    for c in classes:
        anc = onto.ancestors(c) | {c}
        common = anc if common is None else (common & anc)
    if not common:
        return None
    # deepest common ancestor: the one subsumed by no other member
    best = [a for a in sorted(common) if not any(b != a and onto.subsumes(a, b) for b in common)]
    return best[0] if best else None


def _check_expect_unit(
    term: OQIRTerm, onto: Ontology, expect_unit: str, inf: Inference
) -> Optional[TypeError_]:
    m = _result_measure(term, onto, inf)
    if isinstance(m, TypeError_):
        return m
    if m is None:
        if expect_unit not in UNITS:
            return _err(f"unknown unit {expect_unit!r}")
        return None
    if m.unit is None:
        return _err(
            f"result measure {m.name!r} carries no declared unit; cannot express it in {expect_unit!r}"
        )
    ok = unit_convertible(m.unit, expect_unit)
    return None if ok is True else ok  # type: ignore[return-value]


def _result_measure(
    term: OQIRTerm, onto: Ontology, inf: Inference
) -> Union[PropertyDef, None, TypeError_]:
    if isinstance(term, AsOf):
        return _result_measure(term.term, onto, inf)
    if isinstance(term, TopK):
        return _result_measure(term.source, onto, inf)
    if isinstance(term, Aggregate) and term.measure_prop and term.agg is not Agg.COUNT:
        for c in inf.targets.get(id(term.source), ()):
            p = resolve_prop(onto, c, term.measure_prop)
            if p is not None:
                return p
        return None
    return None


def _infer(
    term: OQIRTerm, onto: Ontology, inf: Inference
) -> Union[frozenset[str], TableT, TypeError_]:
    out = _infer_inner(term, onto, inf)
    if isinstance(out, frozenset):
        inf.targets[id(term)] = tuple(sorted(out))
    return out


def _infer_inner(
    term: OQIRTerm, onto: Ontology, inf: Inference
) -> Union[frozenset[str], TableT, TypeError_]:
    if isinstance(term, Select):
        if onto.get(term.class_uri) is None:
            return _err(f"unknown class {term.class_uri!r}", term)
        for cond in term.conditions:
            e = _check_condition(cond, term.class_uri, onto)
            if e is not None:
                return e
        return frozenset({term.class_uri})

    if isinstance(term, Traverse):
        src = _infer(term.source, onto, inf)
        if isinstance(src, TypeError_):
            return src
        if isinstance(src, TableT):
            return _err("traverse over a Table; traversal needs an entity set", term)
        if term.reverse:
            owners: set[str] = set()
            for s in src:
                for d_uri in sorted(onto.classes):
                    p = all_props(onto, d_uri).get(term.link)
                    if (
                        p is not None
                        and p.is_link
                        and p.range_class
                        and (onto.subsumes(p.range_class, s) or onto.subsumes(s, p.range_class))
                    ):
                        owners.add(d_uri)
            # most-derived owners only: a link declared on a parent is visible
            # on each descendant; that is one link, not many
            owners = {o for o in owners if not any(o != q and onto.subsumes(o, q) for q in owners)}
            if not owners:
                names = sorted(onto.get(s).name if onto.get(s) else s for s in src)
                return _err(
                    f"phantom reverse traversal: no class links to {names} via {term.link!r}", term
                )
            targets = owners
        else:
            ranges: set[str] = set()
            for s in src:
                p = resolve_prop(onto, s, term.link)
                if p is not None and p.is_link and p.range_class:
                    ranges.add(p.range_class)
            if not ranges:
                names = sorted(onto.get(s).name if onto.get(s) else s for s in src)
                return _err(
                    f"phantom traversal: {term.link!r} is not a link property of {names}", term
                )
            targets = ranges
        # conditions narrow the target union to the classes they type on
        if term.conditions:
            ok_targets: set[str] = set()
            first_err: Optional[TypeError_] = None
            for t in sorted(targets):
                errs = [e for e in (_check_condition(c, t, onto) for c in term.conditions) if e]
                if errs:
                    first_err = first_err or errs[0]
                else:
                    ok_targets.add(t)
            if not ok_targets:
                return first_err or _err("conditions satisfiable on no traversal target", term)
            targets = ok_targets
        return frozenset(targets)

    if isinstance(term, TextJoin):
        src = _infer(term.source, onto, inf)
        if isinstance(src, TypeError_):
            return src
        if isinstance(src, TableT):
            return _err("textJoin over a Table; it needs an entity set", term)
        if not term.pattern:
            return _err("textJoin with empty pattern", term)
        ok: set[str] = set()
        bad: Optional[TypeError_] = None
        for s in sorted(src):
            p = resolve_prop(onto, s, term.text_prop)
            if p is None:
                bad = bad or _err(f"unknown text property {term.text_prop!r}", term)
            elif p.datatype is not Datatype.TEXT:
                bad = bad or _err(
                    f"textJoin requires a TEXT property; {term.text_prop!r} is {p.datatype.value}",
                    term,
                )
            else:
                ok.add(s)
        if not ok:
            return bad or _err(f"no textJoin host for {term.text_prop!r}", term)
        # narrow the source node's resolution too (lowering scans only hosts)
        inf.targets[id(term.source)] = tuple(sorted(ok))
        return frozenset(ok)

    if isinstance(term, Aggregate):
        src = _infer(term.source, onto, inf)
        if isinstance(src, TypeError_):
            return src
        if isinstance(src, TableT):
            return _err("aggregate over a Table; aggregation consumes an entity set", term)
        classes = set(src)
        cols: list[str] = []
        for g in term.group_by:
            ok = {c for c in classes if not isinstance(resolve_path(onto, c, g), TypeError_)}
            if not ok:
                first = resolve_path(onto, sorted(classes)[0], g)
                return first if isinstance(first, TypeError_) else _err(f"bad group-by {g!r}", term)
            classes = ok
            cols.append(g)
        if term.agg is Agg.COUNT:
            if term.measure_prop is not None:
                ok = {
                    c
                    for c in classes
                    if not isinstance(resolve_path(onto, c, term.measure_prop), TypeError_)
                }
                if not ok:
                    return _err(
                        f"unknown count-distinct property {term.measure_prop!r}", term
                    )
                classes = ok
        else:
            if term.measure_prop is None:
                return _err(f"{term.agg.value} requires a measure property", term)
            ok = set()
            bad: Optional[TypeError_] = None
            for c in sorted(classes):
                p = resolve_path(onto, c, term.measure_prop)
                if isinstance(p, TypeError_):
                    bad = bad or p
                elif p.is_link:
                    bad = bad or _err(f"aggregate over link property {term.measure_prop!r}", term)
                elif p.datatype not in NUMERIC:
                    bad = bad or _err(
                        f"aggregate {term.agg.value} over non-numeric property "
                        f"{term.measure_prop!r} (datatype {p.datatype.value})",
                        term,
                    )
                else:
                    ok.add(c)
            if not ok:
                return bad or _err(f"no measure host for {term.measure_prop!r}", term)
            classes = ok
        inf.targets[id(term.source)] = tuple(sorted(classes))
        agg_col = f"{term.agg.value}_{term.measure_prop or 'rows'}"
        cols.append(agg_col)
        for h in term.having:
            if h.prop != agg_col:
                return _err(f"having references unknown column {h.prop!r}", term)
            if not isinstance(h.value, (int, float)):
                return _err(f"having compares {agg_col!r} to non-numeric {h.value!r}", term)
        return TableT(tuple(cols))

    if isinstance(term, TopK):
        src = _infer(term.source, onto, inf)
        if isinstance(src, TypeError_):
            return src
        if not isinstance(src, TableT):
            return _err("topK requires a Table source; got entity set", term)
        if term.by not in src.columns:
            return _err(f"topK column {term.by!r} not in table columns {src.columns}", term)
        if term.k < 1:
            return _err(f"topK with k={term.k}", term)
        return src

    if isinstance(term, AsOf):
        e = _check_stance(term.stance)
        if e is not None:
            return e
        return _infer(term.term, onto, inf)

    return _err(f"unknown OQIR term {type(term).__name__}", term)
