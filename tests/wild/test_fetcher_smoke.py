"""ONE live smoke over the fetcher primitives (3 URLs) — slow, skip-if-offline.

Everything else in tests/wild runs against the committed snapshot with zero
network; this is the only test allowed to touch the internet, and it skips
itself cleanly when there is none.
"""

from __future__ import annotations

import socket

import pytest

from ontoforge.estates import wild

pytestmark = pytest.mark.slow

SMOKE_URLS = [
    # (url, headerless headers or None)
    ("https://raw.githubusercontent.com/mwaskom/seaborn-data/master/iris.csv", None),
    ("https://raw.githubusercontent.com/jpatokal/openflights/master/data/planes.dat",
     wild.OPENFLIGHTS_HEADERS["planes"]),
    ("https://raw.githubusercontent.com/fivethirtyeight/data/master/bad-drivers/bad-drivers.csv",
     None),
]


def _online() -> bool:
    try:
        socket.create_connection(("raw.githubusercontent.com", 443), timeout=5).close()
        return True
    except OSError:
        return False


@pytest.fixture(scope="module", autouse=True)
def require_network():
    if not _online():
        pytest.skip("offline: raw.githubusercontent.com unreachable")


@pytest.mark.parametrize("url,headers", SMOKE_URLS, ids=lambda v: str(v)[:60])
def test_fetch_parse_normalize_one_url(url, headers, tmp_path):
    raw = wild._http_bytes(url)
    assert raw, f"download failed: {url}"
    df = wild.parse_csv_bytes(raw, headers=headers)
    out = wild.normalize(df)
    assert out is not None, f"{url} failed the admission gates"
    assert wild.MIN_ROWS <= len(out) <= wild.ROW_CAP
    assert wild.MIN_COLS <= out.shape[1] <= wild.MAX_COLS
    # written form round-trips as the wart-preserving CSV the pipeline reads
    p = tmp_path / "smoke.csv"
    out.to_csv(p, index=False, encoding="utf-8", lineterminator="\n")
    import pandas as pd

    back = pd.read_csv(p, dtype=str, keep_default_na=False, encoding="utf-8")
    assert back.shape == out.shape
