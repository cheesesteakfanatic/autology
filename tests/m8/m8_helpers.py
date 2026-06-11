"""M8 test helpers: clean synthetic tables, the SEEDED corruption suite, target
class specs, and end-to-end fix-rate measurement.

Corruptions are applied PROGRAMMATICALLY with seeded RNGs — detectors must
recover them from evidence, never from fixture names.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ontoforge.contracts import (
    COUNT,
    CURRENCY,
    LENGTH,
    ClassDef,
    Datatype,
    Ontology,
    PropertyDef,
    ShapeConstraint,
)
from ontoforge.profiling import profile_table

FT_PER_M = 1.0 / 0.3048

#: corruption class -> measured end-to-end fix rate (filled by the T0/T1 suites,
#: reported by test_m8_zz_report.py)
MEASURED_RATES: dict[str, float] = {}

SITES = ["ALB", "BOS", "CHI", "DEN", "ELP", "FRG", "GSO", "HOU"]
STATUSES = ["ACTIVE", "RETIRED", "MAINT"]


# -------------------------------------------------------------- clean table


def clean_sensors(n: int = 300, seed: int = 7) -> pd.DataFrame:
    """Clean synthetic table; all columns as strings (raw-layer convention)."""
    rng = np.random.default_rng(seed)
    ids = [f"SN-{i:04d}" for i in range(1, n + 1)]
    sites = [SITES[int(i)] for i in rng.integers(0, len(SITES), n)]
    statuses = [STATUSES[int(i)] for i in rng.integers(0, len(STATUSES), n)]
    readings = [f"{v:.2f}" for v in rng.uniform(0.5, 99.5, n)]
    prices = [f"{v:.2f}" for v in rng.uniform(10, 90000, n)]
    days = rng.integers(0, 364, n)
    months = 1 + (days // 31)
    dom = 1 + (days % 28)
    dates = [f"20{15 + int(i % 8)}-{m:02d}-{d:02d}" for i, (m, d) in enumerate(zip(months, dom))]
    lengths = [f"{v:.1f}" for v in rng.uniform(5, 900, n)]
    return pd.DataFrame(
        {
            "SENSOR_ID": ids,
            "SITE_CODE": sites,
            "STATUS": statuses,
            "READING": readings,
            "PRICE": prices,
            "INSTALLED": dates,
            "LENGTH_FT": lengths,
        }
    )


def sensor_class() -> ClassDef:
    ns = "onto://test/sensor"
    props = (
        PropertyDef(f"{ns}/prop/sensor_id", "sensor_id", Datatype.STRING, functional=True),
        PropertyDef(f"{ns}/prop/site_code", "site_code", Datatype.STRING),
        PropertyDef(f"{ns}/prop/status", "status", Datatype.STRING),
        PropertyDef(f"{ns}/prop/reading", "reading", Datatype.FLOAT, dimension=COUNT, unit="count"),
        PropertyDef(f"{ns}/prop/price", "price", Datatype.FLOAT, dimension=CURRENCY, unit="USD"),
        PropertyDef(f"{ns}/prop/installed", "installed", Datatype.DATE),
        PropertyDef(f"{ns}/prop/length_ft", "length_ft", Datatype.FLOAT, dimension=LENGTH, unit="ft"),
    )
    shapes = (
        ShapeConstraint("sensor_id", min_count=1, max_count=1, pattern=r"SN-[0-9]{4}"),
        ShapeConstraint("site_code", pattern=r"[A-Z]{3}"),
        ShapeConstraint("status", in_values=tuple(STATUSES)),
        ShapeConstraint("reading", datatype=Datatype.FLOAT, min_value=0, max_value=100),
        ShapeConstraint("price", datatype=Datatype.FLOAT, min_value=0, unit="USD"),
        ShapeConstraint("installed", min_count=1, datatype=Datatype.DATE),
        ShapeConstraint("length_ft", datatype=Datatype.FLOAT, min_value=0, max_value=3100, unit="ft"),
    )
    return ClassDef(uri=ns, name="Sensor", properties=props, shapes=shapes)


def sensor_ontology() -> Ontology:
    onto = Ontology()
    onto.add(sensor_class())
    return onto


def profile(df: pd.DataFrame, table: str = "sensors"):
    return profile_table(df, "m8test", table)


# ---------------------------------------------------------------- corruptors
# Each returns (corrupted_df, corruption_spec) where corruption_spec maps
# row position -> expected POST-TRANSFORM value for the corrupted column.


def corrupt_null_tokens(df: pd.DataFrame, seed: int = 11, frac: float = 0.25):
    rng = np.random.default_rng(seed)
    tokens = ["N/A", "NULL", "-", "UNK", "", "n/a"]
    out = df.copy()
    rows = sorted(rng.choice(len(df), int(frac * len(df)), replace=False).tolist())
    spec = {}
    for k, r in enumerate(rows):
        out.loc[r, "READING"] = tokens[k % len(tokens)]
        spec[r] = None  # expected: normalized to NULL
    return out, ("reading", spec)


def corrupt_padding(df: pd.DataFrame, seed: int = 12, frac: float = 0.3):
    rng = np.random.default_rng(seed)
    out = df.copy()
    rows = sorted(rng.choice(len(df), int(frac * len(df)), replace=False).tolist())
    spec = {}
    for k, r in enumerate(rows):
        clean = out.loc[r, "SITE_CODE"]
        out.loc[r, "SITE_CODE"] = " " * (1 + k % 3) + clean + " " * (1 + (k + 1) % 3)
        spec[r] = clean
    return out, ("site_code", spec)


def corrupt_case(df: pd.DataFrame, seed: int = 13, frac: float = 0.3):
    rng = np.random.default_rng(seed)
    out = df.copy()
    rows = sorted(rng.choice(len(df), int(frac * len(df)), replace=False).tolist())
    spec = {}
    for k, r in enumerate(rows):
        clean = out.loc[r, "STATUS"]
        out.loc[r, "STATUS"] = clean.lower() if k % 2 == 0 else clean.title()
        spec[r] = clean
    return out, ("status", spec)


def corrupt_dates(df: pd.DataFrame, seed: int = 14, frac: float = 0.4):
    rng = np.random.default_rng(seed)
    out = df.copy()
    rows = sorted(rng.choice(len(df), int(frac * len(df)), replace=False).tolist())
    spec = {}
    for k, r in enumerate(rows):
        iso = out.loc[r, "INSTALLED"]
        y, m, d = iso.split("-")
        out.loc[r, "INSTALLED"] = f"{m}/{d}/{y}" if k % 2 == 0 else f"{d}.{m}.{y}"
        spec[r] = iso
    return out, ("installed", spec)


def corrupt_currency(df: pd.DataFrame, seed: int = 15, frac: float = 0.4):
    rng = np.random.default_rng(seed)
    out = df.copy()
    rows = sorted(rng.choice(len(df), int(frac * len(df)), replace=False).tolist())
    spec = {}
    for k, r in enumerate(rows):
        v = float(out.loc[r, "PRICE"])
        styled = f"{v:,.2f}"
        out.loc[r, "PRICE"] = f"USD {styled}" if k % 2 == 0 else f"${styled}"
        spec[r] = v
    return out, ("price", spec)


def corrupt_units(df: pd.DataFrame, seed: int = 16, frac: float = 0.25):
    """Meters slice hiding in an ft column, with explicit 'm' suffixes (§3.2)."""
    rng = np.random.default_rng(seed)
    out = df.copy()
    rows = sorted(rng.choice(len(df), int(frac * len(df)), replace=False).tolist())
    spec = {}
    for r in rows:
        ft = float(out.loc[r, "LENGTH_FT"])
        meters = ft * 0.3048
        out.loc[r, "LENGTH_FT"] = f"{meters:.4f}m"
        spec[r] = float(f"{meters:.4f}") * FT_PER_M
    return out, ("length_ft", spec)


def corrupt_dup_rows(df: pd.DataFrame, seed: int = 17, frac: float = 0.15):
    rng = np.random.default_rng(seed)
    rows = sorted(rng.choice(len(df), int(frac * len(df)), replace=False).tolist())
    dups = df.iloc[rows]
    out = pd.concat([df, dups], ignore_index=True)
    perm = np.random.default_rng(seed + 1).permutation(len(out))
    out = out.iloc[perm].reset_index(drop=True)
    return out, ("__rows__", {"n_distinct": len(df), "n_dup": len(rows)})


def corrupt_header_rows(df: pd.DataFrame, seed: int = 18, copies: int = 6):
    header = {c: c for c in df.columns}
    rng = np.random.default_rng(seed)
    out = df.copy()
    blocks = [out]
    for _ in range(copies):
        blocks.append(pd.DataFrame([header]))
    out = pd.concat(blocks, ignore_index=True)
    perm = rng.permutation(len(out))
    out = out.iloc[perm].reset_index(drop=True)
    return out, ("__rows__", {"n_clean": len(df), "n_header": copies})


def add_constant_column(df: pd.DataFrame):
    out = df.copy()
    out["BATCH"] = "A"
    return out


# ------------------------------------------------------------ measurement


def run_transform(sql: str, df: pd.DataFrame, table: str = "sensors",
                  extra: dict | None = None) -> pd.DataFrame:
    import duckdb

    con = duckdb.connect(":memory:")
    try:
        con.register(table, df)
        for name, t in (extra or {}).items():
            con.register(name, t)
        return con.execute(sql).df()
    finally:
        con.close()


def _is_null(v) -> bool:
    return v is None or v != v or v is pd.NaT


def cell_fix_rate(out: pd.DataFrame, target_col: str, spec: dict) -> float:
    """Fraction of corrupted cells restored to the expected clean value.
    Rowwise transforms preserve scan order, so positions align."""
    fixed = 0
    for r, expected in spec.items():
        got = out[target_col].iloc[r]
        if expected is None:
            fixed += int(_is_null(got))
        elif isinstance(expected, float):
            fixed += int(not _is_null(got) and abs(float(got) - expected) <= 1e-6 * max(1.0, abs(expected)))
        else:
            got_s = got.date().isoformat() if hasattr(got, "date") and not isinstance(got, str) else str(got)
            fixed += int(got_s == str(expected))
    return fixed / max(1, len(spec))
