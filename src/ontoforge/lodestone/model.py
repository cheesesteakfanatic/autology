"""Shared LODESTONE model types and ontology helpers (whitepaper §6.2, M12).

Everything here is pure data + pure functions over contracts.Ontology:
property resolution with inheritance, the class link graph, and the Candidate
record that carries an OQIR term together with its execution directives
(projection, expected output unit, post-processing, default stance).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from ontoforge.contracts import Stance
from ontoforge.contracts.ontology import ClassDef, Ontology, PropertyDef
from ontoforge.contracts.oqir import OQIRTerm

# Default temporal stance sentinel: "ever" = every system-open cell regardless
# of valid time (registry questions without a temporal qualifier ask about the
# record, not about a single instant; reads with an explicit "as of" get a
# real contracts.Stance).
EVER: Optional[Stance] = None


# ------------------------------------------------------------------ ontology


def resolve_prop(onto: Ontology, class_uri: str, name: str) -> Optional[PropertyDef]:
    """Property lookup on the class or any ancestor (§6.2 well-formedness:
    'every Condition.prop must exist on the class or ancestors')."""
    c = onto.get(class_uri)
    if c is None:
        return None
    p = c.prop(name)
    if p is not None:
        return p
    for a_uri in sorted(onto.ancestors(class_uri)):
        a = onto.get(a_uri)
        if a is not None:
            p = a.prop(name)
            if p is not None:
                return p
    return None


def all_props(onto: Ontology, class_uri: str) -> dict[str, PropertyDef]:
    """name -> PropertyDef including inherited (own definitions win)."""
    out: dict[str, PropertyDef] = {}
    for a_uri in sorted(onto.ancestors(class_uri)):
        a = onto.get(a_uri)
        if a is not None:
            for p in a.properties:
                out[p.name] = p
    c = onto.get(class_uri)
    if c is not None:
        for p in c.properties:
            out[p.name] = p
    return out


@dataclass(frozen=True, slots=True)
class Hop:
    """One step in a link path: follow `link` (reverse when the edge points
    back at us) landing on class `target_uri`."""

    link: str
    reverse: bool
    target_uri: str


def link_edges(onto: Ontology) -> list[tuple[str, Hop, float]]:
    """All traversable edges of the class graph, both directions, including
    inherited link properties and subclass-polymorphic ranges. Exact-range
    hops cost 1.0; subclass-polymorphic hops cost 2.6 (legal, but a path
    through declared ranges must win every tie — a 'registrant' that happens
    to be a Manufacturer is not THE manufacturer path)."""
    edges: list[tuple[str, Hop, float]] = []
    for c_uri in sorted(onto.classes):
        for name, p in sorted(all_props(onto, c_uri).items()):
            if p.is_link and p.range_class:
                edges.append((c_uri, Hop(name, False, p.range_class), 1.0))
                edges.append((p.range_class, Hop(name, True, c_uri), 1.0))
                for d in sorted(onto.descendants(p.range_class)):
                    edges.append((c_uri, Hop(name, False, d), 2.6))
                    edges.append((d, Hop(name, True, c_uri), 2.6))
    return edges


def find_paths(
    onto: Ontology,
    src_uri: str,
    dst_uri: str,
    max_hops: int = 3,
    *,
    forward_only: bool = False,
    bound: Optional[set[str]] = None,
    k: int = 3,
) -> list[list[Hop]]:
    """Up to k cheapest simple link paths src -> dst (DFS over the small class
    graph). Ordered by (cost, #intermediate classes NOT mentioned in the
    question, link names) — grounding-guided path preference: between
    equal-cost spellings ('model.manufacturer' vs 'engine.manufacturer') the
    one whose intermediate classes the question actually grounded wins.
    Subsumption counts as identity (empty path). `forward_only` restricts to
    declared link directions — such paths lower to unambiguous dotted
    conditions / traverses."""
    if src_uri == dst_uri or onto.subsumes(dst_uri, src_uri) or onto.subsumes(src_uri, dst_uri):
        return [[]]
    adj: dict[str, list[tuple[Hop, float]]] = {}
    for a, hop, cost in link_edges(onto):
        if forward_only and hop.reverse:
            continue
        adj.setdefault(a, []).append((hop, cost))
    bound = bound or set()
    found: dict[tuple, float] = {}      # link signature -> min cost
    paths: dict[tuple, list[Hop]] = {}

    def dfs(at: str, path: list[Hop], cost: float, visited: set[str]) -> None:
        if len(found) >= 32:
            return
        if path and (path[-1].target_uri == dst_uri or onto.subsumes(dst_uri, path[-1].target_uri)):
            sig = tuple((h.link, h.reverse) for h in path)
            if cost < found.get(sig, float("inf")):
                found[sig] = cost
                paths[sig] = list(path)
            return
        if len(path) >= max_hops:
            return
        for hop, c in adj.get(at, []):
            if hop.target_uri in visited:
                continue
            dfs(hop.target_uri, path + [hop], cost + c, visited | {hop.target_uri})

    dfs(src_uri, [], 0.0, {src_uri})

    def unbound_intermediates(p: list[Hop]) -> int:
        return sum(1 for h in p[:-1] if h.target_uri not in bound)

    ordered = sorted(
        found,
        key=lambda sig: (found[sig], unbound_intermediates(paths[sig]),
                         tuple(h.link for h in paths[sig])),
    )
    if not ordered:
        return []
    best_cost = found[ordered[0]]
    out = [paths[sig] for sig in ordered if found[sig] <= best_cost + 0.01][:k]
    return out


def find_path(
    onto: Ontology,
    src_uri: str,
    dst_uri: str,
    max_hops: int = 3,
    *,
    forward_only: bool = False,
    bound: Optional[set[str]] = None,
) -> Optional[list[Hop]]:
    """The single best path per find_paths ordering (None when unreachable)."""
    paths = find_paths(onto, src_uri, dst_uri, max_hops, forward_only=forward_only,
                       bound=bound, k=1)
    return paths[0] if paths else None


def class_label(c: ClassDef) -> str:
    """Human label for clarification options."""
    d = c.definition.split(".")[0].strip()
    return f"{c.name}" + (f" — {d}" if d else "")


# ----------------------------------------------------------------- candidate


@dataclass(frozen=True, slots=True)
class Candidate:
    """One interpretation: an OQIR term plus execution directives.

    `project` are property names to output (resolved against the final entity
    set, falling back to columns retained from earlier stages). `expect_unit`
    is the unit the question asked the result in (the type checker verifies
    convertibility; lowering injects the conversion). `round_digits` applies
    a 'rounded to the nearest ...' cue. `stance` overrides the default EVER
    stance (an explicit as-of question).
    """

    cand_id: str
    term: OQIRTerm
    project: tuple[str, ...] = ()
    expect_unit: Optional[str] = None
    round_digits: Optional[int] = None
    stance: Optional[Stance] = None
    score: float = 0.0
    template: str = ""
    rationale: str = ""


@dataclass(slots=True)
class GroundingResult:
    """Output of grounding: weighted bindings + coverage accounting."""

    bindings: list["Binding"] = field(default_factory=list)
    content_words: tuple[str, ...] = ()
    coverage: float = 0.0
    consumed: tuple[str, ...] = ()
    unconsumed: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Binding:
    """One question-span -> ontology-element link.

    kind: class | prop | value | unit | agg | cmp | time | textjoin |
          recorded_unit | round | having_gt1 | number
    """

    kind: str
    span: tuple[str, ...]            # the consumed question tokens (lowercased)
    target: str = ""                 # class uri / "class_uri::prop" / unit symbol / agg name
    value: object = None             # literal / parsed number / instant / pattern
    score: float = 1.0
    strong: bool = True              # strong bindings count toward coverage
    pos: int = -1                    # first token index in the question (adjacency cues)
