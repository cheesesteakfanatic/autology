"""The connection atlas over a crafted 6-table mini-corpus.

The corpus is designed so every tier has exactly one unambiguous witness:

- flights.origin   ⊆ airports.airport_id (100%)  -> a CONFIRMED link (the IND
  is admitted by STRATA and materializes as the Flight.origin link property);
- aircraft.carrier_code ⊆ carriers.carrier_code (100%) -> a second island;
- aircraft.operated_by  has exactly 60% distinct-value coverage into
  carriers.carrier_code (15 of 25 codes real, 10 foreign) -> LIKELY, with
  shared-value samples and the documented 0.45/0.25/0.15/0.15 score;
- staff.contact_email / vendors.contact_email are both semantic-type 'email'
  with DISJOINT value sets -> HINT (score = name_sim*0.5 + 0.3 = 0.8);
- staff and vendors share no values with anything -> two SILOS.

Every other column carries a deliberately distinct format signature so no
accidental hint can appear; expected tiers/components/stats are pinned
exactly. Zero network; everything is deterministic (no RNG).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

from ontoforge.pipeline import (
    build_and_persist_atlas,
    build_atlas,
    discover_sources,
    induce_estate,
    profile_estate,
)
from ontoforge.pipeline.atlas import ATLAS_FILE, AtlasReport

CITIES = ["alphaville", "bravotown", "carsonia", "deltaburg", "echofield",
          "foxglove", "golfport", "hotelia"]
FIRST = ["Ava", "Ben", "Carla", "Dev", "Elena", "Felix"]
LAST = ["Adler", "Brandt", "Chen", "Duarte", "Ebert"]
VWORDS = ["ACME", "BOREAL", "CASCADE", "DELTA", "EMBER", "FALCON", "GRANITE",
          "HOOLI", "INITECH", "JUPITER", "KESTREL", "LUMEN", "MONARCH", "NIMBUS",
          "ORION", "PINNACLE", "QUARTZ", "RIDGE", "STARK", "TYRELL"]


def mini_frames() -> dict[str, pd.DataFrame]:
    airports = pd.DataFrame({
        "airport_id": [f"AP{i:02d}" for i in range(1, 41)],
        "city": [CITIES[i % len(CITIES)] for i in range(40)],
    })
    flights = pd.DataFrame({
        "flight_id": [f"FL{i:04d}" for i in range(1, 121)],
        "origin": [f"AP{(i * 7) % 40 + 1:02d}" for i in range(120)],
        "depart_date": [f"2024-{1 + i % 12:02d}-{1 + i % 27:02d}" for i in range(120)],
    })
    carriers = pd.DataFrame({
        "carrier_code": [f"C-{i:02d}" for i in range(1, 26)],
        "carrier_name": [f"line {i:02d} group" for i in range(1, 26)],
    })
    # 25 distinct operator codes: 15 real carriers + 10 foreign -> coverage 0.6
    op_pool = [f"C-{i:02d}" for i in range(1, 16)] + [f"Z-{i:02d}" for i in range(81, 91)]
    aircraft = pd.DataFrame({
        "aircraft_id": [f"ACF{i:03d}" for i in range(1, 81)],
        "carrier_code": [f"C-{(i * 3) % 25 + 1:02d}" for i in range(80)],
        "operated_by": [op_pool[i % 25] for i in range(80)],
        "seats": [str(4 + i % 6) for i in range(80)],
    })
    staff = pd.DataFrame({
        "staff_id": [f"ST{i:03d}" for i in range(1, 31)],
        "staff_name": [f"{FIRST[i % 6]} {LAST[(i * 2) % 5]}" for i in range(30)],
        "contact_email": [
            f"{FIRST[i % 6].lower()}.{LAST[(i * 2) % 5].lower()}{i}@northops.example"
            for i in range(30)
        ],
    })
    vendors = pd.DataFrame({
        "vendor_id": [str(9000 + i) for i in range(1, 21)],
        "vendor_name": [f"{VWORDS[i]}-SUPPLY-{i:02d}" for i in range(20)],
        "contact_email": [f"sales{i}@{VWORDS[i].lower()}-goods.example" for i in range(20)],
    })
    return {"airports": airports, "flights": flights, "carriers": carriers,
            "aircraft": aircraft, "staff": staff, "vendors": vendors}


@pytest.fixture(scope="module")
def mini_dir(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("atlas-mini")
    for name, df in mini_frames().items():
        df.to_csv(out / f"{name}.csv", index=False)
    return out


@pytest.fixture(scope="module")
def mini_world(mini_dir):
    """(estate, artifacts, inds, ontology) — the raw induced world."""
    estate = discover_sources(mini_dir)
    profiles, inds = profile_estate(estate)
    artifacts = induce_estate(estate, None, profiles=profiles, inds=inds)
    return estate, artifacts, inds, artifacts.ontology


@pytest.fixture(scope="module")
def atlas(mini_world) -> AtlasReport:
    estate, artifacts, inds, onto = mini_world
    return build_atlas(estate, artifacts, inds, onto)


def _by_name(onto):
    return {c.name: c.uri for c in onto.iter_classes()}


def _links(atlas: AtlasReport, tier: str):
    return [lk for lk in atlas.links if lk.tier == tier]


# ------------------------------------------------------------------- tiering


def test_true_fk_is_confirmed_with_recovered_ind_evidence(atlas, mini_world):
    _, _, inds, onto = mini_world
    names = _by_name(onto)
    flights = [
        lk for lk in _links(atlas, "confirmed")
        if lk.src_class == names["Flight"] and lk.dst_class == names["Airport"]
    ]
    assert len(flights) == 1, "the one true FK confirms exactly once"
    lk = flights[0]
    assert (lk.src_prop, lk.dst_prop) == ("origin", "airport_id")
    # the admitting IND was recovered: the link's score IS the IND's score
    ind = next(
        i for i in inds
        if (i.lhs_table, i.lhs_column, i.rhs_table, i.rhs_column)
        == ("flights", "origin", "airports", "airport_id")
    )
    assert lk.score == ind.score
    ev = lk.evidence
    assert ev.coverage == 1.0
    assert ev.overlap_count == 40          # every distinct origin value
    assert 1 <= len(ev.sample_shared_values) <= 5
    assert all(s.startswith("AP") for s in ev.sample_shared_values)
    assert ev.semtype_match is False


def test_sixty_percent_pair_is_likely_with_exact_evidence(atlas, mini_world):
    _, _, _, onto = mini_world
    names = _by_name(onto)
    likely = _links(atlas, "likely")
    assert len(likely) == 1
    lk = likely[0]
    assert lk.src_class == names["Aircraft"]
    assert lk.dst_class == names["Carrier"]
    assert (lk.src_prop, lk.dst_prop) == ("operated_by", "carrier_code")
    ev = lk.evidence
    assert ev.coverage == 0.6              # 15 of 25 distinct codes are real
    assert ev.overlap_count == 15
    assert ev.name_similarity == 0.0
    assert ev.semtype_match is False
    # actual shared values, from the column value sets
    assert 1 <= len(ev.sample_shared_values) <= 5
    assert all(s in {f"C-{i:02d}" for i in range(1, 16)} for s in ev.sample_shared_values)
    # score = 0.45*coverage + 0.25*name + 0.15*semtype + 0.15*rhs_uniqueness
    # carriers.carrier_code is a key (uniqueness 1.0): 0.27 + 0 + 0 + 0.15
    assert lk.score == pytest.approx(0.42)


def test_same_semtype_disjoint_pair_is_a_hint(atlas, mini_world):
    _, _, _, onto = mini_world
    names = _by_name(onto)
    hints = _links(atlas, "hint")
    assert len(hints) == 1
    lk = hints[0]
    assert {lk.src_class, lk.dst_class} == {names["Staff"], names["Vendor"]}
    assert lk.src_prop == "contact_email" and lk.dst_prop == "contact_email"
    ev = lk.evidence
    assert ev.semtype_match is True        # both columns type 'email'
    assert ev.coverage == 0.0 and ev.overlap_count == 0
    assert ev.sample_shared_values == ()
    # score = name_sim*0.5 + 0.3 with identical column names
    assert lk.score == pytest.approx(0.8)


def test_likely_cap_is_a_score_ranked_scale_guard(mini_world, monkeypatch):
    """LIKELY_CAP bounds the emitted likely arcs (strongest evidence first) —
    the same discipline as the hint cap, sized to the UI's 600-arc budget.
    A capped-away pair must not resurface as a hint."""
    import ontoforge.pipeline.atlas as atlas_mod

    estate, artifacts, inds, onto = mini_world
    monkeypatch.setattr(atlas_mod, "LIKELY_CAP", 0)
    capped = atlas_mod.build_atlas(estate, artifacts, inds, onto)
    assert capped.stats["likely"] == 0
    assert capped.stats["hint"] == 1, "the capped likely pair does not leak into hints"
    assert capped.stats["confirmed"] == 3


def test_confirmed_arcs_never_repeat_as_likely_or_hint(atlas):
    confirmed = {
        (lk.src_class, lk.src_prop, lk.dst_class, lk.dst_prop)
        for lk in _links(atlas, "confirmed")
    }
    for lk in _links(atlas, "likely") + _links(atlas, "hint"):
        assert (lk.src_class, lk.src_prop, lk.dst_class, lk.dst_prop) not in confirmed


# -------------------------------------------------------- components & stats


def test_components_islands_and_silos(atlas, mini_world):
    _, _, _, onto = mini_world
    names = _by_name(onto)
    by_label = {c.label: c for c in atlas.components}

    # confirmed links alone shape the islands; likely/hint never merge them
    flight_island = by_label["Flight"]     # labeled by its largest class
    assert set(flight_island.class_uris) == {names["Flight"], names["Airport"]}
    assert flight_island.dataset_count == 2
    assert flight_island.is_silo is False

    silos = [c for c in atlas.components if c.is_silo]
    assert {c.label for c in silos} == {"Staff", "Vendor"}
    assert all(len(c.class_uris) == 1 and c.dataset_count == 1 for c in silos)

    # ids are dense, ordered by island size; URIs globally unique (UI contract)
    assert [c.id for c in atlas.components] == [f"c{i}" for i in range(len(atlas.components))]
    every = [u for c in atlas.components for u in c.class_uris]
    assert len(every) == len(set(every))
    # every link endpoint appears in some component (UI fixture invariant)
    for lk in atlas.links:
        assert lk.src_class in set(every) and lk.dst_class in set(every)


def test_stats_pin_the_whole_atlas(atlas):
    assert atlas.stats == {
        "classes": 7,        # 6 table classes + the OperatedBy decomp class
        "components": 4,     # 2 islands + 2 silos
        "silos": 2,
        "confirmed": 3,      # Flight->Airport, Aircraft->Carrier, OperatedBy->Carrier
        "likely": 1,
        "hint": 1,
    }
    assert atlas.stats["confirmed"] == len(_links(atlas, "confirmed"))
    assert atlas.stats["likely"] == len(_links(atlas, "likely"))
    assert atlas.stats["hint"] == len(_links(atlas, "hint"))
    assert atlas.stats["classes"] == sum(len(c.class_uris) for c in atlas.components)


# ------------------------------------------------- persistence & ledger hook


def test_atlas_json_roundtrip_and_ledger_artifact(tmp_path, mini_world):
    from ontoforge.ledger import SqliteLedger

    estate, artifacts, inds, onto = mini_world
    ledger = SqliteLedger(str(tmp_path / "ledger.sqlite"))
    try:
        report = build_and_persist_atlas(
            tmp_path, estate, artifacts, inds=inds, ontology=onto, ledger=ledger
        )
        on_disk = json.loads((tmp_path / ATLAS_FILE).read_text(encoding="utf-8"))
        assert on_disk == report.to_payload()
        # constraint H: ONE-leaf provenance over the synthetic atlas-build atom
        rows = ledger.connection.execute(
            "SELECT artifact_id, prov_ref FROM artifact WHERE kind = 'atlas'"
        ).fetchall()
        assert len(rows) == 1
        atoms = ledger.valuate_ref(rows[0][1], "citations")
        assert len(atoms) == 1
        # idempotent: persisting again neither errors nor duplicates
        build_and_persist_atlas(
            tmp_path, estate, artifacts, inds=inds, ontology=onto, ledger=ledger
        )
        (n,) = ledger.connection.execute(
            "SELECT COUNT(*) FROM artifact WHERE kind = 'atlas'"
        ).fetchone()
        assert n == 1
    finally:
        ledger.close()


def test_materialize_induced_atlas_dir_hook(tmp_path, mini_dir):
    """materialize_induced(..., atlas_dir=...) writes the atlas at the end of
    materialization (from the ENRICHED ontology) without changing any
    existing-caller behavior."""
    from ontoforge.hearth import Hearth
    from ontoforge.ledger import SqliteLedger
    from ontoforge.pipeline import materialize_induced

    estate = discover_sources(mini_dir)
    ledger = SqliteLedger(str(tmp_path / "ledger.sqlite"))
    try:
        artifacts = induce_estate(estate, ledger)
        hearth = Hearth(tmp_path / "hearth", ledger)
        stats = materialize_induced(
            estate, artifacts.ontology, artifacts, hearth, ledger, atlas_dir=tmp_path
        )
        assert stats["entities"] > 0
        payload = json.loads((tmp_path / ATLAS_FILE).read_text(encoding="utf-8"))
        assert set(payload) == {"components", "links", "stats"}
        assert payload["stats"]["confirmed"] >= 3
        (n,) = ledger.connection.execute(
            "SELECT COUNT(*) FROM artifact WHERE kind = 'atlas'"
        ).fetchone()
        assert n == 1
    finally:
        ledger.close()


def test_python_dash_m_rebuild_path(tmp_path, mini_dir, atlas):
    """`python -m ontoforge.pipeline.atlas <project>` rebuilds atlas.json
    offline from config.json (the CLI/demo project path)."""
    project = tmp_path / "proj"
    project.mkdir()
    (project / "config.json").write_text(
        json.dumps({"estate": "generic", "source_dir": str(mini_dir),
                    "ledger": "ledger.sqlite", "hearth_root": "hearth"}),
        encoding="utf-8",
    )
    (project / "state.json").write_text(
        json.dumps({"limit": None, "cdc": {}, "stages": []}), encoding="utf-8"
    )
    proc = subprocess.run(
        [sys.executable, "-m", "ontoforge.pipeline.atlas", str(project)],
        capture_output=True, text=True, timeout=300,
    )
    assert proc.returncode == 0, proc.stderr
    assert "atlas built" in proc.stdout
    payload = json.loads((project / ATLAS_FILE).read_text(encoding="utf-8"))
    # the offline rebuild reproduces the in-process build exactly
    assert payload == atlas.to_payload()


def test_python_dash_m_usage_error():
    proc = subprocess.run(
        [sys.executable, "-m", "ontoforge.pipeline.atlas"],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 2
    assert "usage:" in proc.stderr
