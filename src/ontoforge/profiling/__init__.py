"""M3 — Profiler & Dependency Discovery (whitepaper §3.1–3.2, §11.2 M3; AMD-0003).

One profiling pass produces the universal feature object φ(p) — ColumnProfile
sketches, FDs, INDs, candidate keys, units/dimensions, semantic types — consumed
by STRATA (M4), ANVIL (M8), and WARDEN (M9).

§11.2 M3 interface mapping:
    profile(stream) -> φ            : profile_table / profile_column
    discover_fds(table) -> [FD]     : discover_fds (+ candidate_keys)
    discover_inds(corpus) -> [IND]  : discover_inds
    dimension(column) -> vector     : dimension_of (full detail via infer_unit)
"""

from .fds import (
    candidate_keys,
    discover_fds,
    g3_confidence,
    partition_product,
    stripped_partition,
)
from .format_signature import (
    Tok,
    format_signature,
    generalize,
    merge_token_seqs,
    render,
    to_regex,
    tokenize,
)
from .inds import discover_inds, name_token_jaccard
from .profile import (
    detect_append_mostly,
    profile,
    profile_column,
    profile_table,
    profile_table_detailed,
)
from .semantic_types import (
    CONFIDENCE_FLOOR,
    SEMANTIC_RULES,
    SemanticClassifier,
    SemanticRule,
    SklearnSemanticHook,
    extract_semantic_features,
    infer_datatype,
    infer_semantic_type,
)
from .sketches import HyperLogLog, KLLSketch, MinHash
from .units_infer import (
    UnitInference,
    dimension_of,
    infer_unit,
    parse_value_suffix,
    split_name_tokens,
)
from .units_table import ALIASES, UNITS, UnitAlias, lookup_unit, resolve_token, units_in_dimension

__all__ = [
    "ALIASES",
    "CONFIDENCE_FLOOR",
    "HyperLogLog",
    "KLLSketch",
    "MinHash",
    "SEMANTIC_RULES",
    "SemanticClassifier",
    "SemanticRule",
    "SklearnSemanticHook",
    "Tok",
    "UNITS",
    "UnitAlias",
    "UnitInference",
    "candidate_keys",
    "detect_append_mostly",
    "dimension_of",
    "discover_fds",
    "discover_inds",
    "extract_semantic_features",
    "format_signature",
    "g3_confidence",
    "generalize",
    "infer_datatype",
    "infer_semantic_type",
    "infer_unit",
    "lookup_unit",
    "merge_token_seqs",
    "name_token_jaccard",
    "parse_value_suffix",
    "partition_product",
    "profile",
    "profile_column",
    "profile_table",
    "profile_table_detailed",
    "render",
    "resolve_token",
    "split_name_tokens",
    "stripped_partition",
    "to_regex",
    "tokenize",
    "units_in_dimension",
]
