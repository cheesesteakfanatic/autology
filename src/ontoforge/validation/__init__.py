"""SQL-synthesis-and-execute backward join validation (v2.1 §1.4).

CLOSED-CORE IP per OntoForge_Build_Instructions.md §18 — proprietary engine.

This package validates a relationship hypothesis the strongest way there is:
it actually *synthesizes the join, executes it in DuckDB, and measures the
result against real data* (match / orphan / fan-out / null-key), then derives a
typed verdict. Nothing here touches the network; it runs purely in-process.
"""

from __future__ import annotations

from .join_exec import (
    BatchValidationConfig,
    validate_candidates,
    validate_join,
    validate_join_frames,
)

__all__ = [
    "BatchValidationConfig",
    "validate_candidates",
    "validate_join",
    "validate_join_frames",
]
