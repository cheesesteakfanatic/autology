"""Aviation hero-estate loader (whitepaper §12.4, §17.2.1, §17.4 Tier-2; AMD-0006).

Loads the committed ``fixtures/aviation`` corpus into pandas DataFrames plus
estate metadata (source ids, key columns, text columns) that downstream modules
(M3 profiler, M5 ER, M6 HEARTH, M12 LODESTONE) consume.

All columns load as strings with blanks preserved (``keep_default_na=False``):
the FAA layout's trailing-space padding and blank "permissible" fields are
*documented warts*, not noise to be cleaned at load time — cleaning is ANVIL's
job (§17.2.1).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from .gold import load_competency_questions as _load_cq
from .gold import load_gold_ontology as _load_gold

ESTATE_NAME = "aviation"

#: per-table source metadata: stable source id, primary-key columns, and which
#: columns are long-form text (structured<->unstructured join surface).
TABLES: dict[str, dict[str, Any]] = {
    "faa_master": {
        "source_id": "faa_registry",
        "file": "faa_master.csv",
        "key_columns": ["N-NUMBER", "SERIAL NUMBER"],
        "text_columns": [],
        "kind": "structured",
    },
    "faa_acftref": {
        "source_id": "faa_registry",
        "file": "faa_acftref.csv",
        "key_columns": ["CODE"],
        "text_columns": [],
        "kind": "structured",
    },
    "asrs_reports": {
        "source_id": "asrs",
        "file": "asrs_reports.csv",
        "key_columns": ["ACN"],
        "text_columns": ["NARRATIVE", "SYNOPSIS"],
        "kind": "semi_structured",
    },
    "ntsb_events": {
        "source_id": "ntsb",
        "file": "ntsb_events.csv",
        "key_columns": ["EV_ID"],
        "text_columns": ["NARR_CAUSE"],
        "kind": "structured",
    },
    "maintenance_erp": {
        "source_id": "erp",
        "file": "maintenance_erp.csv",
        "key_columns": ["WORK_ORDER_ID"],
        "text_columns": [],
        "kind": "structured",
    },
}

KEY_SEP = "|"


def default_fixtures_dir() -> Path:
    """Repo-relative default (editable install): <repo>/fixtures/aviation."""
    return Path(__file__).resolve().parents[3] / "fixtures" / "aviation"


def row_key(table: str, row: Any) -> str:
    """Canonical row key: key-column values stripped of FAA padding, '|'-joined.

    This is the row coordinate used by gold citations (competency questions)
    and by ``gold/er_gold_pairs.csv``.
    """
    cols = TABLES[table]["key_columns"]
    return KEY_SEP.join(str(row[c]).strip() for c in cols)


def load_estate(fixtures_dir: str | Path | None = None) -> dict[str, Any]:
    """Load all estate tables and metadata.

    Returns ``{"name", "tables": {table: DataFrame}, "metadata": {...}}``.
    """
    base = Path(fixtures_dir) if fixtures_dir is not None else default_fixtures_dir()
    tables: dict[str, pd.DataFrame] = {}
    for name, meta in TABLES.items():
        tables[name] = pd.read_csv(
            base / meta["file"], dtype=str, keep_default_na=False, encoding="utf-8"
        )
    metadata = {
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
    }
    return {"name": ESTATE_NAME, "tables": tables, "metadata": metadata}


def load_gold_ontology(fixtures_dir: str | Path | None = None):
    base = Path(fixtures_dir) if fixtures_dir is not None else default_fixtures_dir()
    return _load_gold(base / "gold" / "mini_ontology.json")


def load_competency_questions(fixtures_dir: str | Path | None = None) -> dict[str, Any]:
    base = Path(fixtures_dir) if fixtures_dir is not None else default_fixtures_dir()
    return _load_cq(base / "gold" / "competency_questions.yaml")


def load_er_gold_pairs(fixtures_dir: str | Path | None = None) -> pd.DataFrame:
    base = Path(fixtures_dir) if fixtures_dir is not None else default_fixtures_dir()
    return pd.read_csv(
        base / "gold" / "er_gold_pairs.csv", dtype=str, keep_default_na=False
    )
