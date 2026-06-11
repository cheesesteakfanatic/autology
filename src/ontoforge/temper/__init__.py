"""M10 — TEMPER: the Ontology Evolution Calculus (whitepaper §3.6, §11.2 M10)."""

from .apply import DataAdapter, MigrationReport, OperatorDeferred, TemperEngine
from .morphism import ARTIFACT_KIND, MorphismLedger, MorphismRecord, invert_record, load_morphisms, replay
from .ops import (
    DATA_TOUCHING,
    OP_REGISTRY,
    ORIGIN_PREFIX,
    QUARANTINE_PROP,
    RETIRED_MARK,
    SPINE_GATED,
    AddClass,
    AddFacet,
    AddProperty,
    DemoteClass,
    DropClass,
    DropProperty,
    Generalize,
    MergeClasses,
    Operator,
    PreconditionError,
    PromoteProperty,
    RenameClass,
    RenameProperty,
    RetireClass,
    RetireFacet,
    RetypeProperty,
    Specialize,
    SplitClass,
    UnretireClass,
    conversion,
    facet_params,
    is_retired,
    mint_entity_uri,
    op_from_dict,
    op_to_dict,
    resolve_prop,
    storage_key,
)
from .views import Branch, Deref, Direct, Plan, Regroup, RewriterChain, StructuredQuery, execute, lift

__all__ = [name for name in dir() if not name.startswith("_")]
