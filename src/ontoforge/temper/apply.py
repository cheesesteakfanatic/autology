"""M10 — TEMPER application engine.

``TemperEngine.apply(op)``:

1. precondition check (typed; raises PreconditionError, nothing changes);
2. spine gating (§3.6 autonomy integration): SplitClass/MergeClasses over a
   POPULATED extent are DecisionSpine decisions with impact = extent size;
   non-auto-accepted decisions raise OperatorDeferred (human-review queue).
   Low-impact operators (everything else, or empty extents) auto-apply;
3. ontology rewrite -> O^(t+1) (version+1; untouched ClassDef objects are
   SHARED with O^(t), so URI stability and bit-identity are structural);
4. forward migration over HEARTH entity cells via the store's commit path
   (counted: label/axiom-only operators perform ZERO commits);
5. backward-view registration (views.op_rewriter) on the rewriter chain;
6. morphism-ledger append (kind 'temper-op').

``rewrite(query, from_version)`` / ``answer(query, from_version)`` discharge
snapshot-queryability: queries authored against any retained version answer
against the current store through the composed backward views.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from ontoforge.contracts import (
    DecisionKind,
    DecisionRequest,
    Layer,
    Ontology,
    ValueCell,
)

from .morphism import MorphismLedger, MorphismRecord
from .ops import (
    DATA_TOUCHING,
    SPINE_GATED,
    MergeClasses,
    Operator,
    PreconditionError,
    SplitClass,
)
from .views import Plan, RewriterChain, StructuredQuery, execute, op_rewriter


class OperatorDeferred(Exception):
    """A spine-gated operator was not auto-accepted; it is queued for human
    review and NOT applied."""


@dataclass(frozen=True)
class MigrationReport:
    op_type: str
    from_version: int
    to_version: int
    stats: dict[str, Any]                 # cells_written / entities_touched / op-specific
    commits: int                          # Hearth commits performed by this op
    inverse: Optional[Operator]           # inverse operator (None if not invertible)
    gated: bool = False                   # routed through the DecisionSpine
    decision_id: str = ""


class DataAdapter:
    """The engine's read/write surface over a Hearth store. All TEMPER
    migrations go through ``commit_cells`` (Hearth's validated commit path),
    which is also the commit COUNTER the §3.6 cost target is asserted on."""

    def __init__(self, hearth) -> None:
        self.hearth = hearth
        self.commits = 0
        self.cells_committed = 0

    def extent_own(self, class_uri: str) -> dict[str, dict[str, ValueCell]]:
        """entity -> {storage_key: current ValueCell} for one ENTITY shard."""
        shard = self.hearth._shards.get((Layer.ENTITY, class_uri))
        if shard is None:
            return {}
        out: dict[str, dict[str, ValueCell]] = {}
        for (entity, prop), seq in shard.current.items():
            out.setdefault(entity, {})[prop] = shard.cells[seq]
        return out

    def commit_cells(self, class_uri: str, cells: list[ValueCell]) -> None:
        if not cells:
            return
        self.hearth.commit(Layer.ENTITY, class_uri, cells)
        self.commits += 1
        self.cells_committed += len(cells)


class TemperEngine:
    """Owns the live ontology, version snapshots, the rewriter chain, the
    morphism ledger, and (optionally) a Hearth + DecisionSpine."""

    def __init__(self, base: Ontology, hearth=None, spine=None, ledger=None) -> None:
        self.ontology = base.clone()
        self.snapshots: dict[int, Ontology] = {base.version: base.clone()}
        self.chain = RewriterChain()
        self.morphisms = MorphismLedger(ledger)
        self.adapter = DataAdapter(hearth) if hearth is not None else None
        self.spine = spine
        self.base_version = base.version

    # ------------------------------------------------------------- helpers

    @property
    def commit_count(self) -> int:
        return self.adapter.commits if self.adapter is not None else 0

    def _gate_extent(self, op: Operator) -> int:
        if self.adapter is None:
            return 0
        if isinstance(op, SplitClass):
            return len(self.adapter.extent_own(op.uri))
        if isinstance(op, MergeClasses):
            return len(self.adapter.extent_own(op.c1_uri)) + len(self.adapter.extent_own(op.c2_uri))
        return 0

    def propose(self, op: Operator) -> dict[str, Any]:
        """Dry-run: precondition verdict + gating impact, nothing applied."""
        try:
            op.precondition(self.ontology, self.adapter)
            valid, reason = True, ""
        except PreconditionError as exc:
            valid, reason = False, str(exc)
        extent = self._gate_extent(op) if valid else 0
        return {
            "op_type": op.op_type,
            "valid": valid,
            "reason": reason,
            "spine_gated": op.op_type in SPINE_GATED and extent > 0,
            "impact_extent": extent,
            "data_touching": op.op_type in DATA_TOUCHING,
        }

    # --------------------------------------------------------------- apply

    def apply(self, op: Operator, *, now: Optional[int] = None) -> MigrationReport:
        pre = self.ontology
        op.precondition(pre, self.adapter)

        # ---- spine gating (§3.6): high-impact structural ops are decisions
        gated = False
        decision_id = ""
        extent = self._gate_extent(op)
        if op.op_type in SPINE_GATED and extent > 0 and self.spine is not None:
            gated = True
            decision_id = f"temper:{op.op_type}:{pre.version + 1}"
            # Neutral features: with no T0 rule, no calibration, and no model
            # client the spine lands in the escalation band and DEFERS — a
            # high-impact structural change needs a confident signal to pass.
            req = DecisionRequest(
                kind=DecisionKind.SM,
                decision_id=decision_id,
                candidates=("no", "yes"),
                features=(("structural_change", 0.5),),
                context=(("op", op.op_type), ("extent", extent),
                         ("params", str(sorted(op.params().items())))),
                impact=float(extent),
            )
            result = self.spine.decide(req)
            if result.outcome != "yes" or not result.auto_decided:
                raise OperatorDeferred(
                    f"{op.op_type} over extent {extent} not auto-accepted "
                    f"(outcome={result.outcome!r}, tier={result.tier.name}, "
                    f"deferred={result.deferred_to_human}, quarantined={result.quarantined})"
                )

        # ---- ontology rewrite (pure; untouched classes shared)
        post = op.rewrite(pre)
        post.version = pre.version + 1

        # ---- inverse (computed against the pre-state, before data moves)
        inverse = op.invert(pre)

        # ---- forward migration over Hearth cells
        commits_before = self.commit_count
        stats: dict[str, Any] = {}
        if self.adapter is not None:
            stats = op.migrate(pre, post, self.adapter) or {}
        commits = self.commit_count - commits_before

        # ---- backward view + morphism record + state swap
        self.chain.add(pre.version, op_rewriter(op, pre, post))
        rec_stats = {**{k: v for k, v in stats.items() if not callable(v)}, "commits": commits}
        rec_stats = _jsonable(rec_stats)
        self.morphisms.record(op, pre.version, post.version, rec_stats, now=now)
        self.ontology = post
        self.snapshots[post.version] = post.clone()

        return MigrationReport(
            op_type=op.op_type,
            from_version=pre.version,
            to_version=post.version,
            stats=rec_stats,
            commits=commits,
            inverse=inverse,
            gated=gated,
            decision_id=decision_id,
        )

    # -------------------------------------------------------------- queries

    def rewrite(self, query: StructuredQuery, from_version: int) -> Plan:
        """The M10 contract's rewrite(query, from_version) -> query'."""
        return self.chain.rewrite(query, from_version, self.snapshots)

    def answer(self, query: StructuredQuery, from_version: int) -> frozenset:
        plan = self.rewrite(query, from_version)
        hearth = self.adapter.hearth if self.adapter is not None else None
        return execute(plan, hearth, self.ontology)

    def records(self) -> list[MorphismRecord]:
        return list(self.morphisms.records)


def _jsonable(d: dict[str, Any]) -> dict[str, Any]:
    def fix(v: Any) -> Any:
        if isinstance(v, tuple):
            return [fix(x) for x in v]
        if isinstance(v, dict):
            return {k: fix(x) for k, x in v.items()}
        if isinstance(v, list):
            return [fix(x) for x in v]
        return v

    return {k: fix(v) for k, v in d.items()}
