"""T1 scoring and calibration for the decision spine (whitepaper §8; MVP plan §5.3).

Per DecisionKind we maintain:

- a base multinomial/binary logistic model fit on `CalibrationSample` features
  (sklearn LogisticRegression); before any fit a transparent feature-average
  heuristic is used and is clearly marked uncalibrated;
- TWO recalibrators of the base model's raw scores — Platt scaling (a logistic
  regression on the logit of the raw score) and isotonic regression — fit on a
  calibration split; the one with lower ECE on a held-out selection split wins
  (MVP plan §5.3 "temperature/Platt + conformal" control plane);
- split conformal calibration scores (nonconformity = 1 - RAW base-model
  probability of the true outcome) on a final, untouched conformal split, giving
  distribution-free prediction sets at level alpha (whitepaper §3.4 admission
  gating: singleton set => auto-decide). The raw score is used (not the
  recalibrated one) because conformal validity only needs a fixed score
  function, and the raw logistic score is continuous: isotonic recalibration
  produces heavily tied scores whose quantile over-covers badly (empirically
  +7% at alpha=0.2), while the continuous raw score tracks nominal coverage.

Determinism: the train/cal-fit/select/conformal split is a fixed permutation
seeded from the kind name, and all arithmetic is closed-form numpy (the fitted
sklearn coefficients are extracted once), so identical inputs give identical
calibrators and identical decisions.
"""

from __future__ import annotations

import math
import zlib
from dataclasses import dataclass
from typing import Optional, Sequence

import numpy as np
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression

from ontoforge.contracts import CalibrationSample, DecisionKind, DecisionRequest

EPS = 1e-6
MIN_FIT_SAMPLES = 50
N_ECE_BINS = 15
# train (base model) / cal-fit (Platt+isotonic) / select (ECE referee) / conformal
SPLIT_FRACTIONS = (0.45, 0.20, 0.10)  # remainder -> conformal


# --------------------------------------------------------------------- utils


def _clip01(p: np.ndarray | float) -> np.ndarray | float:
    return np.clip(p, EPS, 1.0 - EPS)


def _logit(p: np.ndarray | float) -> np.ndarray | float:
    p = _clip01(p)
    return np.log(p / (1.0 - p))


def _sigmoid(z: np.ndarray | float) -> np.ndarray | float:
    return 1.0 / (1.0 + np.exp(-z))


def expected_calibration_error(
    confidences: Sequence[float], correct: Sequence[bool], n_bins: int = N_ECE_BINS
) -> float:
    """Standard equal-width-binned ECE: sum_b (n_b/n) * |acc_b - conf_b|."""
    conf = np.asarray(confidences, dtype=float)
    corr = np.asarray(correct, dtype=float)
    if conf.size == 0:
        return 0.0
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    idx = np.clip(np.digitize(conf, edges[1:-1], right=False), 0, n_bins - 1)
    ece = 0.0
    n = conf.size
    for b in range(n_bins):
        mask = idx == b
        cnt = int(mask.sum())
        if cnt == 0:
            continue
        ece += (cnt / n) * abs(float(corr[mask].mean()) - float(conf[mask].mean()))
    return float(ece)


def heuristic_probabilities(req: DecisionRequest) -> dict[str, float]:
    """Transparent pre-calibration fallback (clearly marked uncalibrated upstream).

    Priority: (1) features named exactly like candidates are treated as
    unnormalized per-candidate scores; (2) for binary requests the mean of the
    feature values, clipped to [0, 1], is read as P(positive = candidates[1]);
    (3) otherwise uniform.
    """
    cands = req.candidates
    fmap = dict(req.features)
    if any(c in fmap for c in cands):
        raw = {c: max(float(fmap.get(c, 0.0)), 0.0) for c in cands}
        tot = sum(raw.values())
        if tot > 0:
            return {c: v / tot for c, v in raw.items()}
    if len(cands) == 2:
        vals = [float(v) for _, v in req.features]
        s = float(np.mean(vals)) if vals else 0.5
        s = float(min(1.0 - EPS, max(EPS, s)))
        return {cands[0]: 1.0 - s, cands[1]: s}
    u = 1.0 / len(cands)
    return {c: u for c in cands}


# -------------------------------------------------------------- recalibrators


class _PlattScaler:
    """Platt scaling: logistic regression on the logit of the raw score."""

    name = "platt"

    def __init__(self) -> None:
        self.a = 1.0
        self.b = 0.0

    def fit(self, scores: np.ndarray, correct: np.ndarray) -> None:
        y = np.asarray(correct, dtype=bool)
        if y.all() or (~y).all():
            # Degenerate split: nothing to learn; keep the identity mapping.
            self.a, self.b = 1.0, 0.0
            return
        z = np.asarray(_logit(scores), dtype=float).reshape(-1, 1)
        lr = LogisticRegression(C=1e3, max_iter=1000)
        lr.fit(z, y)
        # sklearn orders bool classes [False, True]; decision>0 => True.
        self.a = float(lr.coef_[0, 0])
        self.b = float(lr.intercept_[0])

    def predict(self, scores: np.ndarray) -> np.ndarray:
        z = _logit(np.asarray(scores, dtype=float))
        return np.asarray(_clip01(_sigmoid(self.a * z + self.b)), dtype=float)


class _IsotonicScaler:
    """Isotonic regression on the raw score (monotone, non-parametric)."""

    name = "isotonic"

    def __init__(self) -> None:
        self._iso = IsotonicRegression(y_min=0.0, y_max=1.0, increasing=True, out_of_bounds="clip")

    def fit(self, scores: np.ndarray, correct: np.ndarray) -> None:
        self._iso.fit(np.asarray(scores, dtype=float), np.asarray(correct, dtype=float))

    def predict(self, scores: np.ndarray) -> np.ndarray:
        return np.asarray(_clip01(self._iso.predict(np.asarray(scores, dtype=float))), dtype=float)


# ------------------------------------------------------------------- reports


@dataclass(slots=True)
class CalibrationReport:
    """Per-kind calibration state ("expose ece(kind) and report")."""

    kind: str
    fitted: bool = False
    method: str = "uncalibrated"
    n_samples: int = 0
    n_train: int = 0
    n_cal_fit: int = 0
    n_select: int = 0
    n_conformal: int = 0
    ece_platt: Optional[float] = None
    ece_isotonic: Optional[float] = None
    ece: Optional[float] = None           # held-out ECE of the selected method
    classes: tuple[str, ...] = ()
    binary: bool = False


# ----------------------------------------------------------- kind calibrator


class KindCalibrator:
    """Calibrated T1 scorer + split conformal predictor for one DecisionKind."""

    def __init__(self, kind: DecisionKind) -> None:
        self.kind = kind
        self.report = CalibrationReport(kind=kind.value)
        self._feature_names: tuple[str, ...] = ()
        self._findex: dict[str, int] = {}
        self._classes: tuple[str, ...] = ()
        self._coef: Optional[np.ndarray] = None       # (k, d) or (1, d)
        self._intercept: Optional[np.ndarray] = None  # (k,) or (1,)
        self._binary = False
        self._positive = ""                            # classes[1] in binary mode
        self._scaler: Optional[_PlattScaler | _IsotonicScaler] = None
        self._conformal_scores: np.ndarray = np.empty(0)

    # ---------------------------------------------------------------- state

    @property
    def fitted(self) -> bool:
        return self.report.fitted

    @property
    def method(self) -> str:
        return self.report.method

    @property
    def ece(self) -> Optional[float]:
        return self.report.ece

    # ------------------------------------------------------------------ fit

    def fit(self, samples: Sequence[CalibrationSample]) -> None:
        """Fit base model + both recalibrators; select by held-out ECE; bank
        conformal scores on a final untouched split. No-op (stays uncalibrated)
        below MIN_FIT_SAMPLES or with a single observed class."""
        mine = [s for s in samples if s.kind == self.kind]
        n = len(mine)
        self.report = CalibrationReport(kind=self.kind.value, n_samples=n)
        if n < MIN_FIT_SAMPLES:
            return

        names = sorted({nm for s in mine for nm, _ in s.features})
        findex = {nm: i for i, nm in enumerate(names)}
        X = np.zeros((n, len(names)), dtype=float)
        for i, s in enumerate(mine):
            for nm, v in s.features:
                X[i, findex[nm]] = float(v)
        y = np.array([s.true_outcome for s in mine], dtype=object)

        seed = zlib.crc32(f"spine.calibration.{self.kind.value}".encode()) & 0xFFFFFFFF
        order = np.random.default_rng(seed).permutation(n)
        n_tr = int(n * SPLIT_FRACTIONS[0])
        n_cf = int(n * SPLIT_FRACTIONS[1])
        n_se = int(n * SPLIT_FRACTIONS[2])
        idx_tr = order[:n_tr]
        idx_cf = order[n_tr : n_tr + n_cf]
        idx_se = order[n_tr + n_cf : n_tr + n_cf + n_se]
        idx_co = order[n_tr + n_cf + n_se :]
        if len(np.unique(y[idx_tr].astype(str))) < 2 or len(idx_cf) < 10 or len(idx_se) < 10:
            return

        base = LogisticRegression(C=1.0, max_iter=2000)
        base.fit(X[idx_tr], y[idx_tr].astype(str))
        self._feature_names = tuple(names)
        self._findex = findex
        self._classes = tuple(str(c) for c in base.classes_)
        self._coef = np.asarray(base.coef_, dtype=float)
        self._intercept = np.asarray(base.intercept_, dtype=float)
        self._binary = len(self._classes) == 2 and all(len(s.candidates) == 2 for s in mine)
        self._positive = self._classes[1] if self._binary else ""

        # ---- fit both recalibrators on the cal-fit split
        if self._binary:
            s_cf = self._raw_positive(X[idx_cf])
            t_cf = (y[idx_cf].astype(str) == self._positive)
        else:
            proba = self._raw_proba(X[idx_cf])
            s_cf = proba.max(axis=1)
            pred = np.array([self._classes[j] for j in proba.argmax(axis=1)])
            t_cf = (pred == y[idx_cf].astype(str))
        platt = _PlattScaler()
        platt.fit(s_cf, t_cf)
        iso = _IsotonicScaler()
        iso.fit(s_cf, t_cf)

        # ---- referee on the held-out selection split
        cand_se = [mine[i].candidates for i in idx_se]
        true_se = [str(y[i]) for i in idx_se]
        eces: dict[str, float] = {}
        for scaler in (platt, iso):
            confs, corrs = [], []
            for row, cands, truth in zip(idx_se, cand_se, true_se):
                probs = self._calibrated_probs_vec(X[row], cands, scaler)
                pick = max(cands, key=lambda c: probs[c])
                confs.append(probs[pick])
                corrs.append(pick == truth)
            eces[scaler.name] = expected_calibration_error(confs, corrs)
        # tie -> Platt (parametric, more stable under shift)
        self._scaler = platt if eces["platt"] <= eces["isotonic"] else iso

        self.report.fitted = True
        self.report.method = self._scaler.name
        self.report.n_train = len(idx_tr)
        self.report.n_cal_fit = len(idx_cf)
        self.report.n_select = len(idx_se)
        self.report.ece_platt = eces["platt"]
        self.report.ece_isotonic = eces["isotonic"]
        self.report.ece = eces[self._scaler.name]
        self.report.classes = self._classes
        self.report.binary = self._binary

        # ---- split conformal scores on the final untouched split (same score
        # function as decide-time: nonconformity = 1 - RAW P(true); the raw
        # logistic score is continuous, so the quantile is tie-free and the
        # empirical coverage tracks 1-alpha instead of over-covering).
        scores = []
        for row in idx_co:
            probs = self._raw_probs_dict(X[row], mine[row].candidates)
            scores.append(1.0 - probs.get(str(y[row]), EPS))
        self._conformal_scores = np.sort(np.asarray(scores, dtype=float))
        self.report.n_conformal = len(scores)

    # ----------------------------------------------------------- base model

    def _vectorize(self, features: Sequence[tuple[str, float]]) -> np.ndarray:
        x = np.zeros(len(self._feature_names), dtype=float)
        for nm, v in features:
            j = self._findex.get(nm)
            if j is not None:
                x[j] = float(v)
        return x

    def _raw_positive(self, X: np.ndarray) -> np.ndarray:
        """Binary raw P(classes[1]) via the extracted coefficients."""
        assert self._coef is not None and self._intercept is not None
        z = X @ self._coef[0] + self._intercept[0]
        return np.asarray(_sigmoid(z), dtype=float)

    def _raw_proba(self, X: np.ndarray) -> np.ndarray:
        """Multiclass softmax probabilities via the extracted coefficients."""
        assert self._coef is not None and self._intercept is not None
        if self._coef.shape[0] == 1:  # sklearn stores binary as one row
            p1 = self._raw_positive(X)
            return np.stack([1.0 - p1, p1], axis=1)
        z = X @ self._coef.T + self._intercept
        z = z - z.max(axis=1, keepdims=True)
        e = np.exp(z)
        return e / e.sum(axis=1, keepdims=True)

    def _raw_probs_dict(self, x: np.ndarray, candidates: Sequence[str]) -> dict[str, float]:
        """RAW (pre-recalibration) base-model probabilities over `candidates`.
        Continuous in the features — this is the conformal score function."""
        if self._binary:
            p_pos = float(self._raw_positive(x[None, :])[0])
            neg = next((c for c in candidates if c != self._positive), None)
            if self._positive not in candidates or neg is None:
                u = 1.0 / len(candidates)
                return {c: u for c in candidates}
            return {self._positive: p_pos, neg: 1.0 - p_pos}
        proba = self._raw_proba(x[None, :])[0]
        by_class = {c: float(p) for c, p in zip(self._classes, proba)}
        raw = np.array([max(by_class.get(c, EPS), EPS) for c in candidates], dtype=float)
        raw = raw / raw.sum()
        return {c: float(p) for c, p in zip(candidates, raw)}

    def _calibrated_probs_vec(
        self,
        x: np.ndarray,
        candidates: Sequence[str],
        scaler: _PlattScaler | _IsotonicScaler,
    ) -> dict[str, float]:
        if self._binary:
            p_pos = float(scaler.predict(np.array([float(self._raw_positive(x[None, :])[0])]))[0])
            neg = next((c for c in candidates if c != self._positive), None)
            if self._positive not in candidates or neg is None:
                # candidate labels do not match the fitted classes
                u = 1.0 / len(candidates)
                return {c: u for c in candidates}
            return {self._positive: p_pos, neg: 1.0 - p_pos}
        proba = self._raw_proba(x[None, :])[0]
        by_class = {c: float(p) for c, p in zip(self._classes, proba)}
        raw = np.array([max(by_class.get(c, EPS), EPS) for c in candidates], dtype=float)
        raw = raw / raw.sum()
        cal = scaler.predict(raw)
        cal = cal / cal.sum()
        return {c: float(p) for c, p in zip(candidates, cal)}

    # ------------------------------------------------------------- decide API

    def probabilities(
        self, features: Sequence[tuple[str, float]], candidates: Sequence[str]
    ) -> Optional[dict[str, float]]:
        """Calibrated per-candidate probabilities, or None when this calibrator
        cannot score the request (unfitted, or binary labels do not match)."""
        if not self.fitted or self._scaler is None:
            return None
        if self._binary and set(candidates) != set(self._classes):
            return None
        return self._calibrated_probs_vec(self._vectorize(features), tuple(candidates), self._scaler)

    def conformal_quantile(self, alpha: float) -> Optional[float]:
        """Finite-sample-adjusted quantile: the ceil((n+1)(1-alpha))/n empirical
        quantile of the banked nonconformity scores (split conformal)."""
        if not self.fitted or self._conformal_scores.size == 0:
            return None
        n = int(self._conformal_scores.size)
        k = math.ceil((n + 1) * (1.0 - alpha))
        if k > n:
            return float("inf")
        return float(self._conformal_scores[k - 1])

    def conformal_set(
        self, features: Sequence[tuple[str, float]], candidates: Sequence[str], alpha: float
    ) -> Optional[tuple[str, ...]]:
        """Candidates whose nonconformity (1 - RAW base-model prob) <= q_hat.
        May be empty (extreme ambiguity); empty/large sets route to escalation.
        None when this calibrator cannot score the request."""
        q = self.conformal_quantile(alpha)
        if q is None:
            return None
        if self._binary and set(candidates) != set(self._classes):
            return None
        raw = self._raw_probs_dict(self._vectorize(features), tuple(candidates))
        return tuple(c for c in candidates if (1.0 - raw[c]) <= q + 1e-12)
