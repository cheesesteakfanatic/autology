"""The scale guard's equivalence contract (pinned, per scale.py's docstring):

when every column has <= BAND_CAP distinct values the banded candidate
pre-pass is exact, and ``discover_inds_scaled(corpus)`` equals the frozen
``profiling.discover_inds(corpus)`` element for element — same INDs, same
scores, same order — over randomized corpora. ``pair_affinities`` (the
atlas's likely-tier engine) is checked against a brute-force exact
recomputation at its lower floor.
"""

from __future__ import annotations

import random

import pandas as pd
import pytest

from ontoforge.profiling import discover_inds
from ontoforge.pipeline.scale import (
    BAND_CAP,
    column_facts,
    discover_inds_scaled,
    pair_affinities,
)

POOLS = {
    "codes": [f"K{i:04d}" for i in range(400)],
    "names": [f"item {i} mark" for i in range(300)],
    "cities": [f"city{i}" for i in range(60)],
    "nums": [str(i * 3 + 1) for i in range(500)],
}


def random_corpus(rng: random.Random) -> dict[str, pd.DataFrame]:
    """3-6 tables, 2-5 columns each, values drawn from overlapping pools so
    real inclusions, partial overlaps, and disjoint pairs all occur."""
    corpus: dict[str, pd.DataFrame] = {}
    for t in range(rng.randint(3, 6)):
        n_rows = rng.randint(20, 120)
        cols: dict[str, list[str]] = {}
        for c in range(rng.randint(2, 5)):
            pool_name = rng.choice(list(POOLS))
            pool = POOLS[pool_name]
            lo = rng.randint(0, len(pool) // 2)
            hi = rng.randint(lo + 5, len(pool))
            slice_ = pool[lo:hi]
            cols[f"{pool_name}_{c}"] = [rng.choice(slice_) for _ in range(n_rows)]
        corpus[f"t{t}"] = pd.DataFrame(cols)
    return corpus


@pytest.mark.parametrize("seed", [11, 23, 47, 89, 131, 197, 251, 313])
def test_scaled_ind_discovery_equals_frozen_discovery(seed):
    corpus = random_corpus(random.Random(seed))
    assert all(
        df[c].nunique() <= BAND_CAP for df in corpus.values() for c in df.columns
    ), "the equivalence regime: every column within the band cap"
    assert discover_inds_scaled(corpus) == list(discover_inds(corpus))


@pytest.mark.parametrize("seed", [7, 71, 709])
def test_pair_affinities_are_exact_and_floor_respected(seed):
    corpus = random_corpus(random.Random(seed))
    facts = column_facts(corpus)
    floor = 0.2
    affs = pair_affinities(facts, floor=floor)

    # exactness: every reported coverage/overlap matches a brute-force check
    by_coord = {(f.table, f.column): f for f in facts}
    for a in affs:
        fl = by_coord[(a.lhs_table, a.lhs_column)]
        fr = by_coord[(a.rhs_table, a.rhs_column)]
        inter = len(fl.hashes & fr.hashes)
        assert a.overlap == inter
        assert a.coverage == pytest.approx(inter / len(fl.hashes))
        assert a.coverage >= floor
        assert len(a.shared_samples) <= 5

    # completeness at small scale: NO qualifying ordered pair is missed
    got = {(a.lhs_table, a.lhs_column, a.rhs_table, a.rhs_column) for a in affs}
    for fl in facts:
        for fr in facts:
            if (fl.table, fl.column) == (fr.table, fr.column):
                continue
            if fl.dtype != fr.dtype or len(fl.hashes) < 2 or len(fr.hashes) < 2:
                continue
            cov = len(fl.hashes & fr.hashes) / len(fl.hashes)
            if cov >= floor:
                assert (fl.table, fl.column, fr.table, fr.column) in got

    # determinism: descending score order, stable on re-run
    scores = [a.score for a in affs]
    assert scores == sorted(scores, reverse=True)
    assert pair_affinities(facts, floor=floor) == affs
