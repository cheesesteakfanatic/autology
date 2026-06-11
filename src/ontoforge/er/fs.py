"""Pair features + Fellegi-Sunter match model with EM (M5 step 3).

Per candidate pair we compute (i) a continuous feature vector (field-wise
jaro-winkler, token jaccard, exact-match flags, date-window deltas, missing
indicators) used by the spine's T1 calibrated scorer, and (ii) discretized
per-field agreement LEVELS (0=disagree, 1=partial, 2=agree, None=missing)
which are the Fellegi-Sunter observations.

Fellegi-Sunter (1969) with EM (Winkler 1988): a two-component conditionally
independent mixture over the level vectors. m_f[l] = P(level=l | match),
u_f[l] = P(level=l | non-match), prior p = P(match). Fitting is UNSUPERVISED
on the candidate-pair population, randomly initialized from a fixed seed.

Convergence criterion: |ll_t - ll_{t-1}| < tol * (1 + |ll_t|) on the observed
data log-likelihood (tol=1e-8, max 300 iterations); the trace is kept so
tests can assert monotone non-decreasing likelihood. Because the components
of a random init are exchangeable, after convergence the MATCH component is
identified as the one with the higher expected full-agreement mass
sum_f P(level=agree); the labelling is swapped if needed (documented
deterministic rule).

Match weight: w(pair) = sum over OBSERVED fields of log2(m_f[l] / u_f[l]).
Two-threshold banding ON THE WEIGHT: with prior p the posterior is the
monotone map P(M|levels) = 1 / (1 + (1-p)/p * 2^-w), so posterior cuts
(hi, lo) correspond exactly to weight thresholds
w_* = log2(P/(1-P)) - log2(p/(1-p)).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional, Sequence

import numpy as np

from .similarity import (
    char_ngrams,
    fuzzy_token_containment,
    fuzzy_token_jaccard,
    jaro_winkler,
    token_jaccard,
)
from .records import core_tokens

__all__ = [
    "PairFeatures",
    "aircraft_pair_features",
    "operator_pair_features",
    "pair_features",
    "FellegiSunter",
    "AIRCRAFT_FIELDS",
    "OPERATOR_FIELDS",
]

AIRCRAFT_FIELDS = ("tail", "serial", "model", "window", "name")
OPERATOR_FIELDS = ("name", "tokens", "alias", "shared_tail")

MISSING = -1  # level encoding for "field not observed on this pair"
N_LEVELS = 3  # disagree / partial / agree

# grace period (days) for registration-window containment: certificates lag
# events slightly in the wild; 180d absorbs that without bridging the decades
# that separate temporal N-number reuses.
WINDOW_GRACE_DAYS = 180


@dataclass(frozen=True, slots=True)
class PairFeatures:
    """levels: FS observations per field; continuous: T1 evidence vector."""

    kind: str
    levels: tuple[int, ...]                 # per FIELDS order; MISSING = -1
    continuous: tuple[tuple[str, float], ...]


def _lv(value: Optional[bool | int]) -> int:
    return MISSING if value is None else int(value)


# ---------------------------------------------------------------- aircraft


def _window_relation(fa: dict, fb: dict) -> tuple[Optional[int], float, float]:
    """Date compatibility between two aircraft mentions.

    Registry rows carry a true validity window [cert issue, expiration]; event
    rows carry an event date (or work-order span). The field is OBSERVED only
    when at least one side is a registry row with a usable window (event-event
    date gaps say nothing about identity — the same airframe accrues events
    decades apart). Returns (level, overlap_flag, gap_years).
    """
    a_lo, a_hi = fa.get("date_lo"), fa.get("date_hi")
    b_lo, b_hi = fb.get("date_lo"), fb.get("date_hi")
    a_reg = fa.get("is_registry") == "1" and a_lo is not None and a_hi is not None
    b_reg = fb.get("is_registry") == "1" and b_lo is not None and b_hi is not None
    if not a_reg and not b_reg:
        return None, 0.0, 0.0
    if a_lo is None or a_hi is None or b_lo is None or b_hi is None:
        return None, 0.0, 0.0
    lo = max(int(a_lo), int(b_lo)) - WINDOW_GRACE_DAYS
    hi = min(int(a_hi), int(b_hi)) + WINDOW_GRACE_DAYS
    if lo <= hi:
        return 2, 1.0, 0.0
    gap_years = (lo - hi) / 365.25
    # within ~2y of the window edge counts as partial (late paperwork), beyond
    # that it is a hard disagreement — the temporal-reuse signal.
    level = 1 if gap_years <= 2.0 else 0
    return level, 0.0, gap_years


def _name_levels(name_a: str, name_b: str) -> tuple[Optional[int], float, float]:
    if not name_a or not name_b:
        return None, 0.0, 0.0
    jw = jaro_winkler(name_a, name_b)
    tj = fuzzy_token_jaccard(core_tokens(name_a), core_tokens(name_b))
    if jw >= 0.92 or tj >= 0.7:
        level = 2
    elif jw >= 0.80 or tj >= 0.34:
        level = 1
    else:
        level = 0
    return level, jw, tj


def aircraft_pair_features(fa: dict, fb: dict) -> PairFeatures:
    tail_a, tail_b = str(fa.get("tail", "")), str(fb.get("tail", ""))
    tail_level: Optional[int] = None if not tail_a or not tail_b else (2 if tail_a == tail_b else 0)

    ser_a, ser_b = str(fa.get("serial", "")), str(fb.get("serial", ""))
    if not ser_a or not ser_b:
        serial_level: Optional[int] = None
        serial_jw = 0.0
    else:
        serial_jw = jaro_winkler(ser_a, ser_b)
        serial_level = 2 if ser_a == ser_b else (1 if serial_jw >= 0.88 else 0)

    mod_a, mod_b = str(fa.get("model", "")), str(fb.get("model", ""))
    if not mod_a or not mod_b:
        model_level: Optional[int] = None
        model_sim = 0.0
    else:
        toks_a, toks_b = mod_a.split(), mod_b.split()
        sim_tok = fuzzy_token_jaccard(toks_a, toks_b)
        sim_3g = token_jaccard(char_ngrams(mod_a), char_ngrams(mod_b))
        contained = fuzzy_token_containment(toks_a, toks_b)
        model_sim = max(sim_tok, sim_3g, 0.7 if contained else 0.0)
        model_level = 2 if model_sim >= 0.6 else (1 if model_sim >= 0.2 else 0)

    window_level, win_overlap, win_gap = _window_relation(fa, fb)
    name_level, name_jw, name_tj = _name_levels(str(fa.get("name", "")), str(fb.get("name", "")))

    cont = (
        ("kind_aircraft", 1.0),
        ("tail_exact", 1.0 if tail_level == 2 else 0.0),
        ("serial_exact", 1.0 if serial_level == 2 else 0.0),
        ("serial_jw", serial_jw),
        ("serial_conflict", 1.0 if serial_level == 0 else 0.0),
        ("serial_missing", 1.0 if serial_level is None else 0.0),
        ("model_sim", model_sim),
        ("model_missing", 1.0 if model_level is None else 0.0),
        ("window_overlap", win_overlap),
        ("window_gap_years", min(win_gap, 40.0) / 40.0),
        ("window_missing", 1.0 if window_level is None else 0.0),
        ("name_jw", name_jw),
        ("name_token_jaccard", name_tj),
        ("name_missing", 1.0 if name_level is None else 0.0),
    )
    levels = tuple(_lv(v) for v in (tail_level, serial_level, model_level, window_level, name_level))
    return PairFeatures(kind="aircraft", levels=levels, continuous=cont)


# ---------------------------------------------------------------- operator


def _acronym_match(toks_a: list[str], toks_b: list[str]) -> bool:
    """UNITED PARCEL SERVICE ~ UPS: initials of one side equal a single token
    of the other (>= 2 letters, generic — no lookup tables)."""
    for single, multi in ((toks_a, toks_b), (toks_b, toks_a)):
        if len(single) >= 1 and len(multi) >= 2:
            initials = "".join(t[0] for t in multi)
            if len(initials) >= 2 and single[0] == initials:
                return True
    return False


def _fused_prefix_match(toks_a: list[str], toks_b: list[str]) -> bool:
    """FEDEX ~ FEDERAL EXPRESS: one token splits into prefixes (>=2 chars) of
    the other side's first two tokens."""
    for single, multi in ((toks_a, toks_b), (toks_b, toks_a)):
        if len(single) >= 1 and len(multi) >= 2:
            t = single[0]
            b1, b2 = multi[0], multi[1]
            for cut in range(2, len(t) - 1):
                x, y = t[:cut], t[cut:]
                if len(y) >= 2 and b1.startswith(x) and b2.startswith(y):
                    return True
    return False


def operator_pair_features(fa: dict, fb: dict) -> PairFeatures:
    na, nb = str(fa.get("name_norm", "")), str(fb.get("name_norm", ""))
    toks_a, toks_b = core_tokens(na), core_tokens(nb)

    jw = jaro_winkler(na, nb)
    tj = fuzzy_token_jaccard(toks_a, toks_b)
    contained = fuzzy_token_containment(toks_a, toks_b)
    alias = _acronym_match(toks_a, toks_b) or _fused_prefix_match(toks_a, toks_b)

    tails_a: set[str] = set(fa.get("tails") or ())
    tails_b: set[str] = set(fb.get("tails") or ())
    shared_tail = bool(tails_a & tails_b)

    name_level = 2 if jw >= 0.93 else (1 if jw >= 0.85 else 0)
    token_level = 2 if (tj >= 0.7 or contained) else (1 if tj >= 0.34 else 0)
    alias_level = 2 if alias else 0
    tail_level = 2 if shared_tail else 0

    cont = (
        ("kind_aircraft", 0.0),
        ("name_jw", jw),
        ("name_token_jaccard", tj),
        ("name_containment", 1.0 if contained else 0.0),
        ("alias_signal", 1.0 if alias else 0.0),
        ("shared_tail", 1.0 if shared_tail else 0.0),
    )
    return PairFeatures(
        kind="operator",
        levels=(name_level, token_level, alias_level, tail_level),
        continuous=cont,
    )


def pair_features(kind: str, fa: dict, fb: dict) -> PairFeatures:
    if kind == "aircraft":
        return aircraft_pair_features(fa, fb)
    if kind == "operator":
        return operator_pair_features(fa, fb)
    raise ValueError(f"unknown entity kind {kind!r}")


# ------------------------------------------------------------ Fellegi-Sunter


@dataclass(slots=True)
class FellegiSunter:
    """Two-class conditionally-independent FS model fit by EM."""

    fields: tuple[str, ...]
    seed: int = 17
    max_iter: int = 300
    tol: float = 1e-8
    smoothing: float = 0.5  # Laplace pseudo-count per (field, level)

    m: np.ndarray = field(init=False)         # (n_fields, N_LEVELS)
    u: np.ndarray = field(init=False)
    prior: float = field(init=False, default=0.5)
    converged: bool = field(init=False, default=False)
    n_iter: int = field(init=False, default=0)
    loglik_trace: list[float] = field(init=False, default_factory=list)

    # ------------------------------------------------------------------ fit

    def fit(self, level_rows: Sequence[Sequence[int]]) -> "FellegiSunter":
        L = np.asarray(level_rows, dtype=np.int64)
        if L.ndim != 2 or L.shape[1] != len(self.fields):
            raise ValueError("level matrix shape mismatch")
        n, f = L.shape
        if n == 0:
            raise ValueError("cannot fit on zero pairs")
        observed = L != MISSING
        # one-hot (n, f, N_LEVELS) of observed levels
        onehot = np.zeros((n, f, N_LEVELS), dtype=np.float64)
        idx = np.where(observed)
        onehot[idx[0], idx[1], L[idx]] = 1.0

        rng = np.random.default_rng(self.seed)
        # random init (exchangeable components; identified post hoc)
        m = rng.uniform(0.2, 0.8, size=(f, N_LEVELS))
        u = rng.uniform(0.2, 0.8, size=(f, N_LEVELS))
        m /= m.sum(axis=1, keepdims=True)
        u /= u.sum(axis=1, keepdims=True)
        p = float(rng.uniform(0.2, 0.8))

        prev_ll = -np.inf
        self.loglik_trace = []
        self.converged = False
        for it in range(1, self.max_iter + 1):
            # E-step: responsibilities in log space
            log_m = np.einsum("nfl,fl->n", onehot, np.log(m))
            log_u = np.einsum("nfl,fl->n", onehot, np.log(u))
            a = math.log(p) + log_m
            b = math.log(1.0 - p) + log_u
            mx = np.maximum(a, b)
            ll = float(np.sum(mx + np.log(np.exp(a - mx) + np.exp(b - mx))))
            self.loglik_trace.append(ll)
            r = np.exp(a - mx) / (np.exp(a - mx) + np.exp(b - mx))  # P(match | row)

            # M-step with Laplace smoothing (avoids zero cells -> infinite weights)
            w_m = np.einsum("n,nfl->fl", r, onehot) + self.smoothing
            w_u = np.einsum("n,nfl->fl", 1.0 - r, onehot) + self.smoothing
            m = w_m / w_m.sum(axis=1, keepdims=True)
            u = w_u / w_u.sum(axis=1, keepdims=True)
            p = float(np.clip(r.mean(), 1e-6, 1.0 - 1e-6))

            self.n_iter = it
            if abs(ll - prev_ll) < self.tol * (1.0 + abs(ll)):
                self.converged = True
                break
            prev_ll = ll

        # component identification: the MATCH class has the larger total
        # full-agreement probability mass across fields (deterministic rule).
        if float(m[:, 2].sum()) < float(u[:, 2].sum()):
            m, u = u, m
            p = 1.0 - p
        self.m, self.u, self.prior = m, u, p
        return self

    # -------------------------------------------------------------- scoring

    def weight(self, levels: Sequence[int]) -> float:
        """sum over observed fields of log2(m_f[l] / u_f[l])."""
        w = 0.0
        for fidx, lvl in enumerate(levels):
            if lvl == MISSING:
                continue
            w += math.log2(self.m[fidx, lvl] / self.u[fidx, lvl])
        return w

    def posterior(self, levels: Sequence[int]) -> float:
        """P(match | levels) = 1 / (1 + (1-p)/p * 2^-w)."""
        w = self.weight(levels)
        odds = self.prior / (1.0 - self.prior) * (2.0**w)
        return odds / (1.0 + odds)

    def weight_threshold(self, posterior_cut: float) -> float:
        """The weight w at which the posterior equals posterior_cut (the
        two-threshold band is expressed in weight space)."""
        return math.log2(posterior_cut / (1.0 - posterior_cut)) - math.log2(
            self.prior / (1.0 - self.prior)
        )

    def field_weights(self) -> dict[str, dict[str, float]]:
        """Per-field log2(m/u) by level name — README/report surface."""
        names = ("disagree", "partial", "agree")
        return {
            fname: {
                names[lvl]: float(math.log2(self.m[fidx, lvl] / self.u[fidx, lvl]))
                for lvl in range(N_LEVELS)
            }
            for fidx, fname in enumerate(self.fields)
        }
