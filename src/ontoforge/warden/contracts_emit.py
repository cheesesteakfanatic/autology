"""Contract emission (whitepaper §5.3 generator iii).

Per source table, materialize the implied DATA CONTRACT — expected schema,
types, units, key columns, null policies, freshness — as a human-readable
markdown artifact, written to the ledger (kind 'data-contract') and returned
as a string. Upstream violations are then detectable at ingest, before they
poison the entity layer.

Deterministic by construction: no wall-clock content; the artifact id and the
provenance leaf derive from (source_id, table).
"""

from __future__ import annotations

import re
from typing import Any, Optional, Sequence

from ontoforge.contracts import ShapeConstraint, TableProfile, leaf

__all__ = ["emit_contract", "parse_contract", "contract_artifact_id"]

ARTIFACT_KIND = "data-contract"
FRESHNESS_PLACEHOLDER = (
    "expected cadence: per CDC cycle (placeholder — inferred from CDC history once M1 "
    "feeds arrival timestamps)"
)


def contract_artifact_id(source_id: str, table: str) -> str:
    return f"data-contract/{source_id}/{table}"


def _null_policy(cp, shapes_by_prop: dict[str, ShapeConstraint], column_map: dict[str, str]) -> str:
    prop = column_map.get(cp.column, cp.column)
    sc = shapes_by_prop.get(prop)
    if sc is not None and sc.min_count >= 1:
        return "required"
    return f"nullable (observed {cp.null_rate:.1%})"


def _unit_of(cp, shapes_by_prop: dict[str, ShapeConstraint], column_map: dict[str, str]) -> str:
    prop = column_map.get(cp.column, cp.column)
    sc = shapes_by_prop.get(prop)
    if sc is not None and sc.unit:
        return sc.unit
    return cp.unit or "-"


def emit_contract(
    profile: TableProfile,
    *,
    key_columns: Sequence[str] = (),
    shapes: Sequence[ShapeConstraint] = (),
    column_map: Optional[dict[str, str]] = None,
    ledger: Any = None,
) -> str:
    """Render the implied data contract for one source table as markdown.

    `shapes` are the Σ constraints governing this table's properties (optional);
    `column_map` maps physical column name -> ontology property name so shape
    policy lands on the right physical column. When `ledger` is given the
    artifact is appended with kind 'data-contract'.
    """
    cmap = dict(column_map or {})
    shapes_by_prop = {sc.prop: sc for sc in shapes}
    lines: list[str] = []
    lines.append(f"# Data contract: `{profile.table}`")
    lines.append("")
    lines.append(f"- **source**: `{profile.source_id}`")
    lines.append(f"- **rows at emission**: {profile.row_count}")
    keys = ", ".join(f"`{k}`" for k in key_columns) if key_columns else "(none declared)"
    lines.append(f"- **key columns**: {keys}")
    lines.append(f"- **freshness**: {FRESHNESS_PLACEHOLDER}")
    lines.append("")
    lines.append("## Expected schema")
    lines.append("")
    lines.append("| column | type | unit | null policy | distinct | format |")
    lines.append("|---|---|---|---|---|---|")
    for name in sorted(profile.columns):
        cp = profile.columns[name]
        fmt = cp.format_signature.replace("|", "\\|") if cp.format_signature else "-"
        lines.append(
            f"| `{name}` | {cp.inferred_type.value} | {_unit_of(cp, shapes_by_prop, cmap)} "
            f"| {_null_policy(cp, shapes_by_prop, cmap)} | {cp.distinct_estimate} | `{fmt}` |"
        )
    constraint_lines: list[str] = []
    for col in sorted(profile.columns):
        prop = cmap.get(col, col)
        sc = shapes_by_prop.get(prop)
        if sc is None:
            continue
        facts: list[str] = []
        if sc.pattern:
            facts.append(f"pattern `/{sc.pattern}/`")
        if sc.in_values:
            facts.append("one of {" + ", ".join(f"`{v}`" for v in sc.in_values) + "}")
        if sc.min_value is not None or sc.max_value is not None:
            facts.append(f"range [{sc.min_value if sc.min_value is not None else '-inf'}, "
                         f"{sc.max_value if sc.max_value is not None else 'inf'}]")
        if sc.max_count is not None:
            facts.append(f"max_count {sc.max_count}")
        if facts:
            constraint_lines.append(f"- `{col}`: " + "; ".join(facts))
    if constraint_lines:
        lines.append("")
        lines.append("## Value constraints (from Σ)")
        lines.append("")
        lines.extend(constraint_lines)
    lines.append("")
    lines.append(
        f"_Emitted by WARDEN from profile sketch of `{profile.source_id}/{profile.table}`; "
        "violations of this contract are detected at ingest._"
    )
    md = "\n".join(lines) + "\n"

    if ledger is not None:
        prov_ref = ledger.intern(leaf(f"profile://{profile.source_id}/{profile.table}"))
        ledger.append_artifact(
            contract_artifact_id(profile.source_id, profile.table), ARTIFACT_KIND, md, prov_ref
        )
    return md


_HEADER_RE = re.compile(r"^# Data contract: `(?P<table>[^`]+)`")
_SOURCE_RE = re.compile(r"^- \*\*source\*\*: `(?P<source>[^`]+)`")
_ROWS_RE = re.compile(r"^- \*\*rows at emission\*\*: (?P<rows>\d+)")
_KEYS_RE = re.compile(r"^- \*\*key columns\*\*: (?P<keys>.+)$")
_ROW_RE = re.compile(r"^\| `(?P<col>[^`]+)` \| (?P<type>[a-z]+) \| (?P<unit>[^|]+) \| (?P<policy>[^|]+) \|")


def parse_contract(md: str) -> dict[str, Any]:
    """Round-trip the key facts out of an emitted contract (test surface)."""
    out: dict[str, Any] = {"table": None, "source": None, "rows": None, "key_columns": [], "columns": {}}
    for line in md.splitlines():
        if (m := _HEADER_RE.match(line)) is not None:
            out["table"] = m.group("table")
        elif (m := _SOURCE_RE.match(line)) is not None:
            out["source"] = m.group("source")
        elif (m := _ROWS_RE.match(line)) is not None:
            out["rows"] = int(m.group("rows"))
        elif (m := _KEYS_RE.match(line)) is not None:
            out["key_columns"] = re.findall(r"`([^`]+)`", m.group("keys"))
        elif (m := _ROW_RE.match(line)) is not None and m.group("col") != "column":
            out["columns"][m.group("col")] = {
                "type": m.group("type"),
                "unit": m.group("unit").strip(),
                "null_policy": m.group("policy").strip(),
            }
    return out
