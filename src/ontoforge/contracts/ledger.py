"""Ledger protocol (whitepaper §11.2 M0 interface) — frozen so M1/M4/M5/M6 can build
against it while M0 is implemented in parallel.

Invariants (M0 acceptance tests):
- append-only: nothing is ever updated or deleted; corrections supersede.
- content-addressed atom identity: re-registering identical content is a no-op (dedup).
- every artifact has non-zero provenance (constraint H).
- invalidate() is EXACT on derivation DAGs: no over- or under-invalidation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Optional, Protocol, Sequence

from .atoms import Atom
from .decisions import DecisionResult
from .provenance import ProvTerm


@dataclass(frozen=True, slots=True)
class AtomDelta:
    """One CDC change: an atom appearing, changing, or disappearing at a source."""

    kind: str          # "insert" | "update" | "delete"
    atom: Atom
    superseded_atom_id: str = ""   # update/delete: the previous atom at the same uri


@dataclass(slots=True)
class DeltaBatch:
    source_id: str
    cycle: int
    deltas: list[AtomDelta] = field(default_factory=list)

    @property
    def changed_atom_ids(self) -> list[str]:
        out = []
        for d in self.deltas:
            if d.superseded_atom_id:
                out.append(d.superseded_atom_id)
        return out


class Ledger(Protocol):
    """M0's public surface. SQLite-backed reference implementation in ontoforge.ledger."""

    # -- atoms
    def register_atoms(self, atoms: Sequence[Atom]) -> list[str]:
        """Register atoms; returns atom_ids. Identical content dedups to the same id."""
        ...

    def get_atom(self, atom_id: str) -> Optional[Atom]: ...

    # -- provenance interning (two-level: shape dictionary + leaf arrays, §4.2)
    def intern(self, term: ProvTerm) -> str:
        """Intern a term; returns prov_ref (term_hash). Idempotent."""
        ...

    def resolve(self, prov_ref: str) -> ProvTerm: ...

    def valuate_ref(self, prov_ref: str, valuation: str) -> Any:
        """Run a named valuation ('citations' | 'confidence' | 'derivable') over a ref."""
        ...

    # -- artifacts & decisions (append-only)
    def append_artifact(self, artifact_id: str, kind: str, payload: str, prov_ref: str) -> None:
        """Record a derived artifact. prov_ref must resolve to a non-ZERO term."""
        ...

    def append_decision(self, result: DecisionResult, prov_atoms: Sequence[str] = ()) -> None: ...

    # -- invalidation (the dictionary-side join, §4.2)
    def invalidate(self, changed_atom_ids: Iterable[str]) -> set[str]:
        """Changed atoms -> the EXACT set of affected artifact_ids."""
        ...

    # -- cost
    def record_cost(self, task: str, tokens: int) -> None: ...

    def total_cost_tokens(self) -> int: ...
