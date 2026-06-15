"""The OntoForge web application: REST API + SPA over a project directory.

    create_app(project) -> FastAPI       (mounted SPA at /, API under /api)
    run_server(project, host, port)      (uvicorn entry the CLI `serve` calls)

Every endpoint is ``async`` ON PURPOSE: FastAPI then runs them on the single
event-loop thread, which keeps the project's sqlite connection (thread-affine)
on one thread; ``world.lock`` additionally serializes ledger access.

Review/active-learning loop (§4.8): POST /api/review/{decision_id} appends an
append-only 'review-verdict' artifact (constraint-H provenance over a minted
human-review atom). Once a decision kind accumulates
``REVIEW_RECALIBRATION_THRESHOLD`` (20) verdicts — and at every further
multiple — all of its verdicts are replayed as contracts.CalibrationSample
into ``spine.recalibrate(kind, samples)`` and a 'recalibration' artifact is
recorded whose provenance sums the verdict atoms.
"""

from __future__ import annotations

import csv
import io
import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from ontoforge.amber import AmberError

from ontoforge.contracts import (
    Atom,
    CalibrationSample,
    DecisionKind,
    Stance,
    from_instant,
    leaf,
    prov_sum,
    to_instant,
)
from ontoforge.contracts.provenance import ONE, ZERO, Leaf, Prod, ProvTerm, Sum
from ontoforge.vista import propose, render_with_data

from . import schemas as S
from .search import run_search
from .world import REVIEW_RECALIBRATION_THRESHOLD, ProjectError, ProjectWorld, jsonable

STATIC_DIR = Path(__file__).parent / "static"


class _NoCacheStatic(StaticFiles):
    """StaticFiles that forbids browser caching of the SPA assets.

    The UI is a vanilla ES-module graph: the browser caches each module by
    URL. When a build renames or removes a module (e.g. apps/ask.js →
    surfaces/ask.js), a cached entry-point that still imports the old path
    fails to link and the whole app boots to a blank screen — with every file
    individually returning 200. Revalidating every request makes updates
    always take effect; the assets are tiny and served locally."""

    def file_response(self, *args: Any, **kwargs: Any):  # type: ignore[override]
        resp = super().file_response(*args, **kwargs)
        resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        return resp


#: decision kinds the review queue surfaces (per spec: ER + QI judgments).
REVIEW_KINDS = ("er", "qi")
#: a deferred decision or one auto-resolved below this confidence is queued.
REVIEW_CONFIDENCE_FLOOR = 0.7
#: A genuine low-margin auto-decision is one the spine could NOT settle
#: deterministically (it escalated PAST the T0 rules / FS auto-accept-reject
#: bands into the calibrated/model tiers, ``tier >= T1``) AND resolved with an
#: UNRESOLVED conformal set (size > 1 — the conformal predictor never collapsed
#: to a singleton at level alpha) below this ceiling. These carry honest
#: residual uncertainty even though they auto-decided, so the human-in-the-loop
#: queue surfaces them — clearly labeled ``review_reason='low-margin'`` — rather
#: than pretending the estate was uncertain when it was not. On a clean estate
#: where every decision clears a deterministic band this band is simply empty.
REVIEW_LOW_MARGIN_CEILING = 0.95
ESCALATED_TIER = 1  # contracts.Tier.T1 — first tier past the deterministic bands


def _kind_of(decision_id: str) -> str:
    """Decision kind = the decision_id prefix ('er:a||b' -> 'er', 'qi-x' -> 'qi')."""
    return re.split(r"[:\-/]", decision_id, maxsplit=1)[0].lower()


def _review_reason(
    deferred: bool, quarantined: bool, confidence: float, tier: int, conformal_set: list[str]
) -> Optional[str]:
    """Why a decision belongs in the review queue, or None to skip it.

    The flywheel is HONEST: it surfaces only decisions that carry genuine
    residual uncertainty — never fabricated doubt over a clean auto-decision.
    Precedence:

    * ``deferred``    — tiers exhausted, the spine refused to auto-decide;
    * ``quarantined`` — budget fail-close, also no auto-decision;
    * ``low-confidence`` — auto-resolved but below ``REVIEW_CONFIDENCE_FLOOR``;
    * ``low-margin``  — escalated PAST the deterministic T0/FS bands
      (``tier >= ESCALATED_TIER``) and resolved with an UNRESOLVED conformal
      set (size > 1) below ``REVIEW_LOW_MARGIN_CEILING``. The spine had to
      deliberate and the conformal predictor never collapsed to a singleton,
      so the answer is real but low-margin and worth a human glance.
    """
    if deferred:
        return "deferred"
    if quarantined:
        return "quarantined"
    if confidence < REVIEW_CONFIDENCE_FLOOR:
        return "low-confidence"
    if (
        tier >= ESCALATED_TIER
        and len(conformal_set) > 1
        and confidence < REVIEW_LOW_MARGIN_CEILING
    ):
        return "low-margin"
    return None


def _parse_stance(raw: str) -> Stance:
    """``stance`` query param -> temporal Stance.

    'current' (default)               -> the open-interval read
    'as_of:<ISO-8601 timestamp>'      -> what we now believe held at that time
    """
    s = (raw or "current").strip()
    if s == "current":
        return Stance()
    if s.startswith("as_of:"):
        ts = s[len("as_of:"):]
        try:
            dt = datetime.fromisoformat(ts)
        except ValueError as exc:
            raise HTTPException(
                status_code=422, detail=f"bad as_of timestamp (ISO 8601 expected): {ts!r}"
            ) from exc
        return Stance(kind="as_of", valid_at=to_instant(dt))
    raise HTTPException(
        status_code=422,
        detail=f"bad stance {raw!r} — use 'current' or 'as_of:<ISO-8601 timestamp>'",
    )


def _stance_label(stance: Stance) -> str:
    """The normalized echo of the stance the card was read under."""
    if stance.kind == "current":
        return "current"
    return f"as_of:{from_instant(stance.valid_at).isoformat()}"  # type: ignore[arg-type]


def _term_tree(term: ProvTerm, ledger: Any) -> dict[str, Any]:
    """contracts term walk -> nested JSON of sums/products/leaf atoms."""
    if isinstance(term, Leaf):
        atom = ledger.get_atom(term.atom_id)
        return {
            "kind": "atom",
            "atom_id": term.atom_id,
            "uri": atom.uri if atom else None,
            "value": jsonable(atom.value) if atom else None,
        }
    if term == ZERO:
        return {"kind": "zero"}
    if term == ONE:
        return {"kind": "one"}
    if isinstance(term, Sum):
        return {"kind": "sum", "terms": [_term_tree(t, ledger) for t in term.terms]}
    if isinstance(term, Prod):
        return {"kind": "product", "terms": [_term_tree(t, ledger) for t in term.terms]}
    raise TypeError(f"not a ProvTerm: {term!r}")  # pragma: no cover


def _extract_csv_response(payload: dict[str, Any]) -> StreamingResponse:
    """Stream an /api/extract result as a CSV download (header + rows)."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(payload["columns"])
    for row in payload["rows"]:
        writer.writerow(["" if v is None else v for v in row])
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="extract.csv"'},
    )


def create_app(project: Path | str) -> FastAPI:
    """Build the FastAPI app over one project directory."""
    world = ProjectWorld(Path(project))
    app = FastAPI(
        title="OntoForge",
        description="Induced ontologies, bitemporal entities, provenance-grounded answers.",
        version="0.1.0",
    )
    app.state.world = world

    # The UI is served same-origin; CORS admits localhost dev servers only.
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
        allow_methods=["*"],
        allow_headers=["*"],
    )

    def fail(exc: ProjectError) -> HTTPException:
        return HTTPException(status_code=409, detail=str(exc))

    # ------------------------------------------------------------ status

    @app.get("/api/status", response_model=S.StatusOut)
    async def api_status() -> S.StatusOut:
        try:
            cfg = world.config
        except ProjectError as exc:
            raise fail(exc) from exc
        state = world.state()
        out = S.StatusOut(
            project=str(world.project),
            estate=str(cfg.get("estate", "?")),
            limit=state.get("limit"),
            stages=list(state.get("stages", [])),
            ledger_exists=world.ledger_path.is_file(),
            materialized=state.get("materialized"),
        )
        if not out.ledger_exists:
            return out
        with world.lock:
            conn = world.ledger.connection
            (out.atoms,) = conn.execute("SELECT COUNT(*) FROM atom").fetchone()
            for tier, n, deferred, quarantined in conn.execute(
                "SELECT tier, COUNT(*), SUM(deferred_to_human), SUM(quarantined) "
                "FROM decision GROUP BY tier ORDER BY tier"
            ).fetchall():
                out.decisions_by_tier[str(tier)] = S.TierCount(
                    count=n, deferred=deferred or 0, quarantined=quarantined or 0
                )
            kind_counts: Counter[str] = Counter()
            for decision_id, n in conn.execute(
                "SELECT decision_id, COUNT(*) FROM decision GROUP BY decision_id"
            ).fetchall():
                kind_counts[_kind_of(str(decision_id))] += n
            out.decisions_by_kind = dict(sorted(kind_counts.items()))
            out.artifacts = {
                str(kind): n
                for kind, n in conn.execute(
                    "SELECT kind, COUNT(*) FROM artifact GROUP BY kind ORDER BY kind"
                ).fetchall()
            }
            out.cost_tokens = world.ledger.total_cost_tokens()
        return out

    @app.post("/api/reload", response_model=S.ReloadOut)
    async def api_reload() -> S.ReloadOut:
        world.reload()
        return S.ReloadOut(reloaded=True)

    # ---------------------------------------------------------- ontology

    def _class_out(c: Any) -> S.ClassOut:
        return S.ClassOut(
            uri=c.uri,
            name=c.name,
            parents=list(c.parents),
            properties=[
                S.PropertyOut(
                    uri=p.uri,
                    name=p.name,
                    datatype=p.datatype.value,
                    is_link=p.is_link,
                    range_class=p.range_class,
                    unit=p.unit,
                    dimension=list(p.dimension.exps) if p.dimension is not None else None,
                    cardinality=p.cardinality,
                    functional=p.functional,
                    synonyms=list(p.synonyms),
                    definition=p.definition,
                )
                for p in c.properties
            ],
            confidence=float(c.confidence),
            is_event=bool(c.is_event),
            definition=c.definition,
            n_shapes=len(c.shapes),
        )

    @app.get("/api/ontology", response_model=S.OntologyOut)
    async def api_ontology() -> S.OntologyOut:
        try:
            with world.lock:
                onto = world.ontology
        except ProjectError as exc:
            raise fail(exc) from exc
        classes = [_class_out(c) for _, c in sorted(onto.classes.items(), key=lambda kv: kv[1].name)]
        edges = [
            S.EdgeOut(source=c.uri, link=p.name, target=p.range_class)
            for c, p in onto.link_properties()
            if p.range_class
        ]
        edges.sort(key=lambda e: (e.source, e.link, e.target))
        return S.OntologyOut(version=onto.version, classes=classes, edges=edges)

    @app.get("/api/ontology/class/{uri:path}", response_model=S.ClassOut)
    async def api_ontology_class(uri: str) -> S.ClassOut:
        try:
            with world.lock:
                onto = world.ontology
        except ProjectError as exc:
            raise fail(exc) from exc
        c = onto.get(uri)
        if c is None:
            raise HTTPException(status_code=404, detail=f"unknown class uri: {uri}")
        return _class_out(c)

    # -------------------------------------------------------------- atlas

    def _atlas_or_404() -> dict[str, Any]:
        atlas = world.read_atlas()
        if atlas is None:
            raise HTTPException(
                status_code=404,
                detail=(
                    "atlas not built — run: "
                    f"python -m ontoforge.pipeline.atlas {world.project}"
                ),
            )
        return atlas

    @app.get("/api/atlas", response_model=S.AtlasOut)
    async def api_atlas() -> S.AtlasOut:
        """The connection atlas (<project>/atlas.json): islands of
        confirmed-connected classes plus every tiered join arc with its
        evidence. 404 until built; /api/reload drops the cache."""
        return S.AtlasOut(**_atlas_or_404())

    @app.get("/api/atlas/link", response_model=S.AtlasLinksOut)
    async def api_atlas_link(src: str, dst: str) -> S.AtlasLinksOut:
        """Every atlas arc between two class URIs (either direction), with
        full evidence — the evidence-card deep link."""
        atlas = _atlas_or_404()
        links = [
            lk
            for lk in atlas.get("links", [])
            if (lk.get("src_class") == src and lk.get("dst_class") == dst)
            or (lk.get("src_class") == dst and lk.get("dst_class") == src)
        ]
        return S.AtlasLinksOut(
            src=src, dst=dst, links=[S.AtlasLink(**lk) for lk in links]
        )

    # ------------------------------------------------------------- search

    @app.get("/api/search", response_model=S.SearchOut)
    async def api_search(q: str = "", limit: int = 20) -> S.SearchOut:
        """Federated search (the frozen Cmd+K contract): classes, entities,
        properties, saved questions, and the static app registry, interleaved
        by score. Each source degrades independently — a project without a
        ledger still searches its apps and ontology."""
        query = q.strip()
        if not query:
            return S.SearchOut(results=[])
        with world.lock:
            try:
                ontology = world.ontology
            except ProjectError:
                ontology = None
            try:
                index = world.search_index
            except ProjectError:
                index = None
            try:
                questions = world.recent_questions()
            except ProjectError:
                questions = []
            results = run_search(
                query, limit, ontology=ontology, index=index, questions=questions
            )
        return S.SearchOut(results=[S.SearchResult(**r) for r in results])

    # ---------------------------------------------------------- workspace

    @app.get("/api/workspace")
    async def api_workspace_get() -> Any:
        """The persisted window-layout blob ({} when never saved)."""
        return world.read_workspace()

    @app.put("/api/workspace")
    async def api_workspace_put(request: Request) -> Any:
        """Persist an arbitrary JSON blob atomically; echoes what was stored."""
        try:
            blob = json.loads(await request.body() or b"null")
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=f"body is not JSON: {exc}") from exc
        world.write_workspace(blob)
        return blob

    # ------------------------------------------------------------- export

    @app.post("/api/export", response_model=S.ExportBundleOut)
    async def api_export(body: Optional[S.ExportIn] = None) -> S.ExportBundleOut:
        """Run amber.snapshot into <project>/exports/<n>/ (or body.out_dir,
        resolved under the project) and summarize the verified-by-construction
        bundle."""
        try:
            summary = world.export_bundle((body.out_dir if body else None) or None)
        except ProjectError as exc:
            raise fail(exc) from exc
        except AmberError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return S.ExportBundleOut(**summary)

    @app.get("/api/exports", response_model=S.ExportsOut)
    async def api_exports() -> S.ExportsOut:
        return S.ExportsOut(
            exports=[S.ExportBundleOut(**b) for b in world.list_exports()]
        )

    # --------------------------------------------------------------- ask

    @app.post("/api/ask", response_model=S.AskOut)
    async def api_ask(body: S.AskIn) -> S.AskOut:
        try:
            payload, cached = world.ask(body.question)
        except ProjectError as exc:
            raise fail(exc) from exc
        return S.AskOut(**payload, cached=cached)

    @app.post("/api/ask/clarify", response_model=S.AskOut)
    async def api_ask_clarify(body: S.ClarifyIn) -> S.AskOut:
        try:
            payload = world.clarify(body.question, body.choice)
        except ProjectError as exc:
            raise fail(exc) from exc
        return S.AskOut(**payload, cached=False)

    # ----------------------------------------------------------- entities

    # NOTE: registered BEFORE the greedy /api/entities/{uri:path} card route —
    # Starlette matches in declaration order and entity uris contain slashes.
    @app.get("/api/entities/{uri:path}/neighbors", response_model=S.NeighborsOut)
    async def api_entity_neighbors(uri: str) -> S.NeighborsOut:
        """Current-stance link neighborhood for the inspector's graph view."""
        try:
            links = world.neighbors(uri)
        except ProjectError as exc:
            raise fail(exc) from exc
        if links is None:
            raise HTTPException(status_code=404, detail=f"unknown entity uri: {uri}")
        return S.NeighborsOut(links=[S.NeighborLink(**link) for link in links])

    @app.get("/api/entities/{uri:path}", response_model=S.EntityOut)
    async def api_entity(uri: str, stance: str = "current") -> S.EntityOut:
        """Entity property card under a temporal stance + full per-property
        history — the time-travel read (HEARTH §4.4)."""
        st = _parse_stance(stance)
        try:
            payload = world.entity(uri, st)
        except ProjectError as exc:
            raise fail(exc) from exc
        if payload is None:
            raise HTTPException(status_code=404, detail=f"unknown entity uri: {uri}")
        return S.EntityOut(**payload, stance=_stance_label(st))

    # ------------------------------------------------- atoms & provenance

    @app.get("/api/atoms/{atom_id}", response_model=S.AtomOut)
    async def api_atom(atom_id: str) -> S.AtomOut:
        try:
            with world.lock:
                atom = world.ledger.get_atom(atom_id)
        except ProjectError as exc:
            raise fail(exc) from exc
        if atom is None:
            raise HTTPException(status_code=404, detail=f"unknown atom_id: {atom_id}")
        return S.AtomOut(atom_id=atom.atom_id, uri=atom.uri, value=jsonable(atom.value))

    @app.get("/api/provenance/{prov_ref}", response_model=S.ProvenanceOut)
    async def api_provenance(prov_ref: str) -> S.ProvenanceOut:
        try:
            with world.lock:
                ledger = world.ledger
                try:
                    term = ledger.resolve(prov_ref)
                except KeyError as exc:
                    raise HTTPException(
                        status_code=404, detail=f"unknown prov_ref: {prov_ref}"
                    ) from exc
                atoms = ledger.valuate_ref(prov_ref, "citations")
                tree = _term_tree(term, ledger)
        except ProjectError as exc:
            raise fail(exc) from exc
        return S.ProvenanceOut(prov_ref=prov_ref, n_atoms=len(atoms), tree=S.ProvNode(**tree))

    # ------------------------------------------------------------ review

    def _verdict_payloads(conn: Any) -> list[dict[str, Any]]:
        out = []
        for (payload,) in conn.execute(
            "SELECT payload FROM artifact WHERE kind = 'review-verdict' ORDER BY seq"
        ).fetchall():
            try:
                out.append(json.loads(payload))
            except (TypeError, ValueError):
                continue
        return out

    @app.get("/api/review", response_model=S.ReviewOut)
    async def api_review() -> S.ReviewOut:
        try:
            with world.lock:
                conn = world.ledger.connection
                verdicts = _verdict_payloads(conn)
                reviewed_ids = {v.get("decision_id") for v in verdicts}
                verdict_tally = Counter(str(v.get("kind", "?")) for v in verdicts)
                recal_tally: Counter[str] = Counter()
                for (payload,) in conn.execute(
                    "SELECT payload FROM artifact WHERE kind = 'recalibration'"
                ).fetchall():
                    try:
                        recal_tally[str(json.loads(payload).get("kind", "?"))] += 1
                    except (TypeError, ValueError):
                        continue

                items: list[S.ReviewItem] = []
                seen: set[str] = set()
                rows = conn.execute(
                    "SELECT decision_id, outcome, confidence, conformal_set, tier, "
                    "deferred_to_human, quarantined, rationale, prov_atoms, created_at "
                    "FROM decision ORDER BY seq DESC"
                ).fetchall()
                for did, outcome, conf, cset, tier, deferred, quarantined, rationale, patoms, ts in rows:
                    kind = _kind_of(str(did))
                    if kind not in REVIEW_KINDS or did in seen:
                        continue
                    seen.add(did)
                    if did in reviewed_ids:
                        continue
                    conf_set = list(json.loads(cset))
                    reason = _review_reason(
                        bool(deferred), bool(quarantined), float(conf), int(tier), conf_set
                    )
                    if reason is None:
                        continue
                    items.append(
                        S.ReviewItem(
                            decision_id=str(did),
                            kind=kind,
                            outcome=str(outcome),
                            confidence=float(conf),
                            conformal_set=conf_set,
                            tier=int(tier),
                            deferred_to_human=bool(deferred),
                            quarantined=bool(quarantined),
                            review_reason=reason,
                            rationale=str(rationale),
                            prov_atoms=list(json.loads(patoms)),
                            created_at=str(ts),
                        )
                    )

                artifacts: list[S.ReviewArtifact] = []
                for aid, payload, prov_ref, ts in conn.execute(
                    "SELECT artifact_id, payload, prov_ref, created_at FROM artifact "
                    "WHERE kind = 'review' ORDER BY seq DESC"
                ).fetchall():
                    try:
                        parsed: Any = json.loads(payload)
                    except (TypeError, ValueError):
                        parsed = payload
                    artifacts.append(
                        S.ReviewArtifact(
                            artifact_id=str(aid), payload=parsed, prov_ref=str(prov_ref),
                            created_at=str(ts),
                        )
                    )
        except ProjectError as exc:
            raise fail(exc) from exc
        return S.ReviewOut(
            items=items,
            artifacts=artifacts,
            verdicts=dict(sorted(verdict_tally.items())),
            recalibrations=dict(sorted(recal_tally.items())),
            threshold=REVIEW_RECALIBRATION_THRESHOLD,
        )

    @app.post("/api/review/{decision_id:path}", response_model=S.VerdictOut)
    async def api_review_verdict(decision_id: str, body: S.VerdictIn) -> S.VerdictOut:
        try:
            with world.lock:
                ledger = world.ledger
                conn = ledger.connection
                row = conn.execute(
                    "SELECT outcome, confidence, conformal_set FROM decision "
                    "WHERE decision_id = ? ORDER BY seq DESC LIMIT 1",
                    (decision_id,),
                ).fetchone()
                if row is None:
                    raise HTTPException(
                        status_code=404, detail=f"unknown decision_id: {decision_id}"
                    )
                outcome, confidence, cset_json = str(row[0]), float(row[1]), row[2]
                candidates = [str(c) for c in json.loads(cset_json)]
                kind = _kind_of(decision_id)

                # ground truth implied by the verdict
                if body.verdict == "accept":
                    true_outcome = outcome
                else:
                    others = [c for c in candidates if c != outcome]
                    true_outcome = others[0] if others else "__rejected__"
                    if true_outcome not in candidates:
                        candidates.append(true_outcome)

                # constraint-H provenance: the verdict itself is evidence — an atom
                atom = Atom(
                    uri=f"atom://human-review/verdict/{decision_id}#verdict",
                    value=f"{body.verdict}|{body.note}",
                )
                ledger.register_atoms([atom])
                prov_ref = ledger.intern(leaf(atom.atom_id))
                payload = {
                    "decision_id": decision_id,
                    "kind": kind,
                    "verdict": body.verdict,
                    "note": body.note,
                    "outcome": outcome,
                    "true_outcome": true_outcome,
                    "candidates": candidates,
                    "predicted_confidence": confidence,
                    "atom_id": atom.atom_id,
                }
                ledger.append_artifact(
                    artifact_id=f"review-verdict:{decision_id}",
                    kind="review-verdict",
                    payload=json.dumps(payload, sort_keys=True),
                    prov_ref=prov_ref,
                )

                # §4.8 loop: recalibrate at every THRESHOLD-multiple of verdicts
                mine = [v for v in _verdict_payloads(conn) if v.get("kind") == kind]
                n = len(mine)
                recalibrated = False
                if n >= REVIEW_RECALIBRATION_THRESHOLD and n % REVIEW_RECALIBRATION_THRESHOLD == 0:
                    recalibrated = _recalibrate_kind(world, kind, mine)
                n_recal = conn.execute(
                    "SELECT COUNT(*) FROM artifact WHERE kind = 'recalibration' "
                    "AND payload LIKE ?",
                    (f'%"kind": "{kind}"%',),
                ).fetchone()[0]
        except ProjectError as exc:
            raise fail(exc) from exc
        return S.VerdictOut(
            decision_id=decision_id,
            kind=kind,
            verdict=body.verdict,
            verdicts_for_kind=n,
            threshold=REVIEW_RECALIBRATION_THRESHOLD,
            recalibrated=recalibrated,
            recalibrations_for_kind=int(n_recal),
        )

    def _recalibrate_kind(world: ProjectWorld, kind: str, verdicts: list[dict[str, Any]]) -> bool:
        """Replay a kind's verdicts as CalibrationSamples into the spine."""
        try:
            dkind = DecisionKind(kind)
        except ValueError:
            return False
        samples = [
            CalibrationSample(
                kind=dkind,
                features=(("confidence", float(v.get("predicted_confidence", 0.0))),),
                candidates=tuple(v.get("candidates", ())) or ("no", "yes"),
                true_outcome=str(v.get("true_outcome", "")),
                predicted_confidence=float(v.get("predicted_confidence", 0.0)),
            )
            for v in verdicts
        ]
        world.spine.recalibrate(dkind, samples)
        ledger = world.ledger
        atom_ids = sorted({str(v["atom_id"]) for v in verdicts if v.get("atom_id")})
        prov_ref = ledger.intern(prov_sum([leaf(a) for a in atom_ids]))
        ledger.append_artifact(
            artifact_id=f"recalibration:{kind}:{len(verdicts)}",
            kind="recalibration",
            payload=json.dumps(
                {
                    "kind": kind,
                    "n_samples": len(samples),
                    "threshold": REVIEW_RECALIBRATION_THRESHOLD,
                    "fitted": bool(world.spine.calibrator(dkind) and world.spine.calibrator(dkind).fitted),
                },
                sort_keys=True,
            ),
            prov_ref=prov_ref,
        )
        return True

    # -------------------------------------------------------- dashboards

    @app.post("/api/dashboards", response_model=S.DashboardsOut)
    async def api_dashboards(body: S.DashboardIn) -> S.DashboardsOut:
        try:
            with world.lock:
                onto = world.ontology
                dashboards = propose(body.utterance, onto, k=3)
                executor = world.oqir_executor()
                rendered = [render_with_data(d, executor) for d in dashboards]
        except ProjectError as exc:
            raise fail(exc) from exc
        return S.DashboardsOut(
            utterance=body.utterance,
            dashboards=[
                S.DashboardOut(
                    title=d.title,
                    score=float(d.score),
                    rationale=d.rationale,
                    charts=[S.ChartOut(title=c.title, vega=c.vega) for c in d.charts],
                )
                for d in rendered
            ],
        )

    @app.get("/api/dashboards", response_model=S.SavedDashboardsOut)
    async def api_dashboards_saved() -> S.SavedDashboardsOut:
        out: list[S.SavedDashboardOut] = []
        d = world.dashboards_dir
        if d.is_dir():
            for f in sorted(d.glob("*.json")):
                if f.name.endswith(".vl.json"):
                    continue
                try:
                    bundle = json.loads(f.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                charts: list[S.ChartOut] = []
                for c in bundle.get("charts", []):
                    vf = d / str(c.get("vega_file", ""))
                    if vf.is_file():
                        try:
                            charts.append(
                                S.ChartOut(
                                    title=str(c.get("title", vf.name)),
                                    vega=json.loads(vf.read_text(encoding="utf-8")),
                                )
                            )
                        except (OSError, ValueError):
                            continue
                out.append(
                    S.SavedDashboardOut(
                        file=f.name,
                        title=str(bundle.get("title", f.stem)),
                        score=bundle.get("score"),
                        rationale=str(bundle.get("rationale", "")),
                        charts=charts,
                    )
                )
        return S.SavedDashboardsOut(dashboards=out)

    # ----------------------------------------------------------- catalog

    @app.get("/api/catalog", response_model=S.CatalogOut)
    async def api_catalog() -> S.CatalogOut:
        """Every downloaded dataset (wild corpus + meridian + aviation) with
        deterministic domain + description, plus the domain histogram."""
        entries, domains = world.catalog()
        return S.CatalogOut(
            datasets=[S.CatalogDataset(**e.to_public()) for e in entries],
            domains=[S.CatalogDomain(**d) for d in domains],
        )

    # -------------------------------------------------- workspace / playground

    @app.get("/api/workspace/state", response_model=S.WorkspaceStateOut)
    async def api_workspace_state() -> S.WorkspaceStateOut:
        st = world.workspace_state()
        return S.WorkspaceStateOut(
            datasets=st["datasets"],
            built=st["built"],
            active_world=st["active_world"],
            stats=S.WorkspaceStats(**st["stats"]),
        )

    @app.post("/api/workspace/build", response_model=S.WorkspaceBuildOut)
    async def api_workspace_build(body: S.WorkspaceBuildIn) -> S.WorkspaceBuildOut:
        """Build a PLAYGROUND world from the selected datasets (cap 25) and make
        it the active world for reads once done. Returns a pollable job_id."""
        try:
            job_id = world.start_build(body.dataset_ids, body.mode)
        except ProjectError as exc:
            # a clear, actionable message (over-cap, unknown ids, empty selection)
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return S.WorkspaceBuildOut(job_id=job_id)

    @app.get("/api/workspace/build/{job_id}", response_model=S.BuildStatusOut)
    async def api_workspace_build_status(job_id: str, since: int = 0) -> S.BuildStatusOut:
        """Poll a build: status + new events since `since` (the discovery
        narrative the constellation animates)."""
        snap = world.build_status(job_id, since=since)
        if snap is None:
            raise HTTPException(status_code=404, detail=f"unknown job_id: {job_id}")
        return S.BuildStatusOut(**snap)

    # ----------------------------------------------------------- engineer

    @app.post("/api/engineer/interpret", response_model=S.InterpretOut)
    async def api_engineer_interpret(body: S.InterpretIn) -> S.InterpretOut:
        """Deterministically parse a plain-English data-engineering imperative
        into op+preview | clarification | unsupported. PREVIEW ONLY — never
        mutates. A low-coverage join is flagged/refused, not asserted."""
        try:
            out = world.interpret(body.command)
        except ProjectError as exc:
            raise fail(exc) from exc
        if out.get("unsupported"):
            return S.InterpretOut(
                unsupported=True, reason=out["reason"],
                supported_examples=out.get("supported_examples", []),
            )
        if out.get("clarification") is not None:
            return S.InterpretOut(
                clarification=out["clarification"], options=out.get("options", [])
            )
        return S.InterpretOut(
            op=S.InterpretOp(**out["op"]),
            preview=S.InterpretPreview(**out["preview"]),
        )

    @app.post("/api/engineer/apply", response_model=S.ApplyOut)
    async def api_engineer_apply(body: S.ApplyIn) -> S.ApplyOut:
        """Apply a previewed op via the real TEMPER/ANVIL/ER machinery; returns
        atlas_delta + an exact undo_token. Spine-gated ops may DEFER (ok=False,
        deferred=True) — sent to review, never force-applied."""
        try:
            out = world.engineer_apply(body.op)
        except ProjectError as exc:
            raise fail(exc) from exc
        return S.ApplyOut(
            ok=out["ok"],
            deferred=out.get("deferred", False),
            blocked=out.get("blocked", False),
            human_summary=out.get("human_summary", ""),
            new_stats=out.get("new_stats", {}),
            atlas_delta=S.AtlasDelta(**out.get("atlas_delta", {})),
            undo_token=out.get("undo_token"),
            gate=out.get("gate"),
        )

    @app.post("/api/engineer/undo", response_model=S.UndoOut)
    async def api_engineer_undo(body: S.UndoIn) -> S.UndoOut:
        """Undo a prior apply via the TEMPER inverse (exact)."""
        try:
            out = world.engineer_undo(body.undo_token)
        except ProjectError as exc:
            raise fail(exc) from exc
        return S.UndoOut(
            ok=out["ok"], human_summary=out.get("human_summary", ""),
            new_stats=out.get("new_stats", {}),
        )

    # ------------------------------------------------------------- extract

    def _run_extract(body: S.ExtractIn) -> dict[str, Any]:
        try:
            return world.extract(
                body.type_uri,
                [f.model_dump() for f in body.filters],
                list(body.columns),
                int(body.limit),
            )
        except ProjectError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/extract", response_model=S.ExtractOut)
    async def api_extract(body: S.ExtractIn, format: str = "") -> Any:
        """Filtered entity rows + per-cell citations for a class. ``?format=csv``
        streams a CSV download instead of JSON."""
        payload = _run_extract(body)
        if format.lower() == "csv":
            return _extract_csv_response(payload)
        return S.ExtractOut(
            columns=payload["columns"],
            rows=payload["rows"],
            citations=[S.ExtractCitation(**c) for c in payload["citations"]],
        )

    # ----------------------------------------------------------- the SPA

    app.mount("/static", _NoCacheStatic(directory=STATIC_DIR), name="static")

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        # never let a browser serve a stale shell against a fresh build — an
        # ES-module graph that imports a renamed/removed module fails silently
        # (blank app). Revalidate every load; the assets are tiny and local.
        return FileResponse(
            STATIC_DIR / "index.html",
            headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
        )

    return app


def run_server(project: Path | str, host: str = "127.0.0.1", port: int = 8765) -> None:
    """Run the OntoForge web app via uvicorn (the CLI `serve` entry point)."""
    import sys

    import uvicorn

    project = Path(project)
    if not (project / "config.json").is_file():
        print(f"no project at {project} — run `ontoforge init {project}` first", file=sys.stderr)
        raise SystemExit(1)
    app = create_app(project)
    print(f"OntoForge serving {project} on http://{host}:{port}")
    uvicorn.run(app, host=host, port=port, log_level="info")
