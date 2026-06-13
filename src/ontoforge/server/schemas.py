"""Pydantic request/response models for the OntoForge REST API."""

from __future__ import annotations

from typing import Any, Literal, Optional, Union

from pydantic import BaseModel, Field

# --------------------------------------------------------------------- status


class TierCount(BaseModel):
    count: int
    deferred: int
    quarantined: int


class StatusOut(BaseModel):
    project: str
    estate: str
    limit: Optional[int] = None
    stages: list[str]
    ledger_exists: bool
    atoms: int = 0
    decisions_by_tier: dict[str, TierCount] = Field(default_factory=dict)
    decisions_by_kind: dict[str, int] = Field(default_factory=dict)
    artifacts: dict[str, int] = Field(default_factory=dict)
    cost_tokens: int = 0
    materialized: Optional[dict[str, Any]] = None


# ------------------------------------------------------------------- ontology


class PropertyOut(BaseModel):
    uri: str
    name: str
    datatype: str
    is_link: bool
    range_class: Optional[str] = None
    unit: Optional[str] = None
    dimension: Optional[list[int]] = None
    cardinality: str = "one"
    functional: bool = False
    synonyms: list[str] = Field(default_factory=list)
    definition: str = ""


class ClassOut(BaseModel):
    uri: str
    name: str
    parents: list[str]
    properties: list[PropertyOut]
    confidence: float
    is_event: bool
    definition: str = ""
    n_shapes: int = 0


class EdgeOut(BaseModel):
    source: str          # class uri
    link: str            # property name
    target: str          # class uri


class OntologyOut(BaseModel):
    version: int
    classes: list[ClassOut]
    edges: list[EdgeOut]


# ------------------------------------------------------------------------ ask


class AskIn(BaseModel):
    question: str = Field(min_length=1)


class ClarifyIn(BaseModel):
    question: str = Field(min_length=1)
    choice: Union[int, str]


class CitationOut(BaseModel):
    row: int
    column: str
    value: Any = None
    atom_ids: list[str]


class AskOut(BaseModel):
    question: str
    columns: list[str]
    rows: list[list[Any]]
    confidence: float
    abstained: bool
    abstain_reason: str = ""
    clarification: Optional[str] = None
    clarification_options: list[str] = Field(default_factory=list)
    citations: list[CitationOut] = Field(default_factory=list)
    cached: bool = False


# --------------------------------------------------------- atoms & provenance


class AtomOut(BaseModel):
    atom_id: str
    uri: str
    value: Any = None


class ProvNode(BaseModel):
    """One node of a resolved provenance polynomial: sums/products over atoms."""

    kind: Literal["sum", "product", "atom", "one", "zero"]
    atom_id: Optional[str] = None
    uri: Optional[str] = None
    value: Any = None
    terms: list["ProvNode"] = Field(default_factory=list)


ProvNode.model_rebuild()


class ProvenanceOut(BaseModel):
    prov_ref: str
    n_atoms: int
    tree: ProvNode


# --------------------------------------------------------------------- review


class ReviewItem(BaseModel):
    decision_id: str
    kind: str
    outcome: str
    confidence: float
    conformal_set: list[str]
    tier: int
    deferred_to_human: bool
    quarantined: bool
    rationale: str = ""
    prov_atoms: list[str] = Field(default_factory=list)
    created_at: str = ""


class ReviewArtifact(BaseModel):
    artifact_id: str
    payload: Any = None
    prov_ref: str
    created_at: str = ""


class ReviewOut(BaseModel):
    items: list[ReviewItem]
    artifacts: list[ReviewArtifact] = Field(default_factory=list)
    verdicts: dict[str, int] = Field(default_factory=dict)
    recalibrations: dict[str, int] = Field(default_factory=dict)
    threshold: int


class VerdictIn(BaseModel):
    verdict: Literal["accept", "reject"]
    note: str = ""


class VerdictOut(BaseModel):
    decision_id: str
    kind: str
    verdict: str
    verdicts_for_kind: int
    threshold: int
    recalibrated: bool
    recalibrations_for_kind: int


# ------------------------------------------------------------------- entities


class HistoryCellOut(BaseModel):
    """One HEARTH value cell: bitemporal bounds (None = open/FOREVER), source
    rank, confidence, and the interned provenance ref (/api/provenance takes it)."""

    value: Any = None
    valid_from: Optional[str] = None
    valid_to: Optional[str] = None
    system_from: Optional[str] = None
    system_to: Optional[str] = None
    confidence: float
    src_rank: int
    prov_ref: str
    is_current: bool


class EntityOut(BaseModel):
    uri: str
    classes: list[str]
    stance: str                       # 'current' | 'as_of:<ISO-8601, UTC>'
    properties: dict[str, Any]        # the property card under the stance
    history: dict[str, list[HistoryCellOut]]  # prop -> every cell ever written


# ----------------------------------------------------------------- dashboards


class DashboardIn(BaseModel):
    utterance: str = Field(min_length=1)


class ChartOut(BaseModel):
    title: str
    vega: dict[str, Any]


class DashboardOut(BaseModel):
    title: str
    score: float
    rationale: str = ""
    charts: list[ChartOut]


class DashboardsOut(BaseModel):
    utterance: str
    dashboards: list[DashboardOut]


class SavedDashboardOut(BaseModel):
    file: str
    title: str
    score: Optional[float] = None
    rationale: str = ""
    charts: list[ChartOut] = Field(default_factory=list)


class SavedDashboardsOut(BaseModel):
    dashboards: list[SavedDashboardOut]


# --------------------------------------------------------------------- search


class SearchResult(BaseModel):
    """One hit of the federated search (the frozen Cmd+K contract)."""

    kind: Literal["class", "entity", "property", "question", "app"]
    title: str
    subtitle: str
    ref: str
    score: float


class SearchOut(BaseModel):
    results: list[SearchResult]


# ------------------------------------------------------------------ neighbors


class NeighborLink(BaseModel):
    predicate: str
    direction: Literal["out", "in"]
    target_uri: str
    target_label: str


class NeighborsOut(BaseModel):
    links: list[NeighborLink]


# --------------------------------------------------------------------- export


class ExportIn(BaseModel):
    out_dir: Optional[str] = None


class ExportBundleOut(BaseModel):
    bundle_dir: str
    manifest_path: str
    files: int
    total_bytes: int


class ExportsOut(BaseModel):
    exports: list[ExportBundleOut]


# ---------------------------------------------------------------------- atlas


class AtlasEvidence(BaseModel):
    """WHY one atlas arc exists — the evidence-card contract."""

    coverage: float
    overlap_count: int
    sample_shared_values: list[str] = Field(default_factory=list)
    name_similarity: float
    semtype_match: bool


class AtlasLink(BaseModel):
    """One tiered arc between two class URIs."""

    src_class: str
    dst_class: str
    src_prop: Optional[str] = None
    dst_prop: Optional[str] = None
    tier: Literal["confirmed", "likely", "hint"]
    score: float
    evidence: AtlasEvidence


class AtlasComponent(BaseModel):
    """One island (or silo) of confirmed-connected classes."""

    id: str
    label: str
    class_uris: list[str]
    dataset_count: int
    is_silo: bool


class AtlasStats(BaseModel):
    classes: int
    components: int
    silos: int
    confirmed: int
    likely: int
    hint: int


class AtlasOut(BaseModel):
    """GET /api/atlas — the full connection atlas the constellation renders."""

    components: list[AtlasComponent]
    links: list[AtlasLink]
    stats: AtlasStats


class AtlasLinksOut(BaseModel):
    """GET /api/atlas/link?src=&dst= — the matching arcs with full evidence."""

    src: str
    dst: str
    links: list[AtlasLink]


# --------------------------------------------------------------------- reload


class ReloadOut(BaseModel):
    reloaded: bool
