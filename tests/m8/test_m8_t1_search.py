"""T1 constrained search: PROSE-lite extraction, IND joins, dedupe, and the
Auto-Pipeline FD/IND/Σ pruning rule (pruning effectiveness is counted)."""

from __future__ import annotations

import numpy as np
import pandas as pd

import m8_helpers as H
from m8_helpers import MEASURED_RATES

from ontoforge.anvil import Anvil, induce_extraction
from ontoforge.contracts import IND, ClassDef, Datatype, PropertyDef, ShapeConstraint
from ontoforge.profiling import profile_table

GATE = 0.70


def _cls(name, props, shapes):
    ns = f"onto://test/{name.lower()}"
    return ClassDef(uri=ns, name=name, properties=tuple(props), shapes=tuple(shapes))


# ----------------------------------------------------------- PROSE-lite unit


def test_induce_extraction_split_and_regex():
    examples = [("unit SN-1234 ok", "SN-1234"), ("unit SN-0007 ok", "SN-0007")]
    cands = induce_extraction(examples, r"^SN-[0-9]{4}$")
    sqls = [t for t, _ in cands]
    assert any("SPLIT_PART" in s for s in sqls)
    assert any("REGEXP_EXTRACT" in s for s in sqls)


def test_induce_extraction_identity_and_trim():
    assert induce_extraction([("A", "A"), ("B", "B")], None)[0][0] == "{e}"
    cands = induce_extraction([(" A ", "A"), ("B ", "B")], None)
    assert any("TRIM" in t for t, _ in cands)


# ------------------------------------------------------- embedded extraction


def test_t1_extracts_embedded_id_from_text():
    n = 200
    rng = np.random.default_rng(3)
    ids = [f"SN-{i:04d}" for i in range(1, n + 1)]
    df = pd.DataFrame(
        {
            "NOTES": [f"unit {i} inspected at bay {int(rng.integers(1, 9))}" for i in ids],
            "READING": [f"{v:.2f}" for v in rng.uniform(1, 99, n)],
        }
    )
    target = _cls(
        "Unit",
        [
            PropertyDef("onto://test/unit/p/sensor_id", "sensor_id", Datatype.STRING, functional=True),
            PropertyDef("onto://test/unit/p/reading", "reading", Datatype.FLOAT),
        ],
        [
            ShapeConstraint("sensor_id", min_count=1, max_count=1, pattern=r"SN-[0-9]{4}"),
            ShapeConstraint("reading", datatype=Datatype.FLOAT, min_value=0, max_value=100),
        ],
    )
    anvil = Anvil(seed=0)
    accepted = anvil.synthesize(df, profile_table(df, "t", "units"), target, H.sensor_ontology())
    assert accepted, "T1 must synthesize an extraction for the embedded id"
    tdef, report = accepted[0]
    assert tdef.synthesized_by == "anvil:T1"
    assert report.provenance_equivalent is True
    out = H.run_transform(tdef.sql, df, table="units")
    rate = float((out["sensor_id"].astype(str).sort_values().values == np.array(sorted(ids))).mean())
    MEASURED_RATES["t1_extraction"] = rate
    assert rate >= GATE
    assert rate == 1.0


# ----------------------------------------------------------------- IND join


def test_t1_join_along_discovered_ind():
    devices = pd.DataFrame(
        {
            "DEVICE_ID": [f"D-{i:03d}" for i in range(1, 61)],
            "LOC": [H.SITES[i % 4] for i in range(60)],
        }
    )
    sites = pd.DataFrame(
        {
            "CODE": H.SITES[:4],
            "SITE_NAME": ["Albany Plant", "Boston Plant", "Chicago Plant", "Denver Plant"],
        }
    )
    target = _cls(
        "Device",
        [
            PropertyDef("onto://test/device/p/device_id", "device_id", Datatype.STRING, functional=True),
            PropertyDef("onto://test/device/p/site_name", "site_name", Datatype.STRING),
        ],
        [
            ShapeConstraint("device_id", min_count=1, max_count=1, pattern=r"D-[0-9]{3}"),
            ShapeConstraint("site_name", min_count=1, pattern=r"[A-Z][a-z]+ [A-Z][a-z]+"),
        ],
    )
    ind = IND(lhs_table="devices", lhs_column="LOC", rhs_table="sites", rhs_column="CODE",
              coverage=1.0, score=1.0)
    anvil = Anvil(seed=0)
    accepted = anvil.synthesize(
        devices,
        profile_table(devices, "t", "devices"),
        target,
        H.sensor_ontology(),
        extra_tables={"sites": sites},
        inds=[ind],
    )
    assert accepted, "T1 must synthesize the IND join"
    tdef, report = accepted[0]
    assert tdef.synthesized_by == "anvil:T1"
    assert "LEFT JOIN" in tdef.sql.upper()
    assert "raw.sites" in tdef.inputs
    assert report.provenance_equivalent is True
    out = H.run_transform(tdef.sql, devices, table="devices", extra={"sites": sites})
    rate = float(out["site_name"].notna().mean())
    MEASURED_RATES["t1_ind_join"] = rate
    assert rate >= GATE


# ---------------------------------------------------------------- pruning


def test_pruning_counts_shape_and_fd_violations():
    """Crafted case: (a) an extraction candidate that fits its induced examples
    but violates the target pattern on the wider sample (Σ pruning); (b) a join
    candidate that fans out the source key (IND/key pruning)."""
    n = 80
    good = [f"GC{i % 7}" for i in range(n)]  # source for a working column
    tricky = ["XYZ"] * 5 + [f"x{i}" for i in range(n - 5)]  # examples pass, sample fails
    devices = pd.DataFrame(
        {
            "DEVICE_ID": [f"D-{i:03d}" for i in range(1, n + 1)],
            "TRICKY": tricky,
            "GOODCODE": good,
            "LOC": [H.SITES[i % 4] for i in range(n)],
        }
    )
    fanout_sites = pd.DataFrame(
        {
            "CODE": H.SITES[:4] * 2,
            "SITE_NAME": ["Albany Plant", "Boston Plant", "Chicago Plant", "Denver Plant"] * 2,
        }
    )
    target = _cls(
        "Device",
        [
            PropertyDef("onto://t/p/device_id", "device_id", Datatype.STRING, functional=True),
            PropertyDef("onto://t/p/code3", "code3", Datatype.STRING),
            PropertyDef("onto://t/p/site_name", "site_name", Datatype.STRING),
        ],
        [
            ShapeConstraint("device_id", min_count=1, max_count=1, pattern=r"D-[0-9]{3}"),
            ShapeConstraint("code3", pattern=r"[A-Z]{3}"),
            ShapeConstraint("site_name", min_count=1, pattern=r"[A-Z][a-z]+ [A-Z][a-z]+"),
        ],
    )
    ind = IND(lhs_table="devices", lhs_column="LOC", rhs_table="sites", rhs_column="CODE",
              coverage=1.0, score=1.0)
    anvil = Anvil(seed=0)
    anvil.synthesize(
        devices,
        profile_table(devices, "t", "devices"),
        target,
        H.sensor_ontology(),
        extra_tables={"sites": fanout_sites},
        inds=[ind],
    )
    stats = anvil.last_run.search_stats
    assert stats is not None
    assert stats.pruned_shape >= 1, "Σ-violating intermediate must be pruned"
    assert stats.pruned_fd >= 1, "fan-out join violating key uniqueness must be pruned"
    # and the leaky join must NOT be in any accepted transform
    for tdef, _ in anvil.last_run.accepted:
        assert "LEFT JOIN" not in tdef.sql.upper()
