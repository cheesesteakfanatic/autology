"""Thin shim: regenerate fixtures/meridian from the packaged generator.

The real generator lives INSIDE the package (``ontoforge.estates.meridian_gen``)
so ``ontoforge demo meridian`` can rebuild the corpus from an installed wheel;
this script only preserves the repo's `scripts/` entry point convention:

    uv run python scripts/build_meridian_corpus.py [out_dir] [--seed N]
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ontoforge.estates.meridian_gen import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
