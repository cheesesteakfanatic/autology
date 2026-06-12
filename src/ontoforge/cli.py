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


def _open_ledger(project: Path, cfg: dict[str, Any]):
    from ontoforge.ledger import SqliteLedger

    return SqliteLedger(str(project / cfg["ledger"]))


def _source_csv(project: Path, cfg: dict[str, Any], table: str, limit: Optional[int]) -> Path:
    """The CSV the connector reads: the fixture itself, or a deterministic
    head-N subsample written once into the project (preserves warts verbatim)."""
    from ontoforge.estates.aviation import TABLES

    src = Path(cfg["fixtures_dir"]) / TABLES[table]["file"]
    if limit is None:
        return src
    sub_dir = project / "subsample"
    sub_dir.mkdir(exist_ok=True)
    dst = sub_dir / f"{limit}_{src.name}"
    if not dst.exists():
        lines = src.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
        dst.write_text("".join(lines[: limit + 1]), encoding="utf-8")  # header + N rows
    return dst


def _load_tables(project: Path, cfg: dict[str, Any], state: dict[str, Any]):
    """All estate tables as wart-preserving string DataFrames (estate rules),
    respecting the project's active --limit."""
    import pandas as pd

    from ontoforge.estates.aviation import TABLES

    limit = state.get("limit")
    tables = {}
    for name in TABLES:
        path = _source_csv(project, cfg, name, limit)
        tables[name] = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8")
    return tables


def _estate_dict(project: Path, cfg: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    from ontoforge.estates.aviation import ESTATE_NAME, KEY_SEP, TABLES

    base = Path(cfg["fixtures_dir"])
    return {
        "name": ESTATE_NAME,
        "tables": _load_tables(project, cfg, state),
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


def _profiles_and_inds(project: Path, cfg: dict[str, Any], state: dict[str, Any]):
    from ontoforge.estates.aviation import TABLES
    from ontoforge.profiling import discover_inds, profile_table

    tables = _load_tables(project, cfg, state)
    profiles = [
        profile_table(df, TABLES[name]["source_id"], name) for name, df in tables.items()
    ]
    inds = discover_inds(tables)
    return tables, profiles, inds


# -------------------------------------------------------------------- commands


@app.command()
def init(
    project_dir: Path = typer.Argument(DEFAULT_PROJECT, help="Project directory to create."),
    fixtures: Optional[Path] = typer.Option(None, help="Estate fixtures dir (default: bundled aviation)."),
) -> None:
    """Create the project layout + config.json."""
    from ontoforge.estates.aviation import default_fixtures_dir

    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / "dashboards").mkdir(exist_ok=True)
    cfg = {
        "estate": "aviation",
        "fixtures_dir": str(fixtures if fixtures is not None else default_fixtures_dir()),
        "ledger": "ledger.sqlite",
        "hearth_root": "hearth",
        "raw_root": "raw",
    }
    _config_path(project_dir).write_text(json.dumps(cfg, indent=1, sort_keys=True), encoding="utf-8")
    if not _state_path(project_dir).is_file():
        _write_state(project_dir, {"limit": None, "cdc": {}, "stages": []})
    console.print(f"[green]initialized project at {project_dir}[/] (estate: {cfg['estate']})")


@app.command()
def ingest(
    project: Path = _PROJECT_OPT,
    limit: Optional[int] = typer.Option(
        None, "--limit", help="Subsample: first N rows per table (sticky across commands)."
    ),
) -> None:
    """CDC-pull the estate CSVs into the ledger + RAW Parquet mirror (M1 x M0)."""
    from ontoforge.cdc import CsvConnector, RawMirror, ingest as cdc_ingest
    from ontoforge.estates.aviation import TABLES

    cfg = _load_config(project)
    state = _read_state(project)
    if limit is not None:
        state["limit"] = limit
    effective_limit = state.get("limit")

    ledger = _open_ledger(project, cfg)
    mirror = RawMirror(project / cfg["raw_root"])
    out = Table(title="CDC ingest" + (f" (limit={effective_limit})" if effective_limit else ""))
    for col in ("table", "source", "cycle", "inserts", "updates", "deletes", "atoms registered"):
        out.add_column(col)
    total_deltas = 0
    try:
        for name, meta in TABLES.items():
            connector = CsvConnector(
                source_id=meta["source_id"],
                path=_source_csv(project, cfg, name, effective_limit),
                key_columns=meta["key_columns"],
                object_name=name,
            )
            batch, new_state = cdc_ingest(connector, ledger, state["cdc"].get(name), mirror=mirror)
            kinds = Counter(d.kind for d in batch.deltas)
            registered = kinds["insert"] + kinds["update"]
            total_deltas += len(batch.deltas)
            state["cdc"][name] = new_state
            out.add_row(
                name, meta["source_id"], str(batch.cycle),
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
        result = Strata(ledger=ledger).induce(profiles, inds)
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
    try:
        from ontoforge.estates.aviation import load_gold_ontology

        return load_gold_ontology(cfg["fixtures_dir"])
    except Exception:
        return None


@app.command()
def resolve(project: Path = _PROJECT_OPT) -> None:
    """ER cascade over the estate mentions (M5); saves resolved.json."""
    from ontoforge.er import ERCascade, extract_mentions, load_gold, pairwise_prf

    cfg = _load_config(project)
    state = _read_state(project)
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


MATERIALIZED_ONTOLOGY_FILE = "ontology.materialized.json"


@app.command()
def materialize(project: Path = _PROJECT_OPT) -> None:
    """Commit the FULL estate into HEARTH with constraint-H provenance (M6).

    Materializes under the frozen GOLD ontology (whitepaper §11.3 de-risking
    slice) via the same world builder the M12 competency suite proves.
    """
    from ontoforge.hearth import Hearth
    from ontoforge.lodestone.worldbuild import build_estate_world, extend_gold_ontology
    from ontoforge.vista._pipeline import save_ontology

    cfg = _load_config(project)
    state = _read_state(project)
    gold = _gold_ontology(cfg)
    if gold is None:
        console.print("[red]gold ontology unavailable — cannot materialize the estate[/]")
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
            "artifact; the STRATA swap-in is the documented next phase"
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


def _drive_lodestone(lodestone: Any, question: str, project: Path, cfg: dict[str, Any]) -> Any:
    """Best-effort adapter over the M12 surface (built in parallel): try the
    documented entry points in order. Raises if none fits."""
    # Answer under the SAME ontology the world was materialized with (gold per
    # §11.3) when state.json says so; otherwise fall back to the induced one.
    onto = None
    state = _read_state(project)
    materialized = state.get("materialized") or {}
    if materialized.get("ontology"):
        mat_path = project / materialized.get("ontology_file", MATERIALIZED_ONTOLOGY_FILE)
        if mat_path.is_file():
            from ontoforge.vista._pipeline import load_ontology

            onto = load_ontology(mat_path)
    if onto is None:
        onto_path = project / "ontology.json"
        if onto_path.is_file():
            from ontoforge.vista._pipeline import load_ontology

            onto = load_ontology(onto_path)
    if onto is None:
        onto = _gold_ontology(cfg)

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

    _load_config(project)  # validate the project exists
    fn = getattr(amber, "snapshot", None)
    if fn is None:
        # the package exists (M14 lands in parallel) but exposes no entry point yet
        console.print("[yellow]AMBER not yet available[/]")
        raise typer.Exit(code=0)
    try:
        result = None
        for args in ((project, out_dir), (str(project), str(out_dir)), (out_dir,)):
            try:
                result = fn(*args)
                break
            except TypeError:
                continue
        else:
            raise RuntimeError("no compatible amber.snapshot() signature")
        console.print(f"[green]AMBER bundle written to {out_dir}[/] {result if result is not None else ''}")
    except Exception as exc:
        console.print(f"[yellow]AMBER present but snapshot failed: {exc}[/]")
        raise typer.Exit(code=0)


@app.command()
def status(project: Path = _PROJECT_OPT) -> None:
    """Project state summary from the ledger + state.json."""
    cfg = _load_config(project)
    state = _read_state(project)
    console.print(f"project: {project}  estate: {cfg['estate']}  limit: {state.get('limit')}")
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


if __name__ == "__main__":
    app()
