"""Induced-ontology enrichment for materialization + grounding (pipeline-side).

STRATA emits the ontology from schema-level evidence only. The pipeline knows
more by materialization time — instance-level conformance decisions and ER
outcomes — and records that knowledge ON ITS OWN SIDE of the interface
(strata/ is frozen), exactly the way the gold world-builder extends the gold
ontology with the pipeline-recorded ``altitude_agl_unit`` annotation:

1. **measure patches** — a property STRATA typed as string because its column
   mixes lexical unit forms ('1010m' among bare feet, 'USD 1,234.56' among
   bare costs) becomes a real measure: numeric datatype + conformed unit +
   dimension, with the ShapeConstraint kept in step;
2. **unit annotations** — mixed-unit properties get the ``<prop>_unit``
   companion (the source lexical unit of each conformed cell);
3. **ER links** — when generic ER resolved a cross-table identity domain, the
   referencing classes get a link property to the resolved class (STRATA saw
   no IND through the name variants; ER instance evidence licenses the edge);
4. **grounding surface forms** — each backed class's definition names its
   source table in acronym-visible form ("Source table: ASRS REPORTS"), so
   questions phrased against source vocabulary can ground.

Synonym propagation from STRATA candidates into ``PropertyDef.synonyms``
already happens inside the frozen emitter (member column names); enrichment
only ADDS — it never renames or removes induced structure.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import Optional

from ontoforge.contracts import Datatype, Ontology, PropertyDef, property_uri
from ontoforge.lodestone.model import all_props
from ontoforge.profiling.units_table import UNITS
from ontoforge.strata._norm import GENERIC_SUFFIX_TOKENS, name_tokens

from .conform import ColumnConformance
from .er_generic import ClassResolution
from .mapping import ClassPlan

__all__ = ["ERLink", "enrich_ontology"]


@dataclass(frozen=True)
class ERLink:
    """A pipeline-added link property backed by an ER/variant resolution."""

    subject_class_uri: str
    table: str
    column: str
    predicate: str
    target_class_uri: str
    from_text: bool = False     # column is a TEXT surface: spot identity values


def _owner_of(onto: Ontology, prop_name: str, class_uri: str) -> Optional[str]:
    """The class (self or ancestor) whose OWN property tuple defines prop_name."""
    for uri in [class_uri, *sorted(onto.ancestors(class_uri))]:
        c = onto.get(uri)
        if c is not None and any(p.name == prop_name for p in c.properties):
            return uri
    return None


def _patch_property(onto: Ontology, owner_uri: str, prop_name: str, conf: ColumnConformance) -> None:
    c = onto.get(owner_uri)
    if c is None:
        return
    new_props = []
    changed = False
    datatype = Datatype.INTEGER if conf.integral else Datatype.FLOAT
    dimension = UNITS[conf.unit].dimension if conf.unit in UNITS else None
    for p in c.properties:
        if p.name == prop_name and not p.is_link:
            needs_type = p.datatype not in (Datatype.INTEGER, Datatype.FLOAT)
            needs_unit = conf.unit is not None and p.unit != conf.unit
            if needs_type or needs_unit:
                p = dataclasses.replace(
                    p,
                    datatype=datatype if needs_type else p.datatype,
                    unit=conf.unit if conf.unit is not None else p.unit,
                    dimension=dimension if dimension is not None else p.dimension,
                )
                changed = True
        new_props.append(p)
    if not changed:
        return
    new_shapes = []
    for s in c.shapes:
        if s.prop == prop_name:
            s = dataclasses.replace(s, datatype=datatype, unit=conf.unit or s.unit, pattern=None)
        new_shapes.append(s)
    onto.replace_class(
        dataclasses.replace(c, properties=tuple(new_props), shapes=tuple(new_shapes))
    )


def _add_property(onto: Ontology, owner_uri: str, prop: PropertyDef) -> None:
    c = onto.get(owner_uri)
    if c is None or any(p.name == prop.name for p in c.properties):
        return
    onto.replace_class(dataclasses.replace(c, properties=c.properties + (prop,)))


def _link_basename(canonical: str) -> str:
    toks = list(name_tokens(canonical))
    while len(toks) > 1 and toks[-1] in GENERIC_SUFFIX_TOKENS:
        toks.pop()
    return "_".join(toks)


def _expand_synonyms(onto: Ontology) -> None:
    """Synonym propagation through STRATA's own normalizer: every property's
    member-column synonyms ('ACFT_REGIST_NMBR') gain their normalized phrase
    form ('aircraft registration number') plus 2+-token suffix phrases
    ('registration number'), so questions phrased in expanded vocabulary
    ground without estate-specific tables."""
    for c in list(onto.iter_classes()):
        new_props = []
        changed = False
        for p in c.properties:
            extra: list[str] = []
            for raw in (*p.synonyms, p.name):
                toks = name_tokens(raw)
                if len(toks) < 2:
                    continue
                for i in range(len(toks) - 1):
                    phrase = " ".join(toks[i:])
                    if phrase not in p.synonyms and phrase not in extra and phrase != p.name:
                        extra.append(phrase)
            if extra:
                p = dataclasses.replace(p, synonyms=p.synonyms + tuple(sorted(extra)))
                changed = True
            new_props.append(p)
        if changed:
            onto.replace_class(dataclasses.replace(c, properties=tuple(new_props)))


def _annotate_definition(onto: Ontology, class_uri: str, table: str, source_id: str) -> None:
    c = onto.get(class_uri)
    if c is None:
        return
    surface = table.upper().replace("_", " ").replace("-", " ")
    marker = f"Source table: {surface}"
    if marker in c.definition:
        return
    definition = f"{c.definition} {marker} ({source_id})." if c.definition else f"{marker} ({source_id})."
    onto.replace_class(dataclasses.replace(c, definition=definition))


def enrich_ontology(
    onto: Ontology,
    plans: list[ClassPlan],
    conformance: dict[tuple[str, str], ColumnConformance],
    resolutions: dict[str, ClassResolution],
    estate: dict,
) -> list[ERLink]:
    """Mutate ``onto`` in place per the module docstring; returns the ER link
    specs the materializer must emit link cells for."""
    meta = estate["metadata"]["tables"]

    # (4) grounding surface forms
    for plan in plans:
        if plan.table is not None:
            _annotate_definition(onto, plan.class_uri, plan.table, meta[plan.table]["source_id"])
    _expand_synonyms(onto)

    # (1) + (2): measure patches and unit annotations, only when every column
    # backing the property agrees on the decision (a shared parent property
    # conformed inconsistently across tables stays a string)
    decisions: dict[tuple[str, str], list[ColumnConformance]] = {}
    for plan in plans:
        if plan.table is None:
            continue
        for prop_name, cols in plan.prop_columns.items():
            if len(cols) != 1:
                continue  # multi-column concatenation is always a string
            owner = _owner_of(onto, prop_name, plan.class_uri)
            conf = conformance.get((plan.table, cols[0]))
            if owner is not None and conf is not None:
                decisions.setdefault((owner, prop_name), []).append(conf)
    for (owner, prop_name), confs in sorted(decisions.items()):
        if any(c.kind != "number" for c in confs):
            continue
        units = {c.unit for c in confs}
        if len(units) != 1:
            continue
        merged = ColumnConformance(
            kind="number",
            unit=confs[0].unit,
            source_units=tuple(sorted({u for c in confs for u in c.source_units})),
            annotate_unit=any(c.annotate_unit for c in confs),
            integral=all(c.integral for c in confs),
        )
        _patch_property(onto, owner, prop_name, merged)
        if merged.annotate_unit:
            _add_property(
                onto,
                owner,
                PropertyDef(
                    uri=property_uri(owner, f"{prop_name}_unit"),
                    name=f"{prop_name}_unit",
                    datatype=Datatype.STRING,
                    definition=f"source lexical unit {prop_name} was recorded in",
                ),
            )

    # (3) ER/variant-backed link properties from referencing classes into the
    # resolved one, plus identity-value spotting links from TEXT columns
    er_links: list[ERLink] = []
    plan_by_table: dict[str, list[ClassPlan]] = {}
    for plan in plans:
        if plan.kind == "table" and plan.table is not None:
            plan_by_table.setdefault(plan.table, []).append(plan)

    def _domain_predicate(res: ClassResolution) -> str:
        """One predicate per resolved domain: the most descriptive stripped
        base among the identity columns' canonical names (deterministic)."""
        bases: list[str] = []
        for t, c in res.domain.identity_columns:
            for p in plan_by_table.get(t, ()):
                canonical = next((pn for pn, cols in p.prop_columns.items() if c in cols), None)
                if canonical:
                    bases.append(_link_basename(canonical))
        bases = [b for b in bases if len(b) >= 3]
        if not bases:
            return f"ref_{_link_basename(res.domain.class_name.lower()) or 'entity'}"
        return max(bases, key=lambda b: (len(b), b))

    def _add_link(subj_plan: ClassPlan, predicate: str, target_uri: str,
                  t: str, c: str, from_text: bool) -> None:
        existing = set(all_props(onto, subj_plan.class_uri))
        name = predicate if predicate not in existing else f"{predicate}_ref"
        already = next(
            (
                el for el in er_links
                if el.subject_class_uri == subj_plan.class_uri
                and el.table == t and el.column == c and el.target_class_uri == target_uri
            ),
            None,
        )
        if already is not None:
            return
        if name not in existing:
            _add_property(
                onto,
                subj_plan.class_uri,
                PropertyDef(
                    uri=property_uri(subj_plan.class_uri, name),
                    name=name,
                    datatype=Datatype.STRING,
                    is_link=True,
                    range_class=target_uri,
                    synonyms=(c,),
                    definition=f"pipeline-resolved reference via {t}.{c}",
                ),
            )
        er_links.append(
            ERLink(
                subject_class_uri=subj_plan.class_uri,
                table=t,
                column=c,
                predicate=name,
                target_class_uri=target_uri,
                from_text=from_text,
            )
        )

    for target_uri, res in sorted(resolutions.items()):
        predicate = _domain_predicate(res)
        home = res.domain.home_column
        for t, c in res.domain.identity_columns:
            if (t, c) == home:
                continue
            for subj_plan in plan_by_table.get(t, ()):
                if subj_plan.class_uri == target_uri:
                    continue
                _add_link(subj_plan, predicate, target_uri, t, c, from_text=False)
        # text spotting only for explicit-prefix variant domains (the prefix
        # makes in-text identifier mentions precise)
        if res.method == "exact-variant" and res.variant_prefix:
            for t, tmeta in sorted(meta.items()):
                for tc in tmeta.get("text_columns", ()):
                    for subj_plan in plan_by_table.get(t, ()):
                        if subj_plan.class_uri == target_uri:
                            continue
                        _add_link(subj_plan, predicate, target_uri, t, tc, from_text=True)
    return er_links
