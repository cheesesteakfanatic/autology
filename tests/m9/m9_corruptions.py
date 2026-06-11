"""Deterministic corruption injectors for the M9 injected-corruption suite.

Each trial: take an estate table slice, feed `WARMUP_CYCLES` clean profiles to
a fresh DriftSentinel (baseline + EWMA warmup), then one corrupted (or clean /
benign-append, for negatives) profile, route the signals through the spine, and
ask whether ANY alarm fired.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

import pandas as pd

from ontoforge.contracts import TableProfile
from ontoforge.profiling import profile_column
from ontoforge.warden import DriftSentinel, RoutingResult, WardenRouter

WARMUP_CYCLES = 4  # 1 baseline + 3 EWMA warmup observations


def quick_profile(df: pd.DataFrame, source_id: str, table: str) -> TableProfile:
    """contracts.TableProfile via per-column profiling (skips FD/key discovery,
    which drift sentinels don't read) — keeps 60 trials fast."""
    cols = {c: profile_column(source_id, table, c, df[c].tolist())[0] for c in df.columns}
    return TableProfile(source_id=source_id, table=table, row_count=len(df), columns=cols)
_NUM_RE = re.compile(r"^\s*([0-9]+(?:\.[0-9]+)?)\s*$")


def _scale_numeric(v: str, factor: float) -> str:
    m = _NUM_RE.match(str(v))
    if m is None:
        return v
    x = float(m.group(1)) * factor
    return str(int(round(x))) if "." not in m.group(1) else f"{x:.1f}"


# ------------------------------------------------------------- injectors


def null_spike(df: pd.DataFrame, column: str, fraction: float = 0.4) -> pd.DataFrame:
    out = df.copy()
    n = int(len(out) * fraction)
    out[column] = out[column].astype(object)
    out.loc[out.index[:n], column] = None
    return out


def unit_swap(df: pd.DataFrame, column: str, factor: float = 3.28084) -> pd.DataFrame:
    """Upstream silently re-bases the unit: every magnitude scales, lexical
    shape (int/float) preserved."""
    out = df.copy()
    out[column] = out[column].map(lambda v: _scale_numeric(v, factor))
    return out


def value_set_shift(df: pd.DataFrame, column: str) -> pd.DataFrame:
    """Upstream recodes the vocabulary: every distinct value maps to a new code."""
    out = df.copy()
    mapping = {v: f"CODE_{i:03d}" for i, v in enumerate(sorted(out[column].unique()))}
    out[column] = out[column].map(mapping)
    return out


def cardinality_explosion(df: pd.DataFrame, column: str) -> pd.DataFrame:
    """A low-cardinality column suddenly becomes per-row unique (bad join key,
    fan-out bug upstream)."""
    out = df.copy()
    out[column] = [f"{v}-{i}" for i, v in enumerate(out[column])]
    return out


def schema_rename(df: pd.DataFrame, column: str) -> pd.DataFrame:
    return df.rename(columns={column: f"{column}_RENAMED"})


def schema_drop(df: pd.DataFrame, column: str) -> pd.DataFrame:
    return df.drop(columns=[column])


def schema_retype(df: pd.DataFrame, column: str) -> pd.DataFrame:
    """A numeric column starts arriving as opaque tokens."""
    out = df.copy()
    out[column] = [f"X{v}" for v in out[column]]
    return out


def _rotate_digits(s: str) -> str:
    """Format-preserving fresh value: each digit d -> (d+3) % 10."""
    return "".join(str((int(ch) + 3) % 10) if ch.isdigit() else ch for ch in str(s))


def benign_append(df: pd.DataFrame, key_column: str, fraction: float = 0.05) -> pd.DataFrame:
    """Negative control: organic growth — new rows whose key values are fresh
    but format-consistent, all other columns drawn from the existing
    distributions. Must NOT alarm."""
    n = max(1, int(len(df) * fraction))
    extra = df.iloc[:n].copy()
    extra[key_column] = [_rotate_digits(v) for v in extra[key_column]]
    return pd.concat([df, extra], ignore_index=True)


# ----------------------------------------------------------------- trials


@dataclass(frozen=True)
class Trial:
    name: str
    corruption: str          # corruption type or 'clean' / 'benign_append'
    table: str
    columns: tuple[str, ...]  # slice profiled (keeps the suite fast)
    target: str               # corrupted column
    inject: Callable[[pd.DataFrame], pd.DataFrame]
    is_positive: bool
    expected_route: str = ""  # 'temper' | 'anvil' | 'quarantine' | ''


def run_trial(trial: Trial, estate: dict, *, router: WardenRouter | None = None) -> RoutingResult:
    df = estate["tables"][trial.table][list(trial.columns)].copy()
    source = estate["metadata"]["tables"][trial.table]["source_id"]
    sentinel = DriftSentinel()
    router = router if router is not None else WardenRouter()
    for _ in range(WARMUP_CYCLES):
        warm = sentinel.observe(quick_profile(df, source, trial.table))
        router.route(warm)  # warmup signals (there should be none) still adjudicated
    final = trial.inject(df)
    signals = sentinel.observe(quick_profile(final, source, trial.table))
    return router.route(signals)


def make_trials() -> list[Trial]:
    """40 corruption trials (8 per type) + 20 negative trials, all deterministic."""
    t: list[Trial] = []

    def add(corruption, table, cols, target, fn, expected=""):
        t.append(Trial(
            name=f"{corruption}/{table}.{target}",
            corruption=corruption, table=table, columns=tuple(cols), target=target,
            inject=fn, is_positive=True, expected_route=expected,
        ))

    # --- null spikes (quality -> quarantine): 8
    for table, cols, target in [
        ("faa_master", ["N-NUMBER", "REGISTRANT NAME"], "REGISTRANT NAME"),
        ("faa_master", ["N-NUMBER", "CITY"], "CITY"),
        ("asrs_reports", ["ACN", "AIRCRAFT 1 OPERATOR"], "AIRCRAFT 1 OPERATOR"),
        ("asrs_reports", ["ACN", "SYNOPSIS"], "SYNOPSIS"),
        ("ntsb_events", ["EV_ID", "OPERATOR"], "OPERATOR"),
        ("ntsb_events", ["EV_ID", "EV_CITY"], "EV_CITY"),
        ("maintenance_erp", ["WORK_ORDER_ID", "OPERATOR_NAME"], "OPERATOR_NAME"),
        ("maintenance_erp", ["WORK_ORDER_ID", "COMPONENT"], "COMPONENT"),
    ]:
        add("null_spike", table, cols, target,
            lambda df, c=target: null_spike(df, c), "quarantine")

    # --- unit swaps (distribution -> anvil re-verification): 8
    for table, cols, target, factor in [
        ("faa_acftref", ["CODE", "SPEED"], "SPEED", 3.28084),
        ("faa_acftref", ["CODE", "NO-SEATS"], "NO-SEATS", 2.20462),
        ("maintenance_erp", ["WORK_ORDER_ID", "LABOR_HOURS"], "LABOR_HOURS", 60.0),
        ("maintenance_erp", ["WORK_ORDER_ID", "ATA_CHAPTER"], "ATA_CHAPTER", 3.28084),
        ("faa_master", ["N-NUMBER", "YEAR MFR"], "YEAR MFR", 0.3048),
        ("faa_master", ["N-NUMBER", "ENG MFR MDL"], "ENG MFR MDL", 3.28084),
        ("asrs_reports", ["ACN", "DATE"], "DATE", 0.3048),
        ("faa_acftref", ["CODE", "NO-ENG"], "NO-ENG", 10.0),
    ]:
        add("unit_swap", table, cols, target,
            lambda df, c=target, f=factor: unit_swap(df, c, f), "anvil")

    # --- value-set shifts (distribution -> anvil re-verification): 8
    for table, cols, target in [
        ("asrs_reports", ["ACN", "FLIGHT PHASE"], "FLIGHT PHASE"),
        ("asrs_reports", ["ACN", "AIRCRAFT 1 MAKE MODEL"], "AIRCRAFT 1 MAKE MODEL"),
        ("ntsb_events", ["EV_ID", "DAMAGE"], "DAMAGE"),
        ("ntsb_events", ["EV_ID", "ACFT_MAKE"], "ACFT_MAKE"),
        ("maintenance_erp", ["WORK_ORDER_ID", "ACTION"], "ACTION"),
        ("maintenance_erp", ["WORK_ORDER_ID", "COMPONENT"], "COMPONENT"),
        ("faa_master", ["N-NUMBER", "STATE"], "STATE"),
        ("faa_acftref", ["CODE", "MFR"], "MFR"),
    ]:
        add("value_set_shift", table, cols, target,
            lambda df, c=target: value_set_shift(df, c), "anvil")

    # --- cardinality explosions (quality -> quarantine): 8
    for table, cols, target in [
        ("maintenance_erp", ["WORK_ORDER_ID", "ACTION"], "ACTION"),
        ("maintenance_erp", ["WORK_ORDER_ID", "OPERATOR_NAME"], "OPERATOR_NAME"),
        ("asrs_reports", ["ACN", "FLIGHT PHASE"], "FLIGHT PHASE"),
        ("asrs_reports", ["ACN", "STATE REFERENCE"], "STATE REFERENCE"),
        ("ntsb_events", ["EV_ID", "DAMAGE"], "DAMAGE"),
        ("ntsb_events", ["EV_ID", "EV_STATE"], "EV_STATE"),
        ("faa_master", ["N-NUMBER", "STATUS CODE"], "STATUS CODE"),
        ("faa_master", ["N-NUMBER", "REGION"], "REGION"),
    ]:
        add("cardinality_explosion", table, cols, target,
            lambda df, c=target: cardinality_explosion(df, c), "quarantine")

    # --- schema changes (-> TEMPER proposals): 8 (3 renames, 3 drops, 2 retypes)
    for table, cols, target, fn, op in [
        ("asrs_reports", ["ACN", "FLIGHT PHASE"], "ACN", schema_rename, "RenameProperty"),
        ("maintenance_erp", ["WORK_ORDER_ID", "ACTION"], "WORK_ORDER_ID", schema_rename, "RenameProperty"),
        ("ntsb_events", ["EV_ID", "DAMAGE"], "EV_ID", schema_rename, "RenameProperty"),
        ("faa_master", ["N-NUMBER", "STATE"], "STATE", schema_drop, "RemoveProperty"),
        ("ntsb_events", ["EV_ID", "DAMAGE"], "DAMAGE", schema_drop, "RemoveProperty"),
        ("maintenance_erp", ["WORK_ORDER_ID", "ACTION"], "ACTION", schema_drop, "RemoveProperty"),
        ("faa_acftref", ["CODE", "SPEED"], "SPEED", schema_retype, "RetypeProperty"),
        ("maintenance_erp", ["WORK_ORDER_ID", "ATA_CHAPTER"], "ATA_CHAPTER", schema_retype, "RetypeProperty"),
    ]:
        t.append(Trial(
            name=f"schema_change/{table}.{target}:{op}",
            corruption="schema_change", table=table, columns=tuple(cols), target=target,
            inject=(lambda df, c=target, f=fn: f(df, c)),
            is_positive=True, expected_route="temper",
        ))

    # --- negatives: 12 clean re-profiles + 8 benign appends
    clean_slices = [
        ("faa_master", ["N-NUMBER", "REGISTRANT NAME", "STATE"]),
        ("faa_master", ["N-NUMBER", "YEAR MFR"]),
        ("faa_master", ["N-NUMBER", "CITY", "STATUS CODE"]),
        ("faa_acftref", ["CODE", "SPEED", "MFR"]),
        ("faa_acftref", ["CODE", "NO-SEATS"]),
        ("asrs_reports", ["ACN", "FLIGHT PHASE", "AIRCRAFT 1 OPERATOR"]),
        ("asrs_reports", ["ACN", "DATE", "SYNOPSIS"]),
        ("ntsb_events", ["EV_ID", "DAMAGE", "OPERATOR"]),
        ("ntsb_events", ["EV_ID", "EV_CITY"]),
        ("maintenance_erp", ["WORK_ORDER_ID", "ACTION", "OPERATOR_NAME"]),
        ("maintenance_erp", ["WORK_ORDER_ID", "LABOR_HOURS"]),
        ("maintenance_erp", ["WORK_ORDER_ID", "COMPONENT", "ATA_CHAPTER"]),
    ]
    for table, cols in clean_slices:
        t.append(Trial(
            name=f"clean/{table}.{cols[-1]}", corruption="clean", table=table,
            columns=tuple(cols), target=cols[-1], inject=lambda df: df.copy(),
            is_positive=False,
        ))
    append_slices = [
        ("faa_master", ["N-NUMBER", "REGISTRANT NAME"], "N-NUMBER"),
        ("faa_master", ["N-NUMBER", "STATE"], "N-NUMBER"),
        ("asrs_reports", ["ACN", "FLIGHT PHASE"], "ACN"),
        ("asrs_reports", ["ACN", "AIRCRAFT 1 OPERATOR"], "ACN"),
        ("ntsb_events", ["EV_ID", "DAMAGE"], "EV_ID"),
        ("ntsb_events", ["EV_ID", "OPERATOR"], "EV_ID"),
        ("maintenance_erp", ["WORK_ORDER_ID", "ACTION"], "WORK_ORDER_ID"),
        ("maintenance_erp", ["WORK_ORDER_ID", "OPERATOR_NAME"], "WORK_ORDER_ID"),
    ]
    for table, cols, key in append_slices:
        t.append(Trial(
            name=f"benign_append/{table}.{cols[-1]}", corruption="benign_append", table=table,
            columns=tuple(cols), target=key,
            inject=(lambda df, k=key: benign_append(df, k)),
            is_positive=False,
        ))
    return t
