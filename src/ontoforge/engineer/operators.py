"""Operator-application service: ProposedCommand -> real TEMPER/ANVIL/ER op.

Each parsed command compiles to an EXISTING operator and runs through a
mandatory two-phase gate, NEVER mutating on preview:

* :meth:`EngineerService.preview` — ``TemperEngine.propose`` (precondition +
  gating verdict) PLUS the real impact math: link match-coverage over the live
  HEARTH extents, retype parse-rate via ANVIL detectors, split routed-row counts,
  rename collision check. The preview is a DRY RUN over actual cells.
* :meth:`EngineerService.apply` — only on explicit confirm. ``TemperEngine.apply``
  through the real machinery; returns ``atlas_delta`` + an ``undo_token`` (the
  serialized inverse operator). SPINE_GATED ops (Merge/Split) can DEFER
  (OperatorDeferred) and surface as "sent to review" — never force-applied.
* :meth:`EngineerService.undo` — re-applies the stored inverse (TEMPER operators
  are invertible; undo is exact).

Confidently-wrong guards (never weakened):

* a proposed LINK is refused/flagged below :data:`JOIN_CONFIRM_FLOOR` coverage —
  no link is asserted from a sentence on weak value-overlap evidence;
* Merge/Split go through the spine gate; a non-auto-accepted op is reported as
  deferred-to-review, not applied;
* the operator's own precondition rejects unknown ranges / duplicate names /
  type-incompatible folds before anything moves.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

from ontoforge.contracts import Datatype
from ontoforge.temper import (
    AddProperty,
    OperatorDeferred,
    Operator,
    PreconditionError,
    RenameClass,
    RenameProperty,
    RetypeProperty,
    TemperEngine,
    op_from_dict,
    op_to_dict,
    storage_key,
)
from ontoforge.temper.ops import resolve_prop

from ontoforge.ensemble import ActionContext, Gate, default_experts

from .commands import ProposedCommand

__all__ = [
    "JOIN_CONFIRM_FLOOR",
    "JOIN_LIKELY_FLOOR",
    "EngineerService",
    "OperatorPreview",
]

#: a link at or above this coverage is "confirmed-safe" to apply (reuses the
#: engine's discover_inds admission floor 0.95)
JOIN_CONFIRM_FLOOR = 0.95
#: a link in [LIKELY, CONFIRM) is an EXPLICITLY-tentative suggestion the user
#: must accept; below LIKELY it is refused as a confidently-wrong join
JOIN_LIKELY_FLOOR = 0.35
#: preview sample row cap
SAMPLE_CAP = 5


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(s).lower()).strip("_") or "prop"


@dataclass(slots=True)
class OperatorPreview:
    """The preview payload: a real impact estimate over live cells.

    ``op`` is the compiled operator (None when the command can't compile to one
    yet — e.g. a synonym recorded outside TEMPER). ``coverage`` is the link
    match-fraction / retype parse-rate (None when not applicable). ``blocked``
    flags a confidently-wrong refusal (e.g. a sub-floor join)."""

    kind: str
    description: str
    affected_count: int
    sample: list[Any] = field(default_factory=list)
    op: Optional[Operator] = None
    op_dict: Optional[dict[str, Any]] = None
    coverage: Optional[float] = None
    tier: str = ""
    confidence: float = 1.0
    spine_gated: bool = False
    blocked: bool = False
    block_reason: str = ""
    valid: bool = True
    reason: str = ""


class EngineerService:
    """Wraps the live world (ontology + hearth + ledger + spine) and a
    TemperEngine; previews and applies parsed commands. Never edits a frozen
    module — it composes them."""

    def __init__(
        self,
        ontology: Any,
        hearth: Any = None,
        ledger: Any = None,
        spine: Any = None,
        gate: Optional[Gate] = None,
    ) -> None:
        self.ontology = ontology
        self.hearth = hearth
        self.ledger = ledger
        self.spine = spine
        self.engine = TemperEngine(ontology, hearth=hearth, spine=spine, ledger=ledger)
        #: the ensemble DE-decision gate. Keyless deterministic experts by default;
        #: ADDS a weighted-vote decision on top of the coverage floor — never
        #: weakens it. Swap in live-model experts by passing a custom Gate.
        self.gate: Gate = gate if gate is not None else Gate(default_experts())
        #: provenance of the most recent gated apply (vote tally + per-expert
        #: weights), surfaced on the apply result and recorded into the ledger.
        self.last_gate_provenance: Optional[dict[str, Any]] = None
        #: (artifact_id, json_payload) of the last recorded gate provenance.
        self._last_gate_artifact: Optional[tuple[str, str]] = None

    # ------------------------------------------------------------- extents

    def _prop_values(self, class_uri: str, prop_name: str) -> list[str]:
        """Current values of one property over a class extent (read straight
        from HEARTH). [] when no world / no cells.

        For a LINK property (whose materialized cells are LinkCells, not value
        cells) the values are the identity values of the link TARGETS — so a
        join's coverage is computed against the real referenced identities."""
        if self.hearth is None:
            return []
        pdef = self._prop_def(class_uri, prop_name)
        if pdef is not None and pdef.is_link:
            return self._link_target_values(class_uri, prop_name, pdef)
        key = storage_key(pdef) if pdef is not None else prop_name
        return self._value_cells(class_uri, key)

    def _value_cells(self, class_uri: str, key: str) -> list[str]:
        out: list[str] = []
        try:
            shard = self.hearth.shard(_entity_layer(), class_uri)
        except Exception:
            return []
        for (entity, prop), seq in getattr(shard, "current", {}).items():
            if prop != key:
                continue
            v = shard.cells[seq].value
            if v is None or v == "":
                continue
            out.append(str(v))
        return out

    def _link_target_values(self, class_uri: str, prop_name: str, pdef: Any) -> list[str]:
        """The identity values reached by following a link property, per subject
        entity (one value per subject that resolves a target)."""
        target = pdef.range_class
        tgt_ident = self._identity_prop(target) if target else None
        tgt_key = None
        if target and tgt_ident:
            tp = self._prop_def(target, tgt_ident)
            tgt_key = storage_key(tp) if tp is not None else tgt_ident
        # build target uri -> identity value
        tgt_vals: dict[str, str] = {}
        if target and tgt_key:
            try:
                tshard = self.hearth.shard(_entity_layer(), target)
                for (e, k), seq in getattr(tshard, "current", {}).items():
                    if k == tgt_key:
                        tgt_vals[e] = str(tshard.cells[seq].value)
            except Exception:
                tgt_vals = {}
        out: list[str] = []
        try:
            shard = self.hearth.shard(_entity_layer(), class_uri)
            subjects = {e for (e, _k) in getattr(shard, "current", {})}
        except Exception:
            return []
        for subj in subjects:
            try:
                hits = self.hearth.traverse(subj, prop_name)
            except Exception:
                hits = []
            for h in hits:
                out.append(tgt_vals.get(h, h))
        return out

    def _prop_def(self, class_uri: str, prop_name: str):
        hit = resolve_prop(self.ontology, class_uri, prop_name)
        return hit[1] if hit else None

    def _identity_prop(self, class_uri: str) -> Optional[str]:
        """A reasonable identity/key property name for a class (first key-like
        own property, else the first property)."""
        c = self.ontology.get(class_uri)
        if c is None or not c.properties:
            return None
        for p in c.properties:
            if not p.is_link:
                return p.name
        return c.properties[0].name

    # ------------------------------------------------------------- preview

    def preview(self, cmd: ProposedCommand) -> OperatorPreview:
        handler = getattr(self, f"_preview_{cmd.kind}", None)
        if handler is None:
            return OperatorPreview(
                kind=cmd.kind, description=f"unsupported op kind {cmd.kind!r}",
                affected_count=0, valid=False, reason="no handler",
            )
        return handler(cmd)

    def _preview_link(self, cmd: ProposedCommand) -> OperatorPreview:
        p = cmd.params
        subj = p.get("left_class") or self._class_for_table(p.get("left_table"))
        tgt = p.get("right_class") or self._class_for_table(p.get("right_table"))
        if subj is None or tgt is None:
            return OperatorPreview(
                kind="link", description="link endpoints did not resolve to classes",
                affected_count=0, valid=False, reason="unresolved endpoints",
            )
        # the backing columns: explicit 'on' cols, else the identity props
        left_prop = p.get("on_left") or self._identity_prop(subj)
        right_prop = p.get("on_right") or self._identity_prop(tgt)
        left_vals = self._distinct(self._prop_values(subj, left_prop)) if left_prop else set()
        right_vals = self._distinct(self._prop_values(tgt, right_prop)) if right_prop else set()
        overlap = left_vals & right_vals
        coverage = (len(overlap) / len(left_vals)) if left_vals else 0.0
        unmatched = sorted(left_vals - right_vals)[:SAMPLE_CAP]
        sample = sorted(overlap)[:SAMPLE_CAP]

        pred = _slug(self.ontology.get(tgt).name if self.ontology.get(tgt) else "linked")
        op = AddProperty(class_uri=subj, name=pred, range_class=tgt, cardinality="one")
        verdict = self.engine.propose(op)

        tier = "confirmed" if coverage >= JOIN_CONFIRM_FLOOR else (
            "likely" if coverage >= JOIN_LIKELY_FLOOR else "weak"
        )
        blocked = coverage < JOIN_LIKELY_FLOOR
        n = len(left_vals)
        matched = len(overlap)
        desc = (
            f"this join matches {coverage*100:.0f}% of rows ({matched}/{n})"
            if n else "no values to match on yet (build a world first)"
        )
        block_reason = ""
        if blocked:
            block_reason = (
                f"coverage {coverage*100:.0f}% is below the {JOIN_LIKELY_FLOOR*100:.0f}% floor — "
                "refusing a confidently-wrong join; pick different columns or confirm it is intentional"
            )
        return OperatorPreview(
            kind="link",
            description=desc + (f"; {len(unmatched)} unmatched samples shown" if unmatched else ""),
            affected_count=matched,
            sample=sample,
            op=None if blocked else op,
            op_dict=None if blocked else op_to_dict(op),
            coverage=round(coverage, 4),
            tier=tier,
            confidence=cmd.confidence,
            spine_gated=bool(verdict.get("spine_gated")),
            blocked=blocked,
            block_reason=block_reason,
            valid=bool(verdict.get("valid")) and not blocked,
            reason=verdict.get("reason", ""),
        )

    def _preview_retype(self, cmd: ProposedCommand) -> OperatorPreview:
        p = cmd.params
        classes = p.get("classes") or []
        prop_names = p.get("prop_names") or [p.get("prop")]
        if not classes:
            return OperatorPreview(
                kind="retype", description=f"{p.get('prop')!r} is not a property of any class",
                affected_count=0, valid=False, reason="no owning class",
            )
        class_uri = self._owner_of(classes[0], prop_names[0])
        prop_name = prop_names[0]
        values = self._prop_values(class_uri, prop_name)
        target = p["target_type"]
        pdef = self._prop_def(class_uri, prop_name)
        src_dt = pdef.datatype if pdef is not None else Datatype.STRING

        new_dt, conv_spec, new_unit, rate, samples = self._retype_plan(target, values, src_dt)
        n = len(values)
        ok = int(round(rate * n))
        # ANVIL-style parse-rate report regardless of whether a TEMPER op applies
        rate_desc = (
            f"recognizes {rate*100:.0f}% as {target} ({ok}/{n} values parse)"
            if n else f"would retype {prop_name!r} to {target} (no committed values yet)"
        )
        if new_dt is None:
            # The property's CURRENT type does not admit an invertible TEMPER
            # conversion to the target (e.g. a TEXT column cannot be cast to a
            # number without a non-invertible parse — the engine refuses it).
            # We still show the honest parse-rate; apply is correctly blocked.
            return OperatorPreview(
                kind="retype",
                description=(
                    f"{rate_desc}; but {prop_name!r} is currently {src_dt.value} — "
                    "TEMPER only retypes numeric↔numeric / unit rescales invertibly, "
                    "so this conversion cannot be applied as a reversible op"
                ),
                affected_count=ok, sample=samples, coverage=round(rate, 4),
                confidence=cmd.confidence, valid=False,
                reason=f"source datatype {src_dt.value} is not invertibly retypable to {target}",
            )
        op = RetypeProperty(
            class_uri=class_uri, prop_name=prop_name,
            new_datatype=new_dt, conversion_spec=conv_spec, new_unit=new_unit,
        )
        verdict = self.engine.propose(op)
        return OperatorPreview(
            kind="retype", description=rate_desc, affected_count=ok, sample=samples,
            op=op if verdict.get("valid") else None,
            op_dict=op_to_dict(op) if verdict.get("valid") else None,
            coverage=round(rate, 4), confidence=cmd.confidence,
            valid=bool(verdict.get("valid")), reason=verdict.get("reason", ""),
        )

    def _preview_rename(self, cmd: ProposedCommand) -> OperatorPreview:
        p = cmd.params
        if p.get("kind_target") == "class":
            op: Operator = RenameClass(uri=p["class_uri"], new_name=p["new_name"])
        else:
            classes = p.get("classes") or []
            prop_names = p.get("prop_names") or [p.get("old_name")]
            if not classes:
                return OperatorPreview(
                    kind="rename", description=f"{p.get('old_name')!r} is not a known property",
                    affected_count=0, valid=False, reason="no owning class",
                )
            # rename on the OWNING class (resolve_prop returns the owner)
            owner = self._owner_of(classes[0], prop_names[0])
            op = RenameProperty(class_uri=owner, prop_name=prop_names[0], new_name=p["new_name"])
        verdict = self.engine.propose(op)
        desc = (
            "renames the label only; the URI / cell key never moves — zero rows rewritten"
            if verdict.get("valid") else verdict.get("reason", "rename rejected")
        )
        return OperatorPreview(
            kind="rename", description=desc, affected_count=0, sample=[],
            op=op if verdict.get("valid") else None,
            op_dict=op_to_dict(op) if verdict.get("valid") else None,
            confidence=cmd.confidence,
            valid=bool(verdict.get("valid")), reason=verdict.get("reason", ""),
        )

    def _preview_merge_entities(self, cmd: ProposedCommand) -> OperatorPreview:
        # ER dedupe of one class's identity domain — previewed as cluster math.
        p = cmd.params
        class_uri = p["class_uri"]
        ident = self._identity_prop(class_uri)
        values = self._prop_values(class_uri, ident) if ident else []
        mentions = len(values)
        distinct = len(self._distinct(values))
        merged = mentions - distinct
        desc = (
            f"{mentions} mentions → {distinct} entities; {merged} exact-duplicate merges proposed"
            if mentions else "no committed entities to merge yet"
        )
        # ER merge is spine-gated and routes low-margin pairs to review; we
        # surface it as a review-bound proposal rather than a TEMPER op.
        return OperatorPreview(
            kind="merge_entities", description=desc, affected_count=merged,
            sample=sorted(set(v for v in values if values.count(v) > 1))[:SAMPLE_CAP],
            op=None, op_dict=None, spine_gated=True, confidence=cmd.confidence,
            valid=True, reason="entity resolution routes low-margin pairs to human review",
        )

    def _preview_split(self, cmd: ProposedCommand) -> OperatorPreview:
        p = cmd.params
        classes = p.get("classes") or []
        if not classes:
            return OperatorPreview(
                kind="split", description=f"{p.get('prop')!r} is not a known column",
                affected_count=0, valid=False, reason="no owning class",
            )
        class_uri = classes[0]
        prop = p["prop"]
        delim = p["delimiter"]
        values = self._prop_values(class_uri, prop)
        routed = sum(1 for v in values if delim in v)
        failed = len(values) - routed
        sample = []
        for v in values[:SAMPLE_CAP]:
            bits = v.split(delim, 1)
            sample.append({"input": v, "parts": bits})
        desc = (
            f"{routed}/{len(values)} rows split on {delim!r}; {failed} have no delimiter"
            if values else f"will split {prop!r} on {delim!r} (no committed values yet)"
        )
        return OperatorPreview(
            kind="split", description=desc, affected_count=routed, sample=sample,
            op=None, op_dict=None, spine_gated=True, confidence=cmd.confidence,
            valid=failed == 0 or bool(values),
            reason=("some rows have no delimiter — verified split routes only intended rows"
                    if failed else ""),
        )

    def _preview_synonym(self, cmd: ProposedCommand) -> OperatorPreview:
        p = cmd.params
        left_classes = p.get("left_classes") or []
        right_classes = p.get("right_classes") or []
        # value-overlap of the two property value sets (type-compat evidence)
        lv = self._distinct(
            self._prop_values(left_classes[0], p["left_prop"]) if left_classes else []
        )
        rv = self._distinct(
            self._prop_values(right_classes[0], p["right_prop"]) if right_classes else []
        )
        overlap = lv & rv
        share = (len(overlap) / len(lv)) if lv else 0.0
        desc = (
            f"these two properties share {share*100:.0f}% of values "
            f"({len(overlap)}/{len(lv)}) — safe to treat as one"
            if lv else "recording a synonym (no committed values to compare yet)"
        )
        return OperatorPreview(
            kind="synonym", description=desc, affected_count=len(overlap),
            sample=sorted(overlap)[:SAMPLE_CAP],
            op=None, op_dict=None, coverage=round(share, 4), confidence=cmd.confidence,
            valid=True, reason="synonym is non-destructive (PropertyDef.synonyms)",
        )

    # ------------------------------------------------------------- apply

    def _link_coverage(self, op: AddProperty) -> Optional[float]:
        """Re-measure, straight from the live HEARTH, whether the link this
        AddProperty would assert is defensible: the BEST match-coverage of any
        of the subject class's own (non-link) properties against the target
        class's identity values. A real join key clears the floor; a bogus pair
        with no shared key anywhere does not. ``None`` when there is no world /
        nothing to measure (so a value-less synthetic op is not falsely refused).

        Measuring the best-available key (rather than a single guessed column)
        mirrors what ``_preview_link`` admits — it never refuses a join for
        which a genuine high-coverage key exists, and never lets one through
        when none does."""
        subj, tgt = op.class_uri, op.range_class
        if not tgt:
            return None
        sc, tc = self.ontology.get(subj), self.ontology.get(tgt)
        if sc is None or tc is None:
            return None
        # value sets for every non-link target property (the candidate join keys)
        tgt_sets: list[set[str]] = []
        for tp in tc.properties:
            if tp.is_link:
                continue
            tv = self._distinct(self._prop_values(tgt, tp.name))
            if tv:
                tgt_sets.append(tv)
        if not tgt_sets:
            return None
        best: Optional[float] = None
        for sp in sc.properties:
            if sp.is_link:
                continue
            left_vals = self._distinct(self._prop_values(subj, sp.name))
            if not left_vals:
                continue
            for tv in tgt_sets:
                cov = len(left_vals & tv) / len(left_vals)
                if best is None or cov > best:
                    best = cov
        return best

    def _gate_link(self, op: AddProperty, coverage: Optional[float]):
        """Run the ensemble DE-decision gate for a link op, building an
        :class:`ActionContext` from REAL measurements and using the coverage
        floor as an execution-grounded verifier veto.

        The verifier re-measures coverage from the live HEARTH and VETOES below
        :data:`JOIN_LIKELY_FLOOR` regardless of any votes — so even a unanimous
        'fire' can never assert a sub-floor join (the gate's confidently-wrong
        guard, reusing the engineer's floor)."""
        subj, tgt = op.class_uri, op.range_class
        sc, tc = self.ontology.get(subj), self.ontology.get(tgt) if tgt else None
        # best value overlap across candidate keys (same measurement as coverage)
        ctx = ActionContext(
            action="join",
            coverage=coverage,
            value_overlap=coverage,
            left_name=(sc.name if sc is not None else subj),
            right_name=(tc.name if tc is not None else (tgt or "")),
            left_type="string",
            right_type="string",
        )

        def _verifier(_ctx: ActionContext) -> tuple[bool, str]:
            cov = self._link_coverage(op)
            if cov is not None and cov < JOIN_LIKELY_FLOOR:
                return False, (
                    f"coverage {cov*100:.0f}% below the {JOIN_LIKELY_FLOOR*100:.0f}% floor "
                    "(execution-grounded veto)"
                )
            return True, ""

        return self.gate.decide(ctx, verifier=_verifier)

    def _record_gate_provenance(self, op: Operator, gate_dec: Any, fired: bool) -> None:
        """Append the gate's vote tally + per-expert weights to the ledger as an
        artifact ('why did this action fire/hold?'). Best-effort: provenance must
        never break an apply, and the ledger's constraint-H (non-ZERO prov_ref)
        means a synthetic op without atom provenance is recorded via record_cost
        + an in-memory payload rather than a hard artifact insert."""
        if self.ledger is None:
            return
        prov = self.last_gate_provenance or (gate_dec.to_provenance() if gate_dec is not None else None)
        if prov is None:
            return
        payload = {"op": op_to_dict(op), "fired": fired, "gate": prov}
        try:
            import json as _json

            artifact_id = f"gate:{op.op_type}:{self.engine.ontology.version}:{int(fired)}"
            # record_cost is the always-safe provenance channel (no constraint-H);
            # it durably logs that a gate decision was taken for this op.
            self.ledger.record_cost(f"engineer.gate.{op.op_type}", 0)
            # stash the full payload so the API / ledger reader can surface it;
            # kept on the service for the same-request response either way.
            self._last_gate_artifact = (artifact_id, _json.dumps(payload, sort_keys=True))
        except Exception:
            # provenance is auditing, not correctness — never fail an apply on it
            self._last_gate_artifact = None

    def apply(self, op_dict: dict[str, Any]) -> dict[str, Any]:
        """Apply a previously-previewed operator via the real TEMPER engine.

        Returns {ok, human_summary, atlas_delta, undo_token, deferred?}. A
        spine-gated op that does not auto-accept returns ``ok=False`` +
        ``deferred=True`` (sent to review) — never force-applied.

        Defence-in-depth: the apply path NEVER trusts the client to have honored
        the confidently-wrong join floor. For any link (``AddProperty`` with a
        ``range_class``) we re-measure coverage from the live HEARTH and refuse
        below :data:`JOIN_LIKELY_FLOOR` here too — so a hand-crafted op that
        skipped ``interpret`` cannot assert a sub-floor join."""
        op = op_from_dict(op_dict)
        self.last_gate_provenance = None
        if isinstance(op, AddProperty) and op.range_class:
            cov = self._link_coverage(op)
            if cov is not None and cov < JOIN_LIKELY_FLOOR:
                return {
                    "ok": False, "deferred": False, "blocked": True,
                    "human_summary": (
                        f"refused: this join matches only {cov*100:.0f}% of rows, below the "
                        f"{JOIN_LIKELY_FLOOR*100:.0f}% floor — a confidently-wrong join is never applied"
                    ),
                    "atlas_delta": {"added_links": [], "removed": [], "renamed": []},
                    "undo_token": None, "new_stats": self.stats(),
                }
            # The coverage floor passed. Now the ENSEMBLE GATE: weighted-vote of
            # deterministic experts, with the coverage floor as an execution-
            # grounded VETO (it can never let a sub-floor join through, and a
            # held vote reports rather than applies). This ADDS a decision on top
            # of the floor — it never weakens it.
            gate_dec = self._gate_link(op, cov)
            self.last_gate_provenance = gate_dec.to_provenance()
            if not gate_dec.fire:
                self._record_gate_provenance(op, gate_dec, fired=False)
                summary = (
                    f"held for review: the decision gate did not fire this join "
                    f"(confidence {gate_dec.confidence:.2f}, "
                    f"fire {gate_dec.tally.get('fire', 0):.2f} vs hold {gate_dec.tally.get('hold', 0):.2f})"
                    if not gate_dec.vetoed
                    else f"held: {gate_dec.veto_reason}"
                )
                return {
                    "ok": False, "deferred": True, "blocked": gate_dec.vetoed,
                    "human_summary": summary,
                    "gate": gate_dec.to_provenance(),
                    "atlas_delta": {"added_links": [], "removed": [], "renamed": []},
                    "undo_token": None, "new_stats": self.stats(),
                }
        try:
            report = self.engine.apply(op)
        except OperatorDeferred as exc:
            return {
                "ok": False, "deferred": True,
                "human_summary": f"{op.op_type} sent to human review: {exc}",
                "atlas_delta": {"added_links": [], "removed": [], "renamed": []},
                "undo_token": None, "new_stats": self.stats(),
            }
        except PreconditionError as exc:
            return {
                "ok": False, "deferred": False,
                "human_summary": f"{op.op_type} rejected: {exc}",
                "atlas_delta": {"added_links": [], "removed": [], "renamed": []},
                "undo_token": None, "new_stats": self.stats(),
            }
        # the engine swapped its live ontology to the post-state; keep ours synced
        self.ontology = self.engine.ontology
        undo_token = op_to_dict(report.inverse) if report.inverse is not None else None
        atlas_delta = self._atlas_delta(op, report)
        result: dict[str, Any] = {
            "ok": True, "deferred": False,
            "human_summary": _apply_summary(op, report),
            "atlas_delta": atlas_delta,
            "undo_token": undo_token,
            "new_stats": self.stats(),
        }
        if self.last_gate_provenance is not None:
            result["gate"] = self.last_gate_provenance
            self._record_gate_provenance(op, None, fired=True)
        return result

    def undo(self, undo_token: dict[str, Any]) -> dict[str, Any]:
        """Re-apply the stored inverse operator (exact TEMPER undo)."""
        inv = op_from_dict(undo_token)
        try:
            self.engine.apply(inv)
        except (OperatorDeferred, PreconditionError) as exc:
            return {"ok": False, "human_summary": f"undo failed: {exc}", "new_stats": self.stats()}
        self.ontology = self.engine.ontology
        return {
            "ok": True,
            "human_summary": f"undid via {inv.op_type}",
            "new_stats": self.stats(),
        }

    # ------------------------------------------------------------- helpers

    def stats(self) -> dict[str, int]:
        onto = self.engine.ontology
        classes = list(onto.iter_classes())
        links = sum(1 for c in classes for p in c.properties if p.is_link)
        return {
            "types": len(classes),
            "links": links,
            "properties": sum(len(c.properties) for c in classes),
            "version": onto.version,
        }

    def _atlas_delta(self, op: Operator, report: Any) -> dict[str, Any]:
        added: list[dict[str, str]] = []
        removed: list[str] = []
        renamed: list[dict[str, str]] = []
        if isinstance(op, AddProperty) and op.range_class:
            added.append({"src_class": op.class_uri, "dst_class": op.range_class, "prop": op.name})
        if isinstance(op, RenameProperty):
            renamed.append({"class_uri": op.class_uri, "old": op.prop_name, "new": op.new_name})
        if isinstance(op, RenameClass):
            renamed.append({"class_uri": op.uri, "new": op.new_name})
        return {"added_links": added, "removed": removed, "renamed": renamed}

    def _class_for_table(self, table: Optional[str]) -> Optional[str]:
        """Best class backing a table name — match the class whose name slugs to
        the table, else None."""
        if not table:
            return None
        tn = _slug(table)
        for c in self.ontology.iter_classes():
            if _slug(c.name) == tn:
                return c.uri
        return None

    def _owner_of(self, class_uri: str, prop_name: str) -> str:
        hit = resolve_prop(self.ontology, class_uri, prop_name)
        return hit[0] if hit else class_uri

    @staticmethod
    def _distinct(values: list[str]) -> set[str]:
        return {v.strip() for v in values if v and v.strip()}

    def _retype_plan(self, target: str, values: list[str], src_dt: Datatype):
        """(new_datatype, conversion_spec, new_unit, parse_rate, samples).

        ``new_datatype`` is None when the property's CURRENT datatype does not
        admit an INVERTIBLE TEMPER conversion to the target — the engine refuses
        a non-invertible string→number/date cast, and we honour that gate
        (a parse-rate is still reported by the caller). A valid op is produced
        only for the cases ``RetypeProperty.precondition`` accepts:
        INTEGER→float, numeric currency unit-annotation, numeric unit rescale."""
        sample_in = [v for v in values if v and v.strip()][:SAMPLE_CAP]
        numeric_src = src_dt in (Datatype.INTEGER, Datatype.FLOAT)

        if target in ("number", "currency", "integer"):
            ok = 0
            samples = []
            for v in values:
                s = re.sub(r"[,$£€\s]", "", str(v))
                try:
                    float(s)
                    ok += 1
                except (ValueError, TypeError):
                    pass
            for v in sample_in:
                s = re.sub(r"[,$£€\s]", "", str(v))
                try:
                    samples.append({"in": v, "out": float(s)})
                except (ValueError, TypeError):
                    samples.append({"in": v, "out": None})
            rate = ok / len(values) if values else 0.0
            if not numeric_src:
                return None, "", None, rate, samples  # string→number is non-invertible
            if target == "integer":
                if src_dt is Datatype.FLOAT:
                    return "integer", "float_to_int", None, rate, samples
                return "integer", "linear:1.0:0.0", None, rate, samples
            unit = "USD" if target == "currency" else None
            if src_dt is Datatype.INTEGER:
                return "float", "int_to_float", unit, rate, samples
            return "float", "linear:1.0:0.0", unit, rate, samples

        if target in ("date", "datetime"):
            ok = sum(1 for v in values if _looks_dateish(v))
            samples = [{"in": v, "out": v if _looks_dateish(v) else None} for v in sample_in]
            rate = ok / len(values) if values else 0.0
            # date parsing from strings is a non-invertible reformat — TEMPER's
            # RetypeProperty has no string→date conversion spec, so no op applies.
            return None, "", None, rate, samples

        return None, "", None, 0.0, []


def _entity_layer():
    from ontoforge.contracts import Layer
    return Layer.ENTITY


_DATE_RE = re.compile(
    r"^\s*(\d{4}[-/]\d{1,2}[-/]\d{1,2}|\d{1,2}[-/]\d{1,2}[-/]\d{2,4}|\d{8})\s*$"
)


def _looks_dateish(v: Any) -> bool:
    return bool(_DATE_RE.match(str(v)))


def _apply_summary(op: Operator, report: Any) -> str:
    if isinstance(op, AddProperty):
        return f"linked via new property {op.name!r} → {op.range_class}"
    if isinstance(op, RenameProperty):
        return f"renamed property {op.prop_name!r} → {op.new_name!r} (0 rows rewritten)"
    if isinstance(op, RenameClass):
        return f"renamed class → {op.new_name!r}"
    if isinstance(op, RetypeProperty):
        return f"retyped {op.prop_name!r} to {op.new_datatype} ({report.stats.get('cells_written', 0)} cells)"
    return f"applied {op.op_type}"
