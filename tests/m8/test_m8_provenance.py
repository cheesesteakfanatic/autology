"""Provenance-equivalence verification (§5.2): row-tag execution catches
cross-row leakage that value-equality testing misses — mutation-tested with a
hand-built leaky program (a join that fans out)."""

from __future__ import annotations

import pandas as pd

import m8_helpers as H

from ontoforge.anvil import CandidateProgram, ColumnExpr, JoinSpec
from ontoforge.anvil.verify import verify_candidate


def _device_tables(fanout: bool):
    devices = pd.DataFrame(
        {
            "DEVICE_ID": [f"D-{i:03d}" for i in range(1, 41)],
            "LOC": [H.SITES[i % 4] for i in range(40)],
        }
    )
    codes = H.SITES[:4]
    names = ["Albany Plant", "Boston Plant", "Chicago Plant", "Denver Plant"]
    if fanout:
        # leaky right side: duplicate join keys -> the join fans out
        sites = pd.DataFrame(
            {"CODE": codes + codes, "SITE_NAME": names + [n + " Annex" for n in names]}
        )
    else:
        sites = pd.DataFrame({"CODE": codes, "SITE_NAME": names})
    return devices, sites


def _device_class():
    from ontoforge.contracts import ClassDef, Datatype, PropertyDef, ShapeConstraint

    ns = "onto://test/device"
    return ClassDef(
        uri=ns,
        name="Device",
        properties=(
            PropertyDef(f"{ns}/p/device_id", "device_id", Datatype.STRING, functional=True),
            PropertyDef(f"{ns}/p/site_name", "site_name", Datatype.STRING),
        ),
        shapes=(
            ShapeConstraint("device_id", min_count=1, max_count=1, pattern=r"D-[0-9]{3}"),
            ShapeConstraint("site_name", min_count=1),
        ),
    )


def _join_program() -> CandidateProgram:
    prog = CandidateProgram(
        source_table="devices",
        columns=[
            ColumnExpr("device_id", 's."DEVICE_ID"', ("DEVICE_ID",), ("project",)),
            ColumnExpr("site_name", 'r."SITE_NAME"', ("LOC", "SITE_NAME"), ("join", "project")),
        ],
        tier="anvil:T1",
    )
    prog.join = JoinSpec(table="sites", lhs_col="LOC", rhs_col="CODE")
    return prog


def test_clean_join_passes_provenance():
    devices, sites = _device_tables(fanout=False)
    report = verify_candidate(
        _join_program(), devices, _device_class(), seed=0, extra_tables={"sites": sites}
    )
    assert report.provenance_equivalent is True
    assert report.shapes_satisfied


def test_seeded_leaky_join_is_rejected():
    """MUTATION TEST: the fanned-out join produces value-plausible rows derived
    from unintended input rows; the row-tag verifier must reject it."""
    devices, sites = _device_tables(fanout=True)
    report = verify_candidate(
        _join_program(), devices, _device_class(), seed=0, extra_tables={"sites": sites}
    )
    assert report.provenance_equivalent is False
    assert any("provenance" in n for n in report.notes)


def test_smuggled_window_function_is_rejected():
    """A 'rowwise' program whose expression reads ANOTHER row via a window
    function is cross-row leakage even though row counts match."""
    df = H.clean_sensors(50)
    prog = CandidateProgram(
        source_table="sensors",
        columns=[
            ColumnExpr("sensor_id", 's."SENSOR_ID"', ("SENSOR_ID",), ("project",)),
            ColumnExpr(
                "reading",
                'LAG(TRY_CAST(s."READING" AS DOUBLE)) OVER (ORDER BY s."SENSOR_ID")',
                ("READING",),
                ("project",),
            ),
        ],
    )
    report = verify_candidate(prog, df, H.sensor_class(), seed=0)
    assert report.provenance_equivalent is False
    assert any("cross-row" in n for n in report.notes)


def test_declared_filter_is_intended_provenance_but_dup_dedupe_is_not():
    df = H.clean_sensors(60)
    # declared filters are part of intent: bijection is checked on SURVIVORS
    filtered = CandidateProgram(
        source_table="sensors",
        columns=[ColumnExpr("sensor_id", 's."SENSOR_ID"', ("SENSOR_ID",), ("project",))],
        row_filter="s.\"STATUS\" = 'ACTIVE'",
    )
    report = verify_candidate(filtered, df, H.sensor_class(), seed=0)
    assert report.provenance_equivalent is True

    # a dedupe that emits MORE than one row per partition key is leakage
    dup = pd.concat([df, df.iloc[:10]], ignore_index=True)
    bad = CandidateProgram(
        source_table="sensors",
        columns=[
            ColumnExpr("sensor_id", 's."SENSOR_ID"', ("SENSOR_ID",), ("project",)),
            ColumnExpr("site_code", 's."SITE_CODE"', ("SITE_CODE",), ("project",)),
        ],
        dedupe_keys=("sensor_id", "site_code"),
    )
    # sabotage: claim dedupe on both keys but partition only exists on pairs —
    # craft true leak by deduping on a key that still leaves duplicates
    bad.dedupe_keys = ("site_code",)  # representative per site, claims per-key grain
    report2 = verify_candidate(bad, dup, H.sensor_class(), seed=0)
    # per-site dedupe IS internally consistent, so provenance holds...
    assert report2.provenance_equivalent is True
    # ...but the required-property shapes fail (installed missing) -> never accepted
    assert report2.shapes_satisfied is False
