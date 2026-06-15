"""In-process, monotonically-seq'd event log for the live playground build.

The playground build (``pipeline.playground.PlaygroundJob``) runs on a dedicated
worker thread and streams its discovery narrative as small JSON events into a
:class:`JobEventLog`. The server's ``GET /api/workspace/build/{job_id}`` polls
``events_since(seq)`` and replays new events into the constellation animation.

Design constraints (from the architecture brief):

* **No DB, no network, no new async infra.** The log is an in-process list under
  a :class:`threading.Lock`; the worker writes, the event-loop thread reads.
* **Monotonic, gap-free seq.** Every event gets the next integer; pollers ask for
  ``since=<seq>`` and receive exactly the events with ``seq > since``.
* **Small JSON shapes.** Each event serializes to a compact dict so the UI can
  build it into the DOM via ``el()``/``createTextNode`` (never ``innerHTML``).
* **Ephemeral.** The live stream is per-process and not durable across a server
  restart; the FINAL atlas.json + ontology the job persists are the durable
  artifacts (the canonical ``GET /api/atlas`` serves the stable map afterwards).

Event kinds (the API ``events[].kind`` contract): ``stage`` | ``type_found`` |
``join_found`` | ``silo``. ``JobStarted``/``JobDone``/``JobError`` are surfaced
through the job *status*, not the event stream, but are recorded here too so the
log is a complete audit trail of one build.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Literal

__all__ = [
    "EVENT_KINDS",
    "JobEvent",
    "JobEventLog",
]

#: the ``kind`` values that appear in the polled ``events[]`` array (the wire
#: contract the UI animates). Lifecycle markers (started/done/error) ride the
#: job *status* field instead, so the event stream stays a pure discovery
#: narrative.
EVENT_KINDS = ("stage", "type_found", "join_found", "silo")

EventKind = Literal["stage", "type_found", "join_found", "silo"]


@dataclass(frozen=True, slots=True)
class JobEvent:
    """One monotonically-sequenced build event.

    ``seq`` is assigned by the log (gap-free, starting at 1). ``kind`` is one of
    :data:`EVENT_KINDS`. ``msg`` is the human narrative line ("found a join:
    airports <-> routes on iata_code"). ``data`` carries the typed payload the UI
    needs to draw the node/arc (table names, columns, coverage, tier, ...).
    """

    seq: int
    kind: str
    msg: str
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"seq": self.seq, "kind": self.kind, "msg": self.msg, **self.data}


class JobEventLog:
    """Thread-safe append-only event buffer for a single playground build.

    The worker thread calls :meth:`emit`; the server thread calls
    :meth:`since` / :meth:`snapshot`. A bounded ``max_events`` guards memory: the
    LAST events always survive (the discovery tail is what the UI is animating)
    and ``base_seq`` records how many were dropped so seq stays monotone.
    """

    def __init__(self, max_events: int = 5000) -> None:
        self._lock = threading.Lock()
        self._events: list[JobEvent] = []
        self._next_seq = 1
        self._dropped = 0
        self.max_events = max_events

    def emit(self, kind: str, msg: str, **data: Any) -> JobEvent:
        """Append one event; returns it (with its assigned seq)."""
        with self._lock:
            ev = JobEvent(seq=self._next_seq, kind=kind, msg=msg, data=dict(data))
            self._next_seq += 1
            self._events.append(ev)
            if len(self._events) > self.max_events:
                drop = len(self._events) - self.max_events
                self._events = self._events[drop:]
                self._dropped += drop
            return ev

    def since(self, seq: int) -> list[dict[str, Any]]:
        """Every event with ``seq > given``, in order, as wire dicts."""
        with self._lock:
            return [e.to_dict() for e in self._events if e.seq > seq]

    def snapshot(self) -> list[dict[str, Any]]:
        """All retained events as wire dicts (since(0))."""
        return self.since(0)

    @property
    def last_seq(self) -> int:
        with self._lock:
            return self._next_seq - 1

    @property
    def dropped(self) -> int:
        with self._lock:
            return self._dropped
