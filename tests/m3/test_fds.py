"""FD & candidate-key discovery tests (§3.1, AMD-0003): exact recovery on declared
keys, soundness (no false FDs at confidence 1.0), approximate FDs, determinism."""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from m3_helpers import fd_holds, is_unique, make_customers, make_lineitems, make_orders
from ontoforge.profiling import candidate_keys, discover_fds, partition_product, stripped_partition
from ontoforge.profiling._values import value_key


# ----------------------------------------------------- exact recovery + soundness


def test_customers_pk_fds_recovered():
    cust = make_customers()
    fds = discover_fds(cust, "customers")
    exact = {(f.lhs, f.rhs) for f in fds if f.confidence == 1.0}
    for rhs in ("c_name", "c_nationkey", "c_acctbal", "c_mktsegment"):
        assert (("c_custkey",), rhs) in exact, rhs


def test_no_false_fds_at_confidence_one():
    """Soundness: every emitted exact FD must hold under a brute-force scan."""
    for name, table in (("customers", make_customers()),
                        ("orders", make_orders()),
                        ("lineitems", make_lineitems())):
        for fd in discover_fds(table, name):
            if fd.confidence == 1.0:
                assert fd_holds(table, fd.lhs, fd.rhs), (name, fd)


def test_known_non_fd_not_reported_exact():
    cust = make_customers()
    assert not fd_holds(cust, ("c_nationkey",), "c_mktsegment")  # fixture sanity
    fds = discover_fds(cust, "customers")
    assert not any(
        f.lhs == ("c_nationkey",) and f.rhs == "c_mktsegment" and f.confidence == 1.0
        for f in fds
    )


def test_exact_fds_are_lhs_minimal():
    orders = make_orders()
    exact = [(f.lhs, f.rhs) for f in discover_fds(orders, "orders") if f.confidence == 1.0]
    seen = set()
    for lhs, rhs in exact:
        for prev_lhs, prev_rhs in seen:
            assert not (prev_rhs == rhs and set(prev_lhs) < set(lhs)), (lhs, rhs)
        seen.add((lhs, rhs))


def test_composite_key_fds_in_lineitems():
    li = make_lineitems()
    fds = discover_fds(li, "lineitems")
    exact = {(f.lhs, f.rhs) for f in fds if f.confidence == 1.0}
    # composite PK determines the payload columns (lhs in canonical sorted order),
    # and no single column does
    assert (("l_linenumber", "l_orderkey"), "l_quantity") in exact
    assert not any(len(lhs) == 1 and rhs == "l_quantity" for lhs, rhs in exact)


def test_lhs_size_cap_respected():
    li = make_lineitems()
    assert all(len(f.lhs) <= 3 for f in discover_fds(li, "lineitems", max_lhs=3))
    assert all(len(f.lhs) <= 1 for f in discover_fds(li, "lineitems", max_lhs=1))


def test_constant_column_yields_empty_lhs_fd():
    data = {"a": [1, 2, 3, 4], "k": ["x", "x", "x", "x"]}
    fds = discover_fds(data, "t")
    assert any(f.lhs == () and f.rhs == "k" and f.confidence == 1.0 for f in fds)


# ------------------------------------------------------------- approximate FDs


def test_approximate_fd_confidence():
    n = 1000
    x = [i // 2 for i in range(n)]
    y = [3 * (i // 2) for i in range(n)]
    corrupt = [4, 40, 100, 222, 380, 444, 600, 700, 808, 998]  # 10 rows, distinct classes
    for k in corrupt:
        y[k] = 10**6 + k
    fds = discover_fds({"x": x, "y": y}, "axy")
    hit = [f for f in fds if f.lhs == ("x",) and f.rhs == "y"]
    assert len(hit) == 1
    assert 0.985 <= hit[0].confidence < 1.0          # g3 = 1 - 10/1000
    # the corrupted FD must NOT also appear as exact
    assert not any(f.lhs == ("x",) and f.rhs == "y" and f.confidence == 1.0 for f in fds)


def test_below_threshold_afd_suppressed():
    n = 100
    x = [i // 2 for i in range(n)]
    y = [7 * (i // 2) for i in range(n)]
    for k in range(0, 10):                            # 5% corruption -> conf 0.95 < 0.98
        y[k * 10] = 10**6 + k
    fds = discover_fds({"x": x, "y": y}, "t")
    assert not any(f.lhs == ("x",) and f.rhs == "y" for f in fds)


# ---------------------------------------------------------------- candidate keys


def test_declared_pk_recovery():
    assert ("c_custkey",) in candidate_keys(make_customers())
    assert ("o_orderkey",) in candidate_keys(make_orders())
    li_keys = candidate_keys(make_lineitems())
    assert ("l_linenumber", "l_orderkey") in li_keys  # canonical sorted order
    assert not any(len(k) == 1 for k in li_keys)      # no single column is unique


def test_candidate_keys_sound_and_minimal():
    for table in (make_customers(), make_orders(), make_lineitems()):
        for key in candidate_keys(table):
            assert is_unique(table, key), key
            for c in key:
                if len(key) > 1:
                    assert not is_unique(table, tuple(k for k in key if k != c)), key


def test_null_bearing_columns_excluded_from_keys():
    data = {"id": [1, 2, None, 4], "v": ["a", "b", "c", "d"]}
    assert ("id",) not in candidate_keys(data)
    assert ("v",) in candidate_keys(data)


def test_key_size_cap():
    # only the triple (a,b,c) is unique -> nothing returned at cap 2
    data = {
        "a": [0, 0, 0, 0, 1, 1, 1, 1],
        "b": [0, 0, 1, 1, 0, 0, 1, 1],
        "c": [0, 1, 0, 1, 0, 1, 0, 1],
    }
    assert candidate_keys(data, max_key_size=2) == ()


# ------------------------------------------------------ partition machinery


@settings(derandomize=True, max_examples=60, deadline=None)
@given(st.lists(st.tuples(st.integers(0, 4), st.integers(0, 4)), min_size=1, max_size=40))
def test_property_partition_product_equals_direct_partition(pairs):
    """π*(X)·π*(Y) must equal π* computed directly on the combined key."""
    n = len(pairs)
    ka = [value_key(a) for a, _ in pairs]
    kb = [value_key(b) for _, b in pairs]
    kab = [f"{a}|{b}" for a, b in zip(ka, kb)]
    prod = partition_product(stripped_partition(ka), stripped_partition(kb), n)
    direct = stripped_partition(kab)
    def norm(p):
        return sorted(tuple(sorted(c)) for c in p)

    assert norm(prod) == norm(direct)


def test_discover_fds_deterministic():
    li = make_lineitems()
    assert discover_fds(li, "lineitems") == discover_fds(li, "lineitems")
