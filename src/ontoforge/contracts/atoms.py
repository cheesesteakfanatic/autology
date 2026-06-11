"""Atom identity: content-addressed URIs for the smallest units of source evidence.

Whitepaper §1.2: a source atom is the smallest addressable unit (cell, field, text span)
with a stable content-addressed URI. Everything downstream — citations, lineage,
invalidation, incremental recompute — depends on this grammar being stable.

URI grammar
-----------
    cell atom:  atom://{source_id}/{object_name}/{row_key}#{column}
    span atom:  atom://{source_id}/{doc_path}#span:{start}-{end}

`atom_id` is the content address: xxh3_64 over (uri, value_repr). Re-ingesting an
unchanged cell yields the same atom_id (dedup-on-content, M0 test); a changed value
at the same coordinates yields a NEW atom_id — the old atom is superseded, not mutated.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import xxhash

ATOM_SCHEME = "atom://"


def _hash64(*parts: str) -> str:
    h = xxhash.xxh3_64()
    for p in parts:
        h.update(p.encode("utf-8"))
        h.update(b"\x1f")  # unit separator: prevents boundary collisions
    return f"{h.intdigest():016x}"


def value_repr(value: Any) -> str:
    """Canonical string form of a cell value for content addressing.

    None and empty string are distinct; floats use repr() for round-trip fidelity.
    """
    if value is None:
        return "\x00NULL"
    if isinstance(value, float):
        return repr(value)
    return str(value)


def cell_uri(source_id: str, object_name: str, row_key: str, column: str) -> str:
    return f"{ATOM_SCHEME}{source_id}/{object_name}/{row_key}#{column}"


def span_uri(source_id: str, doc_path: str, start: int, end: int) -> str:
    return f"{ATOM_SCHEME}{source_id}/{doc_path}#span:{start}-{end}"


@dataclass(frozen=True, slots=True)
class Atom:
    """A registered source atom. Immutable; identity is content-addressed."""

    uri: str
    value: Any
    atom_id: str = field(default="")

    def __post_init__(self) -> None:
        if not self.uri.startswith(ATOM_SCHEME):
            raise ValueError(f"atom uri must start with {ATOM_SCHEME!r}: {self.uri!r}")
        if not self.atom_id:
            object.__setattr__(self, "atom_id", _hash64(self.uri, value_repr(self.value)))


def make_cell_atom(source_id: str, object_name: str, row_key: str, column: str, value: Any) -> Atom:
    return Atom(uri=cell_uri(source_id, object_name, row_key, column), value=value)


def make_span_atom(source_id: str, doc_path: str, start: int, end: int, text: str) -> Atom:
    return Atom(uri=span_uri(source_id, doc_path, start, end), value=text)
