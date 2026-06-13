"""Generic estate discovery: ANY directory of CSV/Parquet files becomes an
OntoForge estate (pipeline step 0).

- every ``*.csv`` / ``*.parquet`` directly under ``source_dir`` is one table;
- table name = filename stem; ``source_id`` = slugified stem;
- CSVs load wart-preserving (``dtype=str, keep_default_na=False``) exactly like
  the aviation estate loader — cleaning is the conformance layer's job;
- key columns come from M3 candidate-key detection (profile first), picked by
  the same deterministic chooser STRATA uses (minimal arity, then
  identifier-likeness, then lexicographic; composite keys allowed). Tables
  with no exact candidate key get ``key_columns=[]``: the CDC connector's
  documented content-addressed row-key fallback covers ingestion, and
  :func:`table_row_keys` mirrors it bit-for-bit for materialization
  coordinates.

The returned estate dict is shape-compatible with
``ontoforge.estates.aviation.load_estate`` (``{"name", "tables", "metadata"}``)
plus a ``"profiles"`` cache so downstream stages do not re-profile.
"""

from __future__ import annotations

import math
import multiprocessing
import os
import re
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any, Optional

import pandas as pd

from ontoforge.cdc.base import hash64
from ontoforge.contracts import Datatype, TableProfile, value_repr
from ontoforge.profiling import profile_table
from ontoforge.strata.candidates import choose_key

__all__ = ["ESTATE_NAME", "KEY_SEP", "discover_sources", "load_table", "slugify", "table_row_keys"]

ESTATE_NAME = "generic"
KEY_SEP = "|"

_SLUG_RE = re.compile(r"[^a-z0-9]+")
SOURCE_SUFFIXES = (".csv", ".parquet")


def slugify(name: str) -> str:
    """Stable lowercase identifier from a filename stem."""
    s = _SLUG_RE.sub("_", name.lower()).strip("_")
    return s or "table"


def load_table(path: Path, limit: Optional[int] = None) -> pd.DataFrame:
    """One source file -> wart-preserving string DataFrame."""
    if path.suffix.lower() == ".csv":
        df = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8")
    else:
        raw = pd.read_parquet(path)
        df = raw.apply(lambda col: col.map(lambda v: "" if pd.isna(v) else str(v)))
        df.columns = [str(c) for c in raw.columns]
    if limit is not None:
        df = df.head(limit)
    return df


# profile_table's FD/key search is a TANE-class lattice ascent: with the default
# max_lhs=3 it visits attribute sets up to size 4, so per-table cost scales like
# C(cols, 4) x rows — a corpus's profiling bill concentrates in its few widest
# tables. The proxy below only gates serial-vs-pool and orders submissions; it
# never changes what is computed.
_LATTICE_NODE_SIZE = 4
# ~ a few seconds of serial profiling work: below this a process pool's
# spawn+import overhead costs more than it saves.
_PARALLEL_COST_THRESHOLD = 2_000_000


def _lattice_cost(df: pd.DataFrame) -> int:
    c = df.shape[1]
    return math.comb(c, min(c, _LATTICE_NODE_SIZE)) * max(len(df), 1)


def _profile_tables(
    jobs: list[tuple[pd.DataFrame, str, str]], max_workers: Optional[int]
) -> list[TableProfile]:
    """``profile_table`` over independent (df, source_id, name) jobs, in job order.

    Results are byte-identical to the serial loop: each profile is a pure
    function of its own table, workers receive the exact DataFrames the estate
    keeps, and results are reassembled by job index. The spawn context avoids
    fork-while-threaded hazards at the price of a one-time worker import,
    amortized across the whole corpus.
    """
    # the wall clock can never beat the single longest table, so the pool only
    # pays when the OTHER tables' work (sum minus max) amortizes worker spawn
    costs = [_lattice_cost(df) for df, _, _ in jobs]
    serial = (
        max_workers in (0, 1)
        or len(jobs) < 2
        or (
            max_workers is None
            and (
                (os.cpu_count() or 1) < 2
                or sum(costs) - max(costs) < _PARALLEL_COST_THRESHOLD
            )
        )
    )
    if serial:
        return [profile_table(df, source_id, name) for df, source_id, name in jobs]

    # widest-first submission: the single most expensive table bounds the wall
    # clock, so it must start immediately, not sit behind cheap tables.
    order = sorted(range(len(jobs)), key=lambda i: (-costs[i], i))
    results: list[Optional[TableProfile]] = [None] * len(jobs)
    ctx = multiprocessing.get_context("spawn")
    with ProcessPoolExecutor(max_workers=max_workers, mp_context=ctx) as ex:
        futures = {i: ex.submit(profile_table, *jobs[i]) for i in order}
        for i, fut in futures.items():
            results[i] = fut.result()
    return results  # type: ignore[return-value]  # every slot filled above


def discover_sources(
    source_dir: str | Path, *, limit: Optional[int] = None, max_workers: Optional[int] = None
) -> dict[str, Any]:
    """Discover every tabular source under ``source_dir`` -> estate dict.

    ``limit`` keeps only the first N rows of each table (the CLI's sticky
    ``--limit`` subsample semantics).

    ``max_workers`` controls per-table profiling parallelism: ``None`` (the
    default) auto-selects — a process pool when the corpus's estimated
    FD-lattice bill amortizes worker startup, serial otherwise; ``0``/``1``
    force serial; ``n >= 2`` forces a pool of ``n`` processes. The estate dict
    is identical either way.
    """
    base = Path(source_dir)
    files = sorted(
        (p for p in base.iterdir() if p.is_file() and p.suffix.lower() in SOURCE_SUFFIXES),
        key=lambda p: (p.stem.lower(), p.suffix),
    )
    if not files:
        raise FileNotFoundError(f"no *.csv / *.parquet sources found in {base}")

    loaded: list[tuple[str, Path, pd.DataFrame]] = []  # (name, path, df) in file order
    for path in files:
        name = path.stem
        if any(name == n for n, _, _ in loaded):  # foo.csv + foo.parquet: keep both, suffix the later
            name = f"{name}_{path.suffix.lstrip('.')}"
        loaded.append((name, path, load_table(path, limit)))

    jobs = [(df, slugify(path.stem), name) for name, path, df in loaded]
    profiled = _profile_tables(jobs, max_workers)

    tables: dict[str, pd.DataFrame] = {}
    meta_tables: dict[str, dict[str, Any]] = {}
    profiles: dict[str, TableProfile] = {}
    for (name, path, df), tp in zip(loaded, profiled):
        source_id = slugify(path.stem)
        key = choose_key(tp)
        text_columns = [
            c for c, cp in tp.columns.items() if cp.inferred_type is Datatype.TEXT
        ]
        tables[name] = df
        profiles[name] = tp
        meta_tables[name] = {
            "source_id": source_id,
            "file": str(path),
            "format": path.suffix.lstrip(".").lower(),
            "key_columns": list(key) if key is not None else [],
            "text_columns": text_columns,
            "kind": "structured",
        }

    metadata = {
        "estate": ESTATE_NAME,
        "source_dir": str(base),
        "key_separator": KEY_SEP,
        "tables": meta_tables,
    }
    return {"name": ESTATE_NAME, "tables": tables, "metadata": metadata, "profiles": profiles}


def table_row_keys(df: pd.DataFrame, key_columns: list[str] | tuple[str, ...]) -> list[str]:
    """Row coordinates for one table, in row order.

    Keyed tables follow the estate row-key convention the gold artifacts
    document (key-column values stripped of padding, '|'-joined; duplicate
    keys disambiguated ``~n`` like the CDC connector); keyless tables get the
    CDC connector's documented content-addressed fallback, bit-for-bit.
    """
    columns = [str(c) for c in df.columns]
    seen: dict[str, int] = {}
    out: list[str] = []
    for row in df.to_dict("records"):
        if key_columns:
            base = KEY_SEP.join(str(row.get(c, "")).strip() for c in key_columns)
        else:
            base = "row-" + hash64(*(f"{c}\x1e{value_repr(row.get(c))}" for c in columns))
        n = seen.get(base, 0) + 1
        seen[base] = n
        out.append(base if n == 1 else f"{base}~{n}")
    return out
