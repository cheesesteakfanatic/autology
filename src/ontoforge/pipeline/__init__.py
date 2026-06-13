"""Generic estate engine: OntoForge on ANY data, answering over the ontology
it INDUCED (whitepaper §11.3 Phase 3 — the STRATA swap-in, inverted into a
product capability).

The package owns the seam between the frozen modules:

    discover_sources(dir)             any *.csv / *.parquet directory -> estate
    induce_estate(estate, ledger)     M3 profiles + INDs -> M4 STRATA induction
    build_plans(artifacts, onto)      candidate->class evidence recovery
    resolve_generic(...)              generic M5 ER over shared identity domains
    materialize_induced(...)          induced ontology -> HEARTH world with
                                      constraint-H provenance

No frozen module is modified: STRATA's candidate evidence is read back from
its public ``StrataResult`` artifacts, ER runs through the M5 cascade's
name-flavored ("operator") feature path, and conformance/grounding enrichment
happen on the pipeline's side of the interface.
"""

from .atlas import (
    AtlasComponent,
    AtlasEvidence,
    AtlasLink,
    AtlasReport,
    build_and_persist_atlas,
    build_atlas,
)
from .conform import ColumnConformance, conform_value, decide_column, is_null_value, parse_measure
from .discover import ESTATE_NAME, KEY_SEP, discover_sources, load_table, slugify, table_row_keys
from .enrich import ERLink, enrich_ontology
from .er_generic import ClassResolution, IdentityDomain, identity_domains, resolve_generic
from .induce import InducedArtifacts, induce_estate, profile_estate
from .mapping import ClassPlan, build_plans
from .materialize import materialize_induced

__all__ = [
    "ESTATE_NAME",
    "KEY_SEP",
    "AtlasComponent",
    "AtlasEvidence",
    "AtlasLink",
    "AtlasReport",
    "ClassPlan",
    "ClassResolution",
    "ColumnConformance",
    "ERLink",
    "IdentityDomain",
    "InducedArtifacts",
    "build_and_persist_atlas",
    "build_atlas",
    "build_plans",
    "conform_value",
    "decide_column",
    "discover_sources",
    "enrich_ontology",
    "identity_domains",
    "induce_estate",
    "is_null_value",
    "load_table",
    "materialize_induced",
    "parse_measure",
    "profile_estate",
    "resolve_generic",
    "slugify",
    "table_row_keys",
]
