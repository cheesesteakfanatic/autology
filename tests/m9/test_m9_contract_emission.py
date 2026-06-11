"""Contract emission (§5.3 generator iii): per-table data contract rendered as
markdown, written to the ledger as kind 'data-contract', round-tripping the
key facts (schema, types, units, keys, null policies, freshness placeholder)."""

from __future__ import annotations

from m9_corruptions import quick_profile
from ontoforge.contracts import ShapeConstraint
from ontoforge.warden import contract_artifact_id, emit_contract, parse_contract
from ontoforge.warden.contracts_emit import ARTIFACT_KIND, FRESHNESS_PLACEHOLDER


def _erp_profile(estate):
    df = estate["tables"]["maintenance_erp"]
    return quick_profile(df, "erp", "maintenance_erp")


ERP_SHAPES = (
    ShapeConstraint(prop="work_order_id", min_count=1, max_count=1, pattern=r"^WO-[0-9]{6}$"),
    ShapeConstraint(
        prop="action",
        in_values=("INSPECTED", "REPLACED", "REPAIRED", "OVERHAULED", "ADJUSTED", "TESTED"),
    ),
    ShapeConstraint(prop="labor_hours", min_value=0, max_value=2000, unit="h"),
    ShapeConstraint(prop="cost", min_value=0, unit="USD"),
)
ERP_CMAP = {
    "WORK_ORDER_ID": "work_order_id",
    "ACTION": "action",
    "LABOR_HOURS": "labor_hours",
    "COST": "cost",
}


def test_contract_renders_and_round_trips_key_facts(estate):
    profile = _erp_profile(estate)
    md = emit_contract(
        profile,
        key_columns=("WORK_ORDER_ID",),
        shapes=ERP_SHAPES,
        column_map=ERP_CMAP,
    )
    facts = parse_contract(md)
    assert facts["table"] == "maintenance_erp"
    assert facts["source"] == "erp"
    assert facts["rows"] == profile.row_count == 600
    assert facts["key_columns"] == ["WORK_ORDER_ID"]
    # every physical column appears with its profiled type
    assert set(facts["columns"]) == set(profile.columns)
    assert facts["columns"]["LABOR_HOURS"]["type"] == "float"
    assert facts["columns"]["WORK_ORDER_ID"]["type"] == "string"
    # null policy: Σ min_count >= 1 -> required; others observed-nullable
    assert facts["columns"]["WORK_ORDER_ID"]["null_policy"] == "required"
    assert facts["columns"]["COMPONENT"]["null_policy"].startswith("nullable")
    # unit lands from the shape, not just the sketch
    assert facts["columns"]["LABOR_HOURS"]["unit"] == "h"
    assert facts["columns"]["COST"]["unit"] == "USD"
    # freshness placeholder present (cadence inference is M1-fed, later)
    assert FRESHNESS_PLACEHOLDER in md
    # Σ value constraints rendered
    assert "`/^WO-[0-9]{6}$/`" in md
    assert "`INSPECTED`" in md


def test_contract_written_to_ledger_as_data_contract(estate, ledger):
    profile = _erp_profile(estate)
    md = emit_contract(profile, key_columns=("WORK_ORDER_ID",), ledger=ledger)
    artifact_id = contract_artifact_id("erp", "maintenance_erp")
    row = ledger._conn.execute(
        "SELECT kind, payload, prov_ref FROM artifact WHERE artifact_id = ?", (artifact_id,)
    ).fetchone()
    assert row is not None
    kind, payload, prov_ref = row
    assert kind == ARTIFACT_KIND == "data-contract"
    assert payload == md
    # constraint H: non-zero provenance, resolvable
    assert ledger.valuate_ref(prov_ref, "derivable") is True


def test_contract_emission_is_deterministic(estate):
    profile = _erp_profile(estate)
    md1 = emit_contract(profile, key_columns=("WORK_ORDER_ID",), shapes=ERP_SHAPES, column_map=ERP_CMAP)
    md2 = emit_contract(profile, key_columns=("WORK_ORDER_ID",), shapes=ERP_SHAPES, column_map=ERP_CMAP)
    assert md1 == md2
    assert "202" not in md1.split("rows at emission")[0]  # no wall-clock content in the header


def test_contract_for_every_estate_table(estate):
    """One contract per source table, all parseable (the per-source emission
    surface §5.3 promises)."""
    for table, meta in estate["metadata"]["tables"].items():
        profile = quick_profile(estate["tables"][table], meta["source_id"], table)
        md = emit_contract(profile, key_columns=tuple(meta["key_columns"]))
        facts = parse_contract(md)
        assert facts["table"] == table
        assert facts["key_columns"] == list(meta["key_columns"])
        assert set(facts["columns"]) == set(estate["tables"][table].columns)
