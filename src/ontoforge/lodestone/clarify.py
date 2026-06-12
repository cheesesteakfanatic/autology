"""LODESTONE minimal-entropy clarification (whitepaper §6.2; M12 step 4).

When the conformal set over candidate interpretations is non-singleton, the
candidates are TYPED TERMS, so their disagreement is a structural diff:
different entity scope, different filter field (time window), different
metric, or different temporal stance. We emit ONE multiple-choice question
over the axis that partitions the set (which, for a discrete uniform set, is
the maximal-information-gain question), and answering it re-ranks the set to
a singleton.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ontoforge.contracts.ontology import Ontology
from ontoforge.contracts.oqir import Aggregate, AsOf, OQIRTerm, Select, TextJoin, TopK, Traverse

from .model import Candidate, class_label


@dataclass(slots=True)
class Clarification:
    question: str
    options: tuple[str, ...]
    candidates: tuple[Candidate, ...]   # aligned to options


def _root_select(term: OQIRTerm) -> Optional[Select]:
    if isinstance(term, Select):
        return term
    if isinstance(term, (Traverse, TextJoin, Aggregate, TopK)):
        return _root_select(getattr(term, "source"))
    if isinstance(term, AsOf):
        return _root_select(term.term)
    return None


def _agg_sig(term: OQIRTerm) -> Optional[tuple[str, Optional[str]]]:
    if isinstance(term, Aggregate):
        return (term.agg.value, term.measure_prop)
    if isinstance(term, (Traverse, TextJoin, TopK)):
        return _agg_sig(getattr(term, "source"))
    if isinstance(term, AsOf):
        return _agg_sig(term.term)
    return None


def _cond_sig(term: OQIRTerm) -> tuple[tuple[str, str, str], ...]:
    out: list[tuple[str, str, str]] = []
    if isinstance(term, Select):
        out.extend((c.prop, c.op.value, str(c.value)) for c in term.conditions)
    elif isinstance(term, (Traverse, TextJoin, Aggregate, TopK)):
        out.extend(_cond_sig(getattr(term, "source")))
        if isinstance(term, Traverse):
            out.extend((c.prop, c.op.value, str(c.value)) for c in term.conditions)
    elif isinstance(term, AsOf):
        out.extend(_cond_sig(term.term))
    return tuple(sorted(out))


def structural_diff(cands: list[Candidate], onto: Ontology) -> Optional[Clarification]:
    """The single discrete question that partitions the conformal set; None when
    the candidates are not meaningfully distinguishable (then the top one runs)."""
    if len(cands) < 2:
        return None

    # axis 1 — entity scope: the root population differs
    scopes: dict[str, Candidate] = {}
    for c in cands:
        sel = _root_select(c.term)
        if sel is not None and sel.class_uri not in scopes:
            scopes[sel.class_uri] = c
    if len(scopes) > 1:
        opts, chosen = [], []
        for uri in sorted(scopes):
            cdef = onto.get(uri)
            opts.append(class_label(cdef) if cdef is not None else uri)
            chosen.append(scopes[uri])
        return Clarification(
            question="Which entities should the question range over?",
            options=tuple(opts),
            candidates=tuple(chosen),
        )

    # axis 2 — metric: aggregate op/measure differs
    metrics: dict[tuple[str, Optional[str]], Candidate] = {}
    for c in cands:
        sig = _agg_sig(c.term)
        if sig is not None and sig not in metrics:
            metrics[sig] = c
    if len(metrics) > 1:
        opts, chosen = [], []
        for sig in sorted(metrics, key=str):
            agg, measure = sig
            opts.append(f"{agg} of {measure}" if measure else f"{agg} of matching entities")
            chosen.append(metrics[sig])
        return Clarification(
            question="Which metric did you mean?",
            options=tuple(opts), candidates=tuple(chosen),
        )

    # axis 3 — filter field / time window: same values, different condition props
    conds: dict[tuple, Candidate] = {}
    for c in cands:
        sig = _cond_sig(c.term)
        if sig not in conds:
            conds[sig] = c
    if len(conds) > 1:
        all_sigs = list(conds)
        common = set(all_sigs[0])
        for s in all_sigs[1:]:
            common &= set(s)
        opts, chosen = [], []
        for sig in sorted(conds, key=str):
            delta = [t for t in sig if t not in common]
            label = "; ".join(
                f"{prop.split('.')[-1].replace('_', ' ')} {op} {val}" for prop, op, val in delta
            ) or "no additional filter"
            opts.append(label)
            chosen.append(conds[sig])
        return Clarification(
            question="Which field should the filter apply to?",
            options=tuple(opts), candidates=tuple(chosen),
        )

    # axis 4 — stance differs
    stances: dict[str, Candidate] = {}
    for c in cands:
        t = c.term
        key = "no time qualifier"
        if isinstance(t, AsOf):
            key = f"as of {t.stance.valid_at}"
        if key not in stances:
            stances[key] = c
    if len(stances) > 1:
        return Clarification(
            question="Which point in time should the answer reflect?",
            options=tuple(sorted(stances)),
            candidates=tuple(stances[k] for k in sorted(stances)),
        )
    return None


def resolve_choice(clar: Clarification, choice) -> Optional[Candidate]:
    """Map a user's answer (index, exact option, or unambiguous substring) to
    the corresponding candidate."""
    if isinstance(choice, int):
        if 0 <= choice < len(clar.options):
            return clar.candidates[choice]
        return None
    text = str(choice).strip().casefold()
    for i, opt in enumerate(clar.options):
        if opt.casefold() == text:
            return clar.candidates[i]
    hits = [i for i, opt in enumerate(clar.options) if text and text in opt.casefold()]
    if len(hits) == 1:
        return clar.candidates[hits[0]]
    return None
