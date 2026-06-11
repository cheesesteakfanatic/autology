"""HEARTH's record model: the versioned value cell (whitepaper §4.2).

cell(e, p) = { (value, valid_interval, system_interval, prov_ref, confidence, src_rank) }

The CURRENT value of (entity, property) is the unique cell with both intervals open
and top survivorship rank. History is everything else. prov_ref is an interned
provenance reference (term_hash) resolved through the M0 ledger.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .temporal import FOREVER, Instant, Interval, Stance


@dataclass(frozen=True, slots=True)
class ValueCell:
    entity_uri: str
    prop: str
    value: Any
    valid: Interval
    system: Interval
    prov_ref: str  # interned provenance term hash; "" is FORBIDDEN on commit (constraint H)
    confidence: float = 1.0
    src_rank: int = 0  # lower = wins survivorship; rank 0 reserved for human Actions

    @property
    def is_current(self) -> bool:
        return self.valid.open and self.system.open

    def visible_under(self, stance: Stance) -> bool:
        """Does this cell participate in a read at the given stance?"""
        if stance.kind == "current":
            return self.is_current
        if stance.kind == "as_of":
            # what we NOW believe held in the world at valid_at
            return self.system.open and self.valid.contains(stance.valid_at)  # type: ignore[arg-type]
        if stance.kind == "as_known_at":
            # what the system at known_at believed to be true at that same moment
            return self.system.contains(stance.known_at) and self.valid.contains(  # type: ignore[arg-type]
                stance.known_at  # type: ignore[arg-type]
            )
        # audit: what the system at known_at believed about world-time valid_at
        return self.system.contains(stance.known_at) and self.valid.contains(stance.valid_at)  # type: ignore[arg-type]


@dataclass(frozen=True, slots=True)
class LinkCell:
    """A first-class edge with the same versioned-cell temporal model (§4.2 link store)."""

    subject_uri: str
    predicate: str
    object_uri: str
    valid: Interval
    system: Interval
    prov_ref: str
    confidence: float = 1.0
    props: tuple[tuple[str, Any], ...] = field(default=())  # edge properties, small and rare
