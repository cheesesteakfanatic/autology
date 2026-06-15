"""PlaygroundJob — the LIVE build: selected catalog datasets -> a playground
world, streamed as an ordered discovery narrative.

This is the realistically-fast path the architecture brief specifies (measured,
not aspirational):

* A :class:`PlaygroundJob` runs on ONE dedicated worker thread (never the event
  loop). It opens its OWN ledger on that thread (sqlite is thread-affine — the
  server's ``world.lock`` ledger must never be shared across threads), so the
  server keeps serving reads while the build runs.
* Events stream into a :class:`~ontoforge.pipeline.playground_events.JobEventLog`
  in pipeline order: ``stage:loading`` -> ``join_found`` (the
  :func:`discover_inds_scaled` pass over RAW loaded tables, BEFORE any
  profiling — joins animate within the first second) -> ``stage:profiling`` ->
  ``stage:induce`` -> ``type_found`` / ``silo`` -> ``stage:resolve`` ->
  ``stage:materialize`` -> ``stage:atlas`` -> done.
* Profiling uses a PLAYGROUND profile (``max_lhs=2``) — measured 9x faster on the
  worst wide table with zero candidate keys lost at <=150 rows — so N<=25 fits
  comfortably under a few seconds. The frozen ``discover_sources`` default
  (``max_lhs=3``) is untouched: this caps via the existing ``**kwargs`` path.
* Determinism: fixed STRATA/ER seeds already in the engine; the event sequence
  for a fixed dataset selection is reproducible. Zero network.

On completion the job persists ``ontology.json`` + ``ontology.materialized.json``
+ ``atlas.json`` into the playground dir and writes ``config.json``/``state.json``
so the existing ``GET /api/atlas`` + ``/api/ask`` read the playground as a real
project once it becomes the active world.
"""

from __future__ import annotations

import json
import threading
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import pandas as pd

from ontoforge.contracts import Datatype

from .discover import slugify
from .playground_events import JobEventLog

__all__ = [
    "MAX_DATASETS",
    "PLAYGROUND_PROFILE",
    "PlaygroundBuildError",
    "PlaygroundJob",
    "build_playground",
]

#: hard cap on datasets per build (rejected server-side with a clear message)
MAX_DATASETS = 25

#: the playground profiling profile: cap the FD/key lattice depth. Measured 9x
#: faster on the worst observed 36-col table (11.3s -> 1.3s) with zero candidate
#: keys lost at <=150 rows. Passed through profile_table's existing **kwargs;
#: the frozen discover_sources default (max_lhs=3) is NOT changed.
PLAYGROUND_PROFILE: dict[str, Any] = {"max_lhs": 2, "max_key_size": 2}


class PlaygroundBuildError(Exception):
    """A playground build failed (bad selection, empty corpus, pipeline error)."""


# --------------------------------------------------------------- table loading


def _load_csv(path: Path) -> pd.DataFrame:
    """One CSV -> wart-preserving string DataFrame (the estate loader contract:
    dtype=str, keep_default_na=False)."""
    return pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8")


def _unique_table_name(stem: str, taken: set[str]) -> str:
    """A collision-free table name for a selection (two corpora can share a
    stem; append a numeric suffix deterministically)."""
    name = stem
    n = 1
    while name in taken:
        n += 1
        name = f"{stem}_{n}"
    taken.add(name)
    return name


# ----------------------------------------------------------------- the builder


@dataclass(slots=True)
class _Selection:
    """One resolved dataset to build from."""

    dataset_id: str
    name: str
    path: Path


def build_playground(
    selections: list[tuple[str, str, Path]],
    project_dir: Path,
    log: JobEventLog,
    *,
    stop: Optional[threading.Event] = None,
) -> dict[str, Any]:
    """Run the staged playground build, emitting events into ``log``.

    ``selections`` is ``[(dataset_id, display_name, csv_path)]``. Returns the
    build result dict ``{stats, atlas}`` and persists the world into
    ``project_dir``. Synchronous: the caller (PlaygroundJob) runs this on a
    worker thread and owns the ledger lifecycle.

    This imports the existing pipeline functions only — no frozen module is
    edited and existing pipeline APIs are untouched.
    """
    from ontoforge.hearth import Hearth
    from ontoforge.ledger import SqliteLedger
    from ontoforge.profiling import profile_table
    from ontoforge.strata import Strata
    from ontoforge.vista._pipeline import save_ontology

    from .atlas import build_and_persist_atlas
    from .induce import InducedArtifacts
    from .scale import discover_inds_scaled

    if not selections:
        raise PlaygroundBuildError("no datasets selected")
    if len(selections) > MAX_DATASETS:
        raise PlaygroundBuildError(
            f"too many datasets: {len(selections)} (max {MAX_DATASETS})"
        )

    def check_stop() -> None:
        if stop is not None and stop.is_set():
            raise PlaygroundBuildError("build cancelled")

    project_dir.mkdir(parents=True, exist_ok=True)

    # ---- stage: loading -------------------------------------------------
    log.emit("stage", "loading datasets", stage="loading", progress=0.02)
    tables: dict[str, pd.DataFrame] = {}
    meta_tables: dict[str, dict[str, Any]] = {}
    taken: set[str] = set()
    for sel_id, display, path in selections:
        check_stop()
        try:
            df = _load_csv(path)
        except (OSError, ValueError, pd.errors.ParserError) as exc:
            raise PlaygroundBuildError(f"could not load {sel_id}: {exc}") from exc
        tname = _unique_table_name(slugify(Path(path).stem), taken)
        tables[tname] = df
        meta_tables[tname] = {
            "source_id": slugify(Path(path).stem),
            "file": str(path),
            "format": "csv",
            "key_columns": [],
            "text_columns": [],
            "kind": "structured",
            "dataset_id": sel_id,
            "display": display,
        }
        log.emit(
            "stage",
            f"loaded {display}",
            stage="loading",
            table=tname,
            rows=int(len(df)),
            cols=int(df.shape[1]),
        )

    # ---- stage: joins (RAW tables, BEFORE profiling) --------------------
    # discover_inds_scaled consumes only the loaded DataFrames; no profiles, no
    # STRATA, no materialize. This is the early-join pass: arcs animate first.
    check_stop()
    log.emit("stage", "discovering joins", stage="joins", progress=0.15)
    inds = discover_inds_scaled(tables)
    for ind in inds:
        log.emit(
            "join_found",
            f"found a join: {ind.lhs_table} ↔ {ind.rhs_table} "
            f"on {ind.lhs_column} = {ind.rhs_column}",
            lhs_table=ind.lhs_table,
            lhs_col=ind.lhs_column,
            rhs_table=ind.rhs_table,
            rhs_col=ind.rhs_column,
            coverage=float(ind.coverage),
            score=float(ind.score),
            tier="confirmed" if ind.coverage >= 0.95 else "likely",
        )

    # ---- stage: profiling (capped) --------------------------------------
    check_stop()
    log.emit("stage", "profiling columns", stage="profiling", progress=0.35)
    profiles = []
    for tname, df in tables.items():
        check_stop()
        profiles.append(
            profile_table(df, meta_tables[tname]["source_id"], tname, **PLAYGROUND_PROFILE)
        )

    # recover key columns from the (capped) profiles so materialize keys rows
    from ontoforge.strata.candidates import choose_key

    for tp in profiles:
        key = choose_key(tp)
        meta_tables[tp.table]["key_columns"] = list(key) if key else []
        meta_tables[tp.table]["text_columns"] = [
            c for c, cp in tp.columns.items() if cp.inferred_type is Datatype.TEXT
        ]

    estate = {
        "name": "playground",
        "tables": tables,
        "metadata": {
            "estate": "playground",
            "source_dir": str(project_dir),
            "key_separator": "|",
            "tables": meta_tables,
        },
        "profiles": {tp.table: tp for tp in profiles},
    }

    # ---- stage: induce (STRATA) -----------------------------------------
    check_stop()
    log.emit("stage", "inducing types", stage="induce", progress=0.55)
    ledger = SqliteLedger(str(project_dir / "ledger.sqlite"))
    try:
        result = Strata(ledger=ledger).induce(profiles, inds)
        artifacts = InducedArtifacts(
            profiles=list(profiles), inds=list(inds), strata=result
        )
        onto = artifacts.ontology

        # type_found / silo events: which tables back each induced class
        from .mapping import build_plans

        plans = build_plans(result, onto)
        backing: dict[str, set[str]] = {}
        for plan in plans:
            tabs: set[str] = set()
            if plan.kind == "hub":
                tabs = {t for t, _ in plan.member_columns}
            elif getattr(plan, "table", None) is not None:
                tabs = {plan.table}
            backing.setdefault(plan.class_uri, set()).update(tabs)
        for c in sorted(onto.iter_classes(), key=lambda c: c.name):
            tabs = sorted(backing.get(c.uri, ()))
            log.emit(
                "type_found",
                f"found a type: {c.name}",
                class_uri=c.uri,
                name=c.name,
                confidence=float(c.confidence),
                backing_tables=tabs,
                n_props=len(c.properties),
            )

        # ---- stage: resolve + materialize -------------------------------
        check_stop()
        log.emit("stage", "resolving entities", stage="resolve", progress=0.72)
        from .materialize import materialize_induced

        hearth = Hearth(project_dir / "hearth", ledger)
        log.emit("stage", "materializing world", stage="materialize", progress=0.85)
        stats = materialize_induced(estate, onto, artifacts, hearth, ledger)

        # ---- stage: atlas ----------------------------------------------
        check_stop()
        log.emit("stage", "building atlas", stage="atlas", progress=0.95)
        save_ontology(onto, project_dir / "ontology.json")
        save_ontology(onto, project_dir / "ontology.materialized.json")
        report = build_and_persist_atlas(
            project_dir, estate, artifacts, inds=inds, ontology=onto, ledger=ledger
        )
    finally:
        ledger.close()

    # silos: single-class components after the confirmed-link closure
    for comp in report.components:
        if comp.is_silo and comp.class_uris and not comp.class_uris[0].startswith("table://"):
            log.emit("silo", f"silo: {comp.label}", class_uri=comp.class_uris[0], label=comp.label)

    # ---- persist project shell so the playground reads as a real project
    _write_project_shell(project_dir, estate, stats, report.stats)

    out_stats = {
        "types": len(list(onto.iter_classes())),
        "confirmed": report.stats.get("confirmed", 0),
        "likely": report.stats.get("likely", 0),
        "silos": report.stats.get("silos", 0),
        "entities": stats.get("entities", 0),
        "cells": stats.get("cells", 0),
        "links": stats.get("links", 0),
    }
    log.emit("stage", "done", stage="done", progress=1.0)
    return {"stats": out_stats, "atlas": report.to_payload()}


def _write_project_shell(
    project_dir: Path, estate: dict[str, Any], mat_stats: dict[str, Any], atlas_stats: dict[str, Any]
) -> None:
    """config.json + state.json so the playground dir is a readable project."""
    config = {
        "estate": "playground",
        "ledger": "ledger.sqlite",
        "hearth_root": "hearth",
        "source_dir": str(project_dir),
    }
    (project_dir / "config.json").write_text(
        json.dumps(config, indent=1, sort_keys=True), encoding="utf-8"
    )
    state = {
        "limit": None,
        "cdc": {},
        "stages": ["ingest", "profile", "induce", "resolve", "materialize"],
        "materialized": {
            "ontology": "induced",
            "ontology_file": "ontology.materialized.json",
            "entities": mat_stats.get("entities", 0),
            "cells": mat_stats.get("cells", 0),
            "links": mat_stats.get("links", 0),
        },
    }
    (project_dir / "state.json").write_text(
        json.dumps(state, indent=1, sort_keys=True), encoding="utf-8"
    )


# ---------------------------------------------------------------- the job model


@dataclass
class PlaygroundJob:
    """A single in-flight (or finished) playground build.

    Status: ``running`` -> ``done`` | ``error``. The worker thread mutates
    ``status`` / ``result`` / ``error`` under ``_lock``; the server reads the
    snapshot. The event log is the discovery stream the UI polls.
    """

    job_id: str
    selections: list[tuple[str, str, Path]]
    project_dir: Path
    mode: str = "replace"
    log: JobEventLog = field(default_factory=JobEventLog)
    status: str = "running"
    result: Optional[dict[str, Any]] = None
    error: str = ""
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _thread: Optional[threading.Thread] = field(default=None, repr=False)
    _stop: threading.Event = field(default_factory=threading.Event, repr=False)

    def start(self, on_done=None) -> None:
        """Spawn the worker thread. ``on_done(job)`` is called (on the worker
        thread) when the build succeeds — the server uses it to flip the active
        world to the playground."""

        def run() -> None:
            try:
                res = build_playground(
                    self.selections, self.project_dir, self.log, stop=self._stop
                )
                with self._lock:
                    self.result = res
                    self.status = "done"
                if on_done is not None:
                    try:
                        on_done(self)
                    except Exception:  # never let the callback corrupt status
                        pass
            except Exception as exc:  # report, never crash the worker silently
                with self._lock:
                    self.status = "error"
                    self.error = f"{type(exc).__name__}: {exc}"
                self.log.emit("stage", f"build failed: {exc}", stage="error", progress=1.0)
                # keep a trace for server logs without leaking it to the client
                traceback.print_exc()

        self._thread = threading.Thread(target=run, name=f"playground-{self.job_id}", daemon=True)
        self._thread.start()

    def run_sync(self, on_done=None) -> dict[str, Any]:
        """Run the build inline (tests use this for deterministic, fast builds
        on tiny synthetic tables — no thread, no polling indirection)."""
        try:
            res = build_playground(self.selections, self.project_dir, self.log, stop=self._stop)
            with self._lock:
                self.result = res
                self.status = "done"
            if on_done is not None:
                on_done(self)
            return res
        except Exception as exc:
            with self._lock:
                self.status = "error"
                self.error = f"{type(exc).__name__}: {exc}"
            self.log.emit("stage", f"build failed: {exc}", stage="error", progress=1.0)
            raise

    def cancel(self) -> None:
        self._stop.set()

    def progress(self) -> float:
        """Best-known progress 0..1 from the latest stage event."""
        events = self.log.snapshot()
        for ev in reversed(events):
            if "progress" in ev:
                return float(ev["progress"])
        return 0.0

    def snapshot(self, since: int = 0) -> dict[str, Any]:
        """The pollable status + new events (the build-poll API payload)."""
        with self._lock:
            status = self.status
            result = self.result
            error = self.error
        events = self.log.since(since)
        # derive stage from the most recent stage event
        stage = ""
        for ev in self.log.snapshot():
            if ev.get("kind") == "stage" and ev.get("stage"):
                stage = str(ev["stage"])
        out: dict[str, Any] = {
            "job_id": self.job_id,
            "status": status,
            "progress": self.progress(),
            "stage": stage,
            "events": events,
            "last_seq": self.log.last_seq,
        }
        if status == "done" and result is not None:
            out["result"] = result
        if status == "error":
            out["error"] = error
        return out
