"""Generic estate engine, end to end on a NON-aviation corpus: discovery ->
M3/M4 induction -> generic ER -> induced-ontology materialization -> LODESTONE
answers over the world OntoForge built for itself.

This is the product claim ("works on ANY data") under test: nothing below
mentions retail anywhere in src/.
"""

from __future__ import annotations

import pytest

from ontoforge.contracts import SpineProfile
from ontoforge.hearth import Hearth
from ontoforge.ledger import SqliteLedger
from ontoforge.lodestone import Lodestone
from ontoforge.pipeline import discover_sources, induce_estate, materialize_induced
from ontoforge.spine import DecisionSpine


@pytest.fixture(scope="module")
def estate(retail_dir):
    return discover_sources(retail_dir)


@pytest.fixture(scope="module")
def ledger():
    led = SqliteLedger(":memory:")
    yield led
    led.close()


@pytest.fixture(scope="module")
def artifacts(estate, ledger):
    return induce_estate(estate, ledger)


@pytest.fixture(scope="module")
def world(tmp_path_factory, estate, artifacts, ledger):
    hearth = Hearth(tmp_path_factory.mktemp("retail-hearth") / "store", ledger)
    stats = materialize_induced(estate, artifacts.ontology, artifacts, hearth, ledger)
    return hearth, stats


@pytest.fixture(scope="module")
def engine(artifacts, world, ledger):
    hearth, _ = world
    return Lodestone(artifacts.ontology, hearth, ledger, DecisionSpine(SpineProfile(), model_client=None))


# ------------------------------------------------------------------ discovery


def test_discovery_finds_all_tables_with_m3_keys(estate):
    meta = estate["metadata"]["tables"]
    assert set(estate["tables"]) == {"customers", "orders", "products", "suppliers"}
    assert estate["metadata"]["estate"] == "generic"
    # M3 candidate-key detection picked the identifier columns
    assert meta["customers"]["key_columns"] == ["customer_id"]
    assert meta["orders"]["key_columns"] == ["order_id"]
    assert meta["products"]["key_columns"] == ["product_id"]
    assert meta["suppliers"]["key_columns"] == ["supplier_id"]
    # wart preservation: everything loads as strings
    assert all(
        df.dtypes.astype(str).isin(["object", "str"]).all()
        for df in estate["tables"].values()
    )


# ------------------------------------------------------------------ induction


def test_induction_yields_classes_for_every_table(artifacts):
    onto = artifacts.ontology
    names = {c.name for c in onto.iter_classes()}
    assert len(onto.classes) >= 4
    assert {"Customer", "Order", "Product", "Supplier"} <= names


def test_induction_discovers_fk_links(artifacts):
    onto = artifacts.ontology
    order = onto.by_name("Order")
    links = {p.name: p for p in order.properties if p.is_link}
    assert "customer_id" in links and "product_id" in links
    assert onto.classes[links["customer_id"].range_class].name == "Customer"
    assert onto.classes[links["product_id"].range_class].name == "Product"


# ------------------------------------------------------------- materialization


def test_materialization_commits_entities_and_links(world):
    _, stats = world
    classes = stats["classes"]
    assert classes["Order"] == 250
    assert classes["Customer"] == 200
    assert classes["Product"] == 200
    assert classes["Supplier"] == 150
    assert stats["cells"] > stats["entities"]
    # FK links (orders -> customers/products) plus ER links (products -> suppliers)
    assert stats["links"] >= 250 + 250 + 150


def test_conformance_patches_units_into_the_ontology(artifacts):
    onto = artifacts.ontology
    order = onto.by_name("Order")
    product = onto.by_name("Product")
    props = {p.name: p for p in order.properties} | {p.name: p for p in product.properties}
    assert props["total_price"].unit == "USD" and props["total_price"].datatype.value == "float"
    assert props["weight"].unit == "kg" and props["weight"].datatype.value == "float"
    # mixed lexical forms recorded the per-cell source unit annotation
    assert "total_price_unit" in props


def test_constraint_h_provenance_on_committed_cells(world, ledger):
    """Every committed cell carries an interned prov_ref that valuates to
    REAL registered source atoms (constraint H)."""
    hearth, _ = world
    checked = 0
    for shard in hearth.value_shard_items():
        for cell in list(shard.cells)[:5]:
            atoms = ledger.valuate_ref(cell.prov_ref, "citations")
            assert atoms, f"cell {cell.entity_uri}.{cell.prop} has no atoms"
            for atom_id in atoms:
                atom = ledger.get_atom(atom_id)
                assert atom is not None and atom.uri.startswith("atom://")
            checked += 1
    assert checked >= 20


def test_generic_er_folds_supplier_spelling_variants(world):
    """~30% of product rows carry mangled supplier spellings; the M5 cascade
    folds them onto the 150 real suppliers (cross-table identity)."""
    _, stats = world
    er = stats["er"]
    supplier = next(v for k, v in er.items() if k == "Supplier")
    assert supplier["method"] == "er-cascade"
    assert set(supplier["tables"]) == {"products", "suppliers"}
    # >150 lexical identities resolved into exactly the 150 true suppliers
    assert supplier["identities"] > 150
    assert supplier["clusters"] == 150


# ----------------------------------------------------------------------- ask


def _flat(ans):
    return [v for r in ans.rows for v in r]


def _assert_cited(ans, ledger):
    n_cells = sum(len(r) for r in ans.rows)
    assert len(ans.citations) == n_cells
    for cell in ans.citations:
        assert cell.atom_ids
        for atom_id in cell.atom_ids:
            assert ledger.get_atom(atom_id) is not None


def test_ask_count(engine, ledger, retail_frames):
    ans = engine.ask("How many orders are there?")
    assert not ans.abstained and not ans.clarification
    assert _flat(ans) == [len(retail_frames["orders"])]
    _assert_cited(ans, ledger)


def test_ask_aggregate_with_unit(engine, ledger, retail_frames):
    ans = engine.ask("What is the average weight of products in kg?")
    assert not ans.abstained and not ans.clarification
    truth = retail_frames["products"]["_weight_num"].mean()
    assert abs(float(_flat(ans)[0]) - truth) < 1e-6
    _assert_cited(ans, ledger)


def test_ask_sum_in_usd_over_mixed_lexical_forms(engine, ledger, retail_frames):
    ans = engine.ask("What is the total price across all orders in USD?")
    assert not ans.abstained and not ans.clarification
    truth = retail_frames["orders"]["_total_num"].sum()
    assert abs(float(_flat(ans)[0]) - truth) < 1e-6
    _assert_cited(ans, ledger)


def test_ask_one_hop_through_er_resolved_link(engine, ledger, retail_frames):
    """rating-of-supplier-of-product traverses the ER-built Product->Supplier
    link (no IND exists through the spelling variants)."""
    ans = engine.ask("What is the rating of the supplier of product P0042?")
    assert not ans.abstained and not ans.clarification
    products = retail_frames["products"]
    suppliers = retail_frames["suppliers"]
    true_name = products.loc[products["product_id"] == "P0042", "_supplier_true"].iloc[0]
    truth = suppliers.loc[suppliers["supplier_name"] == true_name, "rating"].iloc[0]
    assert [str(v) for v in _flat(ans)] == [truth]
    _assert_cited(ans, ledger)


def test_ask_filtered_count(engine, ledger, retail_frames):
    ans = engine.ask("How many orders have status 'delivered'?")
    assert not ans.abstained and not ans.clarification
    truth = int((retail_frames["orders"]["status"] == "delivered").sum())
    assert _flat(ans) == [truth]
    _assert_cited(ans, ledger)


def test_ask_abstains_on_unanswerable(engine):
    ans = engine.ask("What is the warranty period of product P0042?")
    assert ans.abstained
    assert "grounding" in ans.abstain_reason or "interpretation" in ans.abstain_reason
