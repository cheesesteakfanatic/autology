"""LODESTONE lowering primitives over HEARTH (whitepaper §6.2; M12 step 5).

OQIR lowers to operations over the entity store:

* Select   -> cell-level class scan (class + descendants) + condition filters;
* Traverse -> link-adjacency expansion over the link shards;
* TextJoin -> substring match over the TEXT property of scanned entities;
* AsOf     -> the Stance is pushed into every scan/link visibility test.

Everything here works on *cells*, not bare values, because §6.2 demands
atom-level citations: every value that participates in an answer (or in a
filter that selected the answer) contributes its `prov_ref`.

The default temporal stance is EVER (None): any system-open cell regardless of
valid time — a registry question without a temporal qualifier asks about the
record. An explicit as-of question pushes a real contracts.Stance down.

String matching has three relaxation levels (execution-guided repair, §6.2):
0 = exact (stripped), 1 = case-insensitive, 2 = punctuation/legal-suffix
normalized ('Delta Airlines Inc' ~ 'DELTA AIR LINES INC.').
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

from ontoforge.contracts import Layer, Stance, ValueCell
from ontoforge.contracts.ontology import Datatype, Ontology
from ontoforge.contracts.oqir import CmpOp, Condition
from ontoforge.hearth.store import survivorship_key
from ontoforge.profiling.units_table import UNITS

from .model import resolve_prop

LEGAL_SUFFIXES = {"INC", "LLC", "CORP", "CO", "LTD", "COMPANY", "CORPORATION", "INCORPORATED"}


def normalize_name(s: str) -> str:
    """Aggressive-but-deterministic name fold used at relax level 2."""
    s = re.sub(r"[^\w\s]", " ", str(s).upper())
    s = re.sub(r"\bAIRLINES\b", "AIR LINES", s)
    toks = [t for t in s.split() if t not in LEGAL_SUFFIXES]
    return " ".join(toks)


def str_match(stored: str, literal: str, relax: int) -> bool:
    a, b = str(stored).strip(), str(literal).strip()
    if a == b:
        return True
    if relax >= 1 and a.casefold() == b.casefold():
        return True
    if relax >= 2 and normalize_name(a) == normalize_name(b):
        return True
    return False


def convert_literal(value: float, from_unit: Optional[str], to_unit: Optional[str]) -> float:
    """Convert a question literal into the property's unit (the checker already
    proved dimension compatibility — this is the injected conversion node)."""
    if not from_unit or not to_unit or from_unit == to_unit:
        return value
    return UNITS[to_unit].from_canonical(UNITS[from_unit].to_canonical(value))


def convert_value(value: float, from_unit: str, to_unit: str) -> float:
    """Convert a stored measure value into the requested output unit."""
    if from_unit == to_unit:
        return value
    return UNITS[to_unit].from_canonical(UNITS[from_unit].to_canonical(value))


# ----------------------------------------------------------------- context


def _visible(cell: ValueCell, stance: Optional[Stance]) -> bool:
    if stance is None:  # EVER
        return cell.system.open
    return cell.visible_under(stance)


def _link_visible(link, stance: Optional[Stance]) -> bool:
    if stance is None:
        return link.system.open
    from ontoforge.hearth.links import link_visible

    return link_visible(link, stance)


@dataclass(slots=True)
class ExecContext:
    """Per-execution caches: stance-keyed scans and link adjacency."""

    hearth: Any
    onto: Ontology
    stance: Optional[Stance] = None
    relax: int = 0
    _scans: dict = field(default_factory=dict)
    _links: dict = field(default_factory=dict)
    _entity_cells: dict = field(default_factory=dict)

    def with_stance(self, stance: Optional[Stance]) -> "ExecContext":
        return ExecContext(
            hearth=self.hearth, onto=self.onto, stance=stance, relax=self.relax,
            _scans=self._scans, _links=self._links, _entity_cells=self._entity_cells,
        )

    # ---- cell-level class scan (class + descendants)

    def scan_cells(self, class_uri: str) -> dict[str, dict[str, ValueCell]]:
        key = (class_uri, self._stance_key())
        if key in self._scans:
            return self._scans[key]
        classes = {class_uri} | self.onto.descendants(class_uri)
        per_key: dict[tuple[str, str], list[tuple[int, ValueCell]]] = {}
        for shard in self.hearth.value_shard_items():
            if shard.layer is not Layer.ENTITY or shard.class_uri not in classes:
                continue
            for seq, cell in enumerate(shard.cells):
                if _visible(cell, self.stance):
                    per_key.setdefault((cell.entity_uri, cell.prop), []).append((seq, cell))
        out: dict[str, dict[str, ValueCell]] = {}
        for (uri, prop), cells in per_key.items():
            winner = min(cells, key=lambda sc: survivorship_key(sc[0], sc[1]))[1]
            out.setdefault(uri, {})[prop] = winner
        self._scans[key] = out
        return out

    def entity_cells(self, uri: str) -> dict[str, ValueCell]:
        """prop -> cell for one entity across ALL entity shards (path lookups)."""
        key = ("__all__", self._stance_key())
        if key not in self._entity_cells:
            per_key: dict[tuple[str, str], list[tuple[int, ValueCell]]] = {}
            for shard in self.hearth.value_shard_items():
                if shard.layer is not Layer.ENTITY:
                    continue
                for seq, cell in enumerate(shard.cells):
                    if _visible(cell, self.stance):
                        per_key.setdefault((cell.entity_uri, cell.prop), []).append((seq, cell))
            merged: dict[str, dict[str, ValueCell]] = {}
            for (e, prop), cells in per_key.items():
                winner = min(cells, key=lambda sc: survivorship_key(sc[0], sc[1]))[1]
                merged.setdefault(e, {})[prop] = winner
            self._entity_cells[key] = merged
        return self._entity_cells[key].get(uri, {})

    # ---- link adjacency with provenance

    def link_adj(self, predicate: str, reverse: bool) -> dict[str, list[tuple[str, str]]]:
        """uri -> [(neighbor_uri, prov_ref)] over visible link cells."""
        key = (predicate, reverse, self._stance_key())
        if key in self._links:
            return self._links[key]
        adj: dict[str, list[tuple[str, str]]] = {}
        for shard in self.hearth.links.link_shard_items():
            if shard.predicate != predicate:
                continue
            for link in shard.cells:
                if not _link_visible(link, self.stance):
                    continue
                if reverse:
                    adj.setdefault(link.object_uri, []).append((link.subject_uri, link.prov_ref))
                else:
                    adj.setdefault(link.subject_uri, []).append((link.object_uri, link.prov_ref))
        for k in adj:
            adj[k] = sorted(set(adj[k]))
        self._links[key] = adj
        return self._links[key]

    def _stance_key(self):
        if self.stance is None:
            return None
        return (self.stance.kind, self.stance.valid_at, self.stance.known_at)


# ----------------------------------------------------- condition evaluation


def _terminal_matches(
    cell: ValueCell, cond: Condition, prop_unit: Optional[str], datatype: Datatype, relax: int
) -> bool:
    v = cell.value
    op = cond.op
    if op in (CmpOp.EQ, CmpOp.NE):
        lit = cond.value
        if isinstance(v, (int, float)) and isinstance(lit, (int, float)):
            ok = float(v) == float(convert_literal(float(lit), cond.unit, prop_unit))
        else:
            ok = str_match(str(v), str(lit), relax)
        return ok if op is CmpOp.EQ else not ok
    if op is CmpOp.IN:
        vals = cond.value if isinstance(cond.value, (tuple, list)) else (cond.value,)
        return any(str_match(str(v), str(x), relax) for x in vals)
    if op is CmpOp.CONTAINS:
        return str(cond.value).casefold() in str(v).casefold()
    # ordered comparisons
    if isinstance(v, (int, float)) and isinstance(cond.value, (int, float)):
        lit = convert_literal(float(cond.value), cond.unit, prop_unit)
        x, y = float(v), float(lit)
    else:
        x, y = str(v).strip(), str(cond.value).strip()  # type: ignore[assignment]
    if op is CmpOp.LT:
        return x < y
    if op is CmpOp.LE:
        return x <= y
    if op is CmpOp.GT:
        return x > y
    if op is CmpOp.GE:
        return x >= y
    if op is CmpOp.BETWEEN:
        if isinstance(v, (int, float)) and isinstance(cond.value, (int, float)):
            lo = convert_literal(float(cond.value), cond.unit, prop_unit)
            hi = convert_literal(float(cond.value2), cond.unit, prop_unit)  # type: ignore[arg-type]
            return lo <= float(v) <= hi
        return str(cond.value) <= str(v) <= str(cond.value2)
    return False


def eval_condition(
    ctx: ExecContext,
    class_uri: str,
    uri: str,
    cells: dict[str, ValueCell],
    cond: Condition,
) -> tuple[bool, tuple[str, ...]]:
    """Evaluate a (possibly dotted-path) condition for one entity.

    Returns (satisfied, prov_refs of every cell/link that the decision used).
    Path semantics are existential: some forward-linked entity satisfies the
    terminal comparison.
    """
    parts = cond.prop.split(".")
    frontier: list[tuple[str, dict[str, ValueCell], tuple[str, ...]]] = [(uri, cells, ())]
    at_class = class_uri
    for link in parts[:-1]:
        adj = ctx.link_adj(link, reverse=False)
        nxt: list[tuple[str, dict[str, ValueCell], tuple[str, ...]]] = []
        for e, _cells, refs in frontier:
            for obj, prov in adj.get(e, []):
                nxt.append((obj, ctx.entity_cells(obj), refs + ((prov,) if prov else ())))
        frontier = nxt
        p = resolve_prop(ctx.onto, at_class, link)
        at_class = p.range_class if p is not None and p.range_class else at_class
        if not frontier:
            return False, ()
    terminal = parts[-1]
    p = resolve_prop(ctx.onto, at_class, terminal)
    prop_unit = p.unit if p is not None else None
    datatype = p.datatype if p is not None else Datatype.STRING
    refs_out: list[str] = []
    ok = False
    for e, ecells, refs in frontier:
        cell = ecells.get(terminal)
        if cell is None:
            continue
        if _terminal_matches(cell, cond, prop_unit, datatype, ctx.relax):
            ok = True
            refs_out.extend(refs)
            refs_out.append(cell.prov_ref)
    return ok, tuple(sorted(set(refs_out)))
