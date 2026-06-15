"""Schema linking: extractive bidirectional pruning fits a large induced
ontology into a budget. Target (plan §B): >=90% recall of a known-needed set at
>=70% pruning, on a synthetic 200-property ontology."""

from __future__ import annotations

from ontoforge.aimodels.context import link_schema, render_grounding
from ontoforge.contracts.ontology import ClassDef, Datatype, Ontology, PropertyDef


def _build_200_prop_ontology() -> tuple[Ontology, str, set]:
    """A synthetic ontology with ~200 properties across 40 classes.

    The focus class ``Order`` (10 props incl. a link to Customer and a link to
    Product). The KNOWN-NEEDED set for a join task: Order's own props + the
    identity columns of its forward link target Customer + the props of a class
    that links backward INTO Order (LineItem). Everything else is noise — 37
    unrelated classes the pruner must drop while still recalling the needed set.
    """
    onto = Ontology()
    needed: set = set()

    def cu(slug: str) -> str:
        return f"onto://class/{slug}"

    # --- focus class Order (forward links to Customer, Product) ---
    order_uri = cu("order")
    order_props = (
        PropertyDef(uri="p", name="order_id", datatype=Datatype.STRING),
        PropertyDef(uri="p", name="order_date", datatype=Datatype.STRING),
        PropertyDef(uri="p", name="total_amount", datatype=Datatype.FLOAT),
        PropertyDef(uri="p", name="status", datatype=Datatype.STRING),
        PropertyDef(uri="p", name="currency", datatype=Datatype.STRING),
        PropertyDef(uri="p", name="discount", datatype=Datatype.FLOAT),
        PropertyDef(uri="p", name="channel", datatype=Datatype.STRING),
        PropertyDef(uri="p", name="notes", datatype=Datatype.STRING),
        PropertyDef(uri="p", name="customer_ref", is_link=True, range_class=cu("customer")),
        PropertyDef(uri="p", name="product_ref", is_link=True, range_class=cu("product")),
    )
    onto.add(ClassDef(uri=order_uri, name="Order", properties=order_props))
    for p in order_props:
        needed.add((order_uri, p.name))

    # --- forward target Customer (its identity cols are needed for the join) ---
    cust_uri = cu("customer")
    cust_props = (
        PropertyDef(uri="p", name="customer_id", datatype=Datatype.STRING),
        PropertyDef(uri="p", name="customer_code", datatype=Datatype.STRING),
        PropertyDef(uri="p", name="full_name", datatype=Datatype.STRING),
        PropertyDef(uri="p", name="email", datatype=Datatype.STRING),
        PropertyDef(uri="p", name="region", datatype=Datatype.STRING),
    )
    onto.add(ClassDef(uri=cust_uri, name="Customer", properties=cust_props))
    # the identity/key columns of the target are the join keys we must recall
    needed.add((cust_uri, "customer_id"))
    needed.add((cust_uri, "customer_code"))

    # --- forward target Product (identity col needed) ---
    prod_uri = cu("product")
    prod_props = (
        PropertyDef(uri="p", name="product_id", datatype=Datatype.STRING),
        PropertyDef(uri="p", name="product_name", datatype=Datatype.STRING),
        PropertyDef(uri="p", name="price", datatype=Datatype.FLOAT),
    )
    onto.add(ClassDef(uri=prod_uri, name="Product", properties=prod_props))
    needed.add((prod_uri, "product_id"))

    # --- backward source LineItem (links INTO Order) ---
    li_uri = cu("lineitem")
    li_props = (
        PropertyDef(uri="p", name="line_id", datatype=Datatype.STRING),
        PropertyDef(uri="p", name="order_ref", is_link=True, range_class=order_uri),
        PropertyDef(uri="p", name="qty", datatype=Datatype.INTEGER),
    )
    onto.add(ClassDef(uri=li_uri, name="LineItem", properties=li_props))
    # the backward source's key + its link to the focus must be recalled
    needed.add((li_uri, "line_id"))
    needed.add((li_uri, "order_ref"))

    # --- 36 unrelated noise classes, 5 props each = 180 noise props ---
    noise_topics = [
        "weather", "geology", "music", "recipe", "vehicle", "planet", "language",
        "sport", "painting", "mineral", "insect", "river", "mountain", "festival",
        "currency_rate", "satellite", "protein", "fungus", "alloy", "comet",
        "glacier", "volcano", "reef", "cipher", "tariff", "isotope", "pigment",
        "ballad", "lichen", "monsoon", "quasar", "tundra", "savanna", "fjord",
        "geyser", "atoll",
    ]
    for i, topic in enumerate(noise_topics):
        nu = cu(f"noise_{topic}")
        props = tuple(
            PropertyDef(uri="p", name=f"{topic}_attr_{j}", datatype=Datatype.STRING)
            for j in range(5)
        )
        onto.add(ClassDef(uri=nu, name=topic.replace("_", " ").title(), properties=props))

    return onto, order_uri, needed


def test_schema_linking_high_recall_at_high_pruning() -> None:
    onto, focus, needed = _build_200_prop_ontology()
    total_props = sum(len(c.properties) for c in onto.iter_classes())
    assert total_props >= 200  # the synthetic universe is large

    linked = link_schema(
        onto, focus_class=focus, focus_columns=["customer", "product", "line"], budget=40
    )

    # >=70% pruning (kept << total)
    assert linked.pruning >= 0.70, f"pruning was only {linked.pruning:.2f}"
    # >=90% recall of the known-needed set
    recall = linked.recall(needed)
    assert recall >= 0.90, f"recall was only {recall:.2f}"


def test_schema_linking_is_deterministic() -> None:
    onto, focus, _ = _build_200_prop_ontology()
    a = link_schema(onto, focus, ["customer"], budget=30)
    b = link_schema(onto, focus, ["customer"], budget=30)
    assert [e.key for e in a.kept] == [e.key for e in b.kept]


def test_focus_class_props_always_kept() -> None:
    onto, focus, _ = _build_200_prop_ontology()
    linked = link_schema(onto, focus, [], budget=15)
    focus_keys = {e.key for e in linked.kept if e.class_uri == focus}
    order = onto.get(focus)
    assert focus_keys == {(focus, p.name) for p in order.properties}


def test_render_grounding_is_compact_and_grouped() -> None:
    onto, focus, _ = _build_200_prop_ontology()
    linked = link_schema(onto, focus, ["customer", "product"], budget=40)
    text = render_grounding(linked, onto)
    assert "Order(" in text
    assert "Customer" in text
    # link rendered with its target name
    assert "->Customer" in text
