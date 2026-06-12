"""Value conformance for generic materialization (the ANVIL-shaped seam).

Cells committed into HEARTH must be typed: aggregations run over numbers, unit
conversions over declared measures. The conformance layer decides, per source
column, how raw strings become cell values:

- trim + null-token suppression ('', 'NA', 'N/A', 'null', '-', ...);
- numeric coercion when (almost) every non-null value parses as a number with
  an optional RECOGNIZED unit token (profiling §3.2 unit table) — suffix form
  ('1010m', '12.5 kg') and prefix form ('USD 1,234.56') both count;
- unit conformance: every measure is converted into ONE target unit. The
  target is the declared PropertyDef unit when STRATA asserted one, else the
  dominant explicit unit, else — for the mixed case where a minority carries
  a suffix and the majority is bare — the unit in the suffix's dimension
  whose conversion makes the suffixed magnitudes most consistent with the
  bare magnitudes (median match), the §3.2 magnitude heuristic;
- when a column mixes lexical units, the SOURCE unit of each value is kept
  alongside the conformed measure (the ``<prop>_unit`` annotation — exactly
  what the gold-world pipeline records for the meter-suffixed altitudes);
- temporal columns are never numerically coerced (a YYYYMMDD date is a
  coordinate, not a measure); they stay trimmed strings, which compare
  correctly lexically in both ISO and digit forms.
"""

from __future__ import annotations

import re
import statistics
from collections import Counter
from dataclasses import dataclass
from typing import Any, Optional, Sequence

from ontoforge.contracts import ColumnProfile, Datatype
from ontoforge.profiling.units_table import UNITS, resolve_token, units_in_dimension

__all__ = [
    "ColumnConformance",
    "NULL_TOKENS",
    "conform_value",
    "decide_column",
    "is_null_value",
    "parse_measure",
]

NULL_TOKENS = frozenset({"", "na", "n/a", "null", "none", "nil", "nan", "-", "--", "?"})

#: fraction of non-null values that must parse for numeric coercion
MEASURE_PARSE_FLOOR = 0.98

_TEMPORAL_NAME_TOKENS = ("date", "time", "datetime", "timestamp")
_DIGIT_DATE_SIGS = frozenset({"D{8}", "D{6}"})


def is_null_value(raw: Any) -> bool:
    return str(raw).strip().lower() in NULL_TOKENS


#: number with an optional trailing unit token (self-contained — the profiling
#: parse_value_suffix helper truncates 4+-digit comma-less numbers, so the
#: conformance layer parses on its own to keep cell VALUES exact)
_SUFFIX_MEASURE_RE = re.compile(
    r"^\s*(?P<num>[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?:[eE][+-]?\d+)?)"
    r"\s*(?P<unit>[^\s\d.,+-][^\d]*?)\s*$"
)


def parse_measure(s: str) -> Optional[tuple[float, Optional[str]]]:
    """'12.5' -> (12.5, None); '1010m' -> (1010.0, 'm'); 'USD 1,234.56' ->
    (1234.56, 'USD'). None when the value is not a (unit-)number."""
    s = s.strip()
    if not s:
        return None
    try:
        return float(s.replace(",", "")), None
    except ValueError:
        pass
    m = _SUFFIX_MEASURE_RE.match(s)
    if m is not None:
        resolved = resolve_token(m.group("unit").strip().lower(), context=s)
        if resolved is None:
            return None
        return float(m.group("num").replace(",", "")), resolved[0].symbol
    # prefix-unit form: 'USD 1,234.56'
    parts = s.split(None, 1)
    if len(parts) == 2:
        resolved = resolve_token(parts[0].lower(), context=s)
        if resolved is not None:
            try:
                return float(parts[1].replace(",", "")), resolved[0].symbol
            except ValueError:
                return None
    return None


@dataclass(frozen=True)
class ColumnConformance:
    """Per-source-column conformance decision."""

    kind: str                              # "number" | "string"
    unit: Optional[str] = None             # target unit the measures conform into
    source_units: tuple[str, ...] = ()     # distinct explicit source units seen
    annotate_unit: bool = False            # write the <prop>_unit annotation
    integral: bool = False                 # commit ints, not floats
    lexical_prefix: Optional[str] = None   # identifier-variant canonical prefix


def _temporalish(cp: Optional[ColumnProfile]) -> bool:
    if cp is None:
        return False
    if cp.inferred_type in (Datatype.DATE, Datatype.DATETIME):
        return True
    if cp.semantic_type == "date" and cp.semantic_confidence >= 0.8:
        return True
    name = cp.column.lower()
    if cp.format_signature in _DIGIT_DATE_SIGS and any(t in name for t in _TEMPORAL_NAME_TOKENS):
        return True
    return False


def _magnitude_target(
    suffixed: list[tuple[float, str]], bare: list[float], dominant: str
) -> str:
    """Pick the target unit for a bare-majority column with a suffixed minority.

    First, the identity check: when the suffixed magnitudes already sit on the
    bare distribution unconverted, the suffix is inconsistent formatting of
    the SAME unit — keep it. Otherwise the deviation principle applies: an
    EXPLICIT unit marker on a minority of values signals deviation from the
    column's (unwritten) default unit — if the column's default were the
    suffix unit, nobody would suffix it. The suffix unit is excluded, and
    among the remaining units of its dimension the one whose conversion
    brings the suffixed magnitudes closest to the bare ones (median match)
    wins."""
    dim = UNITS[dominant].dimension
    bare_med = statistics.median(bare)
    if bare_med == 0:
        return dominant
    suff_med = statistics.median([v for v, _ in suffixed])
    if abs(suff_med - bare_med) / max(abs(bare_med), 1e-9) <= 0.35:
        return dominant  # same magnitudes: formatting noise, not a deviation
    excluded = {u for _, u in suffixed}
    candidates = [u for u in units_in_dimension(dim) if u.symbol not in excluded]
    if not candidates:
        return dominant
    best, best_err = dominant, float("inf")
    for u in sorted(candidates, key=lambda u: u.symbol):
        converted = [
            u.from_canonical(UNITS[src].to_canonical(v)) for v, src in suffixed
        ]
        med = statistics.median(converted)
        err = abs(med - bare_med) / max(abs(bare_med), 1e-9)
        if err < best_err:
            best, best_err = u.symbol, err
    return best


def decide_column(
    values: Sequence[Any],
    cp: Optional[ColumnProfile] = None,
    declared_unit: Optional[str] = None,
    *,
    identifier: bool = False,
) -> ColumnConformance:
    """Decide how one column's raw values conform into cell values.

    ``identifier=True`` (key columns, identity domains) pins the column to
    string form: an all-digit ACN or ZIP is a NAME for a thing, not a measure
    — coercing it would break value-index probes and invite nonsense sums."""
    if identifier:
        return ColumnConformance(kind="string")
    if cp is not None and (cp.inferred_type is Datatype.TEXT or _temporalish(cp)):
        return ColumnConformance(kind="string")

    parsed: list[tuple[float, Optional[str]]] = []
    n_nonnull = 0
    for raw in values:
        if is_null_value(raw):
            continue
        n_nonnull += 1
        m = parse_measure(str(raw))
        if m is not None:
            parsed.append(m)
    if n_nonnull == 0 or len(parsed) < MEASURE_PARSE_FLOOR * n_nonnull:
        return ColumnConformance(kind="string")

    unit_counts = Counter(u for _, u in parsed if u is not None)
    source_units = tuple(sorted(unit_counts))
    n_suffixed = sum(unit_counts.values())
    bare = [v for v, u in parsed if u is None]

    target: Optional[str] = declared_unit
    if target is None and unit_counts:
        dominant = max(unit_counts, key=lambda u: (unit_counts[u], u))
        if not bare or n_suffixed >= len(parsed) * 0.5:
            target = dominant
        else:
            suffixed = [(v, u) for v, u in parsed if u is not None]
            target = _magnitude_target(suffixed, bare, dominant)

    annotate = bool(unit_counts) and (len(unit_counts) > 1 or bool(bare))
    conversion_needed = any(u is not None and u != target for _, u in parsed)
    integral = (
        not conversion_needed
        and all(float(v).is_integer() for v, _ in parsed)
        and (cp is None or cp.inferred_type is not Datatype.FLOAT)
    )
    return ColumnConformance(
        kind="number",
        unit=target,
        source_units=source_units,
        annotate_unit=annotate,
        integral=integral,
    )


def conform_value(
    raw: Any, conf: ColumnConformance
) -> tuple[Optional[Any], Optional[str]]:
    """Raw source string -> (cell value, source unit symbol or None).

    Returns ``(None, None)`` when the value is null (or, for numeric columns,
    inside the <=2% unparseable tail — those cells are not committed; the raw
    atom remains reachable through CDC ingestion)."""
    if is_null_value(raw):
        return None, None
    s = str(raw).strip()
    if conf.kind == "string":
        if conf.lexical_prefix and s and s[0].isdigit():
            # identifier-variant unification: bare form -> dominant explicit form
            s = f"{conf.lexical_prefix}{s}"
        return s, None
    m = parse_measure(s)
    if m is None:
        return None, None
    value, src_unit = m
    if (
        src_unit is not None
        and conf.unit is not None
        and src_unit != conf.unit
        and src_unit in UNITS
        and conf.unit in UNITS
        and UNITS[src_unit].dimension == UNITS[conf.unit].dimension
    ):
        value = UNITS[conf.unit].from_canonical(UNITS[src_unit].to_canonical(value))
    if conf.integral and float(value).is_integer():
        return int(value), src_unit or conf.unit
    return float(value), src_unit or conf.unit
