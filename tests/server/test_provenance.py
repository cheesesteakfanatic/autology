"""GET /api/provenance/{prov_ref} — a REAL interned term renders as a nested
sum/product/atom tree whose leaves resolve to ledger atoms."""

from __future__ import annotations


def _leaves(node: dict) -> list[dict]:
    if node["kind"] == "atom":
        return [node]
    return [leaf for t in node.get("terms", []) for leaf in _leaves(t)]


def test_provenance_renders_a_real_term_tree(client, ledger_db):
    # any interned term with at least one leaf, straight from the world build
    row = ledger_db.execute("SELECT prov_ref FROM prov_leaf LIMIT 1").fetchone()
    assert row, "the materialized world interned provenance terms"
    prov_ref = row[0]

    out = client.get(f"/api/provenance/{prov_ref}")
    assert out.status_code == 200
    p = out.json()
    assert p["prov_ref"] == prov_ref
    assert p["n_atoms"] >= 1
    assert p["tree"]["kind"] in {"atom", "sum", "product"}

    leaves = _leaves(p["tree"])
    assert leaves, "the tree bottoms out in atoms"
    assert len({leaf["atom_id"] for leaf in leaves}) >= p["n_atoms"] >= 1
    assert any(leaf["uri"] and leaf["uri"].startswith("atom://") for leaf in leaves)


def test_unknown_prov_ref_is_404(client):
    assert client.get("/api/provenance/no-such-ref").status_code == 404
