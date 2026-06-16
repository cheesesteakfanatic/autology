"""tests/cdc helpers: a uniquely-named module so cross-suite conftest collisions
(pytest prepend import-mode, no package __init__) never shadow it.

Provides the in-memory FakeLedger used by the connector tests — the structural
``register_atoms`` slice of the contracts Ledger protocol, with M0 dedup-on-content.
"""

from __future__ import annotations

from typing import Sequence

from ontoforge.contracts import Atom


class FakeLedger:
    """In-memory register_atoms with M0's dedup-on-content semantics."""

    def __init__(self) -> None:
        self.atoms: dict[str, Atom] = {}
        self.register_calls: int = 0

    def register_atoms(self, atoms: Sequence[Atom]) -> list[str]:
        self.register_calls += 1
        out = []
        for a in atoms:
            self.atoms.setdefault(a.atom_id, a)
            out.append(a.atom_id)
        return out
