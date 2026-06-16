"""ontoforge — the product CLI wiring the real pipeline end-to-end (§11.3).

State persists in a project directory (default ``./onto_project``):

    config.json     estate + paths (fixtures dir, ledger, hearth root, RAW root)
    state.json      CDC connector states, the active --limit, completed stages
    ledger.sqlite   the M0 SqliteLedger (atoms, provenance, decisions, costs)
    raw/            content-addressed RAW Parquet mirror (M1)
    hearth/         canonical bitemporal store (M6)
    ontology.json   induced O^(t), serialized in the documented plain-JSON
                    dialect (see ontoforge.vista._pipeline — NOT the gold
                    dialect: induced URIs/confidences round-trip losslessly)
    resolved.json   ER clusters + mention->URI map (M5)
    dashboards/     VISTA proposals (Vega-Lite v5 JSON)

M12 LODESTONE and M14 AMBER are imported LAZILY inside their commands and the
CLI degrades gracefully when they have not landed yet.
"""

from __future__ import annotations

import importlib
import json
from collections import Counter
from pathlib import Path
from typing import Any, Optional

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table
from rich.tree import Tree

app = typer.Typer(
    name="ontoforge",
    help="OntoForge: autonomous semantic data platform — induced ontologies, "
    "bitemporal entities, provenance-grounded answers.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()

DEFAULT_PROJECT = "onto_project"
_PROJECT_OPT = typer.Option(DEFAULT_PROJECT, "--project", "-p", help="Project directory.")


# ----------------------------------------------------------------- project IO


def _config_path(project: Path) -> Path:
    return project / "config.json"


def _load_config(project: Path) -> dict[str, Any]:
    cfg = _config_path(project)
    if not cfg.is_file():
        console.print(f"[red]no project at {project} — run `ontoforge init {project}` first[/]")
        raise typer.Exit(code=1)
    return json.loads(cfg.read_text(encoding="utf-8"))


def _state_path(project: Path) -> Path:
    return project / "state.json"


def _read_state(project: Path) -> dict[str, Any]:
    p = _state_path(project)
    if p.is_file():
        return json.loads(p.read_text(encoding="utf-8"))
    return {"limit": None, "cdc": {}, "stages": []}


def _write_state(project: Path, state: dict[str, Any]) -> None:
    _state_path(project).write_text(json.dumps(state, indent=1, sort_keys=True), encoding="utf-8")


def _mark_stage(state: dict[str, Any], stage: str) -> None:
    if stage not in state["stages"]:
        state["stages"].append(stage)


#: in-process pipeline memo, ACTIVE ONLY inside `ontoforge demo` (one process
#: runs every stage; recomputing discovery/profiling/induction per stage would
#: only repeat identical deterministic work). Individual commands keep their
#: stand-alone behavior: the memo is None outside demo.
_DEMO_MEMO: Optional[dict[Any, Any]] = None


def _open_ledger(project: Path, cfg: dict[str, Any]):
    from ontoforge.ledger import SqliteLedger

    ledger = SqliteLedger(str(project / cfg["ledger"]))
    if _DEMO_MEMO is not None:
        # demo projects trade crash-durability for startup speed (the corpus
        # and every artifact regenerate deterministically from one command)
        ledger.connection.execute("PRAGMA synchronous = OFF")
        ledger.connection.execute("PRAGMA journal_mode = MEMORY")
    return ledger


def _source_csv(project: Path, cfg: dict[str, Any], table: str, limit: Optional[int]) -> Path:
    """The CSV the connector reads: the fixture itself, or a deterministic
    head-N subsample written once into the project (preserves warts verbatim)."""
    from ontoforge.estates.aviation import TABLES

    src = Path(cfg["fixtures_dir"]) / TABLES[table]["file"]
    return _subsampled(project, src, limit)


def _subsampled(project: Path, src: Path, limit: Optional[int]) -> Path:
    """Head-N subsample of one source file, written once into the project."""
    if limit is None:
        return src
    sub_dir = project / "subsample"
    sub_dir.mkdir(exist_ok=True)
    dst = sub_dir / f"{limit}_{src.name}"
    if not dst.exists():
        if src.suffix.lower() == ".csv":
            lines = src.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
            dst.write_text("".join(lines[: limit + 1]), encoding="utf-8")  # header + N rows
        else:  # parquet
            import pyarrow.parquet as pq

            pq.write_table(pq.read_table(src).slice(0, limit), dst)
    return dst


def _load_tables(project: Path, cfg: dict[str, Any], state: dict[str, Any]):
    """All estate tables as wart-preserving string DataFrames (estate rules),
    respecting the project's active --limit."""
    return _load_estate(project, cfg, state)["tables"]


def _load_estate(project: Path, cfg: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    """The estate dict for ANY estate kind: the bundled aviation fixtures, or
    a generic directory discovered by the pipeline package."""
    limit = state.get("limit")
    if cfg["estate"] == "generic":
        from ontoforge.pipeline import discover_sources

        key = ("estate", cfg["source_dir"], limit)
        if _DEMO_MEMO is not None and key in _DEMO_MEMO:
            return _DEMO_MEMO[key]
        estate = discover_sources(Path(cfg["source_dir"]), limit=limit)
        if _DEMO_MEMO is not None:
            _DEMO_MEMO[key] = estate
        return estate
    return _aviation_estate(project, cfg, state)


def _aviation_estate(project: Path, cfg: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    import pandas as pd

    from ontoforge.estates.aviation import ESTATE_NAME, KEY_SEP, TABLES

    limit = state.get("limit")
    base = Path(cfg["fixtures_dir"])
    tables = {}
    for name in TABLES:
        path = _source_csv(project, cfg, name, limit)
        tables[name] = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8")
    return {
        "name": ESTATE_NAME,
        "tables": tables,
        "metadata": {
            "estate": ESTATE_NAME,
            "fixtures_dir": str(base),
            "key_separator": KEY_SEP,
            "tables": {
                name: {k: v for k, v in meta.items() if k != "file"}
                for name, meta in TABLES.items()
            },
            "gold": {
                "ontology": str(base / "gold" / "mini_ontology.json"),
                "competency_questions": str(base / "gold" / "competency_questions.yaml"),
                "er_pairs": str(base / "gold" / "er_gold_pairs.csv"),
            },
        },
    }


def _estate_dict(project: Path, cfg: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    return _load_estate(project, cfg, state)


def _profiles_and_inds(project: Path, cfg: dict[str, Any], state: dict[str, Any]):
    from ontoforge.pipeline import profile_estate

    estate = _load_estate(project, cfg, state)
    if _DEMO_MEMO is not None and "profiles" in _DEMO_MEMO:
        profiles, inds = _DEMO_MEMO["profiles"]
    else:
        profiles, inds = profile_estate(estate)
        if _DEMO_MEMO is not None:
            _DEMO_MEMO["profiles"] = (profiles, inds)
    return estate["tables"], profiles, inds


def _induced_artifacts(estate: dict[str, Any], ledger):
    """Generic induction with the demo memo (one induction per demo run)."""
    from ontoforge.pipeline import induce_estate

    if _DEMO_MEMO is not None and "artifacts" in _DEMO_MEMO:
        return _DEMO_MEMO["artifacts"]
    artifacts = induce_estate(estate, ledger)
    if _DEMO_MEMO is not None:
        _DEMO_MEMO["artifacts"] = artifacts
    return artifacts


# -------------------------------------------------------------------- commands


@app.command()
def init(
    project_dir: Path = typer.Argument(DEFAULT_PROJECT, help="Project directory to create."),
    fixtures: Optional[Path] = typer.Option(None, help="Estate fixtures dir (default: bundled aviation)."),
    source: Optional[Path] = typer.Option(
        None,
        "--source",
        help="ANY directory of *.csv / *.parquet files -> a GENERIC estate "
        "(tables discovered, keys profiled, ontology induced).",
    ),
    db_url: Optional[str] = typer.Option(
        None,
        "--db-url",
        help="SQLAlchemy connection URL for a SQL source (Postgres/MySQL/SQLite); "
        "pair with --db-table. Requires the 'connectors' extra "
        "(pip install 'ontoforge[connectors]').",
    ),
    db_table: Optional[str] = typer.Option(
        None, "--db-table", help="Table to ingest from the --db-url source (repeatable)."
    ),
    db_key: Optional[list[str]] = typer.Option(
        None, "--db-key", help="Key column(s) for the SQL table; omitted -> introspected PK."
    ),
    db_schema: Optional[str] = typer.Option(
        None, "--db-schema", help="Optional database schema/namespace for --db-table."
    ),
    object_uri: Optional[str] = typer.Option(
        None,
        "--object-uri",
        help="Object-store URI of a CSV/Parquet object (s3://, gcs://, file://, or a "
        "bare path). Requires the 'connectors' extra for remote schemes "
        "(local file:// works offline).",
    ),
    object_key: Optional[list[str]] = typer.Option(
        None, "--object-key", help="Key column(s) for the object-store source."
    ),
    object_fmt: Optional[str] = typer.Option(
        None, "--object-fmt", help="Force the object format: 'csv' or 'parquet' (else inferred)."
    ),
) -> None:
    """Create the project layout + config.json.

    A project sources its estate one of three ways: the bundled aviation
    fixtures (default), a GENERIC directory of files (``--source``), or one or
    more OPEN-SHELL CONNECTORS (``--db-url``/``--db-table`` for SQL, ``--object-uri``
    for an S3/GCS/local object). Connectors are deterministic snapshot-diff pulls
    behind the same Connector protocol as the file connectors; remote drivers ship
    in the optional ``connectors`` extra and are lazy-imported only at ingest time.
    """
    # `init` is also called directly as a Python function (by `demo` and tests),
    # where unsupplied typer.Option params arrive as their OptionInfo sentinel
    # rather than None. Normalize those to None so the connector flags only fire
    # when a real value was passed on the command line.
    db_url = _opt(db_url)
    db_table = _opt(db_table)
    db_key = _opt(db_key)
    db_schema = _opt(db_schema)
    object_uri = _opt(object_uri)
    object_key = _opt(object_key)
    object_fmt = _opt(object_fmt)

    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / "dashboards").mkdir(exist_ok=True)
    cfg: dict[str, Any] = {
        "ledger": "ledger.sqlite",
        "hearth_root": "hearth",
        "raw_root": "raw",
    }
    connectors = _build_connector_specs(
        db_url=db_url,
        db_table=db_table,
        db_key=db_key,
        db_schema=db_schema,
        object_uri=object_uri,
        object_key=object_key,
        object_fmt=object_fmt,
    )
    if connectors and source is not None:
        console.print("[red]use --source OR connector flags (--db-url/--object-uri), not both[/]")
        raise typer.Exit(code=1)
    if connectors:
        cfg["estate"] = "connectors"
        cfg["connectors"] = connectors
    elif source is not None:
        if not source.is_dir():
            console.print(f"[red]--source {source} is not a directory[/]")
            raise typer.Exit(code=1)
        cfg["estate"] = "generic"
        cfg["source_dir"] = str(source.resolve())
    else:
        from ontoforge.estates.aviation import default_fixtures_dir

        cfg["estate"] = "aviation"
        cfg["fixtures_dir"] = str(fixtures if fixtures is not None else default_fixtures_dir())
    _config_path(project_dir).write_text(json.dumps(cfg, indent=1, sort_keys=True), encoding="utf-8")
    if not _state_path(project_dir).is_file():
        _write_state(project_dir, {"limit": None, "cdc": {}, "stages": []})
    msg = f"[green]initialized project at {project_dir}[/] (estate: {cfg['estate']})"
    if connectors:
        msg += f" — {len(connectors)} connector source(s); run `ontoforge ingest -p {project_dir}`"
    # soft_wrap: a long project path must not split `estate: <kind>` across lines.
    console.print(msg, soft_wrap=True)


def _build_connector_specs(
    *,
    db_url: Optional[str],
    db_table: Optional[str],
    db_key: Optional[list[str]],
    db_schema: Optional[str],
    object_uri: Optional[str],
    object_key: Optional[list[str]],
    object_fmt: Optional[str],
) -> list[dict[str, Any]]:
    """Validate the connector flags and freeze them into JSON specs for state.json.

    The specs are pure data (the connector objects are constructed at ingest time
    so the optional drivers stay lazy); raises a clear typer.Exit on misuse.
    """
    specs: list[dict[str, Any]] = []
    if db_url is not None or db_table is not None:
        if not db_url or not db_table:
            console.print("[red]--db-url and --db-table must be given together[/]")
            raise typer.Exit(code=1)
        specs.append(
            {
                "kind": "sql",
                "source_id": _slug(db_table),
                "table": db_table,
                "url": db_url,
                "key_columns": list(db_key or []),
                "schema": db_schema,
            }
        )
    if object_uri is not None:
        fmt = (object_fmt or "").lower() or None
        if fmt is not None and fmt not in ("csv", "parquet"):
            console.print(f"[red]--object-fmt must be 'csv' or 'parquet' (got {object_fmt!r})[/]")
            raise typer.Exit(code=1)
        specs.append(
            {
                "kind": "object",
                "source_id": _slug(Path(object_uri).stem or "object"),
                "uri": object_uri,
                "key_columns": list(object_key or []),
                "fmt": fmt,
            }
        )
    return specs


def _opt(value: Any) -> Any:
    """Coerce a typer.Option sentinel to None.

    ``init`` is invoked both via Typer (real values) and directly as a Python
    function by ``demo``/tests (where unsupplied options arrive as their
    ``OptionInfo`` default object). Treat those sentinels as 'not provided'.
    """
    return None if isinstance(value, typer.models.OptionInfo) else value


def _slug(text: str) -> str:
    """Lowercase identifier from a free string (matches the generic-estate slugger)."""
    from ontoforge.pipeline.discover import slugify

    return slugify(text)


def _connector_from_spec(spec: dict[str, Any]):
    """Build the live Connector for one frozen spec (drivers lazy-imported in pull())."""
    if spec["kind"] == "sql":
        from ontoforge.cdc import SqlConnector

        return SqlConnector(
            source_id=spec["source_id"],
            url=spec["url"],
            table=spec["table"],
            key_columns=spec["key_columns"] or None,
            schema=spec.get("schema"),
        )
    if spec["kind"] == "object":
        from ontoforge.cdc import ObjectStoreConnector

        return ObjectStoreConnector(
            source_id=spec["source_id"],
            uri=spec["uri"],
            key_columns=spec.get("key_columns") or (),
            fmt=spec.get("fmt"),
        )
    raise ValueError(f"unknown connector kind {spec['kind']!r}")


@app.command()
def ingest(
    project: Path = _PROJECT_OPT,
    limit: Optional[int] = typer.Option(
        None, "--limit", help="Subsample: first N rows per table (sticky across commands)."
    ),
) -> None:
    """CDC-pull the estate sources into the ledger + RAW Parquet mirror (M1 x M0)."""
    from ontoforge.cdc import CsvConnector, ParquetConnector, RawMirror, ingest as cdc_ingest

    cfg = _load_config(project)
    state = _read_state(project)
    if limit is not None:
        state["limit"] = limit
    effective_limit = state.get("limit")

    if cfg["estate"] == "connectors":
        connectors = []
        for spec in cfg.get("connectors", []):
            name = spec.get("table") or Path(spec.get("uri", spec["source_id"])).stem
            try:
                conn = _connector_from_spec(spec)
            except Exception as exc:  # construction-time misuse (bad URI/format)
                console.print(f"[red]connector {name!r}:[/] {escape(str(exc))}")
                raise typer.Exit(code=1)
            connectors.append((name, spec["source_id"], conn))
    elif cfg["estate"] == "generic":
        estate = _load_estate(project, cfg, state)
        connectors = []
        for name, meta in estate["metadata"]["tables"].items():
            path = _subsampled(project, Path(meta["file"]), effective_limit)
            cls = ParquetConnector if meta.get("format") == "parquet" else CsvConnector
            connectors.append(
                (name, meta["source_id"],
                 cls(source_id=meta["source_id"], path=path,
                     key_columns=meta["key_columns"], object_name=name))
            )
    else:
        from ontoforge.estates.aviation import TABLES

        connectors = [
            (name, meta["source_id"],
             CsvConnector(
                 source_id=meta["source_id"],
                 path=_source_csv(project, cfg, name, effective_limit),
                 key_columns=meta["key_columns"],
                 object_name=name,
             ))
            for name, meta in TABLES.items()
        ]

    ledger = _open_ledger(project, cfg)
    mirror = RawMirror(project / cfg["raw_root"])
    out = Table(title="CDC ingest" + (f" (limit={effective_limit})" if effective_limit else ""))
    for col in ("table", "source", "cycle", "inserts", "updates", "deletes", "atoms registered"):
        out.add_column(col)
    total_deltas = 0
    try:
        for name, source_id, connector in connectors:
            try:
                batch, new_state = cdc_ingest(connector, ledger, state["cdc"].get(name), mirror=mirror)
            except ImportError as exc:
                # an optional connector driver (sqlalchemy / fsspec) is not installed:
                # the connector constructs fine but pull() names the missing extra.
                # escape: the hint contains "[connectors]", which rich markup would eat.
                console.print(f"[red]connector {name!r}:[/] {escape(str(exc))}")
                console.print(
                    "[yellow]install the connectors extra:[/] "
                    + escape("pip install 'ontoforge[connectors]'")
                    + " (or `uv sync --all-extras`)"
                )
                raise typer.Exit(code=1)
            kinds = Counter(d.kind for d in batch.deltas)
            registered = kinds["insert"] + kinds["update"]
            total_deltas += len(batch.deltas)
            state["cdc"][name] = new_state
            out.add_row(
                name, source_id, str(batch.cycle),
                str(kinds["insert"]), str(kinds["update"]), str(kinds["delete"]), str(registered),
            )
        (n_atoms,) = ledger.connection.execute("SELECT COUNT(*) FROM atom").fetchone()
    finally:
        ledger.close()
    console.print(out)
    console.print(f"total deltas this cycle: {total_deltas}; atoms in ledger: {n_atoms}")
    if total_deltas == 0:
        console.print("[green]no changes detected (CDC steady state)[/]")
    _mark_stage(state, "ingest")
    _write_state(project, state)


@app.command()
def plan(
    project: Path = _PROJECT_OPT,
    budget: int = typer.Option(..., "--budget", "-b", help="Total rows to keep across all tables."),
    hypothesis: Optional[Path] = typer.Option(
        None,
        "--hypothesis",
        help="Optional ontology-hypothesis JSON: "
        '{"join_keys": [[table, col], ...], "key_columns": {table: [col, ...]}}.',
    ),
    out: Optional[Path] = typer.Option(
        None,
        "--out",
        help="Write the governed subset (JSON) here; default <project>/plan_subset.json.",
    ),
) -> None:
    """PLAN mode: pull a governed, budget-bounded SUBSET that preserves schema
    shape + joinability (v2.1 mode one).

    The cheap entry into a new estate — instead of ingesting everything, it keeps a
    smart, budget-bounded slice (cardinality boundaries, distribution edges, and
    enough cross-table key overlap that relationship discovery still fires) and
    reports exactly what it kept and why. PLAN profiles the raw tables itself, so it
    runs standalone before ingest. Keyless, zero-network, deterministic.
    """
    from ontoforge.pipeline import OntologyHypothesis, plan_subset

    cfg = _load_config(project)
    state = _read_state(project)
    if cfg["estate"] == "connectors":
        console.print(
            "[red]PLAN profiles local files (the cheap entry before pulling everything). "
            "A connector source (SQL/object-store) has nothing local to plan over — "
            "run `ontoforge ingest` to pull it first.[/]"
        )
        raise typer.Exit(code=1)
    tables = _load_tables(project, cfg, state)

    hyp: Optional[OntologyHypothesis] = None
    if hypothesis is not None:
        if not hypothesis.is_file():
            console.print(f"[red]--hypothesis {hypothesis} not found[/]")
            raise typer.Exit(code=1)
        raw = json.loads(hypothesis.read_text(encoding="utf-8"))
        hyp = OntologyHypothesis(
            join_keys=tuple(tuple(pair) for pair in raw.get("join_keys", [])),
            key_columns={t: tuple(cols) for t, cols in raw.get("key_columns", {}).items()},
        )

    subset, report = plan_subset(tables, budget=budget, hypothesis=hyp)

    tbl = Table(title=f"PLAN subset (budget {budget}, seed {report.seed})")
    for col in ("table", "kept/total", "kept %", "key columns (min cov)", "edge columns", "top reasons"):
        tbl.add_column(col)
    for tp in report.tables:
        keys = "+".join(tp.candidate_keys[0]) if tp.candidate_keys else "-"
        min_cov = min(tp.key_coverage.values()) if tp.key_coverage else 0.0
        reasons = ", ".join(
            f"{k}:{v}" for k, v in sorted(tp.reasons.items(), key=lambda kv: -kv[1])[:3]
        )
        tbl.add_row(
            tp.table,
            f"{tp.kept_rows}/{tp.total_rows}",
            f"{tp.kept_fraction:.0%}",
            f"{keys} ({min_cov:.2f})" if tp.candidate_keys else "-",
            ", ".join(tp.edge_columns[:4]) + ("…" if len(tp.edge_columns) > 4 else "") or "-",
            reasons or "-",
        )
    console.print(tbl)

    if report.overlaps:
        jt = Table(title="joinability (cross-table key overlap)")
        for col in ("join", "full", "kept", "achievable", "coverage", "survives"):
            jt.add_column(col)
        for o in report.overlaps:
            jt.add_row(
                f"{o.lhs_table}.{o.lhs_column} -> {o.rhs_table}.{o.rhs_column}",
                str(o.full_overlap), str(o.kept_overlap), str(o.achievable),
                f"{o.coverage:.2f}", "yes" if o.survives else "[red]NO[/]",
            )
        console.print(jt)

    console.print(
        f"total kept: {report.total_kept}/{budget}  "
        f"within budget: {'yes' if report.within_budget else '[red]NO[/]'}  "
        f"joinability ok: {'yes' if report.joinability_ok() else '[red]NO[/]'}"
    )
    if not report.joinability_ok():
        severed = ", ".join(
            f"{o.lhs_table}.{o.lhs_column}->{o.rhs_table}.{o.rhs_column}"
            for o in report.severed_joins()
        )
        console.print(f"[yellow]warning: budget severed join(s): {severed}[/]")

    out_path = out if out is not None else project / "plan_subset.json"
    out_path.write_text(json.dumps(subset, indent=1, sort_keys=True), encoding="utf-8")
    console.print(f"-> {out_path}")
    _mark_stage(state, "plan")
    _write_state(project, state)


@app.command()
def profile(project: Path = _PROJECT_OPT) -> None:
    """Profile all estate tables: keys, FDs, INDs, units (M3)."""
    cfg = _load_config(project)
    state = _read_state(project)
    _, profiles, inds = _profiles_and_inds(project, cfg, state)
    inds_by_table = Counter(i.lhs_table for i in inds)

    out = Table(title="table profiles (M3)")
    for col in ("table", "rows", "columns", "candidate keys", "FDs", "INDs (lhs)", "units found"):
        out.add_column(col)
    for tp in profiles:
        units = sorted({c.unit for c in tp.columns.values() if c.unit})
        first_key = "+".join(tp.candidate_keys[0]) if tp.candidate_keys else "-"
        out.add_row(
            tp.table, str(tp.row_count), str(len(tp.columns)),
            f"{len(tp.candidate_keys)} (e.g. {first_key})",
            str(len(tp.fds)), str(inds_by_table[tp.table]),
            ", ".join(units) if units else "-",
        )
    console.print(out)
    console.print(f"cross-table INDs discovered: {len(inds)}")
    _mark_stage(state, "profile")
    _write_state(project, state)


@app.command()
def induce(project: Path = _PROJECT_OPT) -> None:
    """STRATA ontology induction over the profiles (M4); saves ontology.json."""
    from ontoforge.strata import Strata
    from ontoforge.vista._pipeline import match_to_gold, save_ontology

    cfg = _load_config(project)
    state = _read_state(project)
    _, profiles, inds = _profiles_and_inds(project, cfg, state)

    ledger = _open_ledger(project, cfg)
    try:
        if _DEMO_MEMO is not None and "artifacts" in _DEMO_MEMO:
            result = _DEMO_MEMO["artifacts"].strata
        else:
            result = Strata(ledger=ledger).induce(profiles, inds)
            if _DEMO_MEMO is not None:
                from ontoforge.pipeline import InducedArtifacts

                _DEMO_MEMO["artifacts"] = InducedArtifacts(
                    profiles=list(profiles), inds=list(inds), strata=result
                )
    finally:
        ledger.close()
    onto = result.ontology
    save_ontology(onto, project / "ontology.json")

    tree = Tree("[bold]induced ontology O^(t)[/]")
    nodes: dict[str, Any] = {}
    for uri in sorted(onto.classes, key=lambda u: (len(onto.ancestors(u)), onto.classes[u].name)):
        c = onto.classes[uri]
        label = f"{c.name}  [dim](conf {c.confidence:.2f}, {len(c.properties)} props)[/]"
        parent = next((p for p in c.parents if p in nodes), None)
        nodes[uri] = (nodes[parent] if parent else tree).add(label)
    console.print(tree)
    console.print(f"classes: {len(onto.classes)} -> {project / 'ontology.json'}")

    gold = _gold_ontology(cfg)
    if gold is not None:
        p, r, matches = match_to_gold(onto, gold)
        console.print(
            f"vs gold: precision {p:.2f}, recall {r:.2f}, matched {len(matches)}/{len(gold.classes)} "
            f"gold classes (CLI token matcher, not the M4 gate harness)"
        )
    _mark_stage(state, "induce")
    _write_state(project, state)


def _gold_ontology(cfg: dict[str, Any]):
    if cfg.get("estate") != "aviation":
        return None  # generic estates have no gold artifacts
    try:
        from ontoforge.estates.aviation import load_gold_ontology

        return load_gold_ontology(cfg["fixtures_dir"])
    except Exception:
        return None


@app.command()
def resolve(project: Path = _PROJECT_OPT) -> None:
    """ER over the estate (M5); saves resolved.json.

    Aviation runs the estate mention extractors; generic estates resolve the
    induced classes' cross-table identity domains through the same cascade.
    """
    cfg = _load_config(project)
    state = _read_state(project)
    if cfg["estate"] == "generic":
        _resolve_generic_cmd(project, cfg, state)
        return
    from ontoforge.er import ERCascade, extract_mentions, load_gold, pairwise_prf

    estate = _estate_dict(project, cfg, state)
    mentions = extract_mentions(estate)

    gold = None
    try:
        gold = load_gold(cfg["fixtures_dir"])
    except Exception:
        pass
    mention_ids = {m.mention_id for m in mentions}
    train = None
    if gold is not None:
        train = {
            kind: {m: e for m, e in gold.split_labels(kind, "train").items() if m in mention_ids}
            for kind in gold.labels
        }
        if sum(len(v) for v in train.values()) < 10:
            train = None  # too few present labels (heavy subsample) to calibrate on

    ledger = _open_ledger(project, cfg)
    try:
        result = ERCascade(ledger=ledger).run(mentions, train)
    finally:
        ledger.close()

    out = Table(title="entity resolution (M5)")
    for col in ("kind", "mentions", "clusters", "held-out F1"):
        out.add_column(col)
    for kind, clusters in sorted(result.clusters.items()):
        f1 = "-"
        if gold is not None and kind in gold.labels:
            labels = {
                m: e for m, e in gold.split_labels(kind, "test").items() if m in mention_ids
            }
            if labels:
                f1 = f"{pairwise_prf(result.mention_to_uri, labels)['f1']:.3f}"
        n_mentions = sum(1 for m in mentions if m.entity_kind == kind)
        out.add_row(kind, str(n_mentions), str(len(clusters)), f1)
    console.print(out)

    payload = {
        "mention_to_uri": result.mention_to_uri,
        "clusters": {
            kind: {uri: sorted(c.mention_ids) for uri, c in clusters.items()}
            for kind, clusters in result.clusters.items()
        },
    }
    (project / "resolved.json").write_text(json.dumps(payload, indent=1, sort_keys=True), encoding="utf-8")
    console.print(f"-> {project / 'resolved.json'}")
    _mark_stage(state, "resolve")
    _write_state(project, state)


def _resolve_generic_cmd(project: Path, cfg: dict[str, Any], state: dict[str, Any]) -> None:
    """Generic ER (M5): induce, recover plans, resolve every cross-table
    identity domain (name-like domains through the cascade, identifier-variant
    domains exactly); saves resolved.json."""
    from ontoforge.pipeline import build_plans, resolve_generic

    estate = _load_estate(project, cfg, state)
    ledger = _open_ledger(project, cfg)
    try:
        artifacts = _induced_artifacts(estate, ledger)
        plans = build_plans(artifacts.strata, artifacts.ontology)
        resolutions = resolve_generic(estate, artifacts, plans, ledger=ledger)
        if _DEMO_MEMO is not None:
            _DEMO_MEMO["resolutions"] = resolutions
    finally:
        ledger.close()

    class_names = {c.uri: c.name for c in artifacts.ontology.iter_classes()}
    out = Table(title="entity resolution (M5, generic identity domains)")
    for col in ("class", "method", "tables", "mentions", "clusters", "identities"):
        out.add_column(col)
    for class_uri, res in sorted(resolutions.items(), key=lambda kv: class_names.get(kv[0], kv[0])):
        out.add_row(
            class_names.get(class_uri, class_uri),
            res.method,
            ", ".join(res.domain.tables),
            str(len(res.mention_to_uri)),
            str(len(res.clusters)),
            str(len(res.value_to_uri)),
        )
    console.print(out)
    if not resolutions:
        console.print(
            "no cross-table identity domains found — single-table identities "
            "use exact-key dedupe at materialization"
        )

    payload = {
        "mention_to_uri": {
            m: u for _, res in sorted(resolutions.items()) for m, u in sorted(res.mention_to_uri.items())
        },
        "clusters": {
            class_names.get(cu, cu): {u: ids for u, ids in sorted(res.clusters.items())}
            for cu, res in sorted(resolutions.items())
        },
        "methods": {class_names.get(cu, cu): res.method for cu, res in sorted(resolutions.items())},
    }
    (project / "resolved.json").write_text(json.dumps(payload, indent=1, sort_keys=True), encoding="utf-8")
    console.print(f"-> {project / 'resolved.json'}")
    _mark_stage(state, "resolve")
    _write_state(project, state)


MATERIALIZED_ONTOLOGY_FILE = "ontology.materialized.json"


@app.command()
def materialize(
    project: Path = _PROJECT_OPT,
    ontology: Optional[str] = typer.Option(
        None,
        "--ontology",
        help="Materialize under 'gold' (aviation default, §11.3 de-risking slice) "
        "or 'induced' (the STRATA swap-in: the generic engine commits the estate "
        "from the ontology OntoForge induced; the only choice for generic estates).",
    ),
) -> None:
    """Commit the FULL estate into HEARTH with constraint-H provenance (M6).

    Aviation projects default to the frozen GOLD ontology via the proven world
    builder; ``--ontology induced`` (default for generic estates) runs the
    STRATA swap-in: the generic pipeline materializes from the INDUCED
    ontology, and `ask` answers over that world.
    """
    from ontoforge.hearth import Hearth
    from ontoforge.lodestone.worldbuild import build_estate_world, extend_gold_ontology
    from ontoforge.vista._pipeline import save_ontology

    cfg = _load_config(project)
    state = _read_state(project)
    which = ontology or ("induced" if cfg["estate"] == "generic" else "gold")
    if which not in ("gold", "induced"):
        console.print(f"[red]unknown --ontology {which!r} (expected 'gold' or 'induced')[/]")
        raise typer.Exit(code=1)
    if which == "induced":
        _materialize_induced_cmd(project, cfg, state)
        return
    gold = _gold_ontology(cfg)
    if gold is None:
        console.print(
            "[red]gold ontology unavailable — generic estates materialize from "
            "the induced ontology (`ontoforge materialize --ontology induced`)[/]"
        )
        raise typer.Exit(code=1)
    onto = extend_gold_ontology(gold)
    estate = _estate_dict(project, cfg, state)

    ledger = _open_ledger(project, cfg)
    try:
        hearth = Hearth(project / cfg["hearth_root"], ledger)
        stats = build_estate_world(estate, onto, hearth, ledger)
    finally:
        ledger.close()

    # the exact ontology the world was committed under, for `ask` to load
    save_ontology(onto, project / MATERIALIZED_ONTOLOGY_FILE)
    state["materialized"] = {
        "ontology": "gold",
        "ontology_file": MATERIALIZED_ONTOLOGY_FILE,
        "entities": stats["entities"],
        "cells": stats["cells"],
        "links": stats["links"],
    }

    out = Table(title="materialized estate (gold ontology)")
    out.add_column("class")
    out.add_column("entities")
    for cls, n in sorted(stats["classes"].items()):
        out.add_row(cls, str(n))
    console.print(out)
    console.print(
        f"[green]committed {stats['entities']} entities ({stats['cells']} cells, "
        f"{stats['links']} links) into HEARTH under the gold ontology[/]"
    )
    if (project / "ontology.json").is_file():
        console.print(
            "note: the induced ontology (ontology.json) remains an inspection "
            "artifact; rerun with --ontology induced for the STRATA swap-in"
        )
    _mark_stage(state, "materialize")
    _write_state(project, state)


def _materialize_induced_cmd(project: Path, cfg: dict[str, Any], state: dict[str, Any]) -> None:
    """The STRATA swap-in: M3/M4 induction, then the generic engine commits
    the estate into HEARTH from the INDUCED ontology (generic ER included)."""
    from ontoforge.hearth import Hearth
    from ontoforge.pipeline import materialize_induced
    from ontoforge.vista._pipeline import save_ontology

    estate = _load_estate(project, cfg, state)
    ledger = _open_ledger(project, cfg)
    try:
        hearth = Hearth(project / cfg["hearth_root"], ledger)
        artifacts = _induced_artifacts(estate, ledger)
        onto = artifacts.ontology
        stats = materialize_induced(
            estate, onto, artifacts, hearth, ledger,
            resolutions=(_DEMO_MEMO or {}).get("resolutions"),
        )
    finally:
        ledger.close()

    # the exact (enriched) ontology the world was committed under: what `ask` loads
    save_ontology(onto, project / MATERIALIZED_ONTOLOGY_FILE)
    if not (project / "ontology.json").is_file():
        save_ontology(onto, project / "ontology.json")
    state["materialized"] = {
        "ontology": "induced",
        "ontology_file": MATERIALIZED_ONTOLOGY_FILE,
        "entities": stats["entities"],
        "cells": stats["cells"],
        "links": stats["links"],
    }

    out = Table(title="materialized estate (induced ontology — STRATA swap-in)")
    out.add_column("class")
    out.add_column("entities")
    for cls, n in sorted(stats["classes"].items()):
        out.add_row(cls, str(n))
    console.print(out)
    er = stats.get("er") or {}
    if er:
        ert = Table(title="generic ER over cross-table identity domains")
        for col in ("class", "method", "clusters", "identities", "tables"):
            ert.add_column(col)
        for cls, info in sorted(er.items()):
            ert.add_row(
                cls, info["method"], str(info["clusters"]),
                str(info["identities"]), ", ".join(info["tables"]),
            )
        console.print(ert)
    console.print(
        f"[green]committed {stats['entities']} entities ({stats['cells']} cells, "
        f"{stats['links']} links) into HEARTH under the induced ontology[/]"
    )
    _mark_stage(state, "materialize")
    _write_state(project, state)


@app.command()
def ask(
    question: str = typer.Argument(..., help="Natural-language question."),
    project: Path = _PROJECT_OPT,
) -> None:
    """Answer a question via LODESTONE (M12, lazy import; degrades gracefully)."""
    try:
        lodestone = importlib.import_module("ontoforge.lodestone")
    except Exception:
        console.print("[yellow]LODESTONE not yet available[/]")
        raise typer.Exit(code=0)

    cfg = _load_config(project)
    try:
        answer = _drive_lodestone(lodestone, question, project, cfg)
    except RuntimeError:
        # the package exists (M12 lands in parallel) but exposes no entry point yet
        console.print("[yellow]LODESTONE not yet available[/]")
        raise typer.Exit(code=0)
    except Exception as exc:  # never crash the CLI on M12 API drift
        console.print(f"[yellow]LODESTONE present but could not answer: {exc}[/]")
        raise typer.Exit(code=0)
    _render_answer(answer)


def _answering_ontology(project: Path, cfg: dict[str, Any], state: dict[str, Any]):
    """The ontology queries/exports run under: the one the world was materialized
    with (gold per §11.3) when state.json says so, else induced, else gold."""
    materialized = state.get("materialized") or {}
    if materialized.get("ontology"):
        mat_path = project / materialized.get("ontology_file", MATERIALIZED_ONTOLOGY_FILE)
        if mat_path.is_file():
            from ontoforge.vista._pipeline import load_ontology

            return load_ontology(mat_path)
    onto_path = project / "ontology.json"
    if onto_path.is_file():
        from ontoforge.vista._pipeline import load_ontology

        return load_ontology(onto_path)
    return _gold_ontology(cfg)


def _drive_lodestone(lodestone: Any, question: str, project: Path, cfg: dict[str, Any]) -> Any:
    """Best-effort adapter over the M12 surface (built in parallel): try the
    documented entry points in order. Raises if none fits."""
    state = _read_state(project)
    onto = _answering_ontology(project, cfg, state)

    # Attach the project's HEARTH + ledger world when materialize has run
    # (LODESTONE abstains without one — wave-4 integration fix).
    hearth = None
    ledger = None
    hearth_dir = project / cfg.get("hearth_root", "hearth")
    if hearth_dir.is_dir():
        from ontoforge.hearth import Hearth

        ledger = _open_ledger(project, cfg)
        hearth = Hearth(hearth_dir, ledger)

    errors: list[str] = []
    try:
        for name in ("ask", "answer"):
            fn = getattr(lodestone, name, None)
            if fn is None:
                continue
            arg_variants = (
                ((question, onto, hearth, ledger), hearth is not None),
                ((question, onto), True),
                ((question,), True),
            )
            for args, enabled in arg_variants:
                if not enabled:
                    continue
                try:
                    return fn(*args)
                except TypeError as e:
                    errors.append(f"{name}/{len(args)}args: {e}")
    finally:
        if ledger is not None:
            ledger.close()
    cls = getattr(lodestone, "Lodestone", None)
    if cls is not None:
        for kwargs in ({"ontology": onto}, {}):
            try:
                return cls(**kwargs).ask(question)
            except TypeError as e:
                errors.append(f"Lodestone({kwargs}): {e}")
    raise RuntimeError("no compatible LODESTONE entry point; tried: " + "; ".join(errors or ["none found"]))


def _render_answer(answer: Any) -> None:
    if getattr(answer, "abstained", False):
        console.print(f"[yellow]ABSTAINED:[/] {getattr(answer, 'abstain_reason', '')}")
        return
    clarification = getattr(answer, "clarification", None)
    if clarification:
        console.print(f"[cyan]CLARIFICATION NEEDED:[/] {clarification}")
        for opt in getattr(answer, "clarification_options", ()):
            console.print(f"  - {opt}")
        return
    columns = list(getattr(answer, "columns", []) or [])
    rows = list(getattr(answer, "rows", []) or [])
    out = Table(title="answer")
    for c in columns or ["value"]:
        out.add_column(str(c))
    for r in rows:
        out.add_row(*(str(v) for v in r))
    console.print(out)
    citations = getattr(answer, "citations", []) or []
    n_atoms = len({a for c in citations for a in getattr(c, "atom_ids", ())})
    console.print(
        f"confidence: {getattr(answer, 'confidence', 0.0):.2f}; "
        f"citations: {len(citations)} cells over {n_atoms} atoms"
    )


@app.command()
def dashboard(
    utterance: str = typer.Argument(..., help="Vague dashboard request."),
    project: Path = _PROJECT_OPT,
) -> None:
    """VISTA top-3 dashboard proposals (M13); saves Vega-Lite JSON files."""
    from ontoforge.vista import propose
    from ontoforge.vista._pipeline import load_ontology

    cfg = _load_config(project)
    onto_path = project / "ontology.json"
    if onto_path.is_file():
        onto = load_ontology(onto_path)
        source = "induced ontology"
    else:
        onto = _gold_ontology(cfg)
        source = "gold ontology (run `ontoforge induce` to use the induced one)"
    if onto is None:
        console.print("[red]no ontology available[/]")
        raise typer.Exit(code=1)

    dashboards = propose(utterance, onto)
    out_dir = project / "dashboards"
    out_dir.mkdir(exist_ok=True)
    console.print(f"VISTA proposals for {utterance!r} over the {source}:")
    for rank, d in enumerate(dashboards, start=1):
        out = Table(title=f"#{rank}  {d.title}  (score {d.score:.2f})")
        out.add_column("chart")
        out.add_column("vega file")
        files = []
        for j, chart in enumerate(d.charts):
            fname = f"dashboard_{rank}_chart_{j}.vl.json"
            (out_dir / fname).write_text(json.dumps(chart.vega, indent=1, sort_keys=True), encoding="utf-8")
            files.append(fname)
            out.add_row(chart.title, fname)
        console.print(out)
        bundle = {
            "title": d.title,
            "score": d.score,
            "rationale": d.rationale,
            "charts": [{"title": c.title, "vega_file": f} for c, f in zip(d.charts, files)],
        }
        (out_dir / f"dashboard_{rank}.json").write_text(
            json.dumps(bundle, indent=1, sort_keys=True), encoding="utf-8"
        )
    console.print(f"saved to {out_dir}")


@app.command()
def snapshot(
    out_dir: Path = typer.Argument(..., help="Bundle output directory."),
    project: Path = _PROJECT_OPT,
) -> None:
    """Export an AMBER bundle (M14, lazy import; degrades gracefully)."""
    try:
        amber = importlib.import_module("ontoforge.amber")
    except Exception:
        console.print("[yellow]AMBER not yet available[/]")
        raise typer.Exit(code=0)

    cfg = _load_config(project)
    fn = getattr(amber, "snapshot", None)
    if fn is None:
        # the package exists (M14 lands in parallel) but exposes no entry point yet
        console.print("[yellow]AMBER not yet available[/]")
        raise typer.Exit(code=0)

    state = _read_state(project)
    hearth_dir = project / cfg.get("hearth_root", "hearth")
    if not hearth_dir.is_dir():
        console.print("[yellow]no HEARTH store yet — run `ontoforge materialize` first[/]")
        raise typer.Exit(code=0)
    onto = _answering_ontology(project, cfg, state)

    try:
        from ontoforge.hearth import Hearth

        ledger = _open_ledger(project, cfg)
        try:
            hearth = Hearth(hearth_dir, ledger)
            result = fn(out_dir, hearth, onto, ledger)
        finally:
            ledger.close()
        console.print(f"[green]AMBER bundle written to {out_dir}[/] (manifest: {result})")
    except Exception as exc:
        console.print(f"[yellow]AMBER present but snapshot failed: {exc}[/]")
        raise typer.Exit(code=0)


@app.command()
def status(project: Path = _PROJECT_OPT) -> None:
    """Project state summary from the ledger + state.json."""
    cfg = _load_config(project)
    state = _read_state(project)
    # soft_wrap: never insert hard line breaks into this header — a long project
    # path must not split `estate: <kind>` across lines (terminal handles overflow).
    console.print(
        f"project: {project}  estate: {cfg['estate']}  limit: {state.get('limit')}",
        soft_wrap=True,
    )
    console.print(f"stages completed: {', '.join(state['stages']) or '(none)'}")

    ledger_path = project / cfg["ledger"]
    if not ledger_path.is_file():
        console.print("[yellow]no ledger yet — run `ontoforge ingest`[/]")
        return
    ledger = _open_ledger(project, cfg)
    try:
        conn = ledger.connection
        (atoms,) = conn.execute("SELECT COUNT(*) FROM atom").fetchone()
        decisions = conn.execute(
            "SELECT tier, COUNT(*), SUM(deferred_to_human), SUM(quarantined) "
            "FROM decision GROUP BY tier ORDER BY tier"
        ).fetchall()
        kinds = conn.execute(
            "SELECT decision_id, COUNT(*) FROM decision GROUP BY decision_id"
        ).fetchall()
        artifacts = conn.execute(
            "SELECT kind, COUNT(*) FROM artifact GROUP BY kind ORDER BY kind"
        ).fetchall()
        cost = ledger.total_cost_tokens()
    finally:
        ledger.close()

    out = Table(title="ledger")
    out.add_column("metric")
    out.add_column("value")
    out.add_row("atoms", str(atoms))
    for tier, n, deferred, quarantined in decisions:
        out.add_row(f"decisions tier {tier}", f"{n} (deferred {deferred or 0}, quarantined {quarantined or 0})")
    kind_counts: Counter = Counter()
    for d, c in kinds:  # decision kind is the decision_id prefix (e.g. "er", "strata")
        kind_counts[str(d).split(":", 1)[0].split("/", 1)[0]] += c
    for kind, n in sorted(kind_counts.items()):
        out.add_row(f"decisions kind {kind}", str(n))
    for kind, n in artifacts:
        out.add_row(f"artifacts {kind}", str(n))
    out.add_row("cost (tokens)", str(cost))
    console.print(out)


@app.command()
def demo(
    estate: str = typer.Argument(
        ..., help="Demo estate: 'meridian' (generic engine), 'aviation', or 'wild' (real internet corpus)."
    ),
    project_dir: Path = typer.Argument(DEFAULT_PROJECT, help="Project directory to create."),
) -> None:
    """One-command demo: init -> ingest -> profile -> induce -> resolve ->
    materialize, then print how to serve the result.

    'meridian' regenerates the bundled 10-table enterprise corpus from code
    (works from an installed wheel; no fixture files needed) and runs the
    GENERIC engine over it — no estate module, no gold ontology. 'aviation'
    uses the repo's aviation fixtures (source checkout). 'wild' runs the
    generic engine over fixtures/wild — hundreds of REAL datasets snapshotted
    from the public internet (source checkout; see docs/WILD_CORPUS.md).
    """
    estate = estate.strip().lower()
    if estate not in ("meridian", "aviation", "wild"):
        console.print(
            f"[red]unknown demo estate {estate!r} (expected 'meridian', 'aviation', or 'wild')[/]"
        )
        raise typer.Exit(code=1)

    global _DEMO_MEMO
    _DEMO_MEMO = {}  # one process runs every stage: share the deterministic work
    try:
        _run_demo(estate, project_dir)
    finally:
        _DEMO_MEMO = None


def _build_demo_atlas(project_dir: Path) -> None:
    """Build <project>/atlas.json as the demo's final step, reusing the EXACT
    code path `python -m ontoforge.pipeline.atlas <project>` runs (so the demo
    project lights up GET /api/atlas and the UI Constellation instead of 404ing
    into plain star-mode). Imported and called in-process — never shelled out."""
    from ontoforge.pipeline.atlas import rebuild_for_project

    report = rebuild_for_project(project_dir)
    s = report.stats
    islands = s["components"] - s["silos"]
    console.print(
        f"[green]atlas: {islands} islands · {s['silos']} silos[/] "
        f"({s['classes']} classes, {s['confirmed']} confirmed / "
        f"{s['likely']} likely / {s['hint']} hint arcs) -> {project_dir / 'atlas.json'}"
    )


def _run_demo(estate: str, project_dir: Path) -> None:
    # init/generate + ingest + profile + induce + resolve + materialize + atlas;
    # meridian prepends a corpus-generation banner
    stages = 8 if estate == "meridian" else 7
    step = 0
    ingest_limit: Optional[int] = None

    def banner(title: str) -> None:
        nonlocal step
        step += 1
        console.rule(f"[bold cyan]demo {step}/{stages}: {title}")

    if estate == "wild":
        from ontoforge.estates import wild as wild_mod

        fixtures_dir = wild_mod.default_fixtures_dir()
        if not (fixtures_dir / wild_mod.MANIFEST_NAME).is_file():
            console.print(
                f"[red]wild corpus not found at {fixtures_dir} — the wild demo needs a source "
                "checkout (the committed fixtures/wild snapshot is not shipped in the wheel). "
                "Rebuild it with `python scripts/fetch_wild_corpus.py` (network), or use "
                "`ontoforge demo meridian`: its corpus regenerates from code.[/]"
            )
            raise typer.Exit(code=1)
        manifest = wild_mod.load_manifest(fixtures_dir)
        n_sets = manifest["stats"]["datasets_kept"]
        ingest_limit = wild_mod.DEMO_ROW_LIMIT
        banner(f"init (wild corpus: {n_sets} real internet datasets — ontology will be INDUCED)")
        init(project_dir, fixtures=None, source=fixtures_dir)
    elif estate == "meridian":
        from ontoforge.estates.meridian_gen import build_corpus

        source_dir = project_dir / "meridian_source"
        banner("generate the Meridian corpus (seed 7, byte-reproducible)")
        manifest = build_corpus(source_dir)
        console.print(
            f"10 tables, {sum(manifest['rows'].values())} rows, "
            f"{manifest['total_bytes']} bytes -> {source_dir}"
        )
        console.print(f"gold questions: {source_dir / 'gold' / 'questions.yaml'}")
        banner("init (generic estate — the ontology will be INDUCED)")
        init(project_dir, fixtures=None, source=source_dir)
    else:
        from ontoforge.estates.aviation import default_fixtures_dir

        fixtures_dir = default_fixtures_dir()
        if not fixtures_dir.is_dir():
            console.print(
                f"[red]aviation fixtures not found at {fixtures_dir} — the aviation demo "
                "needs a source checkout (fixtures are not shipped in the wheel). "
                "Use `ontoforge demo meridian` instead: its corpus regenerates from code.[/]"
            )
            raise typer.Exit(code=1)
        banner("init (aviation estate)")
        init(project_dir, fixtures=None, source=None)

    banner("ingest (CDC -> ledger + RAW mirror)")
    ingest(project=project_dir, limit=ingest_limit)
    banner("profile (keys, FDs, INDs, units)")
    profile(project=project_dir)
    banner("induce (STRATA ontology induction)")
    induce(project=project_dir)
    banner("resolve (entity resolution)")
    resolve(project=project_dir)
    banner("materialize (commit the world into HEARTH)")
    materialize(project=project_dir, ontology=None)
    banner("atlas (tier every cross-dataset connection)")
    _build_demo_atlas(project_dir)

    console.rule("[bold green]demo ready")
    if estate == "wild":
        console.print(
            "OntoForge just induced an ontology over hundreds of real internet datasets "
            "(OpenFlights, datasets-org world data, FiveThirtyEight, vega, seaborn).\n"
            "inspect what it built:"
        )
        console.print(f"  ontoforge status -p {project_dir}")
        console.print(f"  {project_dir / 'ontology.json'}  (the induced classes + cross-dataset links)")
    elif estate == "meridian":
        console.print("try, with citations on every answered cell:")
        console.print(
            f'  ontoforge ask -p {project_dir} "How many support tickets describe battery swelling?"'
        )
        console.print(
            f'  ontoforge ask -p {project_dir} "What is the annual committed spend on contract MSA-2024-0117?"'
        )
    else:
        console.print(
            f'try:\n  ontoforge ask -p {project_dir} "Which manufacturer name does the FAA aircraft '
            'reference record for the model of the aircraft registered with tail number N4669X?"'
        )
    console.print(f"\nweb app:\n  ontoforge serve -p {project_dir}")


@app.command()
def serve(
    project: Path = _PROJECT_OPT,
    host: str = typer.Option("127.0.0.1", help="Bind address."),
    port: int = typer.Option(8765, help="Port."),
) -> None:
    """Run the OntoForge web app (REST API + UI) over a project."""
    try:
        from ontoforge.server.app import run_server
    except Exception as exc:
        console.print(f"[yellow]server not available: {exc}[/]")
        raise typer.Exit(code=1)
    run_server(project, host=host, port=port)


if __name__ == "__main__":
    app()
