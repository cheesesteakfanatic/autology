"""LODESTONE stage 2 — candidate generation (whitepaper §6.2; M12 step 3).

From grounding bindings, enumerate k<=8 well-typed OQIR terms via compositional
templates over the ontology link graph:

  * entity lookup / filtered select (direct + dotted-path conditions),
  * forward traverse chains for off-class projections (1-3 hops),
  * aggregate with optional group-by/having ('more than one X per Y'),
  * topK, textJoin for narrative predicates, asOf wrapping.

Generation goes through ModelClient task ``lodestone.generate`` with a
HeuristicAdapter handler implementing the enumeration (AMD-0002), so a live
T2/T3 generator can later swap in behind the same task name. The handler
receives the question + serialized bindings + an ontology digest in the
prompt; the *registered handler* closure carries the full Ontology for exact
path search (a live tier would reconstruct it from the digest).

Candidates are scored by grounding coverage, binding quality, and a template
prior; ill-typed candidates are dropped (their TypeError_ is kept for the
abstention message). Selection then goes through the decision spine as a
DecisionKind.QI request.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from ontoforge.contracts import ModelClient, ModelRequest, Stance
from ontoforge.contracts.ontology import Datatype, Ontology
from ontoforge.contracts.oqir import (
    Agg,
    Aggregate,
    AsOf,
    CmpOp,
    Condition,
    OQIRTerm,
    Select,
    TextJoin,
    TopK,
    Traverse,
    TypeError_,
)
from ontoforge.profiling.units_table import UNITS

from .grounding import GroundingResult, iso_to_instant, strip_notes, _norm, _tok
from .model import Binding, Candidate, all_props, find_path, find_paths, resolve_prop
from .typecheck import typecheck

GENERATE_TASK = "lodestone.generate"
MAX_CANDIDATES = 8

NUMERIC = (Datatype.INTEGER, Datatype.FLOAT)
DATELIKE = (Datatype.DATE, Datatype.DATETIME)


# --------------------------------------------------------- spec (de)serialization
#
# Terms cross the ModelClient boundary as JSON specs so a live tier can emit
# the same grammar under constrained decoding.


def term_to_spec(term: OQIRTerm) -> dict:
    if isinstance(term, Select):
        return {"op": "select", "class": term.class_uri, "conds": [cond_to_spec(c) for c in term.conditions]}
    if isinstance(term, Traverse):
        return {
            "op": "traverse", "src": term_to_spec(term.source), "link": term.link,
            "reverse": term.reverse, "conds": [cond_to_spec(c) for c in term.conditions],
        }
    if isinstance(term, TextJoin):
        return {"op": "textjoin", "src": term_to_spec(term.source),
                "prop": term.text_prop, "pattern": term.pattern}
    if isinstance(term, Aggregate):
        return {
            "op": "agg", "src": term_to_spec(term.source), "agg": term.agg.value,
            "measure": term.measure_prop, "group_by": list(term.group_by),
            "having": [cond_to_spec(c) for c in term.having],
        }
    if isinstance(term, TopK):
        return {"op": "topk", "src": term_to_spec(term.source), "by": term.by,
                "k": term.k, "descending": term.descending}
    if isinstance(term, AsOf):
        return {"op": "asof", "src": term_to_spec(term.term),
                "kind": term.stance.kind, "valid_at": term.stance.valid_at,
                "known_at": term.stance.known_at}
    raise TypeError(f"unknown term {term!r}")


def cond_to_spec(c: Condition) -> dict:
    return {"prop": c.prop, "op": c.op.value, "value": _jsonable(c.value),
            "value2": _jsonable(c.value2), "unit": c.unit}


def _jsonable(v: Any) -> Any:
    if isinstance(v, tuple):
        return list(v)
    return v


def spec_to_term(spec: dict) -> OQIRTerm:
    op = spec["op"]
    if op == "select":
        return Select(spec["class"], tuple(spec_to_cond(c) for c in spec.get("conds", [])))
    if op == "traverse":
        return Traverse(spec_to_term(spec["src"]), spec["link"], bool(spec.get("reverse", False)),
                        tuple(spec_to_cond(c) for c in spec.get("conds", [])))
    if op == "textjoin":
        return TextJoin(spec_to_term(spec["src"]), spec["prop"], spec["pattern"])
    if op == "agg":
        return Aggregate(spec_to_term(spec["src"]), Agg(spec["agg"]), spec.get("measure"),
                         tuple(spec.get("group_by", [])),
                         tuple(spec_to_cond(c) for c in spec.get("having", [])))
    if op == "topk":
        return TopK(spec_to_term(spec["src"]), spec["by"], int(spec.get("k", 10)),
                    bool(spec.get("descending", True)))
    if op == "asof":
        return AsOf(Stance(spec.get("kind", "as_of"), valid_at=spec.get("valid_at"),
                           known_at=spec.get("known_at")), spec_to_term(spec["src"]))
    raise ValueError(f"unknown op {op!r}")


def spec_to_cond(spec: dict) -> Condition:
    v = spec.get("value")
    if isinstance(v, list):
        v = tuple(v)
    return Condition(spec["prop"], CmpOp(spec["op"]), v, spec.get("value2"), spec.get("unit"))


def binding_to_spec(b: Binding) -> dict:
    return {"kind": b.kind, "span": list(b.span), "target": b.target,
            "value": _jsonable(b.value), "score": b.score, "strong": b.strong, "pos": b.pos}


def spec_to_binding(spec: dict) -> Binding:
    v = spec.get("value")
    if isinstance(v, list):
        v = tuple(v)
    return Binding(spec["kind"], tuple(spec["span"]), spec.get("target", ""), v,
                   float(spec.get("score", 1.0)), bool(spec.get("strong", True)),
                   int(spec.get("pos", -1)))


# ------------------------------------------------------------------ handler


def make_generate_handler(onto: Ontology) -> Callable[[ModelRequest], str]:
    """The deterministic 'lodestone.generate' specialist (AMD-0002)."""

    def handler(req: ModelRequest) -> str:
        payload = json.loads(req.prompt)
        question = payload["question"]
        bindings = [spec_to_binding(b) for b in payload["bindings"]]
        coverage = float(payload.get("coverage", 0.0))
        cands = _enumerate(question, bindings, coverage, onto)
        return json.dumps(cands, sort_keys=True)

    return handler


# ------------------------------------------------------------- enumeration


@dataclass(slots=True)
class _Draft:
    """A partially assembled candidate (handler-internal)."""

    target: str                      # target class uri
    conds: dict[str, list[dict]] = field(default_factory=dict)  # class_uri -> cond specs
    textjoin: Optional[tuple[str, str]] = None                  # (text_prop, pattern)
    agg: Optional[str] = None
    measure: Optional[str] = None
    group_by: tuple[str, ...] = ()
    having: tuple[dict, ...] = ()
    project: tuple[tuple[str, str], ...] = ()  # (host_class, prop)
    expect_unit: Optional[str] = None
    round_digits: Optional[int] = None
    asof_iso: Optional[str] = None
    topk: Optional[int] = None
    prior: float = 0.85
    quality: float = 1.0
    template: str = ""
    rationale: str = ""


def _ident_prop(onto: Ontology, class_uri: str) -> Optional[str]:
    """Identifying property: first functional datatype prop, else 'name'-like,
    else the first datatype prop (deterministic)."""
    props = all_props(onto, class_uri)
    for name in sorted(props):
        p = props[name]
        if p.functional and not p.is_link:
            return name
    for cand in ("name", "title", "label"):
        if cand in props and not props[cand].is_link:
            return cand
    for name in sorted(props):
        if not props[name].is_link:
            return name
    return None


def _date_props(onto: Ontology, class_uri: str) -> list[str]:
    props = all_props(onto, class_uri)
    return [n for n in sorted(props) if props[n].datatype in DATELIKE and not props[n].is_link]


def _stem(w: str) -> str:
    for suf in ("ing", "ed", "es", "s"):
        if w.endswith(suf) and len(w) - len(suf) >= 3:
            return w[: -len(suf)]
    return w


def _enumerate(question: str, bindings: list[Binding], coverage: float, onto: Ontology) -> list[dict]:
    question = strip_notes(question)
    by_kind: dict[str, list[Binding]] = {}
    for b in bindings:
        by_kind.setdefault(b.kind, []).append(b)
    for k in by_kind:
        by_kind[k].sort(key=lambda b: (b.pos if b.pos >= 0 else 10_000, -b.score, b.target))

    # strong class bindings, deduped by uri (best score, earliest pos)
    classes: dict[str, Binding] = {}
    for b in by_kind.get("class", []):
        if b.strong and b.score >= 0.55:
            cur = classes.get(b.target)
            if cur is None or b.score > cur.score:
                classes[b.target] = b
    class_order = sorted(classes.values(), key=lambda b: (b.pos if b.pos >= 0 else 10_000, -b.score))

    props = [b for b in by_kind.get("prop", []) if b.strong]

    # ---------------- conditions
    #
    # Literal values group into POSITION FAMILIES: distinct (class, prop)
    # probe hits for the same question span are alternative readings, fanned
    # into assignment variants (execution-guided re-ranking settles which
    # population actually carries the value).
    fams: dict[int, dict[str, list[Binding]]] = {}
    for b in by_kind.get("value", []):
        if not b.target:
            continue
        fams.setdefault(b.pos, {}).setdefault(b.target, []).append(b)

    def _host_bound(target: str) -> bool:
        # bound = the host class itself was mentioned, or a mentioned class
        # subsumes it (a mention of a DESCENDANT does not ground the ancestor:
        # 'operator' grounds Operator, not every Agent)
        host = target.split("::")[0]
        return host in classes or any(onto.subsumes(c, host) for c in classes)

    # assignments carry a prior multiplier: a probe hit on a class the question
    # actually mentioned outranks one on an unmentioned class decisively; two
    # equally-grounded hosts stay close (a tie is clarification material)
    assignments: list[tuple[list[Binding], float]] = [([], 1.0)]
    for pos in sorted(fams):
        options = sorted(
            fams[pos].values(),
            key=lambda bs: (not _host_bound(bs[0].target), -max(x.score for x in bs), bs[0].target),
        )[:2]
        if len(options) == 1:
            assignments = [(a + options[0], m) for a, m in assignments]
        else:
            first_bound = _host_bound(options[0][0].target)
            new: list[tuple[list[Binding], float]] = []
            for a, m in assignments:
                for oi, opt in enumerate(options):
                    if oi == 0:
                        new.append((a + opt, m))
                    else:
                        penalty = 0.8 if (first_bound and not _host_bound(opt[0].target)) else 0.97
                        new.append((a + opt, m * penalty))
            assignments = new[:4]

    # shared (assignment-independent) conditions
    shared_conds: dict[str, list[dict]] = {}
    used_cond_props: set[tuple[str, str]] = set()

    def add_shared(cls: str, prop: str, op: str, value: Any, unit: Optional[str] = None) -> None:
        shared_conds.setdefault(cls, []).append(
            {"prop": prop, "op": op, "value": _jsonable(value), "value2": None, "unit": unit}
        )
        used_cond_props.add((cls, prop.split(".")[-1]))

    for a, _m in assignments:
        for b in a:
            cls, prop = b.target.split("::")
            used_cond_props.add((cls, prop))

    def build_cond_map(chosen: list[Binding]) -> dict[str, list[dict]]:
        cmap: dict[str, list[dict]] = {k: list(v) for k, v in shared_conds.items()}
        groups: dict[tuple[str, str], list[str]] = {}
        for b in chosen:
            cls, prop = b.target.split("::")
            groups.setdefault((cls, prop), [])
            if str(b.value) not in groups[(cls, prop)]:
                groups[(cls, prop)].append(str(b.value))
        for b in by_kind.get("value_contains", []):
            if not b.target:
                continue
            cls, prop = b.target.split("::")
            if (cls, prop) not in groups:  # quoted exact literals supersede
                groups[(cls, prop)] = [str(b.value)]
        for (cls, prop), vals in sorted(groups.items()):
            if len(vals) == 1:
                cmap.setdefault(cls, []).append(
                    {"prop": prop, "op": CmpOp.EQ.value, "value": vals[0],
                     "value2": None, "unit": None}
                )
            else:
                cmap.setdefault(cls, []).append(
                    {"prop": prop, "op": CmpOp.IN.value, "value": sorted(vals),
                     "value2": None, "unit": None}
                )
        return cmap

    for b in by_kind.get("value_contains", []):
        if b.target:
            cls, prop = b.target.split("::")
            used_cond_props.add((cls, prop))

    # numeric comparisons: attach to the bound numeric property whose unit
    # dimension matches the literal's unit (else the nearest bound numeric prop)
    classes_for_cond = set(classes)
    for b in by_kind.get("number_cond", []):
        op, val = b.value  # type: ignore[misc]
        unit_sym = b.target or None
        cands: list[tuple[int, str, str]] = []
        for pb in props:
            cls, prop = pb.target.split("::")
            p = resolve_prop(onto, cls, prop)
            if p is None or p.is_link or p.datatype not in NUMERIC:
                continue
            if unit_sym and p.unit and unit_sym in UNITS and p.unit in UNITS:
                if UNITS[unit_sym].dimension != UNITS[p.unit].dimension:
                    continue
            dist = abs((pb.pos if pb.pos >= 0 else 0) - (b.pos if b.pos >= 0 else 0))
            cands.append((dist, cls, prop))
        # FALLBACK: a paraphrase may state the threshold WITHOUT naming the
        # measure ('reports ... under 10000 ft' has no 'altitude' prop binding).
        # Search numeric properties on the bound classes; require unit-dimension
        # AGREEMENT when the literal carries a unit (dimension-guarded — a
        # dimension-incompatible literal stays unhosted and the type checker
        # still rejects a coerced reading, so no trick-unit can sneak through).
        if not cands and classes_for_cond:
            host_cands: list[tuple[str, str, bool]] = []
            for cls in sorted(classes_for_cond):
                for prop, p in sorted(all_props(onto, cls).items()):
                    if p.is_link or p.datatype not in NUMERIC \
                            or (cls, prop) in used_cond_props:
                        continue
                    dim_ok = True
                    if unit_sym and p.unit and unit_sym in UNITS and p.unit in UNITS:
                        dim_ok = UNITS[unit_sym].dimension == UNITS[p.unit].dimension
                    if unit_sym and not p.unit:
                        dim_ok = False  # literal carries a unit; host must too
                    host_cands.append((cls, prop, dim_ok))
            # prefer a dimension-matching host; among those, the one whose unit
            # equals the literal's unit, then deterministic name order
            matching = [(c, p) for c, p, ok in host_cands if ok]
            if matching:
                def _unit_eq(cp: tuple[str, str]) -> int:
                    pu = resolve_prop(onto, cp[0], cp[1])
                    return 0 if (unit_sym and pu and pu.unit == unit_sym) else 1
                cls, prop = sorted(matching, key=lambda cp: (_unit_eq(cp), cp[0], cp[1]))[0]
                cands = [(0, cls, prop)]
        if cands:
            _d, cls, prop = sorted(cands)[0]
            add_shared(cls, prop, op, val, unit_sym)

    # recorded-unit conditions: 'altitude recorded in meters' -> the sibling
    # <measure>_unit property (pipeline-recorded source lexical unit)
    for b in by_kind.get("recorded_unit", []):
        for pb in props:
            cls, prop = pb.target.split("::")
            sib = f"{prop}_unit"
            if resolve_prop(onto, cls, sib) is not None:
                add_shared(cls, sib, CmpOp.EQ.value, str(b.target))
                break

    all_cond_classes = sorted(
        set(shared_conds)
        | {b.target.split("::")[0] for a, _m in assignments for b in a}
        | {b.target.split("::")[0] for b in by_kind.get("value_contains", []) if b.target}
    )

    # ---------------- aggregation: cue + adjacency
    agg_b = by_kind.get("agg", [])
    agg: Optional[str] = None
    measure: Optional[str] = None
    measure_host: Optional[str] = None
    if agg_b:
        # measure = numeric prop binding immediately following a cue
        for cue in agg_b:
            nxt = [
                pb for pb in props
                if pb.pos >= 0 and cue.pos >= 0 and 0 < pb.pos - cue.pos <= 2
            ]
            for pb in sorted(nxt, key=lambda x: x.pos):
                cls, prop = pb.target.split("::")
                p = resolve_prop(onto, cls, prop)
                if p is not None and not p.is_link and p.datatype in NUMERIC \
                        and (cls, prop) not in used_cond_props:
                    agg = cue.target if cue.target != "count" else "sum"
                    measure, measure_host = prop, cls
                    break
            if measure:
                break
        if agg is None:
            cues = sorted(agg_b, key=lambda b: (b.pos if b.pos >= 0 else 10_000))
            agg = cues[0].target
            if agg != "count":
                # sum/avg/min/max need a measure: the best bound numeric prop
                for pb in props:
                    cls, prop = pb.target.split("::")
                    p = resolve_prop(onto, cls, prop)
                    if p is not None and not p.is_link and p.datatype in NUMERIC \
                            and (cls, prop) not in used_cond_props:
                        measure, measure_host = prop, cls
                        break
                if measure is None:
                    agg = "count"

    having_gt1 = bool(by_kind.get("having_gt1"))
    topk_b = by_kind.get("topk", [])
    round_b = by_kind.get("round", [])
    unit_b = by_kind.get("unit", [])
    time_b = by_kind.get("time", [])
    tj_b = by_kind.get("textjoin", [])

    # ---------------- target class options with priors
    #
    # Priors separate only on DISCRIMINATING signals (cue adjacency, measure
    # host, projection reachability). Genuinely undiscriminated readings get
    # EQUAL priors — that tie is exactly what triggers the one clarification.
    PRIMARY, SECONDARY, TIE = 0.9, 0.45, 0.85
    targets: list[tuple[str, float]] = []

    def add_target(uri: str, prior: float) -> None:
        if not any(u == uri for u, _ in targets):
            targets.append((uri, prior))

    if having_gt1:
        # 'which X have more than one Y': group over the class hosting both props
        hosts = [pb.target.split("::")[0] for pb in props]
        for h in hosts:
            if hosts.count(h) >= 2:
                add_target(h, PRIMARY)
    if measure_host and agg in ("sum", "avg", "min", "max"):
        add_target(measure_host, PRIMARY)
    def add_with_span_ties(chosen: Binding) -> None:
        # alternative class readings of the SAME mention ('events' could be
        # NTSB accidents or all safety events) tie — clarification material
        add_target(chosen.target, PRIMARY)
        for cb in class_order:
            if cb.pos == chosen.pos and cb.score == chosen.score and cb.target != chosen.target:
                add_target(cb.target, PRIMARY)

    if agg == "count":
        cue_pos = min((b.pos for b in agg_b if b.pos >= 0), default=-1)
        after = [b for b in class_order if b.pos > cue_pos]
        for b in after + class_order:
            add_with_span_ties(b)
            break
    if not targets and class_order:
        add_with_span_ties(class_order[0])
    for b in class_order:
        add_target(b.target, SECONDARY)
    if not targets:
        # no class grounded: the condition hosts themselves + most-derived
        # classes with a forward path to every condition host
        hosts = list(all_cond_classes)
        if agg == "count":
            # counting entities pinned by their own identity is degenerate;
            # the plausible readings (classes linking to the host) tie
            for c_uri in sorted(onto.classes):
                if onto.descendants(c_uri):
                    continue
                if c_uri in hosts:
                    continue
                if hosts and all(
                    find_path(onto, c_uri, h, max_hops=2, forward_only=True) for h in hosts
                ):
                    add_target(c_uri, TIE)
            for h in hosts:
                add_target(h, SECONDARY)
        else:
            for h in hosts:
                add_target(h, PRIMARY)
            for c_uri in sorted(onto.classes):
                if onto.descendants(c_uri) or c_uri in hosts:
                    continue
                if hosts and all(
                    find_path(onto, c_uri, h, max_hops=2, forward_only=True) for h in hosts
                ):
                    add_target(c_uri, SECONDARY)
    targets = targets[:4]

    # ---------------- textJoin host (a bound TEXT property, if any)
    tj_prop: Optional[tuple[str, str]] = None
    if tj_b:
        for pb in props:
            cls, prop = pb.target.split("::")
            p = resolve_prop(onto, cls, prop)
            if p is not None and p.datatype is Datatype.TEXT:
                tj_prop = (cls, prop)
                break

    # ---------------- projections per wh-clause
    #
    # Within a clause, datatype properties beat link properties ('registration
    # number' over 'aircraft'); among links the one nearest AFTER the wh-word
    # wins ('who manufactured ...' -> manufacturer, not the later 'aircraft').
    used_for_measure = {(measure_host, measure)} if measure else set()
    toks = [_norm(t) for t in _tok(question)]
    WH = ("what", "who", "which", "how", "whose")
    clause_starts = [0] + [
        i for i in range(1, len(toks)) if toks[i] in WH and toks[i - 1] == "and"
    ]
    projections: list[tuple[str, str]] = []
    for ci, start in enumerate(clause_starts):
        end = clause_starts[ci + 1] if ci + 1 < len(clause_starts) else 10_000
        in_clause = []
        for pb in props:
            cls, prop = pb.target.split("::")
            ppos = pb.pos if pb.pos >= 0 else 0
            if not (start <= ppos < end) or pb.score < 0.6:
                continue
            if (cls, prop) in used_cond_props or (cls, prop) in used_for_measure:
                continue
            if tj_prop is not None and prop == tj_prop[1]:
                continue  # the textJoin host is a predicate, not the answer
            p = resolve_prop(onto, cls, prop)
            if p is None or p.datatype is Datatype.TEXT:
                continue  # narratives are evidence surfaces, not answers
            in_clause.append((pb, cls, prop, p.is_link))
        if not in_clause:
            continue
        wh_pos = start
        for i in range(start, min(end, len(toks))):
            if toks[i] in WH:
                wh_pos = i
                break
        datatype = [x for x in in_clause if not x[3]]
        links = [x for x in in_clause if x[3]]
        # wh-adjacency: when a LINK property sits right after the wh-word (the
        # asked-for entity, 'which MANUFACTURER made the model ...') and every
        # datatype prop in the clause appears strictly LATER (those are path
        # waypoints — 'the MODEL of the aircraft'), the link is the answer, not
        # the waypoint's datatype. Otherwise the general datatype-beats-link rule
        # applies ('registration NUMBER' over 'aircraft').
        nearest_link = sorted(
            links,
            key=lambda x: ((x[0].pos - wh_pos) if x[0].pos >= wh_pos else 10_000,
                           -x[0].score, x[1]),
        )[0] if links else None
        if (
            nearest_link is not None
            and 0 <= nearest_link[0].pos - wh_pos <= 2
            and (not datatype or all(d[0].pos > nearest_link[0].pos for d in datatype))
        ):
            best = nearest_link
        elif datatype:
            best = sorted(datatype, key=lambda x: (x[0].pos, -x[0].score, x[1]))[0]
        else:
            best = sorted(
                in_clause,
                key=lambda x: ((x[0].pos - wh_pos) if x[0].pos >= wh_pos else 10_000,
                               -x[0].score, x[1]),
            )[0]
        cls, prop = best[1], best[2]
        # host adjacency: 'manufacturer name' — a class mention immediately
        # before the property that carries it (directly or inherited) IS the host
        ppos = best[0].pos
        for cb in class_order:
            if cb.pos >= 0 and 0 < ppos - cb.pos <= 2 and prop in all_props(onto, cb.target):
                cls = cb.target
                break
        if (cls, prop) not in projections:
            projections.append((cls, prop))

    # ---------------- date conditions (may fan out into variants)
    date_variants: list[list[tuple[str, str, str, str]]] = [[]]  # (cls, prop, op, iso)
    target_uris = [u for u, _ in targets]
    for b in by_kind.get("date_cond", []):
        op, iso = b.value  # type: ignore[misc]
        anchor = _stem(str(b.target))
        scoped = sorted(set(target_uris) | set(all_cond_classes))
        anchored: list[tuple[str, str]] = []
        for cls in scoped:
            for dp in _date_props(onto, cls):
                if anchor and len(anchor) >= 3 and dp.startswith(anchor):
                    anchored.append((cls, dp))
        if len(anchored) == 1:
            for v in date_variants:
                v.append((anchored[0][0], anchored[0][1], op, iso))
        else:
            options = anchored or [
                (cls, dp) for cls in (target_uris[:1] or scoped) for dp in _date_props(onto, cls)
            ]
            options = options[:3]
            if len(options) == 1:
                for v in date_variants:
                    v.append((options[0][0], options[0][1], op, iso))
            elif options:
                date_variants = [
                    v + [(cls, dp, op, iso)] for v in date_variants for (cls, dp) in options
                ]

    # ---------------- assemble drafts
    bound_classes = set(classes) | {u for u, _ in targets}
    drafts: list[_Draft] = []
    for target, prior in targets:
        for assignment, mult in assignments:
            for dv in date_variants:
                d = _Draft(target=target)
                d.conds = build_cond_map(assignment)
                for cls, dp, op, iso in dv:
                    d.conds.setdefault(cls, []).append(
                        {"prop": dp, "op": op, "value": iso, "value2": None, "unit": None}
                    )
                d.agg = agg
                d.measure = measure
                d.expect_unit = unit_b[0].target if unit_b and (measure or agg) else None
                d.round_digits = 0 if round_b else None
                d.asof_iso = str(time_b[0].value) if time_b else None
                d.topk = int(topk_b[0].value) if topk_b else None
                if tj_b:
                    pattern = str(tj_b[0].value)
                    if tj_prop is not None:
                        d.textjoin = (tj_prop[1], pattern)
                    else:
                        own = [
                            n for n, p in sorted(all_props(onto, target).items())
                            if p.datatype is Datatype.TEXT
                        ]
                        if own:
                            d.textjoin = (own[0], pattern)
                if having_gt1 and len(projections) >= 1:
                    # group by the asked-for prop; count distinct the second prop
                    gkey = projections[0][1]
                    others = list(projections[1:]) or [
                        (target, pb.target.split("::")[1]) for pb in props
                        if pb.target.split("::")[0] == target
                        and pb.target.split("::")[1] != gkey
                    ]
                    if others:
                        d.agg = "count"
                        d.measure = others[0][1]
                        d.group_by = (gkey,)
                        d.having = (
                            {"prop": f"count_{others[0][1]}", "op": CmpOp.GT.value,
                             "value": 1, "value2": None, "unit": None},
                        )
                        d.project = ((target, gkey),)
                if not d.project:
                    d.project = tuple(projections)
                d.prior = prior * mult
                d.quality = coverage
                d.template = _template_name(d)
                drafts.append(d)
                if len(drafts) >= MAX_CANDIDATES * 3:
                    break
            if len(drafts) >= MAX_CANDIDATES * 3:
                break
        if len(drafts) >= MAX_CANDIDATES * 3:
            break

    # ---------------- lower drafts to term specs (path variants included)
    out: list[dict] = []
    seen: set[str] = set()
    for d in drafts:
        for spec in _draft_to_specs(d, onto, bound_classes):
            key = json.dumps(spec["term"], sort_keys=True)
            if key in seen:
                continue
            seen.add(key)
            out.append(spec)
            if len(out) >= MAX_CANDIDATES:
                break
        if len(out) >= MAX_CANDIDATES:
            break
    return out


def _template_name(d: _Draft) -> str:
    parts = []
    if d.textjoin:
        parts.append("textjoin")
    if d.agg:
        parts.append(f"agg:{d.agg}")
    if d.group_by:
        parts.append("groupby")
    if d.topk:
        parts.append("topk")
    if d.asof_iso:
        parts.append("asof")
    if not parts:
        parts.append("lookup")
    return "+".join(parts)


def _draft_to_specs(d: _Draft, onto: Ontology, bound: set[str]) -> list[dict]:
    """Assemble the OQIR term spec(s): Select(target) with direct + dotted-path
    conditions, textJoin, projection extension traverses, agg/topk/asof.

    Equal-cost link-path spellings ('model.manufacturer' vs
    'engine.manufacturer') fan out into PATH VARIANTS — execution-guided
    re-ranking (§6.2) settles which spelling has data behind it."""
    target = d.target
    base_conds: list[dict] = list(d.conds.get(target, []))
    # inherited hosts: conditions declared on an ancestor class apply directly
    for cls, cs in sorted(d.conds.items()):
        if cls != target and (onto.subsumes(cls, target) or onto.subsumes(target, cls)):
            base_conds.extend(cs)

    # off-class hosts: dotted forward-path conditions; ≤2 path options each
    host_paths: list[tuple[str, list[dict], list[list]]] = []
    for cls, cs in sorted(d.conds.items()):
        if cls == target or onto.subsumes(cls, target) or onto.subsumes(target, cls):
            continue
        paths = find_paths(onto, target, cls, forward_only=True, bound=bound, k=2)
        if not paths:
            return []  # condition host unreachable: not a valid reading
        host_paths.append((cls, cs, paths))

    combos: list[list[list]] = [[]]
    for _cls, _cs, paths in host_paths:
        combos = [c + [p] for c in combos for p in paths][:4]

    specs: list[dict] = []
    for ci, combo in enumerate(combos):
        conds = list(base_conds)
        for (cls, cs, _paths), path in zip(host_paths, combo):
            prefix = ".".join(h.link for h in path)
            for c in cs:
                conds.append({**c, "prop": f"{prefix}.{c['prop']}" if prefix else c["prop"]})
        spec = _assemble(d, onto, bound, conds, prior=d.prior * (0.985**ci))
        if spec is not None:
            specs.append(spec)
    return specs


def _assemble(
    d: _Draft, onto: Ontology, bound: set[str], conds: list[dict], prior: float
) -> Optional[dict]:
    target = d.target
    term: dict = {"op": "select", "class": target, "conds": conds}
    landed = target

    if d.textjoin:
        prop, pattern = d.textjoin
        if resolve_prop(onto, landed, prop) is None:
            return None
        term = {"op": "textjoin", "src": term, "prop": prop, "pattern": pattern}

    project_cols: list[str] = []
    if d.agg is None:
        for cls, prop in d.project:
            p = resolve_prop(onto, landed, prop)
            if p is None:
                path = find_path(onto, landed, cls, forward_only=True, bound=bound)
                if path is None:
                    return None
                for hop in path:
                    term = {"op": "traverse", "src": term, "link": hop.link,
                            "reverse": hop.reverse, "conds": []}
                    landed = hop.target_uri
                p = resolve_prop(onto, landed, prop)
                if p is None:
                    return None
            if p.is_link and p.range_class:
                term = {"op": "traverse", "src": term, "link": prop, "reverse": False, "conds": []}
                landed = p.range_class
                ident = _ident_prop(onto, landed)
                if ident is None:
                    return None
                project_cols.append(ident)
            else:
                project_cols.append(prop)
        if not project_cols:
            ident = _ident_prop(onto, landed)
            if ident is None:
                return None
            project_cols.append(ident)
    else:
        term = {
            "op": "agg", "src": term, "agg": d.agg, "measure": d.measure,
            "group_by": list(d.group_by), "having": list(d.having),
        }
        if d.group_by and d.having:
            project_cols = list(d.group_by)
        else:
            project_cols = list(d.group_by) + [f"{d.agg}_{d.measure or 'rows'}"]
        if d.topk:
            term = {"op": "topk", "src": term, "by": f"{d.agg}_{d.measure or 'rows'}",
                    "k": d.topk, "descending": True}

    if d.asof_iso:
        term = {"op": "asof", "src": term, "kind": "as_of",
                "valid_at": iso_to_instant(d.asof_iso), "known_at": None}

    return {
        "term": term,
        "project": project_cols,
        "expect_unit": d.expect_unit,
        "round_digits": d.round_digits,
        "prior": prior,
        "quality": d.quality,
        "template": d.template,
        "rationale": f"target={onto.get(d.target).name if onto.get(d.target) else d.target}",
    }


# --------------------------------------------------------------- public API


@dataclass(slots=True)
class CandidateSet:
    candidates: list[Candidate]
    type_errors: list[TypeError_]


def generate_candidates(
    question: str,
    grounding: GroundingResult,
    onto: Ontology,
    client: ModelClient,
) -> CandidateSet:
    """Run grounding bindings through the ModelClient generator, decode, score,
    and type-check. Ill-typed candidates are dropped (kept as TypeError_)."""
    prompt = json.dumps(
        {
            "question": question,
            "coverage": grounding.coverage,
            "bindings": [binding_to_spec(b) for b in grounding.bindings],
            "ontology_digest": sorted(
                onto.classes[c].name for c in onto.classes
            ),
        },
        sort_keys=True,
    )
    resp = client.propose(ModelRequest(task=GENERATE_TASK, prompt=prompt, temperature=0.0))
    specs = resp.parsed if resp.parsed is not None else json.loads(resp.text)

    cands: list[Candidate] = []
    errors: list[TypeError_] = []
    for i, spec in enumerate(specs):
        term = spec_to_term(spec["term"])
        expect_unit = spec.get("expect_unit")
        t = typecheck(term, onto, expect_unit=expect_unit)
        if isinstance(t, TypeError_):
            errors.append(t)
            continue
        score = round(
            0.55 * float(spec.get("quality", 0.0))
            + 0.25 * float(spec.get("prior", 0.8))
            + 0.20,  # type-check pass
            6,
        )
        cands.append(
            Candidate(
                cand_id=f"c{i}",
                term=term,
                project=tuple(spec.get("project", [])),
                expect_unit=expect_unit,
                round_digits=spec.get("round_digits"),
                stance=None,
                score=score,
                template=spec.get("template", ""),
                rationale=spec.get("rationale", ""),
            )
        )
    cands.sort(key=lambda c: (-c.score, c.cand_id))
    return CandidateSet(cands, errors)
