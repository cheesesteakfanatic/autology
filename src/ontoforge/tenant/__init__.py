"""Per-tenant pattern learning (v2.1 §1.5; CLOSED CORE).

Isolated per-tenant priors that compound efficiency within ONE engagement —
naming conventions, semantic-type habits, accepted/rejected join history — used
to NUDGE the heuristic confidence proxy of new relationship candidates.

HARD CONSTRAINT: per-tenant only, NEVER cross-tenant. See :mod:`.priors`.

CLOSED-CORE IP per OntoForge_Build_Instructions.md §18. Ships KEYLESS and
DETERMINISTIC; no model invocation, no network.
"""

from .priors import (
    KIND_JOIN_HISTORY,
    KIND_NAME_CONVENTION,
    KIND_SEMTYPE_MAP,
    MAX_NUDGE,
    MIN_OBSERVATIONS,
    TenantPriors,
    shape_key,
)

__all__ = [
    "KIND_JOIN_HISTORY",
    "KIND_NAME_CONVENTION",
    "KIND_SEMTYPE_MAP",
    "MAX_NUDGE",
    "MIN_OBSERVATIONS",
    "TenantPriors",
    "shape_key",
]
