"""Determinism (§18.4): identical inputs + seed -> identical synthesized SQL,
fingerprints, reports, and splits. Zero network, zero model calls."""

from __future__ import annotations

import m8_helpers as H

from ontoforge.anvil import Anvil, split_indices
from ontoforge.profiling import profile_table


def _run(df, seed=0):
    anvil = Anvil(seed=seed)
    accepted = anvil.synthesize(df, H.profile(df), H.sensor_class(), H.sensor_ontology())
    return anvil, accepted


def test_split_is_seeded_and_deterministic():
    a = split_indices(1000, seed=0)
    b = split_indices(1000, seed=0)
    c = split_indices(1000, seed=1)
    assert a == b
    assert a != c
    synth, hold = a
    assert not set(synth) & set(hold)
    assert len(synth) + len(hold) == 1000
    assert abs(len(hold) / 1000 - 0.3) < 0.02


def test_synthesis_is_deterministic_end_to_end():
    clean = H.clean_sensors()
    df, _ = H.corrupt_units(clean)
    _, acc1 = _run(df)
    _, acc2 = _run(df)
    assert [t.sql for t, _ in acc1] == [t.sql for t, _ in acc2]
    assert [t.fingerprint for t, _ in acc1] == [t.fingerprint for t, _ in acc2]
    assert [r.holdout_pass_rate for _, r in acc1] == [r.holdout_pass_rate for _, r in acc2]


def test_estate_synthesis_is_deterministic(estate, gold_ontology):
    df = estate["tables"]["maintenance_erp"]
    tp = profile_table(df, "erp", "maintenance_erp")
    wo = gold_ontology.by_name("WorkOrder")
    r1 = Anvil(seed=0).synthesize(df, tp, wo, gold_ontology)
    r2 = Anvil(seed=0).synthesize(df, tp, wo, gold_ontology)
    assert [t.fingerprint for t, _ in r1] == [t.fingerprint for t, _ in r2]


def test_spine_decisions_cost_zero_tokens():
    """v0 acceptance is spine-T0 deterministic: no model spend ever."""
    clean = H.clean_sensors()
    df, _ = H.corrupt_currency(clean)
    anvil, accepted = _run(df)
    assert accepted
    assert anvil.acceptor.spine.spent_tokens() == 0
