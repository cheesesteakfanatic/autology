"""M6 — HEARTH error types."""

from __future__ import annotations


class HearthError(Exception):
    """Base class for all HEARTH errors."""


class CommitRejected(HearthError):
    """A commit violated a store invariant (constraint H, interval validity,
    rank reservation, system-time monotonicity, ...). Nothing was written."""


class ActionValidationError(HearthError):
    """A human Action failed SHACL-style pre-validation (whitepaper §4.3.2)."""


class PortabilityError(HearthError):
    """Export/import bundle is malformed or fails hash verification (§7 precursor)."""
