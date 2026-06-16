"""RelationshipGate — typed-relationship plurality voting over reasoning paths.

v2.1 build instructions §1.3 / §1.4. This is the typed counterpart to the fire/hold
:class:`~ontoforge.ensemble.gate.Gate`: instead of "should this action fire?", it
answers "WHAT TYPE of relationship is this — and is the system confident enough to
COMMIT it, or must it route to a human?".

How it decides (:meth:`RelationshipGate.decide`):

1. **Plurality vote on the TYPE.** The three :mod:`paths` each cast a
   :class:`~ontoforge.contracts.PathVote` for a
   :class:`~ontoforge.contracts.RelationshipType`. The plurality type wins.
   ``UNKNOWN`` votes abstain from setting the type (a path that sees no signal does
   not get to veto by voting "unknown") but still count against consensus.

2. **Confidence = MEDIAN of path scores.** Not mean — the median is robust to one
   path being wildly over/under-confident, which is the whole point of running
   distinct reasoning rather than correlated temperature samples.

3. **SQL backward-validation as a strong BOOSTER / VETO** (§1.4). If a
   :class:`~ontoforge.contracts.JoinValidation` is supplied: a validation whose
   ``verdict`` matches the plurality type and is ``ok`` BOOSTS confidence toward 1;
   a validation that CONTRADICTS the leaning type (different verdict, or not ``ok``
   with a poor match rate) VETOES the commit — the data refusing the join overrides
   the paths, exactly as the fire/hold gate's verifier does. Execution-grounded
   evidence is the strongest correctness guarantee in the system.

4. **Commit only above consensus.** Commit requires ALL of: the paths agree (a
   strict-majority plurality, no tie) AND the median confidence ≥ ``threshold`` AND
   validation (if present) does not contradict. Otherwise ``routed_to_human=True``
   — sent to the Build layer for adjudication. Records every vote + rationale for
   provenance.

**Voting is a SCALPEL, not a default** (§1.3 / §1.4 cost discipline).
:func:`should_vote` returns ``True`` only for the AMBIGUOUS band — a candidate
whose heuristic proxy confidence sits in the uncertain region, or that is flagged
``needs_adjudication``, or whose type is ``UNKNOWN``. A confident FK candidate
(high proxy confidence, decisive type) SKIPS voting entirely — the multi-path vote
(and the LLM calls it will become) is reserved for the cases that actually need it.

Each path is a seam for a later LLM call (different prompt per path, via the
``aimodels`` router); the gate math here is unchanged when that swap happens.

CLOSED-CORE IP — proprietary per OntoForge_Build_Instructions.md §18.
"""

from __future__ import annotations

import statistics
from typing import Any, Optional, Sequence

from ..contracts import (
    JoinValidation,
    PathVote,
    RelationshipCandidate,
    RelationshipType,
    RelationshipVerdict,
)
from .paths import PathExpert, default_paths

__all__ = [
    "AMBIGUOUS_BAND",
    "CONSENSUS_THRESHOLD",
    "TOP_K_COMMIT",
    "TOP_CONFIDENCE_RATIO",
    "RelationshipGate",
    "should_vote",
]

#: median-of-path confidence a plurality type must clear to be committed.
CONSENSUS_THRESHOLD = 0.6

#: precision-over-recall commit calibration (RESEARCH_ENGINE_SOTA §3, Tursio
#: "top-3 candidates within 0.9 confidence"). A typed relationship may COMMIT only
#: when it is among the top-:data:`TOP_K_COMMIT` candidates for its slot AND no
#: COMPETING candidate sits within :data:`TOP_CONFIDENCE_RATIO` of the leader's
#: confidence — a near-tie 2nd candidate is ambiguous and routes to a human. This
#: trades recall for precision: a wrong committed join corrupts downstream SQL.
TOP_K_COMMIT = 3
TOP_CONFIDENCE_RATIO = 0.9

#: the uncertain heuristic-proxy band in which a candidate is worth voting on.
#: Below ``lo`` the proxy is confidently-low (skip — it's a clear non/weak link);
#: above ``hi`` the proxy is confidently-high (skip — commit the heuristic).
#: Inside [lo, hi) the proxy is genuinely unsure ⇒ spend a vote.
AMBIGUOUS_BAND: tuple[float, float] = (0.45, 0.85)


# --------------------------------------------------------------------------
# the scalpel — should we even vote?
# --------------------------------------------------------------------------


def should_vote(
    cand: RelationshipCandidate,
    band: tuple[float, float] = AMBIGUOUS_BAND,
) -> bool:
    """Cost-discipline guard: vote ONLY on ambiguous / borderline / conflicting
    candidates. Returns ``True`` when the candidate is worth the multi-path vote
    (and, later, the per-path LLM calls); ``False`` when the heuristic proxy is
    already decisive and voting would just burn cost.

    Fires when ANY of:
      * the candidate is explicitly flagged ``needs_adjudication``;
      * its proxy ``rel_type`` is ``UNKNOWN`` (undecided ⇒ needs adjudication);
      * its proxy ``confidence`` sits inside the ambiguous band ``[lo, hi)``;
      * its evidence trail contains a CONFLICTING signal (a fired signal that
        contradicts the leading hypothesis) — mixed evidence is borderline by
        definition even if the proxy number looks fine.

    A confident, decisively-typed FK (e.g. proxy 0.95, ``FK_JOIN``, no conflicts)
    returns ``False`` and skips voting entirely.
    """
    lo, hi = band
    if cand.needs_adjudication:
        return True
    if cand.rel_type == RelationshipType.UNKNOWN:
        return True
    if any(ev.conflicts for ev in cand.evidence):
        return True
    return lo <= cand.confidence < hi


# --------------------------------------------------------------------------
# the gate
# --------------------------------------------------------------------------


class RelationshipGate:
    """Plurality typed-relationship gate over distinct reasoning paths.

    Construct with the paths (defaults to schema/value/business-logic);
    :meth:`decide` over a candidate (+ optional backward validation) yields a
    :class:`~ontoforge.contracts.RelationshipVerdict` — committed or routed.
    Deterministic in (paths, candidate, validation)."""

    def __init__(
        self,
        paths: Optional[Sequence[PathExpert]] = None,
        *,
        threshold: float = CONSENSUS_THRESHOLD,
        validation_boost: float = 0.25,
        min_match_rate: float = 0.5,
    ) -> None:
        self.paths: list[PathExpert] = list(paths) if paths is not None else default_paths()
        if not self.paths:
            raise ValueError("RelationshipGate needs at least one reasoning path")
        names = [p.path for p in self.paths]
        if len(set(names)) != len(names):
            raise ValueError(f"reasoning paths must be distinct, got {names}")
        self.threshold = threshold
        self.validation_boost = validation_boost
        self.min_match_rate = min_match_rate

    # ----------------------------------------------------------------- vote

    def _collect_votes(
        self,
        cand: RelationshipCandidate,
        validation: Optional[JoinValidation],
    ) -> list[PathVote]:
        return [p.vote(cand, validation) for p in self.paths]

    @staticmethod
    def _plurality(votes: Sequence[PathVote]) -> tuple[RelationshipType, int, bool]:
        """Plurality type over the votes, excluding ``UNKNOWN`` abstentions.

        Returns (winning_type, votes_for_winner, strict). ``strict`` is True when
        the winner has a clear plurality (no tie for the top spot) among the
        non-abstaining votes. With all-UNKNOWN votes the winner is UNKNOWN."""
        tally: dict[RelationshipType, float] = {}
        for v in votes:
            if v.rel_type == RelationshipType.UNKNOWN:
                continue
            # weight the tally by the path's confidence so a barely-held vote does
            # not outrank a strongly-held one in a tie; counts still drive plurality.
            tally[v.rel_type] = tally.get(v.rel_type, 0.0) + 1.0 + 1e-3 * v.confidence
        if not tally:
            return RelationshipType.UNKNOWN, 0, False
        ranked = sorted(tally.items(), key=lambda kv: kv[1], reverse=True)
        winner, top = ranked[0]
        strict = len(ranked) == 1 or ranked[0][1] > ranked[1][1] + 1e-9
        n_for = sum(1 for v in votes if v.rel_type == winner)
        return winner, n_for, strict

    def decide(
        self,
        cand: RelationshipCandidate,
        validation: Optional[JoinValidation] = None,
    ) -> RelationshipVerdict:
        """Decide the relationship type and whether to commit or route.

        Plurality on type · median-of-path confidence · SQL-validation
        booster/veto · commit only on consensus (agreement AND median≥threshold AND
        validation not contradicting), else ``routed_to_human``. Deterministic."""
        votes = self._collect_votes(cand, validation)
        rel_type, n_for, strict = self._plurality(votes)

        # confidence = MEDIAN of path scores (robust to one over/under-confident path).
        scores = [v.confidence for v in votes]
        median_conf = float(statistics.median(scores)) if scores else 0.0

        # ----- SQL backward-validation: strong booster / veto (§1.4) -----
        validation_contradicts = False
        confidence = median_conf
        if validation is not None:
            agrees = validation.verdict == rel_type and rel_type != RelationshipType.UNKNOWN
            poor = (not validation.ok) or validation.match_rate < self.min_match_rate
            if agrees and validation.ok:
                # execution-grounded corroboration ⇒ boost toward 1.
                confidence = min(1.0, median_conf + self.validation_boost * validation.match_rate)
            elif validation.verdict != rel_type and validation.ok:
                # the executed join typed it DIFFERENTLY and that typing is sound:
                # a contradiction the paths must defer to.
                validation_contradicts = True
            elif poor and rel_type in _JOINING_TYPES:
                # paths lean toward a join but the data refuses it (low match /
                # not ok). The data overrides — veto the commit.
                validation_contradicts = True

        # ----- consensus decision -----
        agree = strict and rel_type != RelationshipType.UNKNOWN
        consensus = bool(
            agree
            and confidence >= self.threshold
            and not validation_contradicts
        )
        committed = consensus
        routed_to_human = not consensus

        return RelationshipVerdict(
            left=cand.left,
            right=cand.right,
            rel_type=rel_type,
            confidence=round(confidence, 6),
            consensus=consensus,
            votes=tuple(votes),
            validation=validation,
            committed=committed,
            routed_to_human=routed_to_human,
            prov_ref="",
        )

    # ------------------------------------- top-3-within-0.9 commit calibration

    def calibrate_commits(
        self,
        candidates: Sequence[RelationshipCandidate],
        validations: Optional[Sequence[Optional[JoinValidation]]] = None,
        *,
        top_k: int = TOP_K_COMMIT,
        confidence_ratio: float = TOP_CONFIDENCE_RATIO,
    ) -> list[RelationshipVerdict]:
        """Precision-over-recall commit/abstain over a FIELD of competing candidates.

        ``candidates`` are the rival hypotheses for ONE slot (e.g. the possible
        parents a single FK child could point into). The Tursio calibration
        (RESEARCH_ENGINE_SOTA §3): a candidate COMMITS only when ALL hold —

          1. it is among the TOP-``top_k`` candidates by confidence;
          2. it is within ``confidence_ratio`` of the TOP confidence (the leader
             trivially is; this gates the rest);
          3. its own gate :meth:`decide` reaches consensus (paths agree, median ≥
             threshold, validation does not contradict);
          4. NO OTHER candidate sits within ``confidence_ratio`` of the leader —
             a near-tie means the field is AMBIGUOUS, so even the leader abstains
             and routes to a human (a borderline 2nd candidate within 0.9 ⇒ route).

        Otherwise the candidate is ``routed_to_human``. Returns one
        :class:`RelationshipVerdict` per input candidate, in INPUT order (the
        per-candidate ``decide`` is preserved; only the commit/route flags are
        re-derived under the field-level rule). Deterministic in the inputs.
        """
        cands = list(candidates)
        n = len(cands)
        if validations is None:
            vals: list[Optional[JoinValidation]] = [None] * n
        else:
            vals = list(validations)
            if len(vals) != n:
                raise ValueError("validations must align 1:1 with candidates")
        if n == 0:
            return []

        # base per-candidate verdicts (unchanged decide math)
        base = [self.decide(c, v) for c, v in zip(cands, vals)]

        # rank by confidence (desc), stable tie-break on column address, to pick the
        # top-k field and detect near-ties deterministically.
        order = sorted(
            range(n),
            key=lambda i: (
                -cands[i].confidence,
                cands[i].left.table, cands[i].left.column,
                cands[i].right.table, cands[i].right.column,
            ),
        )
        top_conf = cands[order[0]].confidence
        cutoff = confidence_ratio * top_conf
        top_set = set(order[:top_k])
        # how many candidates are "within ratio of the top" — >1 means a near-tie.
        contenders = [i for i in range(n) if cands[i].confidence >= cutoff]
        ambiguous_field = len(contenders) > 1

        out: list[RelationshipVerdict] = []
        for i in range(n):
            v = base[i]
            in_top_k = i in top_set
            within_ratio = cands[i].confidence >= cutoff
            commit = bool(
                v.consensus
                and in_top_k
                and within_ratio
                and not ambiguous_field
            )
            out.append(
                RelationshipVerdict(
                    left=v.left,
                    right=v.right,
                    rel_type=v.rel_type,
                    confidence=v.confidence,
                    consensus=v.consensus,
                    votes=v.votes,
                    validation=v.validation,
                    committed=commit,
                    routed_to_human=not commit,
                    prov_ref=v.prov_ref,
                )
            )
        return out

    # ------------------------------------------------------- provenance

    @staticmethod
    def to_provenance(verdict: RelationshipVerdict) -> dict[str, Any]:
        """JSON-serializable provenance payload (votes + rationale + validation)."""
        return {
            "left": {"source_id": verdict.left.source_id, "table": verdict.left.table,
                     "column": verdict.left.column},
            "right": {"source_id": verdict.right.source_id, "table": verdict.right.table,
                      "column": verdict.right.column},
            "rel_type": verdict.rel_type.value,
            "confidence": round(verdict.confidence, 6),
            "consensus": verdict.consensus,
            "committed": verdict.committed,
            "routed_to_human": verdict.routed_to_human,
            "votes": [
                {"path": v.path.value, "rel_type": v.rel_type.value,
                 "confidence": round(v.confidence, 6), "rationale": v.rationale}
                for v in verdict.votes
            ],
            "validation": (
                None if verdict.validation is None else {
                    "verdict": verdict.validation.verdict.value,
                    "ok": verdict.validation.ok,
                    "match_rate": round(verdict.validation.match_rate, 6),
                    "orphan_rate": round(verdict.validation.orphan_rate, 6),
                    "fanout_avg": round(verdict.validation.fanout_avg, 6),
                    "null_key_rate": round(verdict.validation.null_key_rate, 6),
                }
            ),
        }


#: relationship types that imply an executable join — for these a failing
#: backward validation is a veto (the data refusing the join overrides the paths).
_JOINING_TYPES = frozenset({
    RelationshipType.FK_JOIN,
    RelationshipType.LOOKUP_DIMENSION,
    RelationshipType.M2M_BRIDGE,
})
