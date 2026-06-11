"""Spine-gated concept admission + T2 naming (whitepaper §3.4.2 item 3, §3.4.3).

Every surviving concept is ONE spine decision of kind ADMIT (AMD-0005) with
candidates ("merge", "admit", "discard"):

- **T0 rule** (registered on the spine): a concept whose distinguishing intent
  vs its admitted ancestors carries no full property — singleton/empty
  effective intent — is merged into its parent, unless it is the object
  concept of a generated candidate (those are the evidence-bearing types).
- **T1** runs the spine's calibrated/heuristic scorer over the structural
  features (support, stability, intent distinctiveness vs parent, extent
  distinctiveness, generator-kind prior).
- **T2/T3** route through the ModelClient task ``spine.adjudicate.admit``; the
  deterministic distilled-judger handler lives here (AMD-0002 keyless tiers).

Hub candidates (G-join) additionally get a *pre-lattice* spine review
(binary discard/admit): a shared reference domain is only posited when its
referring columns agree on meaning (synonym-cluster unity) or share an
identity-like semantic type — this is what discards the junk
"TYPE-ACFT ⊆ NO-SEATS"-style numeric-coincidence hubs while keeping the real
shared state/airport domains (§3.4 failure-mode (b)).

Naming/defining is a T2 ModelClient task ``strata.name_concept``; the
HeuristicAdapter handler derives a deterministic readable name from the
dominant candidate's name hint plus distinguishing attributes. Names are
memoized on the concept's intent hash via the ledger (artifact kind
``strata.name_memo``), so re-induction reuses names and renames can only
happen through TEMPER (§3.4 failure-mode (c) mitigation).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Optional, Sequence

from ontoforge.contracts import (
    DecisionKind,
    DecisionRequest,
    DecisionResult,
    ModelRequest,
    Spine,
    TierScore,
    leaf,
    make_cell_atom,
    prov_sum,
)
from ontoforge.ledger import HeuristicAdapter

from ._norm import camel
from .candidates import TypeCandidate
from .context import FormalContext, attribute_weight
from .lattice import Concept, ConceptLattice

__all__ = [
    "ADMIT_CANDIDATES",
    "AdmissionEngine",
    "AdmissionResult",
    "AdmittedConcept",
    "NameMemo",
    "build_strata_client",
    "name_concept_handler",
    "admit_adjudication_handler",
    "register_admit_rules",
    "review_hub_candidates",
]

ADMIT_CANDIDATES = ("merge", "admit", "discard")
HUB_REVIEW_CANDIDATES = ("discard", "admit")   # binary: candidates[1] is positive

#: T2 distilled-judger thresholds (deterministic rules; AMD-0002)
HUB_UNITY_FLOOR = 0.8         # fraction of hub columns in one synonym cluster
HUB_SEMTYPE_FLOOR = 0.5       # fraction sharing a non-generic semantic type
ADMIT_SCORE_BAR = 0.22        # structural-quality floor for admit-vs-merge
T2_BASE_CONFIDENCE = 0.93     # the deterministic judger's calibrated band

NAME_TASK = "strata.name_concept"
ADJUDICATE_TASK = "spine.adjudicate.admit"

_NAME_SCHEMA = json.dumps(
    {
        "type": "object",
        "properties": {"name": {"type": "string"}, "definition": {"type": "string"}},
        "required": ["name", "definition"],
    },
    sort_keys=True,
)


# ---------------------------------------------------------------------------
# feature computation
# ---------------------------------------------------------------------------


def _weighted(attrs: frozenset[str]) -> float:
    return sum(attribute_weight(a) for a in attrs)


def _n_props(attrs: frozenset[str]) -> int:
    return sum(1 for a in attrs if a.startswith("has-prop:"))


def concept_features(
    concept: Concept,
    ctx: FormalContext,
    admitted_ancestor_intent: frozenset[str],
    parent_support: int,
    n_objects: int,
) -> dict[str, float]:
    """§3.4.3 admission features. ``admitted_ancestor_intent`` is the union of
    intents of already-admitted ancestors (top-down processing order)."""
    distinguishing = concept.intent - admitted_ancestor_intent
    n_props = _n_props(distinguishing)
    protected = any(ctx.objects[g] == concept.intent for g in concept.extent)
    cands = [ctx.candidates[g] for g in concept.extent if g in ctx.candidates]
    gen_prior = max((c.generator_prior for c in cands), default=0.5)
    is_hub_object = 1.0 if (
        protected
        and any(c.kind == "g-join" and ctx.objects[c.cid] == concept.intent for c in cands)
    ) else 0.0
    return {
        "support": float(concept.support),
        "support_n": concept.support / max(1, n_objects),
        "stability": concept.stability,
        "intent_distinct_w": _weighted(distinguishing),
        "n_distinguishing_props": float(n_props),
        "extent_distinct": 1.0 - concept.support / max(1, parent_support),
        "gen_prior": gen_prior,
        "protected": 1.0 if protected else 0.0,
        "is_hub_object": is_hub_object,
        "weak_intent": 1.0 if (n_props < 1 and not protected) else 0.0,
    }


# ---------------------------------------------------------------------------
# T0 rule (singleton-intent / zero-distinctiveness -> merge-into-parent)
# ---------------------------------------------------------------------------


def t0_weak_concept_rule(req: DecisionRequest) -> Optional[TierScore]:
    """Deterministic T0: concepts whose distinguishing intent has no full
    property (and which are not object concepts of generated candidates)
    merge into their parent. §3.4.2 item 3 first gate."""
    if req.kind is not DecisionKind.ADMIT:
        return None
    f = dict(req.features)
    if f.get("phase_hub_review"):
        return None
    if f.get("weak_intent", 0.0) >= 1.0 or f.get("intent_distinct_w", 1.0) <= 0.0:
        return TierScore(scores={"merge": 1.0})
    return None


def register_admit_rules(spine: Any) -> None:
    """Attach STRATA's T0 rules to a DecisionSpine (idempotent per instance)."""
    registered = getattr(spine, "_strata_rules_registered", False)
    if not registered:
        spine.register_rule(DecisionKind.ADMIT, t0_weak_concept_rule)
        try:
            spine._strata_rules_registered = True  # noqa: SLF001 - marker only
        except AttributeError:
            pass


# ---------------------------------------------------------------------------
# T2/T3 deterministic distilled judger (ModelClient handler)
# ---------------------------------------------------------------------------


def _payload_from_prompt(prompt: str) -> dict[str, Any]:
    """Extract the JSON payload that spine.adjudicator.build_prompt embeds."""
    cut = prompt.find("\nRespond with JSON only")
    body = prompt[:cut] if cut >= 0 else prompt
    start = body.find("{")
    if start < 0:
        return {}
    try:
        obj = json.loads(body[start:])
        return obj if isinstance(obj, dict) else {}
    except ValueError:
        return {}


def admit_adjudication_handler(req: ModelRequest) -> dict[str, Any]:
    """Deterministic distilled judger for escalated ADMIT decisions.

    Hub review: admit a posited shared-reference domain only when its
    referring columns agree (cluster unity >= 0.8) or share an identity-like
    semantic type — otherwise the "domain" is a value-range coincidence.

    Concept admission: structural-quality score over support, stability,
    intent distinctiveness, and generator prior; below the bar the concept
    folds into its parent (merge). Confidence sits in the calibrated
    T2 band (>= tau_high) so deterministic tiers conclude decisively.
    """
    payload = _payload_from_prompt(req.prompt)
    f = {k: float(v) for k, v in (payload.get("features") or {}).items()}
    context = payload.get("context") or {}
    phase = context.get("phase", "concept-admission")

    if phase == "hub-review" or f.get("phase_hub_review"):
        unity = f.get("hub_unity", 0.0)
        sem = f.get("hub_semtype", 0.0)
        ok = unity >= HUB_UNITY_FLOOR or sem >= HUB_SEMTYPE_FLOOR
        return {
            "choice": "admit" if ok else "discard",
            "confidence": 0.96 if ok else 0.95,
        }

    score = (
        0.30 * f.get("support_n", 0.0)
        + 0.25 * f.get("stability", 0.0)
        + 0.35 * min(1.0, f.get("intent_distinct_w", 0.0) / 3.0)
        + 0.10 * f.get("gen_prior", 0.5)
    )
    # A non-object concept distinguished by a single shared property is a weak
    # grouping (e.g. "everything carrying a tail number"): fold into parent.
    decisive = f.get("n_distinguishing_props", 0.0) >= 2.0 or f.get("protected", 0.0) >= 1.0
    choice = "admit" if (decisive and score >= ADMIT_SCORE_BAR) else "merge"
    confidence = min(0.99, T2_BASE_CONFIDENCE + 0.06 * min(1.0, abs(score - ADMIT_SCORE_BAR) * 4.0))
    return {"choice": choice, "confidence": confidence}


# ---------------------------------------------------------------------------
# naming handler (task strata.name_concept)
# ---------------------------------------------------------------------------


def name_concept_handler(req: ModelRequest) -> dict[str, str]:
    """Deterministic readable naming from the dominant candidate name hint +
    distinguishing attributes ('AircraftReference', 'Registrant',
    'NarrativeEvent', ...). Pure function of the concept payload."""
    try:
        payload = json.loads(req.prompt)
    except ValueError:
        payload = {}
    hint = payload.get("object_hint") or ""
    distinguishing = [str(p) for p in payload.get("distinguishing_props", [])]
    event_like = bool(payload.get("event_like"))
    tables = [str(t) for t in payload.get("tables", [])]

    if hint:
        name = camel(hint)
    else:
        parts = [camel(p, singular_last=False) for p in distinguishing[:2]]
        name = "".join(parts) or "Concept"
        if event_like and not name.endswith("Event"):
            name += "Event"
    if not name:
        name = "Concept"
    definition = (
        f"Induced type over {', '.join(tables) if tables else 'shared evidence'}; "
        f"distinguished by {', '.join(distinguishing[:4]) if distinguishing else 'its full profile'}."
    )
    return {"name": name, "definition": definition}


def build_strata_client(extra: Optional[Mapping[str, Callable[[ModelRequest], Any]]] = None) -> HeuristicAdapter:
    """The deterministic ModelClient for STRATA's T2/T3 tasks (AMD-0002)."""
    handlers: dict[str, Callable[[ModelRequest], Any]] = {
        NAME_TASK: name_concept_handler,
        ADJUDICATE_TASK: admit_adjudication_handler,
    }
    if extra:
        handlers.update(extra)
    return HeuristicAdapter(handlers)


# ---------------------------------------------------------------------------
# provenance + name memo
# ---------------------------------------------------------------------------


def register_evidence_atoms(
    ledger: Any,
    cands: Sequence[TypeCandidate],
    profiles_by_table: Mapping[str, Any],
) -> list[str]:
    """Register profile-sketch atoms for the member columns of ``cands``.

    The provenance of an induced class is the set of profile sketches it was
    induced from: atom uri atom://{source}/{table}/__profile__#{column} with
    the sketch key as content — re-profiling identical data dedups to the
    same atoms, changed data supersedes them (exact invalidation, §9)."""
    if ledger is None:
        return []
    atoms = []
    seen: set[tuple[str, str]] = set()
    for cand in cands:
        for table, column in cand.member_columns:
            if (table, column) in seen:
                continue
            seen.add((table, column))
            tp = profiles_by_table.get(table)
            if tp is None or column not in tp.columns:
                continue
            cp = tp.columns[column]
            atoms.append(
                make_cell_atom(cp.source_id, table, "__profile__", column, cp.sketch_key())
            )
    return ledger.register_atoms(atoms) if atoms else []


class NameMemo:
    """intent_hash -> (name, definition), memoized via the ledger artifact
    table (kind ``strata.name_memo``) when a SqliteLedger-style ledger with a
    readable ``connection`` is available; in-memory otherwise."""

    def __init__(self, ledger: Any = None) -> None:
        self._ledger = ledger
        self._mem: dict[str, tuple[str, str]] = {}

    @staticmethod
    def _artifact_id(intent_hash: str) -> str:
        return f"strata:name:{intent_hash}"

    def get(self, intent_hash: str) -> Optional[tuple[str, str]]:
        if intent_hash in self._mem:
            return self._mem[intent_hash]
        conn = getattr(self._ledger, "connection", None)
        if conn is None:
            return None
        row = conn.execute(
            "SELECT payload FROM artifact WHERE artifact_id = ? ORDER BY created_at LIMIT 1",
            (self._artifact_id(intent_hash),),
        ).fetchone()
        if row is None:
            return None
        obj = json.loads(row[0])
        got = (str(obj["name"]), str(obj.get("definition", "")))
        self._mem[intent_hash] = got
        return got

    def put(self, intent_hash: str, name: str, definition: str, prov_atom_ids: Sequence[str]) -> None:
        self._mem[intent_hash] = (name, definition)
        if self._ledger is None or getattr(self._ledger, "connection", None) is None:
            return
        if not prov_atom_ids:
            return
        term = prov_sum([leaf(a) for a in prov_atom_ids])
        prov_ref = self._ledger.intern(term)
        self._ledger.append_artifact(
            self._artifact_id(intent_hash),
            "strata.name_memo",
            json.dumps({"name": name, "definition": definition}, sort_keys=True),
            prov_ref,
        )


# ---------------------------------------------------------------------------
# hub pre-review (§3.4 failure-mode (b))
# ---------------------------------------------------------------------------


def hub_review_features(cand: TypeCandidate, ctx_clusters: Any, profiles_by_table: Mapping[str, Any]) -> dict[str, float]:
    members = cand.member_columns
    canons = [ctx_clusters.canonical_of(t, c) for t, c in members]
    unity = max(
        (canons.count(c) for c in set(canons)), default=0
    ) / max(1, len(members))
    semtypes = []
    distincts = []
    for t, c in members:
        tp = profiles_by_table.get(t)
        if tp is None or c not in tp.columns:
            continue
        cp = tp.columns[c]
        distincts.append(cp.distinct_estimate)
        if cp.semantic_type and cp.semantic_confidence >= 0.8:
            semtypes.append(cp.semantic_type)
    sem_frac = 0.0
    if semtypes:
        modal = max(set(semtypes), key=lambda s: (semtypes.count(s), s))
        sem_frac = semtypes.count(modal) / max(1, len(members))
    domain_distinct = max(distincts, default=0)
    return {
        "phase_hub_review": 1.0,
        "hub_unity": round(unity, 4),
        "hub_semtype": round(sem_frac, 4),
        "hub_domain_distinct_n": min(1.0, domain_distinct / 50.0),
        "hub_coverage": cand.confidence,
    }


def review_hub_candidates(
    spine: Spine,
    candidates: Sequence[TypeCandidate],
    clusters: Any,
    profiles_by_table: Mapping[str, Any],
    ledger: Any = None,
) -> tuple[list[TypeCandidate], dict[str, DecisionResult]]:
    """Spine review of G-join hub candidates BEFORE the lattice: discarded
    hubs never enter G. Returns (surviving candidates, review decisions)."""
    surviving: list[TypeCandidate] = []
    reviews: dict[str, DecisionResult] = {}
    for cand in sorted(candidates, key=lambda c: c.cid):
        if cand.kind != "g-join":
            surviving.append(cand)
            continue
        feats = hub_review_features(cand, clusters, profiles_by_table)
        prov_atoms = register_evidence_atoms(ledger, [cand], profiles_by_table)
        req = DecisionRequest(
            kind=DecisionKind.ADMIT,
            decision_id=f"strata:hub:{cand.cid}",
            candidates=HUB_REVIEW_CANDIDATES,
            features=tuple(sorted(feats.items())),
            context=(
                ("phase", "hub-review"),
                ("cid", cand.cid),
                ("domain", cand.name_hint),
                ("tables", ",".join(cand.evidence_tables)),
            ),
            prov_atoms=tuple(prov_atoms),
        )
        result = spine.decide(req)
        reviews[cand.cid] = result
        if result.outcome == "admit":
            surviving.append(cand)
    return sorted(surviving, key=lambda c: c.cid), reviews


# ---------------------------------------------------------------------------
# the admission engine
# ---------------------------------------------------------------------------


@dataclass
class AdmittedConcept:
    concept: Concept
    decision: DecisionResult
    name: str = ""
    definition: str = ""


@dataclass
class AdmissionResult:
    admitted: dict[str, AdmittedConcept] = field(default_factory=dict)
    merged: dict[str, Optional[str]] = field(default_factory=dict)     # hash -> target hash
    discarded: dict[str, str] = field(default_factory=dict)            # hash -> rationale
    decisions: dict[str, DecisionResult] = field(default_factory=dict)

    def outcome_of(self, intent_hash: str) -> str:
        if intent_hash in self.admitted:
            return "admit"
        if intent_hash in self.merged:
            return "merge"
        if intent_hash in self.discarded:
            return "discard"
        return "unknown"


class AdmissionEngine:
    """Routes every lattice concept through the spine (§3.4.2 item 3).

    Deterministic and memoized: identical (intent, features) pairs reuse the
    cached DecisionResult, so incremental re-admission only spends spine
    decisions on concepts whose evidence actually changed."""

    def __init__(self, spine: Spine, ledger: Any = None, profiles_by_table: Optional[Mapping[str, Any]] = None) -> None:
        self.spine = spine
        self.ledger = ledger
        self.profiles_by_table = dict(profiles_by_table or {})
        self._cache: dict[tuple[str, str], DecisionResult] = {}
        register_admit_rules(spine)

    # -- single concept (the §11.2 admit() interface) -------------------------

    def admit(
        self,
        concept: Concept,
        ctx: FormalContext,
        admitted_ancestor_intent: frozenset[str] = frozenset(),
        parent_support: int = 0,
    ) -> DecisionResult:
        n_objects = len(ctx.objects)
        feats = concept_features(
            concept, ctx, admitted_ancestor_intent,
            parent_support or n_objects, n_objects,
        )
        digest = json.dumps(sorted(feats.items()), sort_keys=True)
        key = (concept.intent_hash, digest)
        if key in self._cache:
            return self._cache[key]
        cands = [ctx.candidates[g] for g in concept.extent if g in ctx.candidates]
        prov_atoms = register_evidence_atoms(self.ledger, cands, self.profiles_by_table)
        req = DecisionRequest(
            kind=DecisionKind.ADMIT,
            decision_id=f"strata:admit:{concept.intent_hash}",
            candidates=ADMIT_CANDIDATES,
            features=tuple(sorted(feats.items())),
            context=(
                ("phase", "concept-admission"),
                ("intent_hash", concept.intent_hash),
                ("extent", ",".join(sorted(concept.extent))),
                ("n_intent_attrs", len(concept.intent)),
            ),
            prov_atoms=tuple(prov_atoms),
        )
        result = self.spine.decide(req)
        self._cache[key] = result
        return result

    # -- full lattice pass -----------------------------------------------------

    def process(self, lattice: ConceptLattice, ctx: FormalContext) -> AdmissionResult:
        """Top-down admission over the iceberg lattice. The structural root
        (extent = G) is skipped when it carries no distinguishing property —
        it is the trivial ⊤, not a class."""
        res = AdmissionResult()
        admitted_intents: dict[str, frozenset[str]] = {}
        all_objects = ctx.all_objects

        for concept in lattice.top_down():
            ih = concept.intent_hash
            anc_hashes = [h for h in lattice.ancestors(ih) if h in res.admitted]
            anc_intent: frozenset[str] = frozenset()
            for h in anc_hashes:
                anc_intent |= admitted_intents[h]
            parent_support = min(
                (res.admitted[h].concept.support for h in anc_hashes),
                default=len(all_objects),
            )
            # structural root: extent == G and nothing property-like to say
            if concept.extent == all_objects and _n_props(concept.intent) < 1:
                res.discarded[ih] = "structural-root: extent = G with no shared property"
                continue

            decision = self.admit(concept, ctx, anc_intent, parent_support)
            res.decisions[ih] = decision
            if decision.outcome == "admit":
                res.admitted[ih] = AdmittedConcept(concept=concept, decision=decision)
                admitted_intents[ih] = concept.intent
            elif decision.outcome == "merge":
                target = self._nearest_admitted(concept, lattice, res)
                res.merged[ih] = target
                if target is None:
                    res.discarded[ih] = (
                        f"merge-with-no-admitted-parent (decision {decision.rationale!r})"
                    )
            else:
                res.discarded[ih] = decision.rationale or "discarded by spine"
        return res

    @staticmethod
    def _nearest_admitted(
        concept: Concept, lattice: ConceptLattice, res: AdmissionResult
    ) -> Optional[str]:
        """Most specific admitted ancestor (fold-into-parent target)."""
        best: Optional[str] = None
        best_support = None
        for h in lattice.ancestors(concept.intent_hash):
            if h in res.admitted:
                s = res.admitted[h].concept.support
                if best_support is None or (s, h) < (best_support, best):
                    best, best_support = h, s
        return best
