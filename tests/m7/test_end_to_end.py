"""End-to-end (§5.1 + M6): real estate tables -> 3 registered transforms ->
conformed output committed into a Hearth CONFORMED layer with resolvable
provenance refs (constraint H)."""

from __future__ import annotations

import pytest

from ontoforge.contracts import Leaf, Stance
from ontoforge.contracts.transforms import Layer
from ontoforge.estates import aviation
from ontoforge.hearth import Hearth
from ontoforge.transforms import commit_dataframe_to_hearth, lineage_for_sql
from m7_helpers import make_stack, run_artifacts, tdef

CLASS_URI = "onto://gold/aviation/Aircraft"

CONFORM_MASTER = """
SELECT
  concat(trim("N-NUMBER"), '|', trim("SERIAL NUMBER")) AS key,
  concat('N', trim("N-NUMBER")) AS tail,
  trim("MFR MDL CODE") AS mfr_mdl_code,
  upper(trim("REGISTRANT NAME")) AS registrant,
  trim("STATUS CODE") AS status
FROM raw.faa_master
"""

CONFORM_MODEL = """
SELECT
  trim("CODE") AS code,
  upper(trim("MFR")) AS manufacturer,
  trim("MODEL") AS model,
  CAST("NO-SEATS" AS INT) AS seats
FROM raw.faa_acftref
"""

AIRCRAFT_CONFORMED = """
SELECT
  m.key AS key,
  m.tail AS tail,
  m.registrant AS registrant,
  m.status AS status,
  a.manufacturer AS manufacturer,
  a.model AS model
FROM conformed.master AS m
LEFT JOIN conformed.model AS a ON m.mfr_mdl_code = a.code
"""


@pytest.fixture(scope="module")
def estate_inputs() -> dict:
    estate = aviation.load_estate()
    return {
        "raw.faa_master": estate["tables"]["faa_master"].head(400),
        "raw.faa_acftref": estate["tables"]["faa_acftref"],
    }


def test_pipeline_to_hearth_conformed_layer(tmp_path, estate_inputs) -> None:
    ledger, registry, orch = make_stack()
    registry.register(
        tdef("conform_master", ("raw.faa_master",), "conformed.master", CONFORM_MASTER)
    )
    registry.register(
        tdef("conform_model", ("raw.faa_acftref",), "conformed.model", CONFORM_MODEL)
    )
    registry.register(
        tdef(
            "aircraft_conformed",
            ("conformed.master", "conformed.model"),
            "conformed.aircraft",
            AIRCRAFT_CONFORMED,
            output_layer=Layer.CONFORMED,
        )
    )

    res = orch.run(estate_inputs)
    assert [r.record.status for r in res.results] == ["success"] * 3
    df = res.outputs["conformed.aircraft"]
    assert len(df) == len(estate_inputs["raw.faa_master"])
    assert set(df.columns) == {"key", "tail", "registrant", "status", "manufacturer", "model"}
    # the conforming actually happened: tails carry the leading N the raw file drops
    assert df["tail"].str.startswith("N").all()
    # run history persisted
    assert [a["status"] for a in run_artifacts(ledger)] == ["success"] * 3

    # ---- commit into HEARTH CONFORMED -----------------------------------
    hearth = Hearth(tmp_path / "hearth", ledger)
    n_cells = commit_dataframe_to_hearth(
        hearth,
        ledger,
        df,
        layer=Layer.CONFORMED,
        class_uri=CLASS_URI,
        key_column="key",
        source_id="m7-pipeline",
        object_name="conformed.aircraft",
    )
    assert n_cells == len(df) * (len(df.columns) - 1)

    scanned = hearth.scan(CLASS_URI, Stance(), layer=Layer.CONFORMED).to_pandas()
    assert len(scanned) == len(df)

    # spot-check one entity round-trips, with resolvable provenance
    row = df.iloc[0]
    entity = f"{CLASS_URI}/{row['key']}"
    props = hearth.read(entity, Stance(), class_uri=CLASS_URI, layer=Layer.CONFORMED)
    assert props["tail"] == row["tail"]
    assert props["registrant"] == row["registrant"]

    shard = hearth.shard(Layer.CONFORMED, CLASS_URI)
    cell = next(c for c in shard.cells if c.entity_uri == entity and c.prop == "tail")
    term = ledger.resolve(cell.prov_ref)
    assert isinstance(term, Leaf)
    citations = ledger.valuate_ref(cell.prov_ref, "citations")
    assert len(citations) == 1
    atom = ledger.get_atom(next(iter(citations)))
    assert atom is not None and "m7-pipeline" in atom.uri
    assert ledger.valuate_ref(cell.prov_ref, "derivable") is True


def test_lineage_of_the_conformed_output() -> None:
    schemas = {
        "conformed.master": ["key", "tail", "mfr_mdl_code", "registrant", "status"],
        "conformed.model": ["code", "manufacturer", "model", "seats"],
    }
    lin = {l.output_column: l for l in lineage_for_sql(AIRCRAFT_CONFORMED, schemas)}
    assert lin["tail"].inputs == (("conformed.master", "tail"),)
    assert lin["manufacturer"].inputs == (("conformed.model", "manufacturer"),)
    # and the upstream hop, computed on the raw schema
    raw_schema = {"raw.faa_master": list(aviation.load_estate()["tables"]["faa_master"].columns)}
    up = {l.output_column: l for l in lineage_for_sql(CONFORM_MASTER, raw_schema)}
    assert up["tail"].inputs == (("raw.faa_master", "N-NUMBER"),)
    assert up["tail"].operations == ("CONCAT", "TRIM")
    assert up["key"].inputs == (
        ("raw.faa_master", "N-NUMBER"),
        ("raw.faa_master", "SERIAL NUMBER"),
    )
