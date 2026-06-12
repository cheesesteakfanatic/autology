"""ProjectWorld — the server's lazily-opened view of a project directory.

Opens the same artifacts the CLI writes (config.json, state.json,
ledger.sqlite, hearth/, ontology.materialized.json | ontology.json | gold) and
holds ONE LODESTONE engine per world so pending-clarification state survives
between requests. All sqlite access is serialized behind ``self.lock`` (the
ledger's sqlite connection is also thread-affine, so the API layer keeps every
endpoint on the event-loop thread and takes this lock around ledger work).

The ontology resolution mirrors the CLI's answering rule (reimplemented here,
not imported): the materialized ontology per state.json when present, else the
induced ontology.json, else the estate's gold ontology.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Callable, Optional

from ontoforge.contracts import FOREVER, Layer, SpineProfile, Stance, ValueCell, from_instant
from ontoforge.contracts.ontology import Ontology
from ontoforge.contracts.oqir import Answer, OQIRTerm
from ontoforge.hearth import Hearth
from ontoforge.ledger import SqliteLedger
from ontoforge.lodestone import Lodestone
from ontoforge.lodestone.execute import ExecOutcome, execute_candidate
from ontoforge.lodestone.model import Candidate
from ontoforge.spine import DecisionSpine

#: §4.8 active-learning loop: once a decision kind has accumulated this many
#: human review verdicts, the server replays them as CalibrationSamples into
#: ``spine.recalibrate`` (and again at every further multiple). 20 is the
#: documented v0 threshold — below the calibrator's MIN_FIT_SAMPLES (50) the
#: refit is a recorded no-op, but the loop, its ledger artifacts, and the
#: sample plumbing are real and observable.
REVIEW_RECALIBRATION_THRESHOLD = 20

MATERIALIZED_ONTOLOGY_FILE = "ontology.materialized.json"


class ProjectError(Exception):
    """A project-shape problem the API maps to a clean HTTP error."""


class ProjectWorld:
    """Lazily-opened ledger + hearth + ontology + LODESTONE engine."""

    def __init__(self, project: Path) -> None:
        self.project = Path(project)
        self.lock = threading.RLock()
        self._ledger: Optional[SqliteLedger] = None
        self._hearth: Optional[Hearth] = None
        self._ontology: Optional[Ontology] = None
        self._spine: Optional[DecisionSpine] = None
        self._engine: Optional[Lodestone] = None
        #: server-side answer cache: question -> serialized answer dict.
        #: Invalidated by reload(); clarification answers are never cached
        #: (they carry pending engine state).
        self.answer_cache: dict[str, dict[str, Any]] = {}

    # ----------------------------------------------------------- project IO

    @property
    def config(self) -> dict[str, Any]:
        cfg = self.project / "config.json"
        if not cfg.is_file():
            raise ProjectError(f"no project at {self.project} — run `ontoforge init` first")
        return json.loads(cfg.read_text(encoding="utf-8"))

    def state(self) -> dict[str, Any]:
        p = self.project / "state.json"
        if p.is_file():
            return json.loads(p.read_text(encoding="utf-8"))
        return {"limit": None, "cdc": {}, "stages": []}

    @property
    def ledger_path(self) -> Path:
        return self.project / self.config.get("ledger", "ledger.sqlite")

    @property
    def hearth_dir(self) -> Path:
        return self.project / self.config.get("hearth_root", "hearth")

    @property
    def dashboards_dir(self) -> Path:
        return self.project / "dashboards"

    # -------------------------------------------------------- lazy world

    @property
    def ledger(self) -> SqliteLedger:
        with self.lock:
            if self._ledger is None:
                if not self.ledger_path.is_file():
                    raise ProjectError(
                        f"project has no ledger ({self.ledger_path.name}) — run `ontoforge ingest`"
                    )
                self._ledger = SqliteLedger(str(self.ledger_path))
            return self._ledger

    @property
    def hearth(self) -> Hearth:
        with self.lock:
            if self._hearth is None:
                if not self.hearth_dir.is_dir():
                    raise ProjectError(
                        "project has no HEARTH store — run `ontoforge materialize` first"
                    )
                self._hearth = Hearth(self.hearth_dir, self.ledger)
            return self._hearth

    @property
    def ontology(self) -> Ontology:
        with self.lock:
            if self._ontology is None:
                self._ontology = self._resolve_ontology()
            return self._ontology

    def _resolve_ontology(self) -> Ontology:
        """Materialized (per state.json) -> induced -> gold, like the CLI."""
        from ontoforge.vista._pipeline import load_ontology

        materialized = self.state().get("materialized") or {}
        if materialized.get("ontology"):
            mat = self.project / materialized.get("ontology_file", MATERIALIZED_ONTOLOGY_FILE)
            if mat.is_file():
                return load_ontology(mat)
        induced = self.project / "ontology.json"
        if induced.is_file():
            return load_ontology(induced)
        try:
            from ontoforge.estates import load_gold_ontology

            return load_gold_ontology(self.config["fixtures_dir"])
        except Exception as exc:  # pragma: no cover - estate fixtures missing
            raise ProjectError(f"no ontology available for this project: {exc}") from exc

    @property
    def spine(self) -> DecisionSpine:
        with self.lock:
            if self._spine is None:
                # Ledger-recording spine: QI decisions taken while answering
                # land in the decision table and feed the review queue (§4.8).
                self._spine = DecisionSpine(SpineProfile(), model_client=None, ledger=self.ledger)
            return self._spine

    @property
    def engine(self) -> Lodestone:
        """One engine per world — owns the pending-clarification state."""
        with self.lock:
            if self._engine is None:
                self._engine = Lodestone(self.ontology, self.hearth, self.ledger, self.spine)
            return self._engine

    # ------------------------------------------------------------- asking

    def ask(self, question: str) -> tuple[dict[str, Any], bool]:
        """Answer through the cache; returns (payload, was_cached)."""
        key = question.strip()
        with self.lock:
            if key in self.answer_cache:
                return self.answer_cache[key], True
            answer = self.engine.ask(key)
            payload = serialize_answer(key, answer)
            if answer.clarification is None:
                self.answer_cache[key] = payload
            return payload, False

    def clarify(self, question: str, choice: int | str) -> dict[str, Any]:
        """Re-ask to (re)establish the pending clarification deterministically,
        then resolve the chosen option through the engine."""
        key = question.strip()
        with self.lock:
            answer = self.engine.ask(key)
            if answer.clarification is None:
                # the question resolved without needing the clarification
                return serialize_answer(key, answer)
            resolved = self.engine.answer_clarification(choice)
            return serialize_answer(key, resolved)

    # ------------------------------------------------------------ entities

    def entity(self, uri: str, stance: Stance) -> Optional[dict[str, Any]]:
        """The entity property card under a stance + full per-property history
        (every cell ever written, the §4.4 audit trail). None = unknown URI."""
        with self.lock:
            hearth = self.hearth
            shards = [
                s
                for s in hearth.value_shard_items()
                if s.layer is Layer.ENTITY and uri in s.by_entity
            ]
            if not shards:
                return None
            classes = sorted({s.class_uri for s in shards})
            properties = {
                prop: jsonable(value) for prop, value in sorted(hearth.read(uri, stance).items())
            }
            props_ever = sorted(
                {s.cells[seq].prop for s in shards for seq in s.by_entity.get(uri, ())}
            )
            history = {
                prop: [serialize_cell(c) for c in hearth.history(uri, prop)]
                for prop in props_ever
            }
        return {"uri": uri, "classes": classes, "properties": properties, "history": history}

    def oqir_executor(self) -> Callable[[OQIRTerm], list[dict[str, Any]]]:
        """The VISTA data seam: lower an OQIR term through this world.

        Wraps the term in a LODESTONE Candidate and runs the staged executor;
        non-executable or empty charts render with no data rather than failing
        the whole dashboard.
        """
        onto, hearth = self.ontology, self.hearth

        def executor(term: OQIRTerm) -> list[dict[str, Any]]:
            try:
                out = execute_candidate(Candidate(cand_id="vista", term=term), onto, hearth)
            except Exception:
                return []
            if not isinstance(out, ExecOutcome):
                return []
            return [dict(zip(out.columns, row)) for row in out.rows]

        return executor

    # ------------------------------------------------------------- reload

    def reload(self) -> None:
        """Drop every open handle and cache; the next request re-opens the
        project from disk (for when the CLI mutates it underneath us)."""
        with self.lock:
            if self._ledger is not None:
                try:
                    self._ledger.close()
                except Exception:
                    pass
            self._ledger = None
            self._hearth = None
            self._ontology = None
            self._spine = None
            self._engine = None
            self.answer_cache.clear()


# ------------------------------------------------------------- serialization


def jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return str(value)


def instant_iso(i: int) -> Optional[str]:
    """Instant -> ISO-8601 UTC string; the open end (FOREVER) maps to None."""
    if i >= FOREVER:
        return None
    return from_instant(i).isoformat()


def serialize_cell(c: ValueCell) -> dict[str, Any]:
    """One HEARTH cell as the API exposes it (bitemporal bounds + provenance)."""
    return {
        "value": jsonable(c.value),
        "valid_from": instant_iso(c.valid.start),
        "valid_to": instant_iso(c.valid.end),
        "system_from": instant_iso(c.system.start),
        "system_to": instant_iso(c.system.end),
        "confidence": float(c.confidence),
        "src_rank": int(c.src_rank),
        "prov_ref": c.prov_ref,
        "is_current": bool(c.is_current),
    }


def serialize_answer(question: str, answer: Answer) -> dict[str, Any]:
    return {
        "question": question,
        "columns": [str(c) for c in answer.columns],
        "rows": [[jsonable(v) for v in row] for row in answer.rows],
        "confidence": float(answer.confidence),
        "abstained": bool(answer.abstained),
        "abstain_reason": answer.abstain_reason or "",
        "clarification": answer.clarification,
        "clarification_options": list(answer.clarification_options or ()),
        "citations": [
            {
                "row": c.row,
                "column": c.column,
                "value": jsonable(c.value),
                "atom_ids": list(c.atom_ids),
            }
            for c in (answer.citations or [])
        ],
    }
