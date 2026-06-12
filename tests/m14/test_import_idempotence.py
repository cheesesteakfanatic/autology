"""M14 gates — export-import idempotence + working-store reconstruction.

snapshot(import_bundle(snapshot(X))) must be MANIFEST-EQUAL to snapshot(X) —
full dict equality including every per-file sha256. Timestamps are excluded
from hashed content by construction (ledger created_at columns are never
serialized), so this is byte-level idempotence, not modulo-anything fuzz.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ontoforge.amber import AmberError, import_bundle, snapshot
from ontoforge.contracts import CURRENT, Stance
from ontoforge.hearth import canonical_state
from ontoforge.temper import load_morphisms, replay

NS = "onto://gold/aviation"


@pytest.fixture(scope="module")
def imported(bundle, tmp_path_factory):
    root = tmp_path_factory.mktemp("amber_import") / "root"
    return import_bundle(bundle, root)


def test_manifest_equal_after_roundtrip(world, bundle, imported, tmp_path_factory):
    h2, o2, l2 = imported
    out2 = tmp_path_factory.mktemp("amber_again") / "bundle2"
    snapshot(out2, h2, o2, l2)
    m1 = json.loads((bundle / "manifest.json").read_text())
    m2 = json.loads((out2 / "manifest.json").read_text())
    assert m1 == m2  # hashes and all


def test_imported_hearth_is_cell_identical(world, imported):
    h2, _, _ = imported
    assert canonical_state(h2) == canonical_state(world["hearth"])


def test_imported_store_answers_stanced_reads(world, imported):
    h2, _, _ = imported
    known = world["known_uri"]
    assert h2.read(known, CURRENT) == world["hearth"].read(known, CURRENT)
    t_mid = world["known"]["t_mid"]
    assert h2.read(known, Stance("as_of", valid_at=t_mid)) == world["hearth"].read(
        known, Stance("as_of", valid_at=t_mid)
    )
    assert h2.traverse(known, "model") == world["hearth"].traverse(known, "model")


def test_imported_ontology_is_exact(world, imported):
    _, o2, _ = imported
    assert o2.version == world["ontology"].version
    assert o2.classes == world["ontology"].classes  # frozen dataclasses: deep equality


def test_imported_ledger_resolves_all_cell_provenance(world, imported):
    h2, _, l2 = imported
    for shard in h2.value_shard_items():
        for c in shard.cells:
            assert l2.valuate_ref(c.prov_ref, "derivable") is True
            citations = l2.valuate_ref(c.prov_ref, "citations")
            assert citations == world["ledger"].valuate_ref(c.prov_ref, "citations")


def test_morphism_ledger_replays_from_imported_store(world, imported):
    """The §7 promise that the departing customer can replay the operator
    path: morphisms re-loaded from the REBUILT ledger replay gold v1 into the
    exact bundled ontology."""
    _, o2, l2 = imported
    records = load_morphisms(l2)
    assert [r.to_payload() for r in records] == [world["morphism_record"].to_payload()]
    replayed = replay(records, world["gold"])
    assert replayed.version == o2.version
    assert replayed.classes == o2.classes


def test_imported_store_stays_live(world, imported):
    """Import yields a WORKING store: a post-import commit must succeed and
    respect the restored monotone clock."""
    from ontoforge.contracts import Interval, Layer, ValueCell, leaf, make_cell_atom

    h2, _, l2 = imported
    atom = make_cell_atom("post-import", "t", "r", "c", "v")
    l2.register_atoms([atom])
    ref = l2.intern(leaf(atom.atom_id))
    cell = ValueCell(
        entity_uri=world["known_uri"],
        prop="notes",
        value="written after import",
        valid=Interval(0),
        system=Interval(0),
        prov_ref=ref,
        confidence=1.0,
        src_rank=1,
    )
    assert h2.commit(Layer.ENTITY, f"{NS}/Aircraft", [cell]) == 1
    assert h2.read(world["known_uri"], CURRENT)["notes"] == "written after import"


def test_import_refuses_tampered_bundle(bundle, tmp_path):
    import shutil

    dst = tmp_path / "bad"
    shutil.copytree(bundle, dst)
    victim = dst / "provenance" / "prov_terms.jsonl"
    victim.write_text(victim.read_text().replace("{", "{ ", 1))
    with pytest.raises(AmberError, match="verification"):
        import_bundle(dst, tmp_path / "root")


def test_import_refuses_nonempty_root(bundle, tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    (root / "junk").write_text("x")
    with pytest.raises(AmberError, match="not empty"):
        import_bundle(bundle, root)
