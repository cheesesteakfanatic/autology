"""Shared fixtures for the Meridian estate suite.

The corpus under test is the COMMITTED ``fixtures/meridian`` directory (the
artifact `ontoforge init --source fixtures/meridian` consumes); determinism
tests separately prove it regenerates byte-identically from
``ontoforge.estates.meridian_gen`` with seed 7.
"""

from __future__ import annotations

import pandas as pd
import pytest

from meridian_helpers import FIXTURES, load_frames


@pytest.fixture(scope="session")
def frames() -> dict[str, pd.DataFrame]:
    assert FIXTURES.is_dir(), "fixtures/meridian missing — run scripts/build_meridian_corpus.py"
    return load_frames()


@pytest.fixture(scope="session")
def gold() -> dict:
    from ontoforge.estates import yamlite

    return yamlite.loads((FIXTURES / "gold" / "questions.yaml").read_text(encoding="utf-8"))
