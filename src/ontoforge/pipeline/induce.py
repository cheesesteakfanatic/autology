"""Generic induction driver: estate -> M3 profiles/INDs -> M4 STRATA (§11.2).

This stage is already generic in the frozen modules; the driver only wires the
estate dict (any source, aviation or discovered) through them and keeps the
full :class:`ontoforge.strata.StrataResult` — the candidate/extent/cluster
evidence the materializer reads the column mapping back out of.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from ontoforge.contracts import IND, Ontology, TableProfile
from ontoforge.profiling import discover_inds, profile_table
from ontoforge.strata import Strata, StrataResult

__all__ = ["InducedArtifacts", "induce_estate", "profile_estate"]


@dataclass
class InducedArtifacts:
    """Everything the downstream generic stages need from induction."""

    profiles: list[TableProfile]
    inds: list[IND]
    strata: StrataResult

    @property
    def ontology(self) -> Ontology:
        return self.strata.ontology

    @property
    def profiles_by_table(self) -> dict[str, TableProfile]:
        return {tp.table: tp for tp in self.profiles}


def profile_estate(estate: dict[str, Any]) -> tuple[list[TableProfile], list[IND]]:
    """M3 over every estate table (reusing discovery's profile cache when
    present) plus cross-table IND discovery."""
    meta = estate["metadata"]["tables"]
    cache: dict[str, TableProfile] = estate.get("profiles") or {}
    profiles = [
        cache[name]
        if name in cache
        else profile_table(df, meta[name]["source_id"], name)
        for name, df in estate["tables"].items()
    ]
    inds = discover_inds(estate["tables"])
    return profiles, list(inds)


def induce_estate(
    estate: dict[str, Any],
    ledger: Any = None,
    *,
    profiles: Optional[list[TableProfile]] = None,
    inds: Optional[list[IND]] = None,
) -> InducedArtifacts:
    """Full generic induction: profiles -> candidates -> lattice -> ontology."""
    if profiles is None or inds is None:
        profiles, inds = profile_estate(estate)
    result = Strata(ledger=ledger).induce(profiles, inds)
    return InducedArtifacts(profiles=list(profiles), inds=list(inds), strata=result)
