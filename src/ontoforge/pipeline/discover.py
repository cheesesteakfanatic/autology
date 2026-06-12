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

import re
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


def discover_sources(source_dir: str | Path, *, limit: Optional[int] = None) -> dict[str, Any]:
    """Discover every tabular source under ``source_dir`` -> estate dict.

    ``limit`` keeps only the first N rows of each table (the CLI's sticky
    ``--limit`` subsample semantics).
    """
    base = Path(source_dir)
    files = sorted(
        (p for p in base.iterdir() if p.is_file() and p.suffix.lower() in SOURCE_SUFFIXES),
        key=lambda p: (p.stem.lower(), p.suffix),
    )
    if not files:
        raise FileNotFoundError(f"no *.csv / *.parquet sources found in {base}")

    tables: dict[str, pd.DataFrame] = {}
    meta_tables: dict[str, dict[str, Any]] = {}
    profiles: dict[str, TableProfile] = {}
    for path in files:
        name = path.stem
        if name in tables:  # foo.csv + foo.parquet: keep both, suffix the later
            name = f"{name}_{path.suffix.lstrip('.')}"
        df = load_table(path, limit)
        source_id = slugify(path.stem)
        tp = profile_table(df, source_id, name)
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
