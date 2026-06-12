"""M13 VISTA — metric-layer derivation (whitepaper §6.3, §1.2 M^(t); AMD-0007 minimal).

The semantic layer M^(t) is *derived from* the ontology O^(t), never
hand-authored: every numeric/dimensioned PropertyDef yields aggregate metrics,
every class yields a count metric, and every categorical / temporal / link
property of the class yields a candidate group-by dimension. Downstream,
LODESTONE grounds questions against the same surface and VISTA's composition
search (compose.py) ranks dashboards over it.

Deterministic: output order is a pure function of the ontology contents.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ontoforge.contracts import ClassDef, Datatype, Ontology, PropertyDef
from ontoforge.contracts.oqir import Agg

#: property-name suffixes that mark identifier-like columns — high-cardinality
#: codes, useless as group-by dimensions.
_ID_SUFFIXES = ("_id", "_code", "_number", "_key", "_uri")
_ID_EXACT = frozenset({"acn", "iata", "mode_s_code", "ntsb_number"})

#: aggregates emitted per measure property (count metrics are per-class).
MEASURE_AGGS: tuple[Agg, ...] = (Agg.AVG, Agg.SUM)


@dataclass(frozen=True, slots=True)
class DimRef:
    """One candidate group-by dimension on a class."""

    name: str          # property name (the OQIR group_by key)
    kind: str          # "categorical" | "temporal" | "link"
    target: str = ""   # link-target class *name* when kind == "link"


@dataclass(frozen=True, slots=True)
class MetricDef:
    """One derived metric: an aggregate over a class, with candidate dims."""

    name: str                                # e.g. "avg_cost", "workorder_count"
    class_uri: str
    class_name: str
    agg: Agg
    measure_prop: Optional[PropertyDef]      # None only for COUNT metrics
    dims: tuple[DimRef, ...]                 # candidate group-by dimensions
    unit: Optional[str]                      # canonical unit of the measure

    @property
    def measure_name(self) -> Optional[str]:
        return self.measure_prop.name if self.measure_prop is not None else None


def is_identifier_like(prop: PropertyDef) -> bool:
    low = prop.name.lower()
    return low in _ID_EXACT or any(low.endswith(s) for s in _ID_SUFFIXES)


def is_measure(prop: PropertyDef) -> bool:
    """Numeric AND dimensioned/united — §3.2: only physical quantities are measures."""
    return (
        not prop.is_link
        and prop.datatype in (Datatype.INTEGER, Datatype.FLOAT)
        and (prop.dimension is not None or prop.unit is not None)
    )


def effective_properties(cls: ClassDef, ontology: Ontology) -> list[PropertyDef]:
    """Own properties plus inherited ones (subsumption ≤_C), own names winning."""
    props: dict[str, PropertyDef] = {p.name: p for p in cls.properties}
    for ancestor_uri in sorted(ontology.ancestors(cls.uri)):
        ancestor = ontology.get(ancestor_uri)
        if ancestor is None:
            continue
        for p in ancestor.properties:
            props.setdefault(p.name, p)
    return sorted(props.values(), key=lambda p: p.name)


def candidate_dims(cls: ClassDef, ontology: Ontology) -> tuple[DimRef, ...]:
    """Group-by candidates: categorical props, temporal props, link targets —
    including properties inherited through the subsumption order."""
    dims: list[DimRef] = []
    for p in effective_properties(cls, ontology):
        if p.is_link:
            target = ontology.get(p.range_class or "")
            dims.append(DimRef(name=p.name, kind="link", target=target.name if target else ""))
        elif p.datatype in (Datatype.DATE, Datatype.DATETIME):
            dims.append(DimRef(name=p.name, kind="temporal"))
        elif p.datatype == Datatype.STRING and not is_identifier_like(p):
            dims.append(DimRef(name=p.name, kind="categorical"))
        # TEXT props are textJoin surface, not dimensions; measures are not dims.
    return tuple(sorted(dims, key=lambda d: (d.kind, d.name)))


def _metric_name(agg: Agg, prop: PropertyDef, cls: ClassDef, taken: set[str]) -> str:
    base = f"{agg.value}_{prop.name}"
    if base not in taken:
        return base
    return f"{agg.value}_{cls.name.lower()}_{prop.name}"


def derive_metric_layer(ontology: Ontology) -> list[MetricDef]:
    """M^(t) := derived metrics over O^(t). One COUNT metric per class with at
    least one dimension or measure; AVG+SUM per measure property."""
    metrics: list[MetricDef] = []
    taken: set[str] = set()
    for uri in sorted(ontology.classes):
        cls = ontology.classes[uri]
        dims = candidate_dims(cls, ontology)
        measures = sorted((p for p in cls.properties if is_measure(p)), key=lambda p: p.name)
        if dims or measures:
            name = f"{cls.name.lower()}_count"
            metrics.append(
                MetricDef(
                    name=name,
                    class_uri=uri,
                    class_name=cls.name,
                    agg=Agg.COUNT,
                    measure_prop=None,
                    dims=dims,
                    unit=None,
                )
            )
            taken.add(name)
        for p in measures:
            for agg in MEASURE_AGGS:
                name = _metric_name(agg, p, cls, taken)
                taken.add(name)
                metrics.append(
                    MetricDef(
                        name=name,
                        class_uri=uri,
                        class_name=cls.name,
                        agg=agg,
                        measure_prop=p,
                        dims=dims,
                        unit=p.unit,
                    )
                )
    metrics.sort(key=lambda m: (m.class_name, m.measure_name or "", m.agg.value))
    return metrics


__all__ = ["DimRef", "MetricDef", "derive_metric_layer", "candidate_dims", "is_measure"]
