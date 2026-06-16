"""Per-pair evidence signals — the confidence-proxy substrate (v2.1 §1.1).

CLOSED-CORE IP (OntoForge_Build_Instructions.md §18).

Each function takes two columns' profiles (``ColumnProfile`` sketches) plus a
small SAMPLED value set per side — NEVER bulk rows — and emits one
:class:`~ontoforge.contracts.EvidenceArtifact`: the signal's value, the weight it
contributes to the fused proxy, whether it ``fired`` (cleared its activation
threshold) and whether it ``conflicts`` with a relatedness hypothesis. The fused
set of artifacts is the engine's reasoning trail.

The signals, and why each matters for killing false positives:

VALUE_CONTAINMENT  |A∩B|/|A| in BOTH directions. A genuine FK child is (near-)
    contained in its parent key; a coincidental name match is not. Computed
    exactly on the sampled intersection and corroborated by the MinHash-Jaccard
    estimate; we report the conservative (lower) of the two so noise can only
    DECREASE confidence, never manufacture it.
VALUE_JACCARD      MinHash-estimated J(A,B) over the full value sets (φ carries a
    k=64 signature). Symmetric overlap.
INFREQUENT_TOKEN   Jaccard restricted to the RARE tokens of the two sampled value
    sets. Whole-value overlap collapses on format variants — ``"123 Main St"`` and
    ``"123 Main Street"`` share zero values yet are the same address. Tokenizing the
    values and keeping only the INFREQUENT tokens (the discriminating ones — a
    street name, an account stem — not the boilerplate ``"st"``/``"street"``/``"inc"``
    that appears everywhere) recovers the join: the rare tokens still coincide.
    Fused as one more EvidenceArtifact, never a single-metric gate.
DISTRIBUTION_DIVERGENCE  THE false-positive killer. Two columns can overlap on
    *which* values appear yet disagree wildly on *how often* / *where* — that is
    "looks similar, isn't related." Jensen-Shannon divergence over the
    value-frequency distribution (categoricals) or decile/quantile divergence
    (numerics). High divergence FIRES as a CONFLICT and is weighted strongly
    negative downstream.
CARDINALITY_RATIO  distinct(lhs):distinct(rhs). Sets the many:1 / 1:1 / many:many
    shape that separates FK from bridge from denormalization.
KEY_UNIQUENESS     is the rhs side near-unique (a viable FK parent / dimension
    key)? A join target must be key-like.
ENTROPY            value-distribution entropy. Near-constant / low-entropy columns
    (status flags, booleans) make poor keys and produce vacuous overlaps.
NAME_SIMILARITY    exact / substring / trigram name affinity — emitted as a WEAK
    weight ONLY. Name agreement must never by itself assert a relationship; that
    is precisely the trap.
TYPE_COMPAT        datatype + semantic-type compatibility.
SAMPLED_ROW        corroboration from the actual sampled values (shared-value
    fraction on the samples) — concrete evidence the adjudicator can inspect.

Every computation is deterministic: pure functions of φ + sorted sampled sets,
all floats rounded, fixed thresholds. No seeds drawn at call time, no network.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from ontoforge.contracts import (
    ColumnProfile,
    Datatype,
    EvidenceArtifact,
    SignalKind,
    minhash_jaccard,
)
from ontoforge.profiling import name_token_jaccard

__all__ = [
    "SAMPLE_CAP",
    "INFREQUENT_TOKEN_FRACTION",
    "SampledColumn",
    "shannon_entropy",
    "jensen_shannon",
    "quantile_divergence",
    "trigram_similarity",
    "value_tokens",
    "infrequent_token_sets",
    "containment_signals",
    "jaccard_signal",
    "infrequent_token_signal",
    "distribution_divergence_signal",
    "cardinality_ratio_signal",
    "key_uniqueness_signal",
    "entropy_signal",
    "name_similarity_signal",
    "type_compat_signal",
    "sampled_row_signal",
]

# Defensive cap: a scout/sample set is small by construction; we never iterate bulk.
SAMPLE_CAP = 512

# A token is "infrequent" (discriminating) when it appears in at most this FRACTION
# of the combined distinct value sample. Boilerplate ("st", "street", "inc", "the")
# recurs across many values and is filtered out; the rare tokens that actually
# identify a row (a street name, an account stem) survive. Tunable.
INFREQUENT_TOKEN_FRACTION = 0.5

_NUMERIC = (Datatype.INTEGER, Datatype.FLOAT)
_STRINGY = (Datatype.STRING, Datatype.TEXT)
# Datatype compatibility groups — mirrors profiling.inds._GROUP so the two layers agree.
_GROUP: dict[Datatype, str] = {
    Datatype.INTEGER: "numeric",
    Datatype.FLOAT: "numeric",
    Datatype.STRING: "string",
    Datatype.TEXT: "string",
    Datatype.DATE: "date",
    Datatype.DATETIME: "datetime",
}


@dataclass(frozen=True, slots=True)
class SampledColumn:
    """A column's profile plus a small sampled value set — the signal input unit.

    ``values`` is a SMALL stratified sample (cap :data:`SAMPLE_CAP`); it is never
    the bulk column. Frequency-bearing signals use ``value_counts`` when supplied
    (sample-level counts), else fall back to the distinct sample set. This is the
    only place sampled values enter the engine.
    """

    profile: ColumnProfile
    values: tuple[str, ...] = ()
    value_counts: tuple[tuple[str, int], ...] = ()  # optional (value, freq) on the sample

    @property
    def distinct(self) -> int:
        return max(self.profile.distinct_estimate, 0)

    def value_set(self) -> frozenset[str]:
        vs = set(self.values[:SAMPLE_CAP])
        for v, _ in self.value_counts[:SAMPLE_CAP]:
            vs.add(v)
        return frozenset(vs)

    def freq_map(self) -> dict[str, float]:
        """Normalized value-frequency distribution over the sample (categoricals)."""
        if self.value_counts:
            counts = {v: float(c) for v, c in self.value_counts[:SAMPLE_CAP] if c > 0}
        else:
            # no counts: treat sample as a uniform multiset of distinct values
            counts = {}
            for v in self.values[:SAMPLE_CAP]:
                counts[v] = counts.get(v, 0.0) + 1.0
        total = sum(counts.values())
        if total <= 0:
            return {}
        return {k: v / total for k, v in counts.items()}


# --------------------------------------------------------------------------- math


def shannon_entropy(probs: list[float]) -> float:
    """Shannon entropy in NATS of a probability vector (ignores zero/neg mass)."""
    h = 0.0
    for p in probs:
        if p > 0.0:
            h -= p * math.log(p)
    return h


def _kl(p: dict[str, float], q: dict[str, float]) -> float:
    out = 0.0
    for k, pk in p.items():
        qk = q.get(k, 0.0)
        if pk > 0.0 and qk > 0.0:
            out += pk * math.log(pk / qk)
    return out


def jensen_shannon(p: dict[str, float], q: dict[str, float]) -> float:
    """Jensen-Shannon divergence (base 2) over two value-frequency maps → [0, 1].

    JSD is symmetric and bounded: 0 = identical distributions, 1 = disjoint
    support. This is the categorical distribution-divergence kernel: two columns
    sharing *values* but disagreeing on *frequencies* score high here even when
    overlap/Jaccard look benign.
    """
    if not p or not q:
        return 1.0
    keys = set(p) | set(q)
    m = {k: 0.5 * (p.get(k, 0.0) + q.get(k, 0.0)) for k in keys}
    jsd_nats = 0.5 * _kl(p, m) + 0.5 * _kl(q, m)
    jsd = jsd_nats / math.log(2.0)  # convert nats → bits so the bound is exactly 1
    return min(1.0, max(0.0, jsd))


def quantile_divergence(qa: tuple[float, ...], qb: tuple[float, ...]) -> float:
    """Normalized decile/quantile divergence for numeric columns → [0, 1].

    Mean absolute decile gap normalized by the pooled spread (a scale-free
    L1-between-quantile-functions distance, ≈ normalized Wasserstein-1). Two
    numeric columns with overlapping VALUES but different SHAPES (e.g. an id range
    vs. a measure on the same integers) diverge here. Returns 1.0 when the spread
    is degenerate but the centers differ, 0.0 when both are point masses at the
    same value.
    """
    if not qa or not qb or len(qa) != len(qb):
        return 1.0
    lo = min(qa[0], qb[0])
    hi = max(qa[-1], qb[-1])
    spread = hi - lo
    gaps = [abs(a - b) for a, b in zip(qa, qb)]
    mean_gap = sum(gaps) / len(gaps)
    if spread <= 0.0:
        return 0.0 if mean_gap == 0.0 else 1.0
    return min(1.0, mean_gap / spread)


def trigram_similarity(a: str, b: str) -> float:
    """Dice coefficient over character trigrams of two (lowercased) names → [0, 1]."""
    a, b = a.lower(), b.lower()
    if a == b:
        return 1.0

    def grams(s: str) -> set[str]:
        s = f"  {s} "
        return {s[i : i + 3] for i in range(len(s) - 2)}

    ga, gb = grams(a), grams(b)
    if not ga or not gb:
        return 0.0
    return 2 * len(ga & gb) / (len(ga) + len(gb))


def value_tokens(value: str) -> frozenset[str]:
    """Split a value string into lowercased word tokens (alnum runs).

    Pure and deterministic: lowercases, splits on any non-alphanumeric boundary,
    and drops length-1 tokens (single letters/digits carry no discriminating
    power). ``"123 Main St."`` → ``{"123", "main", "st"}``.
    """
    out: set[str] = set()
    cur: list[str] = []
    for ch in value.lower():
        if ch.isalnum():
            cur.append(ch)
        elif cur:
            tok = "".join(cur)
            if len(tok) > 1:
                out.add(tok)
            cur = []
    if cur:
        tok = "".join(cur)
        if len(tok) > 1:
            out.add(tok)
    return frozenset(out)


def infrequent_token_sets(
    left_values: frozenset[str],
    right_values: frozenset[str],
    *,
    fraction: float = INFREQUENT_TOKEN_FRACTION,
) -> tuple[frozenset[str], frozenset[str]]:
    """Per-side sets of INFREQUENT tokens, computed over the COMBINED sample.

    Document-frequency = how many distinct values (across both sides pooled)
    contain a token. A token kept only when its combined document-frequency ≤
    ``fraction`` × (distinct values pooled): boilerplate that appears in most
    values is dropped, the rare discriminating tokens survive. Returns
    (left_rare_tokens, right_rare_tokens) so the caller can Jaccard them.
    """
    docs = list(left_values) + list(right_values)
    n_docs = len(docs)
    if n_docs == 0:
        return frozenset(), frozenset()
    tokenized = [value_tokens(v) for v in docs]
    df: dict[str, int] = {}
    for toks in tokenized:
        for t in toks:
            df[t] = df.get(t, 0) + 1
    cutoff = max(1, int(fraction * n_docs))
    rare = {t for t, c in df.items() if c <= cutoff}

    def side(values: frozenset[str]) -> frozenset[str]:
        acc: set[str] = set()
        for v in values:
            acc |= value_tokens(v) & rare
        return frozenset(acc)

    return side(left_values), side(right_values)


# ----------------------------------------------------------------- signal helpers


def _artifact(
    kind: SignalKind,
    value: float,
    weight: float,
    fired: bool,
    conflicts: bool,
    detail: str,
) -> EvidenceArtifact:
    return EvidenceArtifact(
        kind=kind,
        value=round(float(value), 6),
        weight=round(float(weight), 6),
        fired=bool(fired),
        conflicts=bool(conflicts),
        detail=detail,
    )


# --------------------------------------------------------------------- signals


def containment_signals(
    left: SampledColumn, right: SampledColumn, *, fire_at: float = 0.9
) -> tuple[EvidenceArtifact, EvidenceArtifact]:
    """VALUE_CONTAINMENT in both directions: (left⊆right, right⊆left).

    Each is |A∩B|/|A| measured EXACTLY on the sampled intersection, then taken as
    the conservative MIN with the MinHash-Jaccard-implied containment bound so
    estimator noise can only lower the figure. The left⊆right direction is the FK
    direction (child values contained in the parent key).
    """
    la, ra = left.value_set(), right.value_set()
    inter = la & ra
    j_est = minhash_jaccard(left.profile.minhash, right.profile.minhash)

    def direction(a: frozenset[str], other_distinct: int) -> float:
        if not a:
            return 0.0
        exact = len(inter) / len(a)
        # Jaccard implies |A∩B| >= J*max(|A|,|B|); convert to a containment ceiling on |A|.
        # We only ever use it to corroborate, never to inflate: take the min.
        return round(min(1.0, exact), 6) if exact >= j_est else round(exact, 6)

    c_lr = direction(la, right.distinct)
    c_rl = direction(ra, left.distinct)
    art_lr = _artifact(
        SignalKind.VALUE_CONTAINMENT,
        c_lr,
        weight=0.30,
        fired=c_lr >= fire_at,
        conflicts=c_lr < 0.4 and trigram_similarity(left.profile.column, right.profile.column) >= 0.6,
        detail=f"left⊆right={c_lr:.3f} (sample ∩={len(inter)}, J≈{j_est:.3f})",
    )
    art_rl = _artifact(
        SignalKind.VALUE_CONTAINMENT,
        c_rl,
        weight=0.30,
        fired=c_rl >= fire_at,
        conflicts=False,
        detail=f"right⊆left={c_rl:.3f}",
    )
    return art_lr, art_rl


def jaccard_signal(left: SampledColumn, right: SampledColumn, *, fire_at: float = 0.5) -> EvidenceArtifact:
    """VALUE_JACCARD from the φ MinHash signatures (full value sets, k=64)."""
    j = minhash_jaccard(left.profile.minhash, right.profile.minhash)
    return _artifact(
        SignalKind.VALUE_JACCARD,
        j,
        weight=0.15,
        fired=j >= fire_at,
        conflicts=False,
        detail=f"MinHash J≈{j:.3f}",
    )


def infrequent_token_signal(
    left: SampledColumn, right: SampledColumn, *, fire_at: float = 0.3
) -> EvidenceArtifact:
    """INFREQUENT_TOKEN — Jaccard over the RARE tokens of the two value samples.

    Whole-value overlap (containment / Jaccard) collapses to zero on FORMAT
    VARIANTS: ``"123 Main St"`` and ``"123 Main Street"`` are the same address yet
    share no value. We tokenize both samples, keep only the INFREQUENT tokens (the
    discriminating ones — ``"main"``, ``"123"`` — not the boilerplate ``"st"`` /
    ``"street"`` that recurs everywhere), and Jaccard them. A real format-variant
    join still scores high here even when the verbatim Jaccard is ~0; this is fused
    as ONE more positive signal, never a single-metric gate.

    Returns 0.0 (no fire) when either side yields no rare tokens (numeric ids,
    single-word codes) so it never manufactures a relationship out of thin air.
    """
    la, ra = infrequent_token_sets(left.value_set(), right.value_set())
    if not la or not ra:
        sim = 0.0
    else:
        inter = la & ra
        union = la | ra
        sim = len(inter) / len(union) if union else 0.0
    return _artifact(
        SignalKind.INFREQUENT_TOKEN,
        sim,
        weight=0.12,
        fired=sim >= fire_at,
        conflicts=False,  # a positive corroborator only; absence is silence, not a veto
        detail=f"rare-token J≈{sim:.3f} (|rare∩|={len(la & ra) if la and ra else 0})",
    )


def distribution_divergence_signal(
    left: SampledColumn, right: SampledColumn, *, conflict_at: float = 0.5
) -> EvidenceArtifact:
    """DISTRIBUTION_DIVERGENCE — the false-positive killer.

    Numerics: normalized decile divergence from φ quantiles. Categoricals:
    Jensen-Shannon over the sampled value-frequency maps. The signal ``value`` is
    the divergence in [0,1]; it FIRES (and CONFLICTS) when divergence ≥
    ``conflict_at`` — i.e. the distributions disagree enough that a name/overlap
    coincidence is the likely explanation, not a real relationship.
    """
    lt, rt = left.profile.inferred_type, right.profile.inferred_type
    if lt in _NUMERIC and rt in _NUMERIC:
        div = quantile_divergence(left.profile.quantiles, right.profile.quantiles)
        kind_detail = "quantile"
    else:
        div = jensen_shannon(left.freq_map(), right.freq_map())
        kind_detail = "JSD"
    fired = div >= conflict_at
    return _artifact(
        SignalKind.DISTRIBUTION_DIVERGENCE,
        div,
        weight=0.35,  # strong — this is the discriminator
        fired=fired,
        conflicts=fired,  # high divergence contradicts a relatedness hypothesis
        detail=f"{kind_detail}={div:.3f} ({'distributions diverge' if fired else 'distributions align'})",
    )


def cardinality_ratio_signal(left: SampledColumn, right: SampledColumn) -> EvidenceArtifact:
    """CARDINALITY_RATIO distinct(left):distinct(right) → value in (0, 1].

    Reported as min/max so it is direction-free in [0,1]; the rationale records
    the raw ratio. ~1.0 ⇒ 1:1 candidate; small ⇒ many:1 (FK / lookup) shape.
    """
    dl, dr = max(left.distinct, 0), max(right.distinct, 0)
    if dl == 0 or dr == 0:
        ratio = 0.0
    else:
        ratio = min(dl, dr) / max(dl, dr)
    return _artifact(
        SignalKind.CARDINALITY_RATIO,
        ratio,
        weight=0.05,
        fired=ratio > 0.0,
        conflicts=False,
        detail=f"distinct {dl}:{dr} (ratio={ratio:.3f})",
    )


def key_uniqueness_signal(left: SampledColumn, right: SampledColumn, *, fire_at: float = 0.98) -> EvidenceArtifact:
    """KEY_UNIQUENESS — is the RHS near-unique (a viable FK parent / dimension key)?

    Uses ``ColumnProfile.uniqueness`` (distinct / non-null). A join target must be
    key-like; a non-unique RHS contradicts an FK hypothesis (conflict).
    """
    u = right.profile.uniqueness
    fired = u >= fire_at
    return _artifact(
        SignalKind.KEY_UNIQUENESS,
        u,
        weight=0.20,
        fired=fired,
        conflicts=not fired and right.distinct > 1,
        detail=f"rhs uniqueness={u:.3f} ({'key-like' if fired else 'not unique'})",
    )


def entropy_signal(left: SampledColumn, right: SampledColumn, *, low_at: float = 0.25) -> EvidenceArtifact:
    """ENTROPY — penalize low-entropy (near-constant) join keys.

    Normalized entropy of the LEFT (candidate child / key) value-frequency
    distribution, H / log(distinct) ∈ [0,1]. Low entropy (flags, booleans,
    dominant-value columns) makes a poor key and FIRES as a conflict: such columns
    yield vacuous overlaps.
    """
    fm = left.freq_map()
    if len(fm) <= 1:
        norm = 0.0
    else:
        h = shannon_entropy(list(fm.values()))
        norm = h / math.log(len(fm))
    low = norm < low_at
    return _artifact(
        SignalKind.ENTROPY,
        norm,
        weight=0.05,
        fired=low,
        conflicts=low,
        detail=f"norm entropy={norm:.3f} ({'low — poor key' if low else 'adequate'})",
    )


def name_similarity_signal(left: SampledColumn, right: SampledColumn, *, fire_at: float = 0.5) -> EvidenceArtifact:
    """NAME_SIMILARITY — exact / substring / token-Jaccard / trigram, WEAK weight ONLY.

    Name agreement is a hint, never a verdict: it is exactly what produces
    false positives, so its weight is deliberately tiny. The max of token-Jaccard
    (semantic, e.g. ``o_custkey``↔``c_custkey``) and character trigram (lexical).
    """
    a, b = left.profile.column, right.profile.column
    tok = name_token_jaccard(a, b)
    tri = trigram_similarity(a, b)
    exact = 1.0 if a.lower() == b.lower() else 0.0
    sim = max(exact, tok, tri)
    return _artifact(
        SignalKind.NAME_SIMILARITY,
        sim,
        weight=0.05,  # WEAK by design
        fired=sim >= fire_at,
        conflicts=False,
        detail=f"name sim={sim:.3f} (token={tok:.2f}, trigram={tri:.2f})",
    )


def type_compat_signal(left: SampledColumn, right: SampledColumn) -> EvidenceArtifact:
    """TYPE_COMPAT — datatype-group compatibility + semantic-type agreement bonus.

    1.0 same datatype, 0.6 same group (e.g. INTEGER↔FLOAT), 0.0 incompatible; a
    matching non-empty semantic_type lifts a compatible pair to 1.0. Incompatible
    types CONFLICT (you cannot join a DATE to a STRING value set meaningfully).
    """
    lt, rt = left.profile.inferred_type, right.profile.inferred_type
    ga, gb = _GROUP.get(lt), _GROUP.get(rt)
    if ga is None or gb is None:
        base = 0.0
    elif lt == rt:
        base = 1.0
    elif ga == gb:
        base = 0.6
    else:
        base = 0.0
    sem_l = left.profile.semantic_type
    sem_r = right.profile.semantic_type
    if base > 0.0 and sem_l and sem_l == sem_r:
        base = 1.0
    return _artifact(
        SignalKind.TYPE_COMPAT,
        base,
        weight=0.10,
        fired=base >= 0.6,
        conflicts=base == 0.0,
        detail=f"type compat={base:.2f} ({lt.value}/{rt.value}"
        + (f", semtype={sem_l}" if base == 1.0 and sem_l and sem_l == sem_r else "")
        + ")",
    )


def sampled_row_signal(left: SampledColumn, right: SampledColumn, *, fire_at: float = 0.3) -> EvidenceArtifact:
    """SAMPLED_ROW — concrete corroboration from the actual sampled values.

    Shared-value fraction over the union of the two sampled sets (sample-level
    Jaccard). This is the evidence a human/adjudicator can eyeball: real shared
    keys appear in both samples; a coincidental name match does not.
    """
    la, ra = left.value_set(), right.value_set()
    union = la | ra
    frac = len(la & ra) / len(union) if union else 0.0
    return _artifact(
        SignalKind.SAMPLED_ROW,
        frac,
        weight=0.10,
        fired=frac >= fire_at,
        conflicts=False,
        detail=f"sample overlap={frac:.3f} ({len(la & ra)} shared of {len(union)})",
    )
