"""M1 test helpers: a tiny in-memory ledger fake + CSV writing.

The fake implements ONLY register_atoms from the contracts Ledger protocol —
M1's spec mandates accepting the protocol structurally (the real M0 ledger is
being built in parallel and must not be imported here).
"""

from __future__ import annotations

import csv
from pathlib import Path
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


def write_csv(path: Path, header: list[str], rows: list[list], encoding: str = "utf-8") -> None:
    with open(path, "w", newline="", encoding=encoding) as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)
