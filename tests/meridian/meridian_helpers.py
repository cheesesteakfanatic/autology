"""Shared helpers for the Meridian estate suite (imported by test modules;
fixtures live in conftest.py)."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[2]
FIXTURES = REPO / "fixtures" / "meridian"


def load_frames(base: Path = FIXTURES) -> dict[str, pd.DataFrame]:
    return {
        p.stem: pd.read_csv(p, dtype=str, keep_default_na=False, encoding="utf-8")
        for p in sorted(base.glob("*.csv"))
    }


def pad_vendor(v: str) -> str:
    """The documented VENDOR_ID recovery rule: zero-pad to 10 digits."""
    v = str(v).strip()
    return v.zfill(10) if v else v
