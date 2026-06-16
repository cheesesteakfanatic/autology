"""Deterministic persistence for criticality scores (Crew A, §6).

:func:`save_scores` writes a byte-stable JSON snapshot of a
:class:`~ontoforge.criticality.recompute.CriticalityModel` — the scores map plus
the watermark — and :func:`load_scores` round-trips it back to a plain dict.

HARD INVARIANTS: keyless, offline, fully deterministic. The on-disk bytes are a
pure function of the model state (``sort_keys=True``, fixed separators, trailing
newline, ``allow_nan=False``), so saving the same model twice produces
byte-identical files and downstream diffs stay clean.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, Union

if TYPE_CHECKING:
    from .recompute import CriticalityModel

#: Version tag embedded in every snapshot so a future format change is detected
#: rather than silently misread (mirrors the cdc STATE_FORMAT convention).
SCORES_FORMAT = "ontoforge.criticality/1"

PathLike = Union[str, Path]


def to_dict(model: CriticalityModel) -> dict[str, Any]:
    """Serialize ``model`` to a plain, JSON-able dict (no I/O)."""
    return {
        "format": SCORES_FORMAT,
        "watermark": model.watermark,
        # Copy into a fresh dict so callers cannot mutate the model via the
        # returned structure; json.dumps sorts keys at write time.
        "scores": dict(model.scores),
    }


def save_scores(path: PathLike, model: CriticalityModel) -> None:
    """Write a deterministic JSON snapshot of ``model`` to ``path``.

    Output is byte-stable: ``sort_keys=True``, compact fixed separators,
    ``allow_nan=False``, and a single trailing newline.
    """
    payload = to_dict(model)
    text = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        ensure_ascii=False,
    )
    Path(path).write_text(text + "\n", encoding="utf-8")


def load_scores(path: PathLike) -> dict[str, Any]:
    """Read a snapshot written by :func:`save_scores` back into a dict.

    Returns the parsed payload ``{"format", "watermark", "scores"}``; the
    ``scores`` value round-trips to the same float mapping that was saved.
    """
    text = Path(path).read_text(encoding="utf-8")
    data: dict[str, Any] = json.loads(text)
    return data
