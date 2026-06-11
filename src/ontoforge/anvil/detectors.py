"""T0 fix detectors (§5.2 step 1): pattern-match the standard corruption taxonomy
against profile evidence and emit parameterized SQL fragments — no search.

Every detector works from EVIDENCE (value distributions, raw-vs-trimmed distinct
counts, strptime coverage, format signatures, the profiler's unit inference),
never from table or fixture names. Each returns a (Fix, expression-rewriter)
pair; the driver composes the rewriters into per-column expression chains.

Corruption taxonomy covered:
  null-token normalization     ('', 'N/A', 'NULL', '-', 'UNK', ... -> NULL)
  trim normalization           (leading/trailing whitespace)
  case normalization           (mixed case on code-like columns)
  date/locale formats          (>=1 non-ISO strptime format -> ISO DATE)
  numeric-in-string            ('USD 1,234.56', '1,234' -> decimal)
  unit conversion (§3.2)       (meters slice in an ft column -> per-row CASE;
                                whole-column unit mismatch vs target property;
                                NO silent unit mixing — foreign currency rows
                                are nulled and reported, never FX-converted)
  header-row-in-data           (a row whose cells equal the column names)
  constant-column drop         (distinct == 1)
  duplicate-row drop           (exact duplicate rows -> dedupe)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Optional, Sequence

import pandas as pd

from ontoforge.contracts import ColumnProfile, Datatype, PropertyDef, TableProfile
from ontoforge.profiling import infer_unit, lookup_unit

from .program import Fix, qident, qstr

__all__ = [
    "ColumnFix",
    "NULL_TOKENS",
    "DATE_FORMATS",
    "detect_column_fixes",
    "detect_header_rows",
    "detect_constant_columns",
    "detect_duplicate_rows",
]

# Canonical null-token vocabulary (matched case-insensitively after TRIM).
NULL_TOKENS: tuple[str, ...] = (
    "", "N/A", "NA", "NULL", "NONE", "-", "--", "?", "UNK", "UNKNOWN",
    "NAN", "NIL", "MISSING", "#N/A",
)

# strptime formats tried for locale-mixed date columns (ISO first).
DATE_FORMATS: tuple[tuple[str, str], ...] = (
    ("%Y-%m-%d", "%Y-%m-%d"),
    ("%m/%d/%Y", "%m/%d/%Y"),
    ("%d/%m/%Y", "%d/%m/%Y"),
    ("%d.%m.%Y", "%d.%m.%Y"),
    ("%Y/%m/%d", "%Y/%m/%d"),
    ("%m-%d-%Y", "%m-%d-%Y"),
    ("%d-%b-%Y", "%d-%b-%Y"),
    ("%b %d, %Y", "%b %d, %Y"),
    ("%Y%m%d", "%Y%m%d"),
)

_MAX_EVIDENCE = 1000
_NUM_CORE = r"[0-9][0-9,]*(\.[0-9]+)?"
_SUFFIX_RE = re.compile(r"^([0-9]+(?:\.[0-9]+)?)\s*([A-Za-z°%/]+)$")
_CCY_PREFIX_RE = re.compile(r"^([A-Z]{3})\s+[0-9]")


@dataclass(slots=True)
class ColumnFix:
    """A detected fix plus its expression rewriter and resulting datatype."""

    fix: Fix
    rewrite: Callable[[str], str]
    produces: Optional[Datatype] = None   # datatype of the rewritten expr (if changed)


def _strings(values: Sequence) -> list[str]:
    out: list[str] = []
    for v in values[:_MAX_EVIDENCE]:
        if v is None or (isinstance(v, float) and v != v):
            continue
        out.append(v if isinstance(v, str) else str(v))
    return out


def _nonnullish(strings: list[str]) -> list[str]:
    toks = {t.upper() for t in NULL_TOKENS}
    return [s for s in strings if s.strip().upper() not in toks]


# --------------------------------------------------------------- null tokens


def _detect_null_tokens(column: str, strings: list[str]) -> Optional[ColumnFix]:
    toks = {t.upper() for t in NULL_TOKENS}
    hits = sorted({s.strip().upper() for s in strings if s.strip().upper() in toks})
    if not hits:
        return None
    in_list = ", ".join(qstr(t) for t in hits)

    def rewrite(e: str) -> str:
        return f"CASE WHEN UPPER(TRIM({e})) IN ({in_list}) THEN NULL ELSE {e} END"

    return ColumnFix(
        Fix(column, "null_tokens", note=f"null tokens observed: {hits}", params=tuple(hits)),
        rewrite,
    )


# ----------------------------------------------------------------- trim/case


def _detect_trim(column: str, strings: list[str]) -> Optional[ColumnFix]:
    padded = sum(1 for s in strings if s != s.strip())
    if padded == 0:
        return None
    return ColumnFix(
        Fix(column, "trim", note=f"{padded}/{len(strings)} values carry padding"),
        lambda e: f"TRIM({e})",
    )


def _looks_code_like(profile: ColumnProfile, strings: list[str]) -> bool:
    if profile.inferred_type not in (Datatype.STRING,):
        return False
    live = _nonnullish(strings)
    if not live:
        return False
    avg_len = sum(len(s.strip()) for s in live) / len(live)
    distinct_ci = len({s.strip().upper() for s in live})
    return avg_len <= 24 and distinct_ci <= max(64, len(live) // 4)


def _detect_case(column: str, profile: ColumnProfile, strings: list[str]) -> Optional[ColumnFix]:
    live = _nonnullish(strings)
    if not live or not _looks_code_like(profile, strings):
        return None
    distinct_cs = len({s.strip() for s in live})
    distinct_ci = len({s.strip().upper() for s in live})
    if distinct_ci >= distinct_cs:
        return None
    return ColumnFix(
        Fix(column, "case", note=f"case-folding merges {distinct_cs}->{distinct_ci} distinct values"),
        lambda e: f"UPPER({e})",
    )


# -------------------------------------------------------------- date formats


def _detect_dates(column: str, target: PropertyDef, strings: list[str]) -> Optional[ColumnFix]:
    if target.datatype not in (Datatype.DATE, Datatype.DATETIME):
        return None
    live = [s.strip() for s in _nonnullish(strings)]
    if not live:
        return None
    matches: dict[str, set[int]] = {fmt: set() for fmt, _ in DATE_FORMATS}
    for i, s in enumerate(live):
        for fmt, _ in DATE_FORMATS:
            try:
                datetime.strptime(s, fmt)
                matches[fmt].add(i)
            except ValueError:
                continue
    parseable = set().union(*matches.values()) if matches else set()
    if len(parseable) < 0.9 * len(live):
        return None
    # greedy minimal cover; prefer formats with more UNIQUELY-identified values
    # (disambiguates %m/%d/%Y vs %d/%m/%Y via day>12 evidence), then list order.
    order = {fmt: k for k, (fmt, _) in enumerate(DATE_FORMATS)}

    def uniq(fmt: str) -> int:
        others = set().union(*(m for f, m in matches.items() if f != fmt)) if len(matches) > 1 else set()
        return len(matches[fmt] - others)

    chosen: list[str] = []
    covered: set[int] = set()
    remaining = dict(matches)
    while covered != parseable and remaining:
        best = max(
            remaining,
            key=lambda f: (len(remaining[f] - covered), uniq(f), -order[f]),
        )
        gain = remaining.pop(best)
        if not (gain - covered):
            continue
        chosen.append(best)
        covered |= gain
    chosen.sort(key=lambda f: order[f])
    if chosen == ["%Y-%m-%d"] and len(covered) == len(live):
        return None  # already ISO: plain cast handles it, no fix needed
    coalesce = ", ".join(f"TRY_STRPTIME({{e}}, {qstr(f)})" for f in chosen)

    def rewrite(e: str) -> str:
        inner = coalesce.replace("{e}", f"TRIM({e})")
        return f"CAST(COALESCE({inner}) AS DATE)"

    return ColumnFix(
        Fix(column, "date_format", note=f"date formats observed: {chosen}", params=tuple(chosen)),
        rewrite,
        produces=Datatype.DATE,
    )


# ---------------------------------------------------------- numeric-in-string


def _castable(s: str) -> bool:
    try:
        float(s)
        return True
    except ValueError:
        return False


def _detect_numeric_string(
    column: str, target: PropertyDef, strings: list[str]
) -> Optional[ColumnFix]:
    if target.datatype not in (Datatype.FLOAT, Datatype.INTEGER):
        return None
    live = [s.strip() for s in _nonnullish(strings)]
    if not live:
        return None
    direct = sum(1 for s in live if _castable(s))
    if direct == len(live):
        return None  # plain cast suffices
    # currency-code / symbol prefixes and thousands separators
    codes = sorted({m.group(1) for s in live if (m := _CCY_PREFIX_RE.match(s))})
    grouped = re.compile(rf"^[\$€£]?\s*{_NUM_CORE}$")
    coded = re.compile(rf"^[A-Z]{{3}}\s+{_NUM_CORE}$")
    fixable = sum(
        1 for s in live if _castable(s) or grouped.match(s) or coded.match(s)
    )
    if fixable < 0.7 * len(live):
        return None

    target_ccy = target.unit if target.unit and lookup_unit(target.unit) else None
    foreign = [c for c in codes if lookup_unit(c) is not None and c != target_ccy] if target_ccy else []
    allowed = [c for c in codes if c not in foreign]
    note = f"numeric-in-string: prefixes={codes or ['$/grouped']}, thousands separators stripped"
    if foreign:
        note += f"; foreign currency rows ({foreign}) -> NULL (no silent FX conversion)"
    allowed_alt = "|".join(re.escape(c) for c in allowed) or "XXX"
    cast_to = "BIGINT" if target.datatype is Datatype.INTEGER else "DOUBLE"

    def rewrite(e: str) -> str:
        t = f"TRIM({e})"
        clean = (
            f"TRY_CAST(REPLACE(REGEXP_REPLACE({t}, '^(({allowed_alt})\\s+|[\\$€£]\\s*)', ''), "
            f"',', '') AS {cast_to})"
        )
        if foreign:
            foreign_alt = "|".join(re.escape(c) for c in foreign)
            return (
                f"CASE WHEN REGEXP_MATCHES({t}, '^({foreign_alt})\\s') THEN NULL "
                f"ELSE {clean} END"
            )
        return clean

    return ColumnFix(
        Fix(column, "numeric_string", note=note, params=tuple(codes)),
        rewrite,
        produces=target.datatype,
    )


# ------------------------------------------------------- unit conversion §3.2


def _conversion(src_symbol: str, dst_symbol: str) -> Optional[tuple[float, float]]:
    """x_dst = x_src * a + b, via the profiling unit table (canonical hop)."""
    src, dst = lookup_unit(src_symbol), lookup_unit(dst_symbol)
    if src is None or dst is None or src.dimension != dst.dimension:
        return None
    if src.canonical != dst.canonical:
        return None  # e.g. cross-currency: no static conversion exists
    a = src.scale / dst.scale
    b = (src.offset - dst.offset) / dst.scale
    return a, b


def _detect_unit_conversion(
    column: str, target: PropertyDef, profile: ColumnProfile, strings: list[str]
) -> Optional[ColumnFix]:
    if target.unit is None or target.dimension is None:
        return None
    if target.datatype not in (Datatype.FLOAT, Datatype.INTEGER):
        return None
    live = [s.strip() for s in _nonnullish(strings)]
    if not live:
        return None
    inference = infer_unit(column, live)

    # (a) explicit per-value suffixes naming a different same-dimension unit
    suffix_units: dict[str, list[str]] = {}
    for s in live:
        m = _SUFFIX_RE.match(s)
        if not m:
            continue
        tok = m.group(2)
        u = infer_unit(column, [s])  # reuse the profiler's suffix resolution
        if u.unit and u.unit != target.unit and u.dimension == target.dimension:
            suffix_units.setdefault(u.unit, []).append(tok)
    cases: list[tuple[str, float, float, str]] = []  # (suffix_alt, a, b, unit)
    for sym, toks in sorted(suffix_units.items()):
        conv = _conversion(sym, target.unit)
        if conv is None:
            continue
        alt = "|".join(sorted({re.escape(t) for t in toks}))
        cases.append((alt, conv[0], conv[1], sym))

    cast_to = "BIGINT" if target.datatype is Datatype.INTEGER else "DOUBLE"
    if cases:
        units = [c[3] for c in cases]

        def rewrite(e: str) -> str:
            t = f"TRIM({e})"
            num = f"TRY_CAST(REGEXP_EXTRACT({t}, '^([0-9]+(\\.[0-9]+)?)', 1) AS DOUBLE)"
            out = f"TRY_CAST({t} AS {cast_to})"
            for alt, a, b, _sym in reversed(cases):
                conv = f"CAST(({num} * {a!r} + {b!r}) AS {cast_to})"
                out = (
                    f"CASE WHEN REGEXP_MATCHES({t}, '^[0-9]+(\\.[0-9]+)?\\s*({alt})$') "
                    f"THEN {conv} ELSE {out} END"
                )
            return out

        return ColumnFix(
            Fix(
                column,
                "unit_convert",
                note=f"suffix slice in {units} converted to target unit {target.unit!r}",
                params=tuple(units),
            ),
            rewrite,
            produces=target.datatype,
        )

    # (b) whole-column unit disagreement (profiler-asserted source unit)
    src_unit = profile.unit or (inference.unit if not inference.mixed else None)
    if src_unit and src_unit != target.unit:
        conv = _conversion(src_unit, target.unit)
        if conv is not None:
            a, b = conv

            def rewrite_whole(e: str) -> str:
                return f"CAST((TRY_CAST({e} AS DOUBLE) * {a!r} + {b!r}) AS {cast_to})"

            return ColumnFix(
                Fix(
                    column,
                    "unit_convert",
                    note=f"column unit {src_unit!r} != target unit {target.unit!r}: whole-column conversion",
                    params=(src_unit,),
                ),
                rewrite_whole,
                produces=target.datatype,
            )
    if inference.mixed and not cases:
        # magnitude-mixed with no separating suffix: NOT silently fixable (§3.2)
        return ColumnFix(
            Fix(
                column,
                "unit_convert",
                note=f"UNRESOLVED mixed units ({inference.note}); no per-row separator — review required",
                params=("unresolved",),
            ),
            lambda e: e,
        )
    return None


# -------------------------------------------------------- table-level fixes


def detect_header_rows(df: pd.DataFrame, columns: Sequence[str]) -> Optional[tuple[Fix, str]]:
    """A data row whose cells equal the column names -> WHERE filter."""
    cols = [c for c in columns if c in df.columns][:6]
    if len(cols) < 2:
        return None
    found = False
    for _, row in df.head(_MAX_EVIDENCE).iterrows():
        eq = sum(
            1 for c in cols if str(row[c]).strip().upper() == str(c).strip().upper()
        )
        if eq >= max(2, int(0.8 * len(cols))):
            found = True
            break
    if not found:
        return None
    preds = [
        f"UPPER(TRIM(s.{qident(c)})) = {qstr(str(c).strip().upper())}" for c in cols[:3]
    ]
    predicate = f"NOT ({' AND '.join(preds)})"
    return Fix("*", "header_row", note="header row repeated in data; filtered"), predicate


def detect_constant_columns(table_profile: TableProfile) -> list[Fix]:
    out = []
    for name, cp in sorted(table_profile.columns.items()):
        nn = cp.row_count - cp.null_count
        if cp.row_count > 1 and nn > 0 and cp.distinct_estimate <= 1:
            out.append(Fix(name, "drop_constant", note="constant column dropped from projection"))
    return out


def detect_duplicate_rows(df: pd.DataFrame, columns: Sequence[str]) -> Optional[Fix]:
    cols = [c for c in columns if c in df.columns]
    if not cols:
        return None
    dups = int(df[cols].duplicated().sum())
    if dups == 0:
        return None
    return Fix("*", "dedupe_rows", note=f"{dups} exact duplicate rows -> dedupe", params=(str(dups),))


# ------------------------------------------------------------------ pipeline


def detect_column_fixes(
    column: str,
    target: PropertyDef,
    profile: ColumnProfile,
    values: Sequence,
) -> list[ColumnFix]:
    """Run all column-level detectors in canonical composition order."""
    strings = _strings(list(values))
    fixes: list[ColumnFix] = []
    for det in (
        lambda: _detect_null_tokens(column, strings),
        lambda: _detect_trim(column, strings),
        lambda: _detect_case(column, profile, strings),
        lambda: _detect_dates(column, target, strings),
        lambda: _detect_numeric_string(column, target, strings),
        lambda: _detect_unit_conversion(column, target, profile, strings),
    ):
        hit = det()
        if hit is not None:
            fixes.append(hit)
    return fixes
