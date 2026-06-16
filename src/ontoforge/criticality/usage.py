"""Append-only usage log for criticality recompute (Crew A, §6).

An :class:`UsageLog` records how the induced ontology graph is actually used at
runtime — every query, join, materialization, or NL-answer touches one or more
``element_uri`` nodes. The log is the *driver* of lazy criticality recompute:
:class:`~ontoforge.criticality.recompute.CriticalityModel` consumes only the
events it has not seen yet (``since(watermark)``).

HARD INVARIANTS (shared with the whole engine):

* keyless / offline — pure stdlib, no imports that touch the network.
* fully deterministic — there is NO wall-clock anywhere. Each appended event
  gets a strictly increasing integer ``seq`` (starting at 1) assigned by the
  log itself, so a given sequence of appends always yields byte-identical
  state. Anything "time-like" (recency) is computed from these integer seqs,
  never from ``time.time()`` / ``datetime.now()``.
"""

from __future__ import annotations

from dataclasses import dataclass

#: The only usage kinds we recognize. A kind outside this set is rejected so a
#: typo can never silently dilute the criticality signal.
USAGE_KINDS: frozenset[str] = frozenset({"query", "join", "materialize", "answer"})


@dataclass(frozen=True)
class UsageEvent:
    """A single recorded touch of one ontology element.

    Immutable on purpose: events are append-only history. ``seq`` is assigned by
    the owning :class:`UsageLog` (the caller never sets it) and is the sole,
    deterministic notion of ordering / "time" in the system.
    """

    element_uri: str
    kind: str
    weight: float = 1.0
    seq: int = 0


class UsageLog:
    """Append-only, deterministic log of :class:`UsageEvent` records.

    ``append`` auto-assigns a strictly increasing integer ``seq`` (1, 2, 3, ...)
    and returns the created event. The log itself holds no wall-clock state, so
    replaying the same appends always reproduces the same ``events``.
    """

    __slots__ = ("_events", "_next_seq")

    def __init__(self) -> None:
        self._events: list[UsageEvent] = []
        self._next_seq: int = 1

    def append(self, element_uri: str, kind: str, weight: float = 1.0) -> UsageEvent:
        """Record one usage touch and return the created (sealed) event.

        Raises ``ValueError`` for an unknown ``kind`` so bad data fails loudly
        rather than corrupting the criticality blend.
        """
        if kind not in USAGE_KINDS:
            raise ValueError(
                f"unknown usage kind {kind!r}; expected one of {sorted(USAGE_KINDS)}"
            )
        event = UsageEvent(
            element_uri=element_uri,
            kind=kind,
            weight=float(weight),
            seq=self._next_seq,
        )
        self._events.append(event)
        self._next_seq += 1
        return event

    @property
    def events(self) -> list[UsageEvent]:
        """The append-only event list (live reference; treat as read-only)."""
        return self._events

    @property
    def max_seq(self) -> int:
        """Highest ``seq`` assigned so far, or 0 when the log is empty."""
        return self._next_seq - 1

    def since(self, watermark: int) -> list[UsageEvent]:
        """Events strictly newer than ``watermark`` (i.e. ``seq > watermark``).

        Because seqs are assigned in strictly increasing order, the tail of the
        list with ``seq > watermark`` is exactly the unseen slice — returned in
        append order.
        """
        if watermark <= 0:
            return list(self._events)
        # events are stored in strictly increasing seq order; slice from the
        # first index whose seq exceeds the watermark.
        return [e for e in self._events if e.seq > watermark]
