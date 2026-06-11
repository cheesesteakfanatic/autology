"""Bridges between the transform graph and HEARTH (M6).

Inputs: `dataframes_from_hearth` turns Hearth `scan()` pivots into the pandas
tables the orchestrator feeds DuckDB.

Outputs: `commit_dataframe_to_hearth` lands a conformed materialization in a
HEARTH layer with constraint-H provenance — one content-addressed cell atom
per (row, column), interned as a Leaf term, so every committed cell resolves
back to the exact transform-output cell that produced it.
"""

from __future__ import annotations

from typing import Mapping, Optional

import pandas as pd

from ontoforge.contracts import (
    Interval,
    Stance,
    ValueCell,
    leaf,
    make_cell_atom,
)
from ontoforge.contracts.ledger import Ledger
from ontoforge.contracts.transforms import Layer
from ontoforge.hearth import Hearth

__all__ = ["dataframes_from_hearth", "commit_dataframe_to_hearth"]


def dataframes_from_hearth(
    hearth: Hearth,
    mapping: Mapping[str, tuple[Layer, str]],
    stance: Stance = Stance(),
) -> dict[str, pd.DataFrame]:
    """table name -> DataFrame, each fed by hearth.scan(class_uri, stance)."""
    return {
        table: hearth.scan(class_uri, stance, layer=layer).to_pandas()
        for table, (layer, class_uri) in mapping.items()
    }


def commit_dataframe_to_hearth(
    hearth: Hearth,
    ledger: Ledger,
    df: pd.DataFrame,
    *,
    layer: Layer,
    class_uri: str,
    key_column: str,
    source_id: str,
    object_name: str,
    src_rank: int = 1,
    valid_from: int = 0,
    now: Optional[int] = None,
) -> int:
    """Commit each non-key column of `df` as ValueCells under
    `{class_uri}/{key}` entities. Returns the number of cells committed."""
    if key_column not in df.columns:
        raise KeyError(f"key column {key_column!r} not in output ({list(df.columns)})")
    atoms = []
    pending: list[tuple[str, str, object, str]] = []  # entity, prop, value, atom_id
    for _, row in df.iterrows():
        key = str(row[key_column])
        entity = f"{class_uri}/{key}"
        for col in df.columns:
            if col == key_column:
                continue
            value = row[col]
            if isinstance(value, float) and value != value:
                continue  # NaN: absent, not a fact
            atom = make_cell_atom(source_id, object_name, key, col, value)
            atoms.append(atom)
            pending.append((entity, col, value, atom.atom_id))
    ledger.register_atoms(atoms)
    cells = [
        ValueCell(
            entity_uri=entity,
            prop=prop,
            value=value,
            valid=Interval(valid_from),
            system=Interval(0),  # store-stamped on commit
            prov_ref=ledger.intern(leaf(atom_id)),
            src_rank=src_rank,
        )
        for entity, prop, value, atom_id in pending
    ]
    return hearth.commit(layer, class_uri, cells, now=now)
