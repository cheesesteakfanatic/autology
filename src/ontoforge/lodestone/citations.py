"""LODESTONE atom-level citation assembly (whitepaper §6.2; M12 step 6).

Every answer cell carries the prov_refs of the HEARTH cells that produced or
selected it; valuating each ref under the ledger's 'citations' semiring yields
the SOURCE ATOM ids — the clickable evidence. 100% of non-abstained answer
cells must cite >= 1 atom (constraint H guarantees every committed cell has a
derivable provenance term, so a citation-less answer cell is a lowering bug,
never a data condition).
"""

from __future__ import annotations

from typing import Iterable

from ontoforge.contracts.oqir import CitedCell


def assemble_citations(
    rows: list[list[object]],
    cell_provs: list[list[tuple[str, ...]]],
    columns: list[str],
    ledger,
) -> list[CitedCell]:
    cache: dict[str, tuple[str, ...]] = {}

    def atoms_for(refs: Iterable[str]) -> tuple[str, ...]:
        out: set[str] = set()
        for ref in refs:
            if not ref:
                continue
            if ref not in cache:
                cited = ledger.valuate_ref(ref, "citations")
                cache[ref] = tuple(sorted(cited)) if cited else ()
            out.update(cache[ref])
        return tuple(sorted(out))

    cells: list[CitedCell] = []
    for r, (row, provs) in enumerate(zip(rows, cell_provs)):
        for c, (value, refs) in enumerate(zip(row, provs)):
            cells.append(
                CitedCell(row=r, column=columns[c], value=value, atom_ids=atoms_for(refs))
            )
    return cells
