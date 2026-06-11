"""Export/import: the AMBER precursor (§4.2 constraint (P), §4.5, §7).

Gold gates: round-trip CELL-SET EQUALITY (bit-equivalent canonical state),
export idempotence (same content hashes from the re-imported store), manifest
hash verification (tamper detection), and stance-answer equality after import
with rebuilt serving indexes.
"""

from __future__ import annotations

import json

import pyarrow.parquet as pq
import pytest

from m6_helpers import lc, mint_prov, vc

from ontoforge.contracts import Layer, Stance
from ontoforge.hearth import (
    Hearth,
    PortabilityError,
    SetProperty,
    canonical_state,
    export_canonical,
    import_canonical,
)
from ontoforge.ledger import SqliteLedger

CLASS = "onto://test/Thing"
NODE = "onto://test/Node"


def _populate(h: Hearth, ledger: SqliteLedger) -> None:
    """Values (with supersession + correction + DOA), links (with unlink),
    an Action, and a RAW-layer shard — every canonical surface."""
    p1 = mint_prov(ledger, "port", 1)
    p2 = mint_prov(ledger, "port", 2)
    h.commit(Layer.RAW, "raw.src_table", [vc("row://1", "col", "raw-v", p1)], now=500)
    h.commit(Layer.ENTITY, CLASS, [vc("e://1", "status", "ok", p1, valid_from=100)], now=1000)
    h.commit(
        Layer.ENTITY, CLASS, [vc("e://1", "status", "bad", p2, valid_from=200, valid_to=300)], now=2000
    )
    h.commit(Layer.ENTITY, CLASS, [vc("e://1", "score", 1.25, p1), vc("e://2", "score", 2, p2)], now=3000)
    h.action("alice", SetProperty(CLASS, "e://1", "note", "checked"), now=4000)
    h.commit(Layer.ENTITY, CLASS, [vc("e://1", "note", "pipeline", p2, rank=2)], now=5000)  # DOA
    h.commit_links(NODE, "feeds", [lc("n://a", "feeds", "n://b", p1), lc("n://b", "feeds", "n://c", p2)], now=6000)
    h.links.unlink(NODE, "feeds", "n://a", "n://b", p1, now=7000)


STANCES = [
    Stance(),
    Stance("as_of", valid_at=250),
    Stance("as_known_at", known_at=1500),
    Stance("audit", valid_at=250, known_at=2500),
]


def test_round_trip_bit_equivalent(tmp_path, ledger) -> None:
    h = Hearth(tmp_path / "src", ledger)
    _populate(h, ledger)
    manifest_path = export_canonical(h, tmp_path / "bundle")
    h2 = import_canonical(tmp_path / "bundle", tmp_path / "dst", ledger)

    # THE gate: identical cell multisets in identical order, values + links
    assert canonical_state(h2) == canonical_state(h)

    # all stance answers survive (serving indexes rebuilt, not carried)
    for stance in STANCES:
        for entity in ("e://1", "e://2"):
            assert h2.read(entity, stance) == h.read(entity, stance), stance
    assert h2.traverse("n://b", "feeds") == h.traverse("n://b", "feeds") == ["n://c"]
    assert h2.traverse("n://a", "feeds") == []
    assert h2.traverse("n://a", "feeds", Stance("as_of", valid_at=6500)) == ["n://b"]
    # RAW layer carried too
    assert h2.read("row://1", class_uri="raw.src_table", layer=Layer.RAW) == {"col": "raw-v"}

    # manifest sanity
    manifest = json.loads(manifest_path.read_text())
    assert manifest["format"] == "hearth-canonical"
    kinds = {(e["kind"], e.get("layer")) for e in manifest["shards"]}
    assert ("values", "entity") in kinds and ("values", "raw") in kinds and ("links", None) in kinds
    assert all(e["rows"] > 0 and len(e["content_hash"]) == 16 for e in manifest["shards"])


def test_export_is_idempotent(tmp_path, ledger) -> None:
    """export -> import -> export must reproduce the SAME content hashes."""
    h = Hearth(tmp_path / "src", ledger)
    _populate(h, ledger)
    m1 = json.loads(export_canonical(h, tmp_path / "b1").read_text())
    h2 = import_canonical(tmp_path / "b1", tmp_path / "dst", ledger)
    m2 = json.loads(export_canonical(h2, tmp_path / "b2").read_text())

    def hashes(m):
        return sorted(
            (e["kind"], e.get("layer"), e["class_uri"], e.get("predicate"), e["rows"], e["content_hash"])
            for e in m["shards"]
        )

    assert hashes(m1) == hashes(m2)


def test_import_detects_tampering(tmp_path, ledger) -> None:
    h = Hearth(tmp_path / "src", ledger)
    _populate(h, ledger)
    export_canonical(h, tmp_path / "bundle")
    manifest = json.loads((tmp_path / "bundle" / "manifest.json").read_text())
    entry = next(e for e in manifest["shards"] if e["kind"] == "values" and e["layer"] == "entity")
    victim = tmp_path / "bundle" / entry["path"]
    # tamper: flip one value inside the Parquet (rewrite with a changed row)
    table = pq.read_table(victim).to_pylist()
    table[0]["value_json"] = json.dumps("TAMPERED")
    import pyarrow as pa

    from ontoforge.hearth import CELL_SCHEMA

    pq.write_table(pa.Table.from_pylist(table, schema=CELL_SCHEMA), victim)
    with pytest.raises(PortabilityError, match="hash mismatch"):
        import_canonical(tmp_path / "bundle", tmp_path / "dst", ledger)


def test_import_refuses_missing_manifest_and_nonempty_target(tmp_path, ledger) -> None:
    with pytest.raises(PortabilityError, match="manifest"):
        import_canonical(tmp_path / "nowhere", tmp_path / "dst0", ledger)
    h = Hearth(tmp_path / "src", ledger)
    _populate(h, ledger)
    export_canonical(h, tmp_path / "bundle")
    with pytest.raises(PortabilityError, match="not an empty Hearth root"):
        import_canonical(tmp_path / "bundle", tmp_path / "src", ledger)  # already populated


def test_imported_store_accepts_new_commits(tmp_path, ledger) -> None:
    """The imported store is LIVE: its clock floor respects imported system
    times, so new commits keep system time monotone."""
    h = Hearth(tmp_path / "src", ledger)
    _populate(h, ledger)
    export_canonical(h, tmp_path / "bundle")
    h2 = import_canonical(tmp_path / "bundle", tmp_path / "dst", ledger)
    prov = mint_prov(ledger, "post-import")
    from ontoforge.hearth import CommitRejected

    with pytest.raises(CommitRejected, match="monotone"):
        h2.commit(Layer.ENTITY, CLASS, [vc("e://3", "x", 1, prov)], now=100)  # before clock floor
    h2.commit(Layer.ENTITY, CLASS, [vc("e://3", "x", 1, prov)], now=8000)
    assert h2.read("e://3") == {"x": 1}
    assert h2.read("e://1")["status"] == "ok"  # imported state intact
