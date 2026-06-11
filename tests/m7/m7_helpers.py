"""M7 test helpers: registry/orchestrator scaffolding over an in-memory ledger."""

from __future__ import annotations

import json

import pandas as pd

from ontoforge.contracts.transforms import TransformDef
from ontoforge.ledger import SqliteLedger
from ontoforge.transforms import Orchestrator, TransformRegistry


def make_stack() -> tuple[SqliteLedger, TransformRegistry, Orchestrator]:
    ledger = SqliteLedger()
    registry = TransformRegistry(ledger)
    return ledger, registry, Orchestrator(registry, ledger)


def tdef(name: str, inputs: tuple[str, ...], output: str, sql: str, **kw) -> TransformDef:
    return TransformDef(name=name, inputs=inputs, output=output, sql=sql, **kw)


def run_artifacts(ledger: SqliteLedger) -> list[dict]:
    """All RunRecords persisted in the ledger (kind 'run'), insertion order."""
    rows = ledger.connection.execute(
        "SELECT payload FROM artifact WHERE kind = 'run' ORDER BY rowid"
    ).fetchall()
    return [json.loads(r[0]) for r in rows]


def src(**cols) -> pd.DataFrame:
    return pd.DataFrame(cols)
