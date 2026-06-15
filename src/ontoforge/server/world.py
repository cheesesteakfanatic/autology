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

import hashlib
import json
import os
import threading
from pathlib import Path
from typing import Any, Callable, Optional

from ontoforge.contracts import (
    FOREVER,
    Atom,
    Layer,
    SpineProfile,
    Stance,
    ValueCell,
    from_instant,
    leaf,
)
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
ATLAS_FILE = "atlas.json"


class ProjectError(Exception):
    """A project-shape problem the API maps to a clean HTTP error."""


class ProjectWorld:
    """Lazily-opened ledger + hearth + ontology + LODESTONE engine."""

    def __init__(self, project: Path) -> None:
        self.project = Path(project)
        #: the ACTIVE world reads come from: the demo project by default, the
        #: playground after a successful build. Switched under self.lock by
        #: ``activate_world``; reads (ask/ontology/atlas/entities/extract/...)
        #: all route through self.active_project.
        self.active_project = Path(project)
        self.active_world = "demo"
        self.lock = threading.RLock()
        self._ledger: Optional[SqliteLedger] = None
        #: ident of the thread that opened ``_ledger`` — sqlite connections are
        #: created check_same_thread=True, so we only ever ``.close()`` from the
        #: owning thread (a playground worker that flips the world via
        #: ``activate_world`` must NOT close the server's request-thread handle).
        self._ledger_owner: Optional[int] = None
        self._hearth: Optional[Hearth] = None
        self._ontology: Optional[Ontology] = None
        self._spine: Optional[DecisionSpine] = None
        self._engine: Optional[Lodestone] = None
        #: lazy federated-search value index (see server.search); dropped on
        #: reload() so /api/reload refreshes what search can see.
        self._search_index: Optional[Any] = None
        #: server-side answer cache: question -> serialized answer dict.
        #: Invalidated by reload(); clarification answers are never cached
        #: (they carry pending engine state).
        self.answer_cache: dict[str, dict[str, Any]] = {}
        #: parsed <project>/atlas.json keyed by mtime; dropped on reload()
        self._atlas_cache: Optional[tuple[int, dict[str, Any]]] = None
        #: in-memory playground build job store (job_id -> PlaygroundJob). Per
        #: process and NOT durable across restart — only the live animation
        #: stream is ephemeral; a completed playground's atlas/ontology persist
        #: on disk and survive (read back as the active world).
        self._jobs: dict[str, Any] = {}
        #: last requested dataset selection (for the workspace-state echo)
        self._selected_datasets: list[str] = []
        #: optional catalog-root override (None -> the repo fixtures/ dir)
        self._fixtures_root_override: Optional[Path] = None

    # ----------------------------------------------------------- project IO

    @property
    def config(self) -> dict[str, Any]:
        cfg = self.active_project / "config.json"
        if not cfg.is_file():
            raise ProjectError(f"no project at {self.active_project} — run `ontoforge init` first")
        return json.loads(cfg.read_text(encoding="utf-8"))

    def state(self) -> dict[str, Any]:
        p = self.active_project / "state.json"
        if p.is_file():
            return json.loads(p.read_text(encoding="utf-8"))
        return {"limit": None, "cdc": {}, "stages": []}

    @property
    def ledger_path(self) -> Path:
        return self.active_project / self.config.get("ledger", "ledger.sqlite")

    @property
    def hearth_dir(self) -> Path:
        return self.active_project / self.config.get("hearth_root", "hearth")

    @property
    def dashboards_dir(self) -> Path:
        return self.active_project / "dashboards"

    @property
    def workspace_path(self) -> Path:
        # the window-layout blob always lives on the BASE project (a build does
        # not relocate the user's saved desktop layout)
        return self.project / "workspace.json"

    @property
    def exports_dir(self) -> Path:
        return self.active_project / "exports"

    @property
    def atlas_path(self) -> Path:
        return self.active_project / ATLAS_FILE

    # --------------------------------------------------------------- atlas

    def read_atlas(self) -> Optional[dict[str, Any]]:
        """The persisted connection atlas (<project>/atlas.json), parsed once
        per file mtime; None when not built. /api/reload drops the cache, and
        a rewritten file invalidates it by mtime anyway."""
        with self.lock:
            p = self.atlas_path
            if not p.is_file():
                self._atlas_cache = None
                return None
            mtime = p.stat().st_mtime_ns
            if self._atlas_cache is not None and self._atlas_cache[0] == mtime:
                return self._atlas_cache[1]
            payload = json.loads(p.read_text(encoding="utf-8"))
            self._atlas_cache = (mtime, payload)
            return payload

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
                self._ledger_owner = threading.get_ident()
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
        """Materialized (per state.json) -> induced -> gold, like the CLI.
        Reads from the ACTIVE world (the playground after a build)."""
        from ontoforge.vista._pipeline import load_ontology

        materialized = self.state().get("materialized") or {}
        if materialized.get("ontology"):
            mat = self.active_project / materialized.get("ontology_file", MATERIALIZED_ONTOLOGY_FILE)
            if mat.is_file():
                return load_ontology(mat)
        induced = self.active_project / "ontology.json"
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

    @property
    def search_index(self) -> Any:
        """The lazy federated-search value index (server.search.WorldIndex)."""
        from .search import build_index

        with self.lock:
            if self._search_index is None:
                self._search_index = build_index(self.hearth, self.ontology)
            return self._search_index

    # ------------------------------------------------------------- asking

    def ask(self, question: str) -> tuple[dict[str, Any], bool]:
        """Answer through the cache; returns (payload, was_cached).

        Every NEW question is recorded in the ledger as an artifact of kind
        'question' (constraint-H provenance over a minted question atom), so
        search kind=question survives server restarts. Cache hits skip the
        write — the question was recorded when first asked."""
        key = question.strip()
        with self.lock:
            if key in self.answer_cache:
                return self.answer_cache[key], True
            answer = self.engine.ask(key)
            self.record_question(key)
            payload = serialize_answer(key, answer)
            if answer.clarification is None:
                self.answer_cache[key] = payload
            return payload, False

    def record_question(self, question: str) -> None:
        """Idempotently persist one asked question as a ledger artifact."""
        qid = hashlib.sha256(question.encode("utf-8")).hexdigest()[:16]
        artifact_id = f"question:{qid}"
        with self.lock:
            ledger = self.ledger
            row = ledger.connection.execute(
                "SELECT 1 FROM artifact WHERE artifact_id = ? AND kind = 'question' LIMIT 1",
                (artifact_id,),
            ).fetchone()
            if row is not None:
                return
            atom = Atom(uri=f"atom://question/{qid}", value=question)
            ledger.register_atoms([atom])
            prov_ref = ledger.intern(leaf(atom.atom_id))
            ledger.append_artifact(
                artifact_id=artifact_id,
                kind="question",
                payload=json.dumps({"question": question}, sort_keys=True),
                prov_ref=prov_ref,
            )

    def recent_questions(self, limit: int = 500) -> list[str]:
        """Saved/recent asks, newest first, deduplicated (search kind=question)."""
        with self.lock:
            if not self.ledger_path.is_file():
                return []
            rows = self.ledger.connection.execute(
                "SELECT payload FROM artifact WHERE kind = 'question' "
                "ORDER BY seq DESC LIMIT ?",
                (int(limit),),
            ).fetchall()
        out: list[str] = []
        for (payload,) in rows:
            try:
                text = json.loads(payload).get("question")
            except (TypeError, ValueError):
                continue
            if text and text not in out:
                out.append(str(text))
        return out

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

    def entity_label(self, uri: str) -> str:
        """A human display label for an entity (best name-ish current value,
        falling back to the uri tail)."""
        return self.search_index.label(uri)

    def neighbors(self, uri: str) -> Optional[list[dict[str, Any]]]:
        """Current-stance link neighborhood for the inspector's graph view:
        [{predicate, direction in|out, target_uri, target_label}].
        None = the uri is unknown to the world (no cells, no links)."""
        with self.lock:
            hearth = self.hearth
            index = self.search_index
            fwd = hearth.links.neighbors(uri)
            rev = hearth.links.neighbors(uri, reverse=True)
            if uri not in index.class_of and not fwd and not rev:
                return None
            links = [
                {
                    "predicate": pred,
                    "direction": direction,
                    "target_uri": target,
                    "target_label": index.label(target),
                }
                for direction, pairs in (("out", fwd), ("in", rev))
                for pred, target in pairs
            ]
        links.sort(key=lambda lk: (lk["predicate"], lk["direction"], lk["target_uri"]))
        return links

    # ---------------------------------------------------------- workspace

    def read_workspace(self) -> Any:
        """The persisted window-layout blob; {} when never saved."""
        with self.lock:
            if not self.workspace_path.is_file():
                return {}
            return json.loads(self.workspace_path.read_text(encoding="utf-8"))

    def write_workspace(self, blob: Any) -> None:
        """Atomically persist the arbitrary JSON layout blob (tmp + rename,
        so a crashed write can never leave a torn workspace.json)."""
        with self.lock:
            write_json_atomic(self.workspace_path, blob)

    # ------------------------------------------------------------- export

    def export_bundle(self, out_dir: Optional[str] = None) -> dict[str, Any]:
        """Run amber.snapshot into <project>/exports/<n>/ (or a caller-named
        directory under the project) and summarize the bundle."""
        from ontoforge.amber import snapshot

        with self.lock:
            if out_dir:
                dest = (self.project / out_dir).resolve()
                if not dest.is_relative_to(self.project.resolve()):
                    raise ProjectError(f"out_dir escapes the project: {out_dir!r}")
            else:
                self.exports_dir.mkdir(parents=True, exist_ok=True)
                taken = [
                    int(p.name)
                    for p in self.exports_dir.iterdir()
                    if p.is_dir() and p.name.isdigit()
                ]
                dest = self.exports_dir / str(max(taken, default=0) + 1)
            manifest_path = snapshot(dest, self.hearth, self.ontology, self.ledger)
        return bundle_summary(dest, manifest_path)

    def list_exports(self) -> list[dict[str, Any]]:
        """Past bundles under <project>/exports/, in numeric-then-name order."""
        from ontoforge.amber import MANIFEST_NAME

        d = self.exports_dir
        if not d.is_dir():
            return []
        dirs = [p for p in d.iterdir() if p.is_dir() and (p / MANIFEST_NAME).is_file()]
        dirs.sort(key=lambda p: (0, int(p.name)) if p.name.isdigit() else (1, p.name))
        return [bundle_summary(p, p / MANIFEST_NAME) for p in dirs]

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

    # ------------------------------------------------------ catalog + build

    @property
    def fixtures_root(self) -> Path:
        """The repo's ``fixtures/`` dir — the catalog reads wild/meridian/
        aviation from here. Resolved from the package location (server ships
        from a source checkout for the playground). Tests may set
        ``world.fixtures_root = <dir>`` to root the catalog at a synthetic
        fixtures tree."""
        if self._fixtures_root_override is not None:
            return self._fixtures_root_override
        from ontoforge import estates  # the package next to fixtures/

        # estates lives under src/ontoforge; fixtures/ is the repo root sibling
        pkg = Path(estates.__file__).resolve().parent  # .../src/ontoforge/estates
        repo_root = pkg.parents[2]  # .../<repo>
        return repo_root / "fixtures"

    @fixtures_root.setter
    def fixtures_root(self, value: Path | str) -> None:
        self._fixtures_root_override = Path(value)

    def catalog(self) -> tuple[list[Any], list[dict[str, Any]]]:
        """(entries, domains) for GET /api/catalog. Read-only from disk."""
        from .catalog import build_catalog, catalog_domains

        entries = build_catalog(self.fixtures_root)
        return entries, catalog_domains(entries)

    def workspace_state(self) -> dict[str, Any]:
        """The playground workspace state (GET /api/workspace/state)."""
        with self.lock:
            built = self.active_world == "playground"
            stats = {"types": 0, "confirmed": 0, "likely": 0, "silos": 0}
            if built:
                atlas = self.read_atlas()
                if atlas:
                    s = atlas.get("stats", {})
                    stats = {
                        "types": int(s.get("classes", 0)),
                        "confirmed": int(s.get("confirmed", 0)),
                        "likely": int(s.get("likely", 0)),
                        "silos": int(s.get("silos", 0)),
                    }
            return {
                "datasets": list(self._selected_datasets),
                "built": built,
                "active_world": self.active_world,
                "stats": stats,
            }

    def start_build(self, dataset_ids: list[str], mode: str = "replace") -> str:
        """Resolve the selection, start a threaded PlaygroundJob, return job_id.

        The job opens its OWN ledger on the worker thread (sqlite is
        thread-affine — never shares ``self.ledger``). On success it flips the
        active world to the playground via ``activate_world`` (taken under
        ``self.lock``)."""
        import uuid

        from ontoforge.pipeline.playground import MAX_DATASETS, PlaygroundJob

        from .catalog import build_catalog

        if not dataset_ids:
            raise ProjectError("no datasets selected")
        if len(dataset_ids) > MAX_DATASETS:
            raise ProjectError(
                f"too many datasets: {len(dataset_ids)} selected, max {MAX_DATASETS} "
                "per playground build — remove some and try again"
            )
        # mode "add" unions the new ids with the already-built selection (the
        # playground grows); "replace" starts from just the new ids
        effective_ids = list(dataset_ids)
        if mode == "add" and self.active_world == "playground":
            for did in self._selected_datasets:
                if did not in effective_ids:
                    effective_ids.append(did)
            if len(effective_ids) > MAX_DATASETS:
                raise ProjectError(
                    f"adding {len(dataset_ids)} would exceed the {MAX_DATASETS}-dataset "
                    f"cap (playground already has {len(self._selected_datasets)})"
                )

        entries = build_catalog(self.fixtures_root)
        by_id = {e.id: e for e in entries}
        selections: list[tuple[str, str, Path]] = []
        missing: list[str] = []
        for did in effective_ids:
            e = by_id.get(did)
            if e is None:
                missing.append(did)
                continue
            selections.append((did, e.name, Path(e.file)))
        if missing:
            raise ProjectError(f"unknown dataset id(s): {', '.join(missing)}")

        job_id = uuid.uuid4().hex[:12]
        play_dir = self.project / "playground" / job_id
        job = PlaygroundJob(
            job_id=job_id, selections=selections, project_dir=play_dir, mode=mode
        )
        with self.lock:
            self._jobs[job_id] = job
            # record the EFFECTIVE selection (the union for add-mode), so a
            # subsequent add unions against everything actually built
            self._selected_datasets = list(effective_ids)

        def on_done(j: Any) -> None:
            # flip reads to the freshly-built playground world
            self.activate_world(j.project_dir)

        job.start(on_done=on_done)
        return job_id

    def build_status(self, job_id: str, since: int = 0) -> Optional[dict[str, Any]]:
        with self.lock:
            job = self._jobs.get(job_id)
        if job is None:
            return None
        return job.snapshot(since=since)

    def activate_world(self, project_dir: Path) -> None:
        """Switch the ACTIVE world reads to ``project_dir`` and drop all cached
        handles so the next read re-opens from the new world."""
        with self.lock:
            self._drop_handles()
            self.active_project = Path(project_dir)
            self.active_world = "playground" if Path(project_dir) != self.project else "demo"

    # ----------------------------------------------------------- engineer

    def engineer_service(self) -> Any:
        """An EngineerService over the ACTIVE world (ontology + hearth + ledger
        + spine). Fresh per call so a /interpret never sees a half-applied
        engine; /apply re-derives it and applies through the real TEMPER
        machinery."""
        from ontoforge.engineer import EngineerService

        with self.lock:
            onto = self.ontology
            try:
                hearth = self.hearth
            except ProjectError:
                hearth = None
            ledger = self.ledger if self.ledger_path.is_file() else None
            spine = self.spine if ledger is not None else None
            return EngineerService(onto, hearth=hearth, ledger=ledger, spine=spine)

    def engineer_schema(self) -> Any:
        """The SchemaView the parser matches against (live ontology + profiles).
        Profiles are recovered from the materialized estate when available; the
        ontology alone still grounds class/property slots."""
        from ontoforge.engineer.commands import SchemaView

        with self.lock:
            onto = self.ontology
            return SchemaView.from_world(onto, profiles=self._estate_profiles())

    def _estate_profiles(self) -> Optional[dict[str, Any]]:
        """Best-effort profile map for slot grounding (table/column names).
        Returns None when no estate is reconstructable — the parser then grounds
        slots against the ontology only (class/property names)."""
        return None

    def interpret(self, command: str) -> dict[str, Any]:
        """Parse a command and PREVIEW it (never mutates). The discriminated
        union the API contract specifies."""
        from ontoforge.engineer import (
            ClarificationNeeded,
            ProposedCommand,
            UnsupportedCommand,
            parse_command,
        )

        schema = self.engineer_schema()
        parsed = parse_command(command, schema)
        if isinstance(parsed, UnsupportedCommand):
            return {
                "unsupported": True,
                "reason": parsed.reason,
                "supported_examples": list(parsed.supported_examples),
            }
        if isinstance(parsed, ClarificationNeeded):
            return {"clarification": parsed.clarification, "options": list(parsed.options)}
        assert isinstance(parsed, ProposedCommand)
        svc = self.engineer_service()
        preview = svc.preview(parsed)
        return {
            "op": {
                "kind": parsed.kind,
                "params": parsed.params,
                "human_summary": parsed.human_summary,
                "confidence": parsed.confidence,
            },
            "preview": {
                "description": preview.description,
                "affected_count": preview.affected_count,
                "sample": preview.sample,
                "coverage": preview.coverage,
                "tier": preview.tier,
                "spine_gated": preview.spine_gated,
                "blocked": preview.blocked,
                "block_reason": preview.block_reason,
                "valid": preview.valid,
                "reason": preview.reason,
                "op_token": preview.op_dict,
            },
        }

    def engineer_apply(self, op_token: dict[str, Any]) -> dict[str, Any]:
        """Apply a previewed op through the real TEMPER engine on the active
        world, persist the new ontology + atlas, and return undo info."""
        with self.lock:
            svc = self.engineer_service()
            out = svc.apply(op_token)
            if out.get("ok"):
                self._persist_engineered(svc)
            return out

    def engineer_undo(self, undo_token: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            svc = self.engineer_service()
            out = svc.undo(undo_token)
            if out.get("ok"):
                self._persist_engineered(svc)
            return out

    def _persist_engineered(self, svc: Any) -> None:
        """Persist the engine's post-state ontology to the active world and drop
        cached ontology/atlas so the next read reflects the edit."""
        from ontoforge.vista._pipeline import save_ontology

        onto = svc.engine.ontology
        save_ontology(onto, self.active_project / "ontology.materialized.json")
        save_ontology(onto, self.active_project / "ontology.json")
        self._ontology = None
        self._atlas_cache = None
        self._engine = None
        self._search_index = None

    # ------------------------------------------------------------- extract

    def extract(
        self,
        type_uri: str,
        filters: list[dict[str, Any]],
        columns: list[str],
        limit: int,
    ) -> dict[str, Any]:
        """Filtered entity rows + per-cell citations for one class (the active
        world). Returns {columns, rows, citations}."""
        from ontoforge.lodestone.model import all_props
        from ontoforge.temper import storage_key

        with self.lock:
            onto = self.ontology
            c = onto.get(type_uri)
            if c is None:
                raise ProjectError(f"unknown type uri: {type_uri}")
            hearth = self.hearth
            props = all_props(onto, type_uri)
            # column selection: requested columns IN REQUESTED ORDER (∩ known),
            # else all non-link props in ontology order
            if columns:
                wanted: list[str] = []
                for col in columns:  # preserve the caller's order, dedup
                    if col in props and col not in wanted:
                        wanted.append(col)
            else:
                wanted = [name for name, p in props.items() if not p.is_link]

            # read the current extent straight from the entity shard
            try:
                shard = hearth.shard(Layer.ENTITY, type_uri)
            except Exception as exc:  # pragma: no cover - empty/absent shard
                raise ProjectError(f"no extent for {type_uri}: {exc}") from exc

            key_of = {name: storage_key(p) for name, p in props.items()}
            # entity -> {prop_name: ValueCell}
            by_entity: dict[str, dict[str, Any]] = {}
            for (entity, key), seq in getattr(shard, "current", {}).items():
                cell = shard.cells[seq]
                for name in wanted:
                    if key_of.get(name) == key:
                        by_entity.setdefault(entity, {})[name] = cell

            def passes(cells: dict[str, Any]) -> bool:
                for f in filters:
                    prop = f.get("prop")
                    op = f.get("op", "==")
                    ref = f.get("value")
                    cell = cells.get(prop)
                    val = None if cell is None else cell.value
                    if not _compare(val, op, ref):
                        return False
                return True

            rows: list[list[Any]] = []
            citations: list[dict[str, Any]] = []
            for entity in sorted(by_entity):
                cells = by_entity[entity]
                if filters and not passes(cells):
                    continue
                if len(rows) >= max(1, limit):
                    break
                row_idx = len(rows)
                row: list[Any] = []
                for col in wanted:
                    cell = cells.get(col)
                    value = None if cell is None else jsonable(cell.value)
                    row.append(value)
                    if cell is not None:
                        atom_ids = self._atoms_for_prov(cell.prov_ref)
                        citations.append(
                            {"row": row_idx, "column": col, "value": value, "atom_ids": atom_ids}
                        )
                rows.append(row)
        return {"columns": wanted, "rows": rows, "citations": citations}

    def _atoms_for_prov(self, prov_ref: Optional[str]) -> list[str]:
        """The atom ids backing a cell's provenance ref (citation evidence)."""
        if not prov_ref:
            return []
        try:
            return sorted(self.ledger.valuate_ref(prov_ref, "citations"))
        except Exception:
            return []

    # ------------------------------------------------------------- reload

    def _drop_handles(self) -> None:
        # Only close the sqlite connection from the thread that opened it; a
        # playground WORKER thread that flips the world (via activate_world ->
        # _drop_handles) must not close the server request thread's handle
        # (check_same_thread would raise). Off-thread, we just drop the
        # reference — the owning thread reopens lazily, GC reclaims the old one.
        if self._ledger is not None and self._ledger_owner == threading.get_ident():
            try:
                self._ledger.close()
            except Exception:
                pass
        self._ledger = None
        self._ledger_owner = None
        self._hearth = None
        self._ontology = None
        self._spine = None
        self._engine = None
        self._search_index = None
        self._atlas_cache = None
        self.answer_cache.clear()

    def reload(self) -> None:
        """Drop every open handle and cache; the next request re-opens the
        project from disk (for when the CLI mutates it underneath us)."""
        with self.lock:
            self._drop_handles()


# ----------------------------------------------------------------- file utils


def _compare(value: Any, op: str, ref: Any) -> bool:
    """Extract filter predicate. String-tolerant: numeric comparisons coerce
    both sides to float when possible, else fall back to string compare; a None
    cell never matches (except the explicit '!=' against a non-None ref)."""
    if op == "contains":
        return value is not None and str(ref).lower() in str(value).lower()
    if value is None:
        return op == "!=" and ref is not None
    if op == "==":
        return str(value) == str(ref)
    if op == "!=":
        return str(value) != str(ref)
    # ordered comparisons: try numeric, fall back to lexical
    try:
        a: Any = float(value)
        b: Any = float(ref)
    except (TypeError, ValueError):
        a, b = str(value), str(ref)
    if op == "<":
        return a < b
    if op == "<=":
        return a <= b
    if op == ">":
        return a > b
    if op == ">=":
        return a >= b
    return False


def write_json_atomic(path: Path, blob: Any) -> None:
    """Write JSON via tmp-file + os.replace (atomic on POSIX): readers see
    either the old blob or the new one, never a torn file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(blob, sort_keys=True, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def bundle_summary(bundle_dir: Path, manifest_path: Path) -> dict[str, Any]:
    """{bundle_dir, manifest_path, files, total_bytes} measured from disk."""
    files = [p for p in bundle_dir.rglob("*") if p.is_file()]
    return {
        "bundle_dir": str(bundle_dir),
        "manifest_path": str(manifest_path),
        "files": len(files),
        "total_bytes": sum(p.stat().st_size for p in files),
    }


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
