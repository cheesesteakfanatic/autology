"""Fellegi-Sunter EM tests (M5 step 3): convergence, identifiability,
m>u sanity on informative fields, and weight/posterior banding algebra."""

from __future__ import annotations

import math

import numpy as np
import pytest

from ontoforge.er.fs import (
    MISSING,
    FellegiSunter,
    aircraft_pair_features,
    operator_pair_features,
)

KINDS = ("aircraft", "operator")


def _synthetic_levels(seed: int = 7, n: int = 4000, p: float = 0.2):
    """Draw level vectors from a known FS model (3 fields, 3 levels)."""
    rng = np.random.default_rng(seed)
    m = np.array([[0.05, 0.15, 0.80], [0.10, 0.20, 0.70], [0.20, 0.30, 0.50]])
    u = np.array([[0.85, 0.10, 0.05], [0.70, 0.20, 0.10], [0.60, 0.30, 0.10]])
    is_match = rng.random(n) < p
    rows = np.empty((n, 3), dtype=np.int64)
    for fidx in range(3):
        probs = np.where(is_match[:, None], m[fidx], u[fidx])
        c = probs.cumsum(axis=1)
        r = rng.random(n)[:, None]
        rows[:, fidx] = (r > c).sum(axis=1)
    return rows, p


class TestEMSynthetic:
    def test_recovers_known_model(self):
        rows, p_true = _synthetic_levels()
        fs = FellegiSunter(fields=("f1", "f2", "f3"), seed=17).fit(rows.tolist())
        assert fs.converged
        assert fs.prior == pytest.approx(p_true, abs=0.05)
        # match class agrees more than non-match class on every field
        assert (fs.m[:, 2] > fs.u[:, 2]).all()
        assert (fs.m[:, 0] < fs.u[:, 0]).all()

    def test_loglik_monotone_nondecreasing(self):
        rows, _ = _synthetic_levels(seed=11)
        fs = FellegiSunter(fields=("f1", "f2", "f3"), seed=17).fit(rows.tolist())
        trace = fs.loglik_trace
        assert len(trace) >= 2
        for a, b in zip(trace, trace[1:]):
            assert b >= a - 1e-6, "EM log-likelihood must not decrease"

    def test_convergence_criterion_documented(self):
        rows, _ = _synthetic_levels(seed=3)
        fs = FellegiSunter(fields=("f1", "f2", "f3"), seed=17, tol=1e-8).fit(rows.tolist())
        assert fs.converged
        assert fs.n_iter < fs.max_iter
        # criterion: |delta ll| < tol * (1 + |ll|)
        assert abs(fs.loglik_trace[-1] - fs.loglik_trace[-2]) < fs.tol * (
            1 + abs(fs.loglik_trace[-1])
        )

    def test_fit_determinism(self):
        rows, _ = _synthetic_levels(seed=5)
        a = FellegiSunter(fields=("f1", "f2", "f3"), seed=17).fit(rows.tolist())
        b = FellegiSunter(fields=("f1", "f2", "f3"), seed=17).fit(rows.tolist())
        assert np.array_equal(a.m, b.m) and np.array_equal(a.u, b.u)
        assert a.prior == b.prior

    def test_missing_fields_skipped_in_weight(self):
        rows, _ = _synthetic_levels()
        fs = FellegiSunter(fields=("f1", "f2", "f3"), seed=17).fit(rows.tolist())
        full = fs.weight([2, 2, 2])
        partial = fs.weight([2, MISSING, 2])
        only_f2 = fs.weight([MISSING, 2, MISSING])
        assert full == pytest.approx(partial + only_f2, abs=1e-9)


class TestWeightBanding:
    def test_posterior_is_monotone_in_weight(self):
        rows, _ = _synthetic_levels()
        fs = FellegiSunter(fields=("f1", "f2", "f3"), seed=17).fit(rows.tolist())
        lvls = [[0, 0, 0], [1, 0, 0], [1, 1, 0], [2, 1, 0], [2, 2, 1], [2, 2, 2]]
        ws = [fs.weight(lv) for lv in lvls]
        ps = [fs.posterior(lv) for lv in lvls]
        for (w1, p1), (w2, p2) in zip(zip(ws, ps), zip(ws[1:], ps[1:])):
            if w2 > w1:
                assert p2 > p1

    def test_two_threshold_band_in_weight_space(self):
        """posterior cuts (hi, lo) ARE weight thresholds: the algebraic map
        w* = log2(P/(1-P)) - log2(p/(1-p)) reproduces the posterior cut."""
        rows, _ = _synthetic_levels()
        fs = FellegiSunter(fields=("f1", "f2", "f3"), seed=17).fit(rows.tolist())
        for cut in (0.95, 0.05):
            w_star = fs.weight_threshold(cut)
            odds = fs.prior / (1 - fs.prior) * 2.0**w_star
            assert odds / (1 + odds) == pytest.approx(cut, abs=1e-9)
        assert fs.weight_threshold(0.95) > fs.weight_threshold(0.05)


class TestEstateFit:
    def test_em_converged_on_estate(self, batch):
        _, res = batch
        for kind in KINDS:
            fs = res.fs_models[kind]
            assert fs.converged, f"{kind} EM did not converge"
            assert 0.0 < fs.prior < 1.0

    def test_m_greater_than_u_on_identity_fields(self, batch):
        """m > u at the AGREE level for every informative identity field —
        the FS sanity gate of the orchestration spec."""
        _, res = batch
        informative = {"aircraft": ("tail", "serial", "name"), "operator": ("name", "tokens")}
        for kind, fields in informative.items():
            fs = res.fs_models[kind]
            for f in fields:
                fidx = fs.fields.index(f)
                assert fs.m[fidx, 2] > fs.u[fidx, 2], f"{kind}.{f}: m[agree] <= u[agree]"
                # agreement carries positive log-weight, disagreement negative
                assert math.log2(fs.m[fidx, 2] / fs.u[fidx, 2]) > 0
                assert math.log2(fs.m[fidx, 0] / fs.u[fidx, 0]) < 0


class TestComparators:
    def test_aircraft_serial_conflict_and_window(self):
        old = {
            "tail": "3484Z", "serial": "H535763", "model": "BEECH V35B",
            "name": "MOORE MICHAEL S", "date_lo": 720000, "date_hi": 727000,
            "is_registry": "1",
        }
        event_new = {
            "tail": "3484Z", "serial": "", "model": "BEECHCRAFT BARON",
            "name": "WILLIAMS JOHN H", "date_lo": 732500, "date_hi": 732500,
            "is_registry": "0",
        }
        pf = aircraft_pair_features(old, event_new)
        lv = dict(zip(("tail", "serial", "model", "window", "name"), pf.levels))
        assert lv["tail"] == 2          # tails agree — that is the trap
        assert lv["serial"] == MISSING  # event carries no serial
        assert lv["window"] == 0        # event outside the validity window
        cont = dict(pf.continuous)
        assert cont["window_overlap"] == 0.0
        assert cont["window_gap_years"] > 0.0

    def test_event_event_window_is_missing(self):
        a = {"tail": "1", "serial": "", "model": "", "name": "", "date_lo": 700000,
             "date_hi": 700000, "is_registry": "0"}
        b = {"tail": "1", "serial": "", "model": "", "name": "", "date_lo": 730000,
             "date_hi": 730000, "is_registry": "0"}
        pf = aircraft_pair_features(a, b)
        assert pf.levels[3] == MISSING  # decades between events != evidence

    def test_operator_alias_signals(self):
        fedex = {"name_norm": "FEDEX EXPRESS", "tails": {"1", "2"}}
        federal = {"name_norm": "FEDERAL EXPRESS CORP", "tails": {"2", "3"}}
        pf = operator_pair_features(fedex, federal)
        cont = dict(pf.continuous)
        assert cont["alias_signal"] == 1.0   # fused-prefix FEDEX ~ FEDERAL EXPRESS
        assert cont["shared_tail"] == 1.0
        ups = {"name_norm": "UPS AIRLINES", "tails": set()}
        ups_full = {"name_norm": "UNITED PARCEL SERVICE CO", "tails": set()}
        assert dict(operator_pair_features(ups, ups_full).continuous)["alias_signal"] == 1.0
