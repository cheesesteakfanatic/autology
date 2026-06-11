"""M6 — HEARTH: the Provenance-Anchored Bi-temporal Entity Store (whitepaper §4).

Public surface:
- Hearth                      — the store: commit/read/scan/history, links +
                                traverse, Actions, export, DuckDB views.
- SetProperty/Link/Unlink/CreateObject — typed Action ops (§4.3.2).
- import_canonical / export_canonical / canonical_state — portability (§4.2(P)).
- errors: HearthError, CommitRejected, ActionValidationError, PortabilityError.
"""

from .actions import (
    HUMAN_EDIT_KIND,
    HUMAN_RANK,
    ActionReceipt,
    CreateObject,
    Link,
    Op,
    SetProperty,
    Unlink,
    validate_op,
)
from .errors import ActionValidationError, CommitRejected, HearthError, PortabilityError
from .links import LinkStore, link_visible
from .portability import canonical_state, export_canonical, import_canonical
from .read import history, read, scan, scan_duckdb
from .store import CELL_SCHEMA, LINK_SCHEMA, Hearth, supersedes, survivorship_key

__all__ = [
    "Hearth",
    "SetProperty",
    "Link",
    "Unlink",
    "CreateObject",
    "Op",
    "ActionReceipt",
    "validate_op",
    "HUMAN_EDIT_KIND",
    "HUMAN_RANK",
    "LinkStore",
    "link_visible",
    "read",
    "scan",
    "scan_duckdb",
    "history",
    "supersedes",
    "survivorship_key",
    "CELL_SCHEMA",
    "LINK_SCHEMA",
    "export_canonical",
    "import_canonical",
    "canonical_state",
    "HearthError",
    "CommitRejected",
    "ActionValidationError",
    "PortabilityError",
]
