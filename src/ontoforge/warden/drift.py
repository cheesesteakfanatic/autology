"""Sketch-drift sentinels (whitepaper §5.3 generator ii).

For every profiled stream (a sequence of contracts.TableProfile over cycles) run
sequential drift tests on the sketch vector:

- **PSI** on quantile sketches: proper population-stability index over the
  baseline's decile buckets, with the current distribution's bucket mass read
  off its own quantile sketch via piecewise-linear CDF inversion.
- **MinHash-Jaccard shift** on value sets (categorical / id columns).
- **EWMA control charts** (3-sigma limits) on null-rate and cardinality-ratio
  per column — the quality stream.
- **Schema diff** between consecutive profiles: column added / removed /
  renamed (paired add+remove with high value-set overlap) / type-changed /
  format-signature-changed.

Each detector emits a DriftSignal(kind ∈ {schema, distribution, quality}) with a
normalized statistic/threshold pair and a severity in [0, 1] that the routing
layer feeds to the Decision Spine (a drift alarm is a decision; §5.3 calibration).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

from ontoforge.contracts import TableProfile, minhash_jaccard

__all__ = [
    "DriftSignal",
    "DriftSentinel",
    "EwmaChart",
    "population_stability_index",
    "severity_of",
]

# Industry-standard PSI bands: < 0.1 stable, 0.1-0.2 moderate, > 0.2 major shift.
PSI_THRESHOLD = 0.2
# Jaccard distance (1 - J) beyond which the value set is considered shifted.
JACCARD_DISTANCE_THRESHOLD = 0.5
# EWMA chart parameters.
EWMA_LAMBDA = 0.3
EWMA_L = 3.0
# Columns whose removed/added minhash overlap exceeds this are a rename, not drop+add.
RENAME_JACCARD = 0.8
_PSI_EPS = 1e-4


@dataclass(frozen=True, slots=True)
class DriftSignal:
    kind: str          # "schema" | "distribution" | "quality"
    table: str
    column: str
    detector: str      # psi | jaccard | null_rate_ewma | cardinality_ewma |
    #                  # column_added | column_removed | column_renamed |
    #                  # column_retyped | format_signature_changed
    statistic: float   # normalized so that bigger is worse
    threshold: float   # alarm-worthy when statistic > threshold
    severity: float    # [0, 1]; feeds the spine's warden.alarm decision
    detail: str = ""
    cycle: int = 0


def severity_of(statistic: float, threshold: float) -> float:
    """Map a (statistic, threshold) excursion to [0.5, 0.99]: 0.5 at the
    threshold, saturating at twice the threshold. Routing hands this to the
    spine, whose tau_high is the calibrated alert-precision knob."""
    if threshold <= 0:
        return 0.99
    excess = (statistic - threshold) / threshold
    return round(min(0.99, 0.5 + 0.5 * max(0.0, min(1.0, excess))), 6)


# --------------------------------------------------------------------- PSI


def _cdf_from_deciles(deciles: tuple[float, ...], x: float) -> float:
    """Piecewise-linear CDF read off an 11-point decile sketch (q=0.0..1.0)."""
    d = deciles
    if x <= d[0]:
        return 0.0
    if x >= d[-1]:
        return 1.0
    n = len(d) - 1
    for j in range(n):
        lo, hi = d[j], d[j + 1]
        if lo <= x <= hi:
            if hi == lo:
                continue  # zero-width segment: mass attributed by a later segment
            return (j + (x - lo) / (hi - lo)) / n
    return 1.0


def population_stability_index(
    baseline_deciles: tuple[float, ...],
    current_deciles: tuple[float, ...],
    eps: float = _PSI_EPS,
) -> Optional[float]:
    """PSI over the baseline's decile buckets.

    Buckets are the baseline's 10 inter-decile intervals (expected share 0.1
    each; zero-width buckets from tied deciles are merged into their
    neighbour). Actual shares come from CDF-inverting the current sketch.
    PSI = sum_i (a_i - e_i) * ln(a_i / e_i), with eps-clamped shares.
    """
    if len(baseline_deciles) != 11 or len(current_deciles) != 11:
        return None
    cuts = list(baseline_deciles)
    # merge zero-width buckets: keep cut points strictly increasing
    edges = [cuts[0]]
    expected: list[float] = []
    pending = 0.0
    for i in range(10):
        pending += 0.1
        if cuts[i + 1] > edges[-1]:
            edges.append(cuts[i + 1])
            expected.append(pending)
            pending = 0.0
    if pending > 0 and expected:
        expected[-1] += pending
    if not expected:
        return 0.0  # constant baseline column: PSI undefined-as-zero
    psi = 0.0
    for k in range(len(expected)):
        lo, hi = edges[k], edges[k + 1]
        a = _cdf_from_deciles(current_deciles, hi) - _cdf_from_deciles(current_deciles, lo)
        if k == 0:  # include current mass below the baseline's minimum
            a += _cdf_from_deciles(current_deciles, lo)
        if k == len(expected) - 1:  # ... and above its maximum
            a += 1.0 - _cdf_from_deciles(current_deciles, hi)
        e = max(expected[k], eps)
        a = max(a, eps)
        psi += (a - e) * math.log(a / e)
    return psi


# --------------------------------------------------------------- EWMA chart


@dataclass(slots=True)
class EwmaChart:
    """EWMA control chart with 3-sigma limits (quality stream, §5.3).

    The first `warmup` observations estimate (mu0, sigma0) and never alarm;
    sigma0 is floored (`sigma_floor`) so a perfectly stable baseline still
    yields finite limits. After warmup, z_t = lam*x + (1-lam)*z_{t-1} is
    compared against mu0 ± L * sigma0 * sqrt(lam/(2-lam) * (1-(1-lam)^(2t))).
    """

    lam: float = EWMA_LAMBDA
    L: float = EWMA_L
    warmup: int = 3
    sigma_floor: float = 0.005
    _baseline: list[float] = field(default_factory=list)
    _mu0: float = 0.0
    _sigma0: float = 0.0
    _z: float = 0.0
    _t: int = 0

    def update(self, x: float) -> Optional[tuple[float, float]]:
        """Feed one observation. Returns (deviation_in_sigma_units, L) when the
        chart is live — i.e. statistic/threshold for the sentinel — else None
        during warmup. Alarm condition: deviation > L."""
        if len(self._baseline) < self.warmup:
            self._baseline.append(x)
            if len(self._baseline) == self.warmup:
                n = len(self._baseline)
                self._mu0 = sum(self._baseline) / n
                var = sum((v - self._mu0) ** 2 for v in self._baseline) / max(1, n - 1)
                self._sigma0 = max(math.sqrt(var), self.sigma_floor)
                self._z = self._mu0
            return None
        self._t += 1
        self._z = self.lam * x + (1.0 - self.lam) * self._z
        width = self._sigma0 * math.sqrt(
            self.lam / (2.0 - self.lam) * (1.0 - (1.0 - self.lam) ** (2 * self._t))
        )
        dev = abs(self._z - self._mu0) / width if width > 0 else 0.0
        return dev, self.L


# ----------------------------------------------------------------- sentinel


@dataclass(slots=True)
class _ColumnState:
    null_chart: EwmaChart
    card_chart: EwmaChart


class DriftSentinel:
    """Stateful drift watcher over a stream of TableProfiles for ONE estate.

    observe(profile) ingests the profile for `profile.table` at the next cycle
    and returns the DriftSignals raised this cycle. The first profile of a
    table is its baseline (PSI / Jaccard reference); EWMA charts warm up over
    `ewma_warmup` cycles before they can alarm.
    """

    def __init__(
        self,
        *,
        psi_threshold: float = PSI_THRESHOLD,
        jaccard_distance_threshold: float = JACCARD_DISTANCE_THRESHOLD,
        ewma_lambda: float = EWMA_LAMBDA,
        ewma_L: float = EWMA_L,
        ewma_warmup: int = 3,
        null_sigma_floor: float = 0.005,
        card_sigma_floor: float = 0.01,
    ) -> None:
        self.psi_threshold = psi_threshold
        self.jaccard_distance_threshold = jaccard_distance_threshold
        self.ewma_lambda = ewma_lambda
        self.ewma_L = ewma_L
        self.ewma_warmup = ewma_warmup
        self.null_sigma_floor = null_sigma_floor
        self.card_sigma_floor = card_sigma_floor
        self._baseline: dict[str, TableProfile] = {}
        self._prev: dict[str, TableProfile] = {}
        self._charts: dict[tuple[str, str], _ColumnState] = {}
        self._cycle: dict[str, int] = {}

    # -- helpers

    def _chart(self, table: str, column: str) -> _ColumnState:
        key = (table, column)
        if key not in self._charts:
            self._charts[key] = _ColumnState(
                null_chart=EwmaChart(
                    lam=self.ewma_lambda, L=self.ewma_L,
                    warmup=self.ewma_warmup, sigma_floor=self.null_sigma_floor,
                ),
                card_chart=EwmaChart(
                    lam=self.ewma_lambda, L=self.ewma_L,
                    warmup=self.ewma_warmup, sigma_floor=self.card_sigma_floor,
                ),
            )
        return self._charts[key]

    @staticmethod
    def _cardinality_ratio(cp) -> float:
        nn = cp.row_count - cp.null_count
        return cp.distinct_estimate / nn if nn > 0 else 0.0

    # -- main entry

    def observe(self, profile: TableProfile) -> list[DriftSignal]:
        table = profile.table
        cycle = self._cycle.get(table, 0)
        self._cycle[table] = cycle + 1
        signals: list[DriftSignal] = []

        baseline = self._baseline.get(table)
        prev = self._prev.get(table)
        if baseline is None:
            self._baseline[table] = profile
        else:
            signals.extend(self._schema_signals(prev or baseline, profile, cycle))
            signals.extend(self._distribution_signals(baseline, profile, cycle))
        signals.extend(self._quality_signals(profile, cycle))
        self._prev[table] = profile
        return signals

    # -- schema drift

    def _schema_signals(
        self, prev: TableProfile, cur: TableProfile, cycle: int
    ) -> list[DriftSignal]:
        out: list[DriftSignal] = []
        prev_cols, cur_cols = set(prev.columns), set(cur.columns)
        removed = sorted(prev_cols - cur_cols)
        added = sorted(cur_cols - prev_cols)

        # pair removed/added columns whose value sets overlap: a rename
        renamed: list[tuple[str, str, float]] = []
        used_added: set[str] = set()
        for r in removed:
            mh_r = prev.columns[r].minhash
            best, best_j = None, 0.0
            for a in added:
                if a in used_added or not mh_r:
                    continue
                j = minhash_jaccard(mh_r, cur.columns[a].minhash)
                if j > best_j:
                    best, best_j = a, j
            if best is not None and best_j >= RENAME_JACCARD:
                renamed.append((r, best, best_j))
                used_added.add(best)

        renamed_from = {r for r, _, _ in renamed}
        renamed_to = {a for _, a, _ in renamed}
        for r, a, j in renamed:
            out.append(DriftSignal(
                kind="schema", table=cur.table, column=r, detector="column_renamed",
                statistic=1.0, threshold=0.5, severity=0.99,
                detail=f"column {r!r} renamed to {a!r} (value-set jaccard={j:.2f})",
                cycle=cycle,
            ))
        for r in removed:
            if r in renamed_from:
                continue
            out.append(DriftSignal(
                kind="schema", table=cur.table, column=r, detector="column_removed",
                statistic=1.0, threshold=0.5, severity=0.99,
                detail=f"column {r!r} disappeared", cycle=cycle,
            ))
        for a in added:
            if a in renamed_to:
                continue
            out.append(DriftSignal(
                kind="schema", table=cur.table, column=a, detector="column_added",
                statistic=1.0, threshold=0.5, severity=0.99,
                detail=f"new column {a!r} (type={cur.columns[a].inferred_type.value})",
                cycle=cycle,
            ))

        for name in sorted(prev_cols & cur_cols):
            p, c = prev.columns[name], cur.columns[name]
            if p.inferred_type is not c.inferred_type:
                out.append(DriftSignal(
                    kind="schema", table=cur.table, column=name, detector="column_retyped",
                    statistic=1.0, threshold=0.5, severity=0.99,
                    detail=f"type {p.inferred_type.value} -> {c.inferred_type.value}",
                    cycle=cycle,
                ))
            elif (
                p.format_signature and c.format_signature
                and p.format_signature != c.format_signature
            ):
                out.append(DriftSignal(
                    kind="schema", table=cur.table, column=name,
                    detector="format_signature_changed",
                    statistic=1.0, threshold=0.5, severity=0.9,
                    detail=f"format {p.format_signature!r} -> {c.format_signature!r}",
                    cycle=cycle,
                ))
        return out

    # -- distribution drift

    def _distribution_signals(
        self, baseline: TableProfile, cur: TableProfile, cycle: int
    ) -> list[DriftSignal]:
        out: list[DriftSignal] = []
        for name in sorted(set(baseline.columns) & set(cur.columns)):
            b, c = baseline.columns[name], cur.columns[name]
            if b.quantiles and c.quantiles:
                psi = population_stability_index(b.quantiles, c.quantiles)
                if psi is not None and psi > self.psi_threshold:
                    out.append(DriftSignal(
                        kind="distribution", table=cur.table, column=name, detector="psi",
                        statistic=round(psi, 6), threshold=self.psi_threshold,
                        severity=severity_of(psi, self.psi_threshold),
                        detail=f"PSI={psi:.3f} over baseline decile buckets", cycle=cycle,
                    ))
            if b.minhash and c.minhash and b.inferred_type is c.inferred_type:
                dist = 1.0 - minhash_jaccard(b.minhash, c.minhash)
                if dist > self.jaccard_distance_threshold:
                    out.append(DriftSignal(
                        kind="distribution", table=cur.table, column=name, detector="jaccard",
                        statistic=round(dist, 6), threshold=self.jaccard_distance_threshold,
                        severity=severity_of(dist, self.jaccard_distance_threshold),
                        detail=f"value-set jaccard distance {dist:.2f} vs baseline",
                        cycle=cycle,
                    ))
        return out

    # -- quality drift (control charts)

    def _quality_signals(self, cur: TableProfile, cycle: int) -> list[DriftSignal]:
        out: list[DriftSignal] = []
        for name in sorted(cur.columns):
            cp = cur.columns[name]
            state = self._chart(cur.table, name)
            res = state.null_chart.update(cp.null_rate)
            if res is not None:
                dev, limit = res
                if dev > limit:
                    out.append(DriftSignal(
                        kind="quality", table=cur.table, column=name, detector="null_rate_ewma",
                        statistic=round(dev, 6), threshold=limit,
                        severity=severity_of(dev, limit),
                        detail=f"null-rate EWMA at {dev:.1f} sigma (limit {limit})", cycle=cycle,
                    ))
            res = state.card_chart.update(self._cardinality_ratio(cp))
            if res is not None:
                dev, limit = res
                if dev > limit:
                    out.append(DriftSignal(
                        kind="quality", table=cur.table, column=name, detector="cardinality_ewma",
                        statistic=round(dev, 6), threshold=limit,
                        severity=severity_of(dev, limit),
                        detail=f"cardinality-ratio EWMA at {dev:.1f} sigma (limit {limit})",
                        cycle=cycle,
                    ))
        return out
