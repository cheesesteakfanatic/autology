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
    #: WHY this decision is in the queue — never faked. One of:
    #: 'deferred' (tiers exhausted, no auto-decision), 'quarantined' (budget
    #: fail-close), 'low-confidence' (auto-resolved below the floor), or
    #: 'low-margin' (escalated past the deterministic auto-bands and resolved
    #: with an unresolved conformal set — a genuine low-margin auto-decision).
    review_reason: str = ""
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
    """One tiered arc between two class URIs.

    ``rel_type`` / ``rel_summary`` are the ADDITIVE typed-relationship overlay
    (v2.1 §1.2): the relationship-taxonomy verdict the closed-core relationships
    engine assigned to this arc's column pair (``fk_join`` · ``lookup_dimension``
    · ``m2m_bridge`` · ``denormalization`` · ``derived_field`` · ``unrelated`` ·
    ``unknown``), plus a one-line evidence summary. Both default to ``None`` and
    the atlas routes serialize with ``exclude_none`` so legacy clients (and the
    pinned UI fixture) see the byte-identical original payload.
    """

    src_class: str
    dst_class: str
    src_prop: Optional[str] = None
    dst_prop: Optional[str] = None
    tier: Literal["confirmed", "likely", "hint"]
    score: float
    evidence: AtlasEvidence
    rel_type: Optional[str] = None
    rel_summary: Optional[str] = None


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


# -------------------------------------------------------------------- catalog


class CatalogDataset(BaseModel):
    """One downloaded dataset available to add to a playground build."""

    id: str
    name: str
    source: str
    domain: str
    rows: int
    cols: int
    columns: list[str] = Field(default_factory=list)
    description: str = ""


class CatalogDomain(BaseModel):
    name: str
    count: int


class CatalogOut(BaseModel):
    datasets: list[CatalogDataset]
    domains: list[CatalogDomain]


# ------------------------------------------------------------------ workspace
# (the playground build workspace — distinct from the window-layout blob above)


class WorkspaceStats(BaseModel):
    types: int = 0
    confirmed: int = 0
    likely: int = 0
    silos: int = 0


class WorkspaceStateOut(BaseModel):
    datasets: list[str] = Field(default_factory=list)
    built: bool = False
    active_world: str = "demo"
    stats: WorkspaceStats = Field(default_factory=WorkspaceStats)


class WorkspaceBuildIn(BaseModel):
    dataset_ids: list[str] = Field(min_length=1)
    mode: Literal["replace", "add"] = "replace"


class WorkspaceBuildOut(BaseModel):
    job_id: str


class BuildEvent(BaseModel):
    seq: int
    kind: str
    msg: str = ""
    # the event carries a free-form typed payload (table/join/type fields); we
    # surface it permissively so the UI animator gets everything the worker emits
    model_config = {"extra": "allow"}


class BuildResult(BaseModel):
    stats: dict[str, Any] = Field(default_factory=dict)
    atlas: Optional[dict[str, Any]] = None


class BuildStatusOut(BaseModel):
    job_id: str
    status: Literal["running", "done", "error"]
    progress: float = 0.0
    stage: str = ""
    events: list[BuildEvent] = Field(default_factory=list)
    last_seq: int = 0
    result: Optional[BuildResult] = None
    error: str = ""


# ------------------------------------------------------------------- engineer


class InterpretIn(BaseModel):
    command: str = Field(min_length=1)


class InterpretOp(BaseModel):
    kind: str
    params: dict[str, Any] = Field(default_factory=dict)
    human_summary: str = ""
    confidence: float = 1.0


class InterpretPreview(BaseModel):
    description: str = ""
    affected_count: int = 0
    sample: list[Any] = Field(default_factory=list)
    coverage: Optional[float] = None
    tier: str = ""
    spine_gated: bool = False
    blocked: bool = False
    block_reason: str = ""
    valid: bool = True
    reason: str = ""
    #: the opaque, server-minted operator handle the apply step echoes back
    op_token: Optional[dict[str, Any]] = None


class InterpretOut(BaseModel):
    """One of: a proposed op+preview | a clarification | an unsupported reason.

    Exactly one branch is populated (the others are None/empty), matching the
    API contract's discriminated union."""

    op: Optional[InterpretOp] = None
    preview: Optional[InterpretPreview] = None
    clarification: Optional[str] = None
    options: list[str] = Field(default_factory=list)
    unsupported: bool = False
    reason: str = ""
    supported_examples: list[str] = Field(default_factory=list)


class ApplyIn(BaseModel):
    #: the op_token returned by /interpret (a serialized operator) — applied
    #: verbatim through the real TEMPER engine; never re-parsed from text
    op: dict[str, Any]


class AtlasDelta(BaseModel):
    added_links: list[dict[str, Any]] = Field(default_factory=list)
    removed: list[Any] = Field(default_factory=list)
    renamed: list[dict[str, Any]] = Field(default_factory=list)


class ApplyOut(BaseModel):
    ok: bool
    deferred: bool = False
    #: True when apply refused a sub-floor confidently-wrong join (distinct from
    #: an ordinary precondition rejection) — the UI shows it as a refused join.
    blocked: bool = False
    human_summary: str = ""
    new_stats: dict[str, Any] = Field(default_factory=dict)
    atlas_delta: AtlasDelta = Field(default_factory=AtlasDelta)
    undo_token: Optional[dict[str, Any]] = None
    #: ensemble DE-gate provenance for a link op (vote tally + per-expert weights
    #: + every expert's vote) — auditable "why did this join fire/hold?". Present
    #: only for gated link ops; None otherwise.
    gate: Optional[dict[str, Any]] = None


class UndoIn(BaseModel):
    undo_token: dict[str, Any]


class UndoOut(BaseModel):
    ok: bool
    human_summary: str = ""
    new_stats: dict[str, Any] = Field(default_factory=dict)


# -------------------------------------------------------------------- extract


class ExtractFilter(BaseModel):
    prop: str
    op: Literal["==", "!=", "<", "<=", ">", ">=", "contains"] = "=="
    value: Any = None


class ExtractIn(BaseModel):
    type_uri: str
    filters: list[ExtractFilter] = Field(default_factory=list)
    columns: list[str] = Field(default_factory=list)
    limit: int = 200


class ExtractCitation(BaseModel):
    row: int
    column: str
    value: Any = None
    atom_ids: list[str] = Field(default_factory=list)


class ExtractOut(BaseModel):
    columns: list[str] = Field(default_factory=list)
    rows: list[list[Any]] = Field(default_factory=list)
    citations: list[ExtractCitation] = Field(default_factory=list)


# ==================================================================== OBSERVATORY
# The OBSERVABILITY surface (R0 P1): four read-only views over the EXISTING
# ledger/HEARTH/CostMeter substrate. Nothing here recomputes — every field is
# SURFACED from what the pipeline already wrote (append-only atoms/decisions/
# artifacts/cost). The differentiator vs column-level incumbents is value-LEVEL
# lineage: a single answer cell resolves all the way back to the RAW source row
# and column it was derived from.


# ---------------------------------------------------------------------- lineage


class LineageAtom(BaseModel):
    """One RAW source record an answer cell rests on — the leaf of the trail.

    ``source`` / ``table`` / ``row`` / ``column`` are PARSED, not recomputed,
    from the atom uri the ingest stage minted
    (``atom://<source>/<table>/<rowkey>#<COLUMN>``); when the uri does not match
    that shape they are simply ``None`` and the full uri still identifies it."""

    atom_id: str
    uri: str
    value: Any = None
    source: Optional[str] = None
    table: Optional[str] = None
    row: Optional[str] = None
    column: Optional[str] = None


class LineageOut(BaseModel):
    """GET /api/lineage — the answer-cell → prov term → atoms → RAW rows trail.

    ``resolved`` is the polynomial provenance tree (the same shape /api/provenance
    returns); ``atoms`` is the flattened RAW leaf set with source/table/row/column
    parsed out — the value-level lineage incumbents cannot show."""

    cell: Optional[str] = None        # the entity uri (when asked by cell)
    prop: Optional[str] = None        # the property (when asked by cell)
    value: Any = None                 # the cell's current value (when by cell)
    prov_ref: str
    n_atoms: int
    atoms: list[LineageAtom] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)   # distinct source systems
    resolved: ProvNode


# ------------------------------------------------------------------------ audit


class AuditEntry(BaseModel):
    """One line of the append-only decision/verdict log.

    ``category`` buckets the heterogeneous substrate so the UI can group it:
    ``decision`` (a spine adjudication), ``verdict`` (a human review verdict),
    ``recalibration`` (the §4.8 loop firing), ``temper`` (an ontology-evolution
    op — retype/rename/merge/split/link/delete), or ``commit`` (an engineer
    typed-relationship / operator commit, with its evidence in ``detail``)."""

    seq: int
    category: Literal["decision", "verdict", "recalibration", "temper", "commit"]
    kind: str
    summary: str = ""
    tier: Optional[int] = None
    confidence: Optional[float] = None
    outcome: Optional[str] = None
    deferred: bool = False
    quarantined: bool = False
    prov_ref: Optional[str] = None
    detail: dict[str, Any] = Field(default_factory=dict)
    created_at: str = ""


class AuditOut(BaseModel):
    entries: list[AuditEntry] = Field(default_factory=list)
    by_category: dict[str, int] = Field(default_factory=dict)
    by_kind: dict[str, int] = Field(default_factory=dict)
    by_tier: dict[str, int] = Field(default_factory=dict)
    total: int = 0


# ------------------------------------------------------------------------- runs


class RunOut(BaseModel):
    """One pipeline/answer run surfaced from the ledger's append-only history."""

    run_id: str
    kind: str                          # ingest|profile|...|ask|engineer|export
    label: str = ""
    started_at: str = ""
    decisions: int = 0
    artifacts: int = 0
    cost_tokens: int = 0


class RunsOut(BaseModel):
    runs: list[RunOut] = Field(default_factory=list)
    stages: list[str] = Field(default_factory=list)
    total_decisions: int = 0
    total_artifacts: int = 0
    total_cost_tokens: int = 0


# --------------------------------------------------------------- compute-ledger


class ComputeRow(BaseModel):
    """One rolled-up line of the per-project CostMeter (task or tier)."""

    label: str
    calls: int
    tokens: int


class ComputeLedgerOut(BaseModel):
    """GET /api/compute-ledger — the compute-at-cost transparency artifact:
    zero margin, exactly what ran. Rolled up two ways from the COST + DECISION
    tables (which never diverge — ``LedgerCostMeter`` writes through both)."""

    by_task: list[ComputeRow] = Field(default_factory=list)
    by_tier: list[ComputeRow] = Field(default_factory=list)
    total_tokens: int = 0
    total_calls: int = 0
    decision_tokens: int = 0    # tokens attributed to spine decisions (by tier)
    estate: str = ""


# ---------------------------------------------------------------- criticality


class CriticalityElement(BaseModel):
    """One ranked ontology element on GET /api/criticality.

    ``uri`` is a class uri (a node of the induced criticality graph), ``label``
    its human display name, ``score`` the lazy criticality blend (0..1, usage +
    centrality + recency + dependents), and ``kind`` the element kind (always
    ``class`` in v0 — the graph nodes are ontology classes)."""

    uri: str
    label: str
    score: float
    kind: str = "class"


class CriticalityOut(BaseModel):
    """GET /api/criticality?top=N — the top-N critical ontology elements,
    score-sorted. ``total`` is how many elements have a non-default score. An
    unbuilt world (no ontology yet) returns an empty list, never an error."""

    elements: list[CriticalityElement] = Field(default_factory=list)
    total: int = 0
