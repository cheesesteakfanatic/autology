"""Unit & dimension inference per column (whitepaper §3.2).

Evidence sources, combined with explicit conflict handling:

1. NAME evidence — unit tokens parsed from the column name ('altitude_ft',
   'speed_kt', 'cost_usd'); ambiguous tokens are context-gated in units_table.
2. VALUE-SUFFIX evidence — unit tokens attached to values ('250 kt', '34.5 °C').
3. MAGNITUDE evidence — the §3.2 silent-corruption class: a numeric column whose
   name claims one unit but whose values split into two well-separated clusters
   with a median ratio matching a known same-dimension conversion factor
   (ft↔m: 3.28, lb↔kg: 2.20, ...) is flagged `mixed` — never silently merged.

Combination rules
-----------------
- ≥2 distinct suffix units each carried by ≥5% of suffixed values  -> mixed=True,
  unit=None (no unit is asserted), dimension = the common dimension if all
  observed units share one, else None. Mixing is *reported*, never resolved here
  (resolution is an ANVIL conversion transform, §3.2/§5.2).
- name and values agree            -> high confidence (the two sources corroborate)
- name and values disagree         -> conflict=True, value evidence wins (data over
  documentation), confidence cut to <= 0.5 so the spine escalates per §3.2.
- only one source                  -> that source's confidence, scaled by coverage.

The result is a UnitInference; profile_table maps it onto ColumnProfile.unit /
.dimension (mixed columns get unit=None) and keeps the full inference available
via profile_table_detailed because the frozen ColumnProfile contract has no
mixed/confidence fields for units (reported as a contract gap).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

from ontoforge.contracts import Dimension, UnitDef

from ._values import display_str, is_null
from .units_table import UNITS, resolve_token, units_in_dimension

__all__ = ["UnitInference", "infer_unit", "dimension_of", "split_name_tokens", "parse_value_suffix"]

_SUFFIX_RE = re.compile(
    r"^\s*[+-]?\d{1,3}(?:,\d{3})*(?:\.\d+)?\s*(?P<unit>[^\s\d.,+-][^\d]*?)\s*$"
    r"|^\s*[+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?\s*(?P<unit2>[^\s\d.,+-][^\d]*?)\s*$"
)
_NUM_RE = re.compile(r"[+-]?\d{1,3}(?:,\d{3})*(?:\.\d+)?|[+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?")


@dataclass(frozen=True, slots=True)
class UnitInference:
    unit: Optional[str] = None              # asserted unit symbol (None when mixed/unknown)
    dimension: Optional[Dimension] = None
    confidence: float = 0.0
    mixed: bool = False                     # >=2 units observed in the SAME column
    conflict: bool = False                  # evidence sources disagree
    observed_units: tuple[tuple[str, float], ...] = ()   # (symbol, share of unit evidence)
    source: str = ""                        # "name" | "values" | "name+values" | "magnitude"
    note: str = ""
    magnitudes: tuple[float, ...] = field(default=(), repr=False)  # suffix-stripped numbers


def split_name_tokens(name: str) -> list[str]:
    """snake/camel/dash split: 'altitudeFt' -> ['altitude','ft']; 'temp_f' -> ['temp','f']."""
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", name)
    return [t for t in re.split(r"[\s_\-./]+", spaced.lower()) if t]


def _name_evidence(column_name: str) -> Optional[tuple[UnitDef, float]]:
    tokens = split_name_tokens(column_name)
    for tok in reversed(tokens):  # unit tokens trail: altitude_ft, fuel_qty_gal
        hit = resolve_token(tok, context=column_name)
        if hit is not None:
            return hit
    return None


def parse_value_suffix(s: str) -> Optional[tuple[float, Optional[str]]]:
    """'250 kt' -> (250.0, 'kt'); '34.5' -> (34.5, None); 'N123AB' -> None."""
    m = _SUFFIX_RE.match(s)
    if m:
        num_m = _NUM_RE.search(s)
        if num_m is None:
            return None
        value = float(num_m.group(0).replace(",", ""))
        unit_tok = (m.group("unit") or m.group("unit2") or "").strip().strip("°").strip()
        raw = (m.group("unit") or m.group("unit2") or "").strip()
        if raw.startswith("°"):
            unit_tok = raw.lower()  # keep '°f' / '°c' intact for the alias table
        return value, unit_tok or None
    # plain number?
    try:
        return float(s.replace(",", "")), None
    except ValueError:
        return None


def _value_evidence(strings: Sequence[str]) -> tuple[dict[str, int], int, list[float], list[float]]:
    """Returns (unit -> count among recognized suffixes, n parsed, magnitudes w/ suffix-unit
    resolved, plain magnitudes)."""
    counts: dict[str, int] = {}
    parsed = 0
    unit_magnitudes: list[float] = []
    plain_magnitudes: list[float] = []
    for s in strings:
        hit = parse_value_suffix(s)
        if hit is None:
            continue
        parsed += 1
        value, tok = hit
        if tok is None:
            plain_magnitudes.append(value)
            continue
        resolved = resolve_token(tok, context=tok)
        if resolved is None:
            continue
        udef, _ = resolved
        counts[udef.symbol] = counts.get(udef.symbol, 0) + 1
        unit_magnitudes.append(value)
    return counts, parsed, unit_magnitudes, plain_magnitudes


# ------------------------------------------------- magnitude bimodality (§3.2)


def _best_1d_split(xs: list[float]) -> Optional[tuple[float, list[float], list[float]]]:
    """Exact 1-D 2-means via split-point scan. Returns (between/total variance ratio,
    lower cluster, upper cluster) for the best split, or None if degenerate."""
    xs = sorted(xs)
    n = len(xs)
    if n < 10:
        return None
    total_mean = sum(xs) / n
    total_var = sum((x - total_mean) ** 2 for x in xs)
    if total_var <= 0:
        return None
    best_ratio, best_i = -1.0, -1
    prefix = 0.0
    total = sum(xs)
    for i in range(1, n):  # split: xs[:i] | xs[i:]
        prefix += xs[i - 1]
        m1 = prefix / i
        m2 = (total - prefix) / (n - i)
        between = i * (m1 - total_mean) ** 2 + (n - i) * (m2 - total_mean) ** 2
        ratio = between / total_var
        if ratio > best_ratio:
            best_ratio, best_i = ratio, i
    if best_i < 0:
        return None
    return best_ratio, xs[:best_i], xs[best_i:]


def _magnitude_mixed_check(numbers: list[float], claimed: UnitDef) -> Optional[tuple[str, str]]:
    """Detect a same-dimension second unit hiding in the magnitudes.

    Conservative gate (all must hold) to avoid false alarms on ordinary skew:
    - exact 1-D 2-means split explains >= 90% of variance,
    - both clusters hold >= 15% of values,
    - a real gap: min(upper)/max(lower) >= 1.3,
    - cluster-median ratio matches a known same-dimension conversion within 12%.
    Affine units (temperatures) are excluded — ratios are meaningless there.
    """
    xs = [x for x in numbers if x > 0 and math.isfinite(x)]
    if len(xs) < 20 or claimed.offset != 0.0:
        return None
    split = _best_1d_split(xs)
    if split is None:
        return None
    ratio_var, lower, upper = split
    n = len(xs)
    if ratio_var < 0.90 or len(lower) < 0.15 * n or len(upper) < 0.15 * n:
        return None
    if min(upper) / max(lower) < 1.3:
        return None
    med_lo = lower[len(lower) // 2]
    med_hi = upper[len(upper) // 2]
    if med_lo <= 0:
        return None
    observed = med_hi / med_lo
    for other in units_in_dimension(claimed.dimension):
        if other.symbol == claimed.symbol or other.offset != 0.0:
            continue
        conv = other.scale / claimed.scale
        for r in (conv, 1.0 / conv):
            if r > 1.0 and abs(observed - r) / r <= 0.12:
                return claimed.symbol, other.symbol
    return None


# ---------------------------------------------------------------- combination


def infer_unit(column_name: str, values: Sequence[Any], max_samples: int = 512) -> UnitInference:
    strings = [display_str(v) for v in values if not is_null(v)][:max_samples]
    name_hit = _name_evidence(column_name)
    counts, _parsed, unit_mags, plain_mags = _value_evidence(strings)
    suffixed = sum(counts.values())

    observed: tuple[tuple[str, float], ...] = ()
    if suffixed:
        observed = tuple(
            sorted(((sym, cnt / suffixed) for sym, cnt in counts.items()), key=lambda t: (-t[1], t[0]))
        )

    # --- mixed suffix units: the silent-corruption class — flag, never merge.
    significant = [(sym, share) for sym, share in observed if share >= 0.05 and counts[sym] >= 2]
    if len(significant) >= 2:
        dims = {UNITS[sym].dimension for sym, _ in significant}
        common_dim = dims.pop() if len(dims) == 1 else None
        majority_share = significant[0][1]
        return UnitInference(
            unit=None,
            dimension=common_dim,
            confidence=round(majority_share, 4),
            mixed=True,
            conflict=common_dim is None,
            observed_units=observed,
            source="values",
            note="mixed unit suffixes observed: " + ", ".join(s for s, _ in significant),
        )

    value_unit: Optional[UnitDef] = None
    value_conf = 0.0
    if suffixed and strings:
        sym = observed[0][0]
        value_unit = UNITS[sym]
        coverage = suffixed / len(strings)
        value_conf = 0.95 * min(1.0, coverage / 0.5)  # full weight once >=50% of values carry it

    name_unit, name_conf = (name_hit if name_hit else (None, 0.0))

    if value_unit is not None and name_unit is not None:
        if value_unit.symbol == name_unit.symbol:
            conf = min(0.98, max(value_conf, name_conf) + 0.15)
            return UnitInference(value_unit.symbol, value_unit.dimension, round(conf, 4),
                                 False, False, observed, "name+values",
                                 magnitudes=tuple(unit_mags + plain_mags))
        # disagreement: data wins, confidence floored low so the spine escalates (§3.2)
        return UnitInference(
            value_unit.symbol, value_unit.dimension, round(min(0.5, value_conf * 0.6), 4),
            False, True, observed, "values",
            note=f"name suggests '{name_unit.symbol}' but values carry '{value_unit.symbol}'",
            magnitudes=tuple(unit_mags),
        )

    if value_unit is not None:
        return UnitInference(value_unit.symbol, value_unit.dimension, round(value_conf, 4),
                             False, False, observed, "values",
                             magnitudes=tuple(unit_mags + plain_mags))

    if name_unit is not None:
        # magnitude cross-check against the claimed unit (suffix-less mixed columns)
        nums = plain_mags if plain_mags else [float(v) for v in values
                                              if isinstance(v, (int, float)) and not is_null(v)
                                              and not isinstance(v, bool)]
        hit = _magnitude_mixed_check(nums, name_unit) if nums else None
        if hit is not None:
            a, b = hit
            return UnitInference(
                unit=None, dimension=name_unit.dimension, confidence=0.3, mixed=True,
                conflict=True, observed_units=((a, 0.5), (b, 0.5)), source="magnitude",
                note=f"bimodal magnitudes match {a}<->{b} conversion ratio; column name claims {a}",
            )
        numeric_share = (len(nums) / len(strings)) if strings else 0.0
        conf = name_conf * (1.0 if numeric_share >= 0.9 else 0.7)
        return UnitInference(name_unit.symbol, name_unit.dimension, round(conf, 4),
                             False, False, observed, "name")

    return UnitInference()


def dimension_of(column_name: str, values: Sequence[Any] = ()) -> Optional[Dimension]:
    """§11.2 M3 interface: `dimension(column) -> unit vector`."""
    return infer_unit(column_name, values).dimension
