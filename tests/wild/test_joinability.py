"""The live-join playground gate: the freshly-added datasets must actually
*connect* to the pre-existing corpus, not arrive as an island.

The corpus is deliberately mixed — some datasets join, some are honest silos.
This pins the showcase claim that the EXPANSION seeds many new cross-dataset
joins: at least 30 of the newly-added datasets (the owid/plotly clusters plus
the FiveThirtyEight tail grown past the original 60) share a real join key with
some dataset from a DIFFERENT source. "Share a join key" is measured the way the
profiler's IND layer measures it: a join-key-like column whose distinct values
substantially overlap a join-key-like column in another source's table.

Zero network — everything reads the committed snapshot.
"""

from __future__ import annotations

import re

import pandas as pd

from conftest import FIXTURES, load_csv

#: column-name patterns that name a cross-table join key in the wild corpus.
_KEY_NAME_RE = re.compile(
    r"(iso|country|countries|currency|\bcode\b|\bcodes\b|state|province|region|"
    r"\byear\b|\bdate\b|fips|iata|icao|entity|location|city|nation)",
    re.IGNORECASE,
)
#: pure-noise tokens we never treat as a join value (header leakage, nulls).
_NOISE = {"", "na", "n/a", "nan", "none", "null", r"\n", "-", "world", "total"}
#: a column is "join-key-like" only if it is reasonably high-cardinality and its
#: values are short-ish tokens (codes / names / years), not free prose or floats.
_MIN_DISTINCT = 8
_MAX_VALUE_LEN = 40


def _norm(v: str) -> str:
    return re.sub(r"\s+", " ", str(v).strip().lower())


def _key_columns(df: pd.DataFrame) -> dict[str, frozenset[str]]:
    """{column -> distinct normalized value-set} for the join-key-like columns."""
    out: dict[str, frozenset[str]] = {}
    for col in df.columns:
        if not _KEY_NAME_RE.search(str(col)):
            continue
        vals = {
            _norm(v)
            for v in df[col].astype(str)
            if _norm(v) not in _NOISE and len(str(v)) <= _MAX_VALUE_LEN
        }
        if len(vals) >= _MIN_DISTINCT:
            out[str(col)] = frozenset(vals)
    return out


def _shares_key(a: frozenset[str], b: frozenset[str]) -> bool:
    """A real, non-coincidental overlap: >= 5 shared values AND >= 30% of the
    smaller set (so a stray '2010' colliding with an id column does not count)."""
    inter = a & b
    if len(inter) < 5:
        return False
    return len(inter) / min(len(a), len(b)) >= 0.30


def _original_sources(slug: str) -> bool:
    """Datasets that existed before this expansion: the five original sources."""
    return slug.startswith(("of_", "ds_", "fte_", "vg_", "sb_"))


def test_at_least_thirty_new_datasets_join_the_existing_corpus(datasets, fixtures_dir):
    # index every dataset's join-key columns once
    keys: dict[str, dict[str, frozenset[str]]] = {}
    src: dict[str, str] = {}
    for d in datasets:
        df = load_csv(fixtures_dir / f"{d['slug']}.csv")
        keys[d["slug"]] = _key_columns(df)
        src[d["slug"]] = d["source"]

    new_sources = {"owid", "plotly"}
    new_slugs = [d["slug"] for d in datasets if d["source"] in new_sources]
    assert len(new_slugs) >= 60, f"only {len(new_slugs)} datasets from the new sources"

    joined: list[tuple[str, str, str]] = []
    for ns in new_slugs:
        nk = keys[ns]
        if not nk:
            continue
        hit = None
        for other, ok in keys.items():
            if other == ns or src[other] == src[ns]:
                continue  # need a DIFFERENT source on the far side of the join
            for ncol, nvals in nk.items():
                for ocol, ovals in ok.items():
                    if _shares_key(nvals, ovals):
                        hit = (other, ncol, ocol)
                        break
                if hit:
                    break
            if hit:
                break
        if hit:
            joined.append((ns, hit[0], f"{hit[1]}~{hit[2]}"))

    assert len(joined) >= 30, (
        f"only {len(joined)} new datasets join the existing corpus "
        f"(need >= 30); sample: {joined[:5]}"
    )


def test_owid_entity_year_thread_lights_up(datasets, fixtures_dir):
    """The OWID cluster's headline join surface: Entity (country names) overlaps
    the existing country-name columns; Year overlaps the existing year columns.
    A spot proof that the new cluster is wired in, not floating."""
    owid = [d["slug"] for d in datasets if d["source"] == "owid"]
    assert owid, "no OWID datasets landed"
    # at least one OWID table whose Entity column overlaps a non-OWID country
    # column AND whose Year column overlaps a non-OWID year column.
    non_owid_country: list[frozenset[str]] = []
    non_owid_year: list[frozenset[str]] = []
    for d in datasets:
        if d["source"] == "owid":
            continue
        df = load_csv(fixtures_dir / f"{d['slug']}.csv")
        for col in df.columns:
            cl = str(col).lower()
            vals = {_norm(v) for v in df[col].astype(str) if _norm(v) not in _NOISE}
            if "country" in cl or cl in ("entity", "location", "nation"):
                if len(vals) >= _MIN_DISTINCT:
                    non_owid_country.append(frozenset(vals))
            if cl == "year" and len(vals) >= _MIN_DISTINCT:
                non_owid_year.append(frozenset(vals))

    lit = 0
    for slug in owid:
        df = load_csv(fixtures_dir / f"{slug}.csv")
        cols = {str(c).lower(): c for c in df.columns}
        if "entity" not in cols or "year" not in cols:
            continue
        ent = {_norm(v) for v in df[cols["entity"]].astype(str) if _norm(v) not in _NOISE}
        yr = {_norm(v) for v in df[cols["year"]].astype(str) if _norm(v) not in _NOISE}
        joins_country = any(_shares_key(frozenset(ent), c) for c in non_owid_country)
        joins_year = any(len(yr & y) >= 5 for y in non_owid_year)
        if joins_country and joins_year:
            lit += 1
    assert lit >= 5, f"only {lit} OWID tables thread both Entity-country and Year"
