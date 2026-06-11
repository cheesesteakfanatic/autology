"""Σ-compilation: SHACL-style ShapeConstraints -> executable streaming expectations.

Whitepaper §5.3 generator (i): every shape constraint of a ClassDef lowers to one
or more streaming expectations evaluated over a DataFrame batch — datatype
conformance, min_count (null policy), max_count (per-cell multiplicity), regex
pattern, in-value enums, numeric ranges, and unit presence. Target: >= 95% of
Σ-expressible constraints auto-compiled with zero human authoring.

Coverage accounting is per *constraint*: a ShapeConstraint counts as compiled
only when EVERY facet it declares produced an executable expectation (a regex
that fails to compile, for example, makes its constraint partial, not silently
covered).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Callable, Mapping, Optional

from ontoforge.contracts import ClassDef, Datatype, Ontology, ShapeConstraint

__all__ = [
    "Expectation",
    "ExpectationResult",
    "CompilationReport",
    "compile_constraint",
    "compile_class",
    "compile_ontology",
    "evaluate_class",
    "MAX_VIOLATION_ROWS",
]

#: cap on violating row indices carried in an ExpectationResult (spec: 100).
MAX_VIOLATION_ROWS = 100

#: violation rate above which a failing expectation is 'error' rather than 'warn'.
ERROR_RATE = 0.05

_BOOL_LEXICAL = {"true", "false", "0", "1", "t", "f", "yes", "no", "y", "n"}
_INT_RE = re.compile(r"^[+-]?\d+$")
_FLOAT_RE = re.compile(r"^[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?$")
_DATE_FORMATS = ("%Y-%m-%d", "%Y%m%d", "%m/%d/%Y", "%d-%b-%Y")
_DATETIME_FORMATS = ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%SZ")
# numeric lexical form with an optional trailing unit token ("3500 m", "12.5h")
_NUM_UNIT_RE = re.compile(
    r"^\s*[A-Z]{0,3}\s?[+-]?[\d,]+(?:\.\d+)?(?:[eE][+-]?\d+)?\s*(?P<unit>[A-Za-z°%$]{0,8})\s*$"
)
_LEADING_CURRENCY_RE = re.compile(r"^\s*(?P<cur>[A-Z]{3}|\$)\s")


def _is_null(v: Any) -> bool:
    if v is None:
        return True
    if isinstance(v, float) and math.isnan(v):
        return True
    if isinstance(v, str) and not v.strip():
        return True
    return False


def _lexical(v: Any) -> str:
    return v.strip() if isinstance(v, str) else str(v)


def _parse_number(v: Any) -> Optional[float]:
    """Parse the numeric magnitude of a value, tolerating thousands separators,
    a leading currency code, or a short trailing unit token ('3500 m' -> 3500)."""
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = _lexical(v)
    s = _LEADING_CURRENCY_RE.sub("", s)
    m = re.search(r"[+-]?[\d,]+(?:\.\d+)?(?:[eE][+-]?\d+)?", s)
    if m is None:
        return None
    head = s[: m.start()].strip()
    tail = s[m.end() :].strip()
    if head:  # non-numeric prefix that wasn't currency: not a number
        return None
    if tail and len(tail) > 8:  # long trailing text: free text, not a unit
        return None
    try:
        return float(m.group(0).replace(",", ""))
    except ValueError:
        return None


def _unit_suffix(v: Any) -> Optional[str]:
    """The trailing unit token of a lexical numeric value, if any ('3500 m' -> 'm')."""
    if not isinstance(v, str):
        return None
    m = _NUM_UNIT_RE.match(v)
    if m is None:
        return None
    tok = m.group("unit").strip()
    return tok or None


def _conforms(v: Any, dt: Datatype) -> bool:
    if dt in (Datatype.STRING, Datatype.TEXT):
        return True
    s = _lexical(v)
    if dt is Datatype.INTEGER:
        return bool(_INT_RE.match(s)) or (isinstance(v, int) and not isinstance(v, bool))
    if dt is Datatype.FLOAT:
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return True
        return bool(_FLOAT_RE.match(s.replace(",", "")))
    if dt is Datatype.BOOLEAN:
        return isinstance(v, bool) or s.lower() in _BOOL_LEXICAL
    if dt is Datatype.DATE:
        if isinstance(v, (date, datetime)):
            return True
        return any(_try_strptime(s, f) for f in _DATE_FORMATS)
    if dt is Datatype.DATETIME:
        if isinstance(v, datetime):
            return True
        return any(_try_strptime(s, f) for f in _DATETIME_FORMATS + _DATE_FORMATS)
    return True


def _try_strptime(s: str, fmt: str) -> bool:
    try:
        datetime.strptime(s, fmt)
        return True
    except ValueError:
        return False


def _cell_count(v: Any) -> int:
    """Value multiplicity of one cell: 0 when null, len() for list-like, else 1."""
    if _is_null(v):
        return 0
    if isinstance(v, (list, tuple, set, frozenset)):
        return len(v)
    return 1


# ------------------------------------------------------------------ results


@dataclass(frozen=True, slots=True)
class ExpectationResult:
    class_uri: str
    prop: str
    facet: str
    n_rows: int
    n_evaluated: int                 # non-null cells actually checked
    n_violations: int
    pass_rate: float                 # 1 - violations / max(1, basis)
    violating_rows: tuple[int, ...]  # positional row indices, capped at 100
    severity: str                    # "ok" | "warn" | "error"
    detail: str = ""

    @property
    def passed(self) -> bool:
        return self.n_violations == 0


@dataclass(frozen=True, slots=True)
class Expectation:
    """One executable streaming check compiled from a ShapeConstraint facet."""

    class_uri: str
    class_name: str
    prop: str
    facet: str          # datatype|min_count|max_count|pattern|in_values|range|unit
    description: str
    constraint: ShapeConstraint
    _check: Callable[[Any], bool] = field(repr=False)
    #: facets evaluated on non-null cells only (SHACL semantics: null = absent)
    null_skipping: bool = True

    def evaluate(self, batch: Any, column: Optional[str] = None) -> ExpectationResult:
        """Evaluate against a DataFrame-like batch (anything with [col] -> iterable).

        `column` overrides the physical column name (defaults to the property name).
        A missing column is a total min_count violation and a vacuous pass for
        null-skipping facets.
        """
        col = column if column is not None else self.prop
        values = _column_values(batch, col)
        n_rows = _batch_len(batch, values)
        if values is None:
            if self.facet == "min_count" and self.constraint.min_count >= 1:
                return self._result(n_rows, 0, n_rows, tuple(range(min(n_rows, MAX_VIOLATION_ROWS))),
                                    detail=f"column {col!r} absent from batch")
            return self._result(n_rows, 0, 0, (), detail=f"column {col!r} absent from batch")

        violations: list[int] = []
        evaluated = 0
        for i, v in enumerate(values):
            if self.null_skipping and _is_null(v):
                continue
            evaluated += 1
            if not self._check(v):
                violations.append(i)
        if not self.null_skipping:
            evaluated = n_rows
        return self._result(n_rows, evaluated, len(violations), tuple(violations[:MAX_VIOLATION_ROWS]))

    def _result(
        self, n_rows: int, evaluated: int, n_viol: int, rows: tuple[int, ...], detail: str = ""
    ) -> ExpectationResult:
        basis = evaluated if self.null_skipping else n_rows
        rate = n_viol / basis if basis else 0.0
        severity = "ok" if n_viol == 0 else ("error" if rate > ERROR_RATE else "warn")
        return ExpectationResult(
            class_uri=self.class_uri,
            prop=self.prop,
            facet=self.facet,
            n_rows=n_rows,
            n_evaluated=evaluated,
            n_violations=n_viol,
            pass_rate=round(1.0 - rate, 6),
            violating_rows=rows,
            severity=severity,
            detail=detail or self.description,
        )


def _column_values(batch: Any, col: str) -> Optional[list]:
    try:
        series = batch[col]
    except (KeyError, IndexError, TypeError):
        return None
    return list(series)


def _batch_len(batch: Any, values: Optional[list]) -> int:
    if values is not None:
        return len(values)
    try:
        return int(len(batch))
    except TypeError:
        return 0


# ----------------------------------------------------------------- compiler


@dataclass(frozen=True, slots=True)
class CompilationReport:
    expectations: tuple[Expectation, ...]
    total_constraints: int
    compiled_constraints: int        # constraints with every declared facet compiled
    skipped: tuple[str, ...] = ()    # human-readable reasons for uncompiled facets

    @property
    def coverage(self) -> float:
        return self.compiled_constraints / self.total_constraints if self.total_constraints else 1.0


def compile_constraint(
    class_def: ClassDef, sc: ShapeConstraint
) -> tuple[list[Expectation], list[str]]:
    """Lower one ShapeConstraint into executable expectations.

    Returns (expectations, skipped_facet_reasons). A constraint is fully
    compiled iff the reasons list is empty.
    """
    out: list[Expectation] = []
    skipped: list[str] = []

    def add(facet: str, desc: str, check: Callable[[Any], bool], null_skipping: bool = True) -> None:
        out.append(
            Expectation(
                class_uri=class_def.uri,
                class_name=class_def.name,
                prop=sc.prop,
                facet=facet,
                description=desc,
                constraint=sc,
                _check=check,
                null_skipping=null_skipping,
            )
        )

    dt = sc.datatype
    if dt is None:
        pd_ = class_def.prop(sc.prop)
        dt = pd_.datatype if pd_ is not None else None
    if dt is not None:
        add("datatype", f"{sc.prop} conforms to {dt.value}", lambda v, _dt=dt: _conforms(v, _dt))

    if sc.min_count >= 1:
        add(
            "min_count",
            f"{sc.prop} present (min_count={sc.min_count})",
            lambda v, _mc=sc.min_count: _cell_count(v) >= _mc if _mc > 1 else not _is_null(v),
            null_skipping=False,
        )

    if sc.max_count is not None:
        add(
            "max_count",
            f"{sc.prop} multiplicity <= {sc.max_count}",
            lambda v, _mx=sc.max_count: _cell_count(v) <= _mx,
            null_skipping=False,
        )

    if sc.pattern is not None:
        try:
            rx = re.compile(sc.pattern)
        except re.error as exc:
            skipped.append(f"{class_def.name}.{sc.prop}: pattern {sc.pattern!r} failed: {exc}")
        else:
            # SHACL sh:pattern is a partial match; authored anchors still bind.
            add("pattern", f"{sc.prop} matches /{sc.pattern}/", lambda v, _rx=rx: bool(_rx.search(_lexical(v))))

    if sc.in_values is not None:
        allowed = frozenset(sc.in_values)
        add("in_values", f"{sc.prop} in {sorted(allowed)}", lambda v, _a=allowed: _lexical(v) in _a)

    if sc.min_value is not None or sc.max_value is not None:
        lo = sc.min_value if sc.min_value is not None else -math.inf
        hi = sc.max_value if sc.max_value is not None else math.inf

        def in_range(v: Any, _lo: float = lo, _hi: float = hi) -> bool:
            n = _parse_number(v)
            return True if n is None else (_lo <= n <= _hi)  # unparseable -> datatype's problem

        add("range", f"{sc.prop} in [{lo}, {hi}]", in_range)

    if sc.unit is not None:
        expected = sc.unit.lower()

        def unit_ok(v: Any, _u: str = expected) -> bool:
            tok = _unit_suffix(v)
            return tok is None or tok.lower() == _u  # plain numbers assumed canonical

        add("unit", f"{sc.prop} unit is {sc.unit} (foreign suffixes violate)", unit_ok)

    if not out and not skipped:
        skipped.append(f"{class_def.name}.{sc.prop}: constraint declares no executable facet")
    return out, skipped


def compile_class(class_def: ClassDef) -> list[Expectation]:
    """§11.2 M9 interface: compile every shape of a class into expectations."""
    return list(compile_class_report(class_def).expectations)


def compile_class_report(class_def: ClassDef) -> CompilationReport:
    exps: list[Expectation] = []
    skipped: list[str] = []
    compiled = 0
    for sc in class_def.shapes:
        e, sk = compile_constraint(class_def, sc)
        exps.extend(e)
        skipped.extend(sk)
        if not sk:
            compiled += 1
    return CompilationReport(
        expectations=tuple(exps),
        total_constraints=len(class_def.shapes),
        compiled_constraints=compiled,
        skipped=tuple(skipped),
    )


def compile_ontology(ontology: Ontology) -> CompilationReport:
    """Compile every shape of every class; coverage is the Σ-coverage metric
    gated at >= 0.95 (whitepaper §5.3 target / §11.2 M9 acceptance)."""
    exps: list[Expectation] = []
    skipped: list[str] = []
    total = compiled = 0
    for c in sorted(ontology.iter_classes(), key=lambda c: c.uri):
        rep = compile_class_report(c)
        exps.extend(rep.expectations)
        skipped.extend(rep.skipped)
        total += rep.total_constraints
        compiled += rep.compiled_constraints
    return CompilationReport(
        expectations=tuple(exps),
        total_constraints=total,
        compiled_constraints=compiled,
        skipped=tuple(skipped),
    )


def evaluate_class(
    class_def: ClassDef,
    batch: Any,
    column_map: Optional[Mapping[str, str]] = None,
) -> list[ExpectationResult]:
    """Compile + evaluate a class's shapes against a batch. `column_map` maps
    property name -> physical column name (defaults to identity)."""
    cmap = dict(column_map or {})
    results = []
    for exp in compile_class(class_def):
        results.append(exp.evaluate(batch, column=cmap.get(exp.prop, exp.prop)))
    return results
