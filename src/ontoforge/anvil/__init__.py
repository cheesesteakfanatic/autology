"""M8 — ANVIL: By-Ontology Transform Synthesis (whitepaper §5.2, §11.2 M8).

The induced/gold ontology O + shapes Σ are the machine-checkable target spec;
ANVIL synthesizes RAW->CONFORMED transforms whose outputs satisfy Σ, verified
on seeded holdouts with provenance-equivalence row-tag checks, and accepted
through DecisionKind.TX spine decisions as readable contracts.TransformDef
artifacts.
"""

from .acceptance import AcceptanceOutcome, Acceptor, pretty_sql, tx_rule
from .anvil import Anvil, SynthesisRun, synthesize
from .detectors import (
    DATE_FORMATS,
    NULL_TOKENS,
    ColumnFix,
    detect_column_fixes,
    detect_constant_columns,
    detect_duplicate_rows,
    detect_header_rows,
)
from .mapping import match_columns, match_score, normalize_name
from .program import CandidateProgram, ColumnExpr, Fix, JoinSpec
from .search import SearchStats, induce_extraction, t1_search
from .verify import check_shapes, run_program, split_indices, verify_candidate

__all__ = [
    "AcceptanceOutcome",
    "Acceptor",
    "Anvil",
    "CandidateProgram",
    "ColumnExpr",
    "ColumnFix",
    "DATE_FORMATS",
    "Fix",
    "JoinSpec",
    "NULL_TOKENS",
    "SearchStats",
    "SynthesisRun",
    "check_shapes",
    "detect_column_fixes",
    "detect_constant_columns",
    "detect_duplicate_rows",
    "detect_header_rows",
    "induce_extraction",
    "match_columns",
    "match_score",
    "normalize_name",
    "pretty_sql",
    "run_program",
    "split_indices",
    "synthesize",
    "t1_search",
    "tx_rule",
    "verify_candidate",
]
