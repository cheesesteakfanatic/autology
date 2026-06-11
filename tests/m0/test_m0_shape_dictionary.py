"""Shape-dictionary efficiency (§4.2): terms sharing a derivation shape share
one PROV_SHAPE row; only the compact leaf arrays differ per term."""

from ontoforge.contracts.provenance import leaf, prov_prod, prov_sum
from ontoforge.ledger import SqliteLedger


def _counts(led):
    n_shapes = led.connection.execute("SELECT COUNT(*) FROM prov_shape").fetchone()[0]
    n_terms = led.connection.execute("SELECT COUNT(*) FROM prov_term").fetchone()[0]
    return n_shapes, n_terms


def test_1000_terms_3_shapes_yield_at_most_5_shape_rows():
    led = SqliteLedger()
    refs = set()
    for i in range(1000):
        x, y, z = f"x{i:04d}", f"y{i:04d}", f"z{i:04d}"
        family = i % 3
        if family == 0:
            # shape: slot0 * slot1   (e.g. same transform applied to two cells)
            t = prov_prod([leaf(x), leaf(y)])
        elif family == 1:
            # shape: slot0 + (slot1 * slot2)   (merge rule with fallback source)
            t = prov_sum([leaf(x), prov_prod([leaf(y), leaf(z)])])
        else:
            # shape: slot0   (direct copy)
            t = leaf(x)
        refs.add(led.intern(t))

    n_shapes, n_terms = _counts(led)
    assert n_shapes <= 5, f"shape dictionary blew up: {n_shapes} rows"
    assert n_shapes == 3  # exactly the three families
    assert n_terms == 1000  # every term distinct (distinct leaf atoms)
    assert len(refs) == 1000

    # round-trip still exact through the shared shapes
    sample = prov_sum([leaf("x0007"), prov_prod([leaf("y0007"), leaf("z0007")])])
    assert led.resolve(led.intern(sample)) == sample


def test_same_shape_different_leaves_distinct_refs():
    led = SqliteLedger()
    r1 = led.intern(prov_prod([leaf("a"), leaf("b")]))
    r2 = led.intern(prov_prod([leaf("c"), leaf("d")]))
    assert r1 != r2
    n_shapes, n_terms = _counts(led)
    assert (n_shapes, n_terms) == (1, 2)
    # leaf arrays differ, shapes shared
    rows = led.connection.execute("SELECT leaf_ids FROM prov_term ORDER BY prov_ref").fetchall()
    leaf_arrays = sorted(r[0] for r in rows)
    assert leaf_arrays == ['["a", "b"]', '["c", "d"]']


def test_reinterning_adds_no_rows():
    led = SqliteLedger()
    t = prov_prod([leaf("a"), leaf("b")])
    for _ in range(10):
        led.intern(t)
    assert _counts(led) == (1, 1)
