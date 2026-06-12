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


# --------------------------------------------------------------------- reload


class ReloadOut(BaseModel):
    reloaded: bool
