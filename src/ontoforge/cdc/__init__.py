"""M1 — CDC & Ingestion (whitepaper §11.2 M1).

Connectors pull JSON-able state -> (contracts.DeltaBatch, new_state); the ingest
driver registers atoms into anything implementing register_atoms and mirrors
snapshots to the RAW layer. See README.md in this directory for design notes.
"""

from .base import STATE_FORMAT, AtomRegistrar, Connector, JSONState, read_text_robust
from .docs import DocConnector, normalize_text, split_paragraphs
from .ingest import RawMirror, ingest
from .largecsv import LargeCsvConnector
from .objectstore import ObjectStoreConnector
from .sql import SqlConnector
from .tabular import CsvConnector, ParquetConnector, parse_csv_text

# Note: SqlConnector / ObjectStoreConnector lazy-import their optional drivers
# (sqlalchemy / fsspec) inside pull(); importing the *classes* here pulls in only
# stdlib + pyarrow, so ``import ontoforge.cdc`` never requires the connectors extra.

__all__ = [
    "STATE_FORMAT",
    "AtomRegistrar",
    "Connector",
    "CsvConnector",
    "DocConnector",
    "JSONState",
    "LargeCsvConnector",
    "ObjectStoreConnector",
    "ParquetConnector",
    "RawMirror",
    "SqlConnector",
    "ingest",
    "normalize_text",
    "parse_csv_text",
    "read_text_robust",
    "split_paragraphs",
]
