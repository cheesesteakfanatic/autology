"""M1 CDC base: connector protocol, hashing, URI quoting, encoding-robust readers.

Whitepaper §11.2 M1: ``pull(source) -> Δbatch`` per connector; deltas are
``contracts.ledger.AtomDelta`` over ``contracts.atoms.Atom``. State is a JSON-able
snapshot (per-source watermark/hashes) owned by the caller; the connector never
persists anything itself.
"""

from __future__ import annotations

import urllib.parse
from pathlib import Path
from typing import Optional, Protocol, Sequence, runtime_checkable

import pyarrow as pa
import xxhash

from ontoforge.contracts import Atom, DeltaBatch

#: Version tag embedded in every connector state dict so future format changes
#: can be detected instead of silently misread.
STATE_FORMAT = "ontoforge.cdc/1"

JSONState = Optional[dict]


def hash64(*parts: str) -> str:
    """xxh3_64 over unit-separated parts (same framing discipline as contracts.atoms)."""
    h = xxhash.xxh3_64()
    for p in parts:
        h.update(p.encode("utf-8"))
        h.update(b"\x1f")  # unit separator: prevents boundary collisions
    return f"{h.intdigest():016x}"


def hash64_bytes(data: bytes) -> str:
    return f"{xxhash.xxh3_64(data).intdigest():016x}"


def quote_part(part: str) -> str:
    """Percent-encode a URI path component (no char is safe; '/' '#' '%' all escaped).

    The contracts.atoms URI grammar does no quoting itself, so M1 quotes every
    data-derived component (object names, row keys, column names) before calling
    cell_uri/span_uri. Quoting is deterministic, hence atom-URI stable.
    """
    return urllib.parse.quote(part, safe="")


def quote_doc_path(path: str) -> str:
    """Quote a relative doc path segment-wise, keeping '/' as the separator."""
    return "/".join(urllib.parse.quote(seg, safe="") for seg in path.split("/"))


def read_text_robust(path: Path | str) -> str:
    """Read text tolerating BOM and non-UTF-8 legacy files.

    Decode order: utf-8-sig (strips a BOM if present, accepts plain UTF-8),
    then latin-1 (total: never fails, byte-preserving). CRLF/CR newlines are
    NOT translated here; tabular/doc readers normalize where they need to.
    """
    raw = Path(path).read_bytes()
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("latin-1")


def next_cycle(state: JSONState) -> int:
    """Cycle numbering: first pull is cycle 1; each subsequent pull increments."""
    if not state:
        return 1
    return int(state.get("cycle", 0)) + 1


def check_state(state: JSONState, kind: str) -> dict:
    """Validate a prior state dict; returns {} for a first pull."""
    if not state:
        return {}
    fmt = state.get("format")
    if fmt != STATE_FORMAT:
        raise ValueError(f"unrecognized CDC state format {fmt!r} (expected {STATE_FORMAT!r})")
    got = state.get("kind")
    if got != kind:
        raise ValueError(f"state kind mismatch: state is {got!r}, connector is {kind!r}")
    return state


@runtime_checkable
class Connector(Protocol):
    """The M1 connector contract: pull(state) -> (DeltaBatch, new_state).

    - state is JSON-able (json.dumps round-trips it) or None for a first pull.
    - First pull emits every atom as an insert.
    - snapshot_tables() exposes the last pulled snapshot for the RAW mirror.
    """

    source_id: str

    def pull(self, state: JSONState) -> tuple[DeltaBatch, dict]: ...

    def snapshot_tables(self) -> list[tuple[str, pa.Table]]: ...


class AtomRegistrar(Protocol):
    """The single slice of the contracts.Ledger protocol M1 needs.

    Accepting this (rather than importing the M0 implementation, which is being
    built in parallel) keeps M1 decoupled per the §18.1 ownership rules.
    """

    def register_atoms(self, atoms: Sequence[Atom]) -> list[str]: ...


__all__ = [
    "STATE_FORMAT",
    "JSONState",
    "AtomRegistrar",
    "Connector",
    "DeltaBatch",
    "check_state",
    "hash64",
    "hash64_bytes",
    "next_cycle",
    "quote_doc_path",
    "quote_part",
    "read_text_robust",
]
