"""M-REL — the typed relationship-inference engine (v2.1 build instructions §1.1, §1.2).

CLOSED-CORE IP per OntoForge_Build_Instructions.md §18: the distribution-aware
confidence-PROXY scoring engine, the typed relationship classifier, and the
RoadSpy scout payload are proprietary inventions and are NOT part of the open
contract surface beyond the shared types in ``ontoforge.contracts.relationships``.

This is the FALSE-POSITIVE KILLER. The v2.1 doc names §1 the central technical
risk: "looks-similar-isn't-related." Two columns can share a name and a
cardinality and still be unrelated; what distinguishes a real relationship from a
coincidence is whether their *value distributions* actually align. This engine
fuses value OVERLAP (containment / Jaccard) with value DISTRIBUTION alignment
(Jensen-Shannon for categoricals, quantile divergence for numerics), key
uniqueness, entropy, cardinality, type compatibility, and sampled-row evidence
into an evidence-bearing confidence proxy, then types the relationship.

The engine ships KEYLESS and DETERMINISTIC: every signal is a pure function of
the two ``ColumnProfile`` sketches (φ(p)) plus small sampled value sets — NO bulk
rows, NO network, NO model invocation. Any "AI"/LLM adjudication step routes
through the existing ``aimodels`` router / ``ensemble`` gate but runs on
deterministic adapters today. Fixed input → identical candidates and evidence.

Layers (all reading ``ontoforge.contracts`` + ``ontoforge.profiling`` read-only):

* :mod:`signals`  — deterministic per-pair signal computation; each returns an
  :class:`~ontoforge.contracts.EvidenceArtifact` with fired / conflicts flags.
* :mod:`classify` — typed deterministic rules over the evidence →
  :class:`~ontoforge.contracts.RelationshipType` (+ ``needs_adjudication``).
* :mod:`score`    — fuse the signals into the confidence PROXY and emit a
  :class:`~ontoforge.contracts.RelationshipCandidate`.
* :mod:`roadspy`  — :func:`~roadspy.build_scout` packages the evidence (signals
  fired vs conflicted + small sterilized samples) as a
  :class:`~ontoforge.contracts.ScoutPayload` for the adjudicator — never bulk data.
* :mod:`discover` — :func:`~discover.discover_relationships` ranks every viable
  column pair across a set of :class:`~ontoforge.contracts.TableProfile`s.
"""

from .classify import ClassifierResult, classify_relationship
from .discover import PairProfiles, discover_relationships
from .roadspy import build_scout
from .score import (
    AMBIGUOUS_BAND,
    FK_PROXY_FLOOR,
    SignalSet,
    compute_signals,
    score_pair,
)
from .signals import (
    SAMPLE_CAP,
    SampledColumn,
    cardinality_ratio_signal,
    containment_signals,
    distribution_divergence_signal,
    entropy_signal,
    jaccard_signal,
    jensen_shannon,
    key_uniqueness_signal,
    name_similarity_signal,
    quantile_divergence,
    sampled_row_signal,
    shannon_entropy,
    type_compat_signal,
)

__all__ = [
    "AMBIGUOUS_BAND",
    "FK_PROXY_FLOOR",
    "SAMPLE_CAP",
    "ClassifierResult",
    "PairProfiles",
    "SampledColumn",
    "SignalSet",
    "build_scout",
    "cardinality_ratio_signal",
    "classify_relationship",
    "compute_signals",
    "containment_signals",
    "discover_relationships",
    "distribution_divergence_signal",
    "entropy_signal",
    "jaccard_signal",
    "jensen_shannon",
    "key_uniqueness_signal",
    "name_similarity_signal",
    "quantile_divergence",
    "sampled_row_signal",
    "score_pair",
    "shannon_entropy",
    "type_compat_signal",
]
