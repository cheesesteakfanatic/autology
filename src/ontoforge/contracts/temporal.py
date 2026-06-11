"""Bi-temporal model: four timestamps per fact (whitepaper §3.5, Graphiti/Zep-style).

(valid_from, valid_to)     — when the fact held in the WORLD     (valid time)
(created_at, expired_at)   — when the SYSTEM believed it          (system/transaction time)

Instants are int64 microseconds since Unix epoch (UTC). `FOREVER` is the open-interval
sentinel; it survives Parquet/JSON round-trips, unlike datetime.max.

Invariants (property-tested in M0/M6):
  valid_from < valid_to;  created_at < expired_at;  system time is append-monotone:
  corrections EXPIRE a cell (close its system interval) and write a new one — never delete.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal, Optional

Instant = int  # microseconds since epoch, UTC
FOREVER: Instant = 2**62


def to_instant(dt: datetime) -> Instant:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1_000_000)


def from_instant(i: Instant) -> datetime:
    if i >= FOREVER:
        raise ValueError("FOREVER has no datetime representation")
    return datetime.fromtimestamp(i / 1_000_000, tz=timezone.utc)


def now_instant() -> Instant:
    return to_instant(datetime.now(timezone.utc))


@dataclass(frozen=True, slots=True)
class Interval:
    """Half-open [start, end)."""

    start: Instant
    end: Instant = FOREVER

    def __post_init__(self) -> None:
        if self.start >= self.end:
            raise ValueError(f"empty/inverted interval [{self.start}, {self.end})")

    @property
    def open(self) -> bool:
        return self.end >= FOREVER

    def contains(self, i: Instant) -> bool:
        return self.start <= i < self.end

    def overlaps(self, other: "Interval") -> bool:
        return self.start < other.end and other.start < self.end

    def intersect(self, other: "Interval") -> Optional["Interval"]:
        s, e = max(self.start, other.start), min(self.end, other.end)
        return Interval(s, e) if s < e else None


StanceKind = Literal["current", "as_of", "as_known_at", "audit"]


@dataclass(frozen=True, slots=True)
class Stance:
    """Temporal stance for reads (whitepaper §4.4).

    current            — open system interval, valid now
    as_of(t)           — what was true in the world at time t (per current belief)
    as_known_at(t)     — what the system believed at time t
    audit(tv, ts)      — what the system at ts believed about world-time tv
    """

    kind: StanceKind = "current"
    valid_at: Optional[Instant] = None
    known_at: Optional[Instant] = None

    def __post_init__(self) -> None:
        if self.kind == "as_of" and self.valid_at is None:
            raise ValueError("as_of stance requires valid_at")
        if self.kind == "as_known_at" and self.known_at is None:
            raise ValueError("as_known_at stance requires known_at")
        if self.kind == "audit" and (self.valid_at is None or self.known_at is None):
            raise ValueError("audit stance requires valid_at and known_at")


CURRENT = Stance()
