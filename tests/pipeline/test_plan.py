"""PLAN mode — governed-subset tests (v2.1 R0/P0, mode one).

The contract ``plan_subset`` must satisfy on a multi-table synthetic estate:

* the subset is WITHIN BUDGET;
* it retains a HIGH FRACTION of candidate-key distinct values and, crucially, the
  CROSS-TABLE key overlap so joins SURVIVE on the subset (relationship discovery
  still fires);
* it OVERSAMPLES distribution edges (tails / modes / min / max / rare values);
* it is DETERMINISTIC (seeded) — equal inputs yield byte-identical subsets;
* a TINY budget still keeps at least one row per key value where feasible;
* an initial ontology HYPOTHESIS bootstraps the subset (oversamples hypothesized
  join keys) without breaking budget or joinability.

Every table is fed as a plain column mapping (the universal M3 input form); the
planner profiles them through the real M3 profiler, so the tests exercise genuine
candidate-key / IND discovery, not stubs.
"""

from __future__ import annotations

import random

import pytest

from ontoforge.profiling import discover_inds
from ontoforge.pipeline.plan import (
    MIN_JOIN_KEYS,
    OntologyHypothesis,
    PlanReport,
    plan_subset,
)


# --------------------------------------------------------------------------
# Synthetic estates with KNOWN joins
# --------------------------------------------------------------------------


def _star_estate(n_customers: int = 200, n_orders: int = 1000, seed: int = 7):
    """A classic star: orders.customer_id is an FK into customers.customer_id."""
    rng = random.Random(seed)
    customers = {
        "customer_id": list(range(1, n_customers + 1)),
        "region": [["north", "south", "east", "west"][i % 4] for i in range(n_customers)],
        "tier": [["gold", "silver", "bronze"][i % 3] for i in range(n_customers)],
        "lifetime_value": [round(rng.uniform(0, 5000), 2) for _ in range(n_customers)],
    }
    orders = {
        "order_id": list(range(1, n_orders + 1)),
        "customer_id": [rng.randint(1, n_customers) for _ in range(n_orders)],
        "amount": [round(rng.uniform(5, 500), 2) for _ in range(n_orders)],
        "status": [["paid", "pending", "refunded"][i % 3] for i in range(n_orders)],
    }
    return {"customers": customers, "orders": orders}


def _bridge_estate(seed: int = 11):
    """A 3-table m2m: authors — wrote(bridge) — books."""
    rng = random.Random(seed)
    authors = {
        "author_id": list(range(1, 21)),
        "country": [["US", "UK", "FR", "DE"][i % 4] for i in range(20)],
    }
    books = {
        "book_id": list(range(1, 51)),
        "title": [f"title_{i}" for i in range(50)],
        "year": [2000 + (i % 20) for i in range(50)],
    }
    wrote = {
        "author_id": [rng.randint(1, 20) for _ in range(120)],
        "book_id": [rng.randint(1, 50) for _ in range(120)],
    }
    return {"authors": authors, "books": books, "wrote": wrote}


def _real_fk_overlap(report: PlanReport, lt: str, lc: str, rt: str, rc: str):
    for o in report.overlaps:
        if (o.lhs_table, o.lhs_column, o.rhs_table, o.rhs_column) == (lt, lc, rt, rc):
            return o
    raise AssertionError(f"no overlap recorded for {lt}.{lc} -> {rt}.{rc}")


# --------------------------------------------------------------------------
# Budget
# --------------------------------------------------------------------------


def test_subset_within_budget():
    tables = _star_estate()
    subset, report = plan_subset(tables, budget=300)
    assert report.within_budget
    assert report.total_kept <= 300
    # materialized subset row counts match the report
    assert sum(len(rows) for rows in subset.values()) == report.total_kept
    for tp in report.tables:
        assert len(subset[tp.table]) == tp.kept_rows
        assert tp.kept_rows <= tp.budget


def test_budget_at_least_full_table_keeps_everything():
    tables = _bridge_estate()
    total = sum(len(next(iter(c.values()))) for c in tables.values())
    subset, report = plan_subset(tables, budget=total + 100)
    # budget >= estate size -> keep all rows, still within budget
    assert report.total_kept == total
    assert report.within_budget
    for tp in report.tables:
        assert tp.kept_rows == tp.total_rows


def test_budget_apportioned_across_tables():
    tables = _star_estate()
    _, report = plan_subset(tables, budget=300)
    # every non-empty table got a non-zero slice
    for tp in report.tables:
        assert tp.kept_rows > 0


# --------------------------------------------------------------------------
# Joinability — the load-bearing guarantee
# --------------------------------------------------------------------------


def test_joins_survive_on_subset():
    tables = _star_estate()
    _, report = plan_subset(tables, budget=300)
    assert report.joinability_ok(), report.severed_joins()
    # the real FK edge is retained with strong co-kept overlap
    o = _real_fk_overlap(report, "orders", "customer_id", "customers", "customer_id")
    assert o.survives
    assert o.kept_overlap >= min(MIN_JOIN_KEYS, o.achievable)


def test_discovery_refires_on_the_subset():
    """The whole point: IND/relationship discovery must still find the join on the
    pulled subset — the subset is not a pile of silos."""
    tables = _star_estate()
    subset, _ = plan_subset(tables, budget=300)
    # rebuild column-mapping corpus from the kept rows and re-run IND discovery
    corpus: dict[str, dict[str, list]] = {}
    for table, rows in subset.items():
        cols = {c: [r[c] for r in rows] for c in (rows[0].keys() if rows else [])}
        corpus[table] = cols
    inds = discover_inds(corpus, min_coverage=0.9)
    # the FK join orders.customer_id -> customers.customer_id must reappear
    found = any(
        i.lhs_table == "orders"
        and i.lhs_column == "customer_id"
        and i.rhs_table == "customers"
        and i.rhs_column == "customer_id"
        for i in inds
    )
    assert found, "FK join did not re-fire on the subset"


def test_bridge_estate_all_joins_survive():
    tables = _bridge_estate()
    _, report = plan_subset(tables, budget=80)
    assert report.joinability_ok(), report.severed_joins()
    assert report.within_budget


def test_high_candidate_key_coverage_retained():
    tables = _star_estate()
    _, report = plan_subset(tables, budget=600)
    customers = next(t for t in report.tables if t.table == "customers")
    # with a generous budget the dimension's primary key keeps most distinct values
    assert customers.key_coverage["customer_id"] >= 0.5


# --------------------------------------------------------------------------
# Distribution edges
# --------------------------------------------------------------------------


def test_oversamples_distribution_edges_min_max():
    # a numeric column with extreme tails plus dense modes
    measure = [50] * 100 + [51] * 100 + [49] * 100 + [999_999] + [1] + [50] * 100
    table = {"measure": measure, "id": list(range(len(measure)))}
    subset, report = plan_subset({"t": table}, budget=12)
    kept = [r["measure"] for r in subset["t"]]
    assert min(measure) in kept, "min tail dropped"
    assert max(measure) in kept, "max tail dropped"
    # the modal value is represented too
    assert 50 in kept
    tp = report.tables[0]
    assert "measure" in tp.edge_columns
    assert tp.reasons.get("distribution_edges", 0) > 0


def test_rare_values_kept():
    # a categorical with one rare class amid common ones
    cat = ["common"] * 200 + ["rare_a", "rare_b"]
    table = {"cat": cat, "id": list(range(len(cat)))}
    subset, _ = plan_subset({"t": table}, budget=20)
    kept = {r["cat"] for r in subset["t"]}
    assert "rare_a" in kept and "rare_b" in kept


# --------------------------------------------------------------------------
# Determinism
# --------------------------------------------------------------------------


def test_deterministic_subset():
    tables = _star_estate()
    s1, r1 = plan_subset(tables, budget=300)
    s2, r2 = plan_subset(tables, budget=300)
    assert [t.kept_indices for t in r1.tables] == [t.kept_indices for t in r2.tables]
    assert s1 == s2
    assert r1.total_kept == r2.total_kept


def test_deterministic_with_hypothesis():
    tables = _bridge_estate()
    hyp = OntologyHypothesis(
        join_keys=(("wrote", "author_id"), ("wrote", "book_id")),
    )
    _, r1 = plan_subset(tables, budget=80, hypothesis=hyp)
    _, r2 = plan_subset(tables, budget=80, hypothesis=hyp)
    assert [t.kept_indices for t in r1.tables] == [t.kept_indices for t in r2.tables]


# --------------------------------------------------------------------------
# Tiny budget — at least one row per key value where feasible
# --------------------------------------------------------------------------


def test_tiny_budget_keeps_one_row_per_key_where_feasible():
    products = {
        "product_id": [1, 2, 3, 4, 5, 6],
        "name": ["a", "b", "c", "d", "e", "f"],
        "price": [10, 20, 30, 40, 50, 9999],
    }
    rng = random.Random(3)
    sales = {
        "sale_id": list(range(1, 61)),
        "product_id": [(i % 6) + 1 for i in range(60)],
        "qty": [rng.randint(1, 5) for _ in range(60)],
    }
    tables = {"products": products, "sales": sales}
    # tiny budget: enough to cover the 6 distinct product keys + some fact rows
    subset, report = plan_subset(tables, budget=12)
    assert report.within_budget
    assert report.joinability_ok(), report.severed_joins()
    # the small dimension is NOT starved below a meaningful share by proportion
    products_plan = next(t for t in report.tables if t.table == "products")
    assert products_plan.kept_rows >= 1
    # the join still has co-kept keys
    o = _real_fk_overlap(report, "sales", "product_id", "products", "product_id")
    assert o.kept_overlap >= 1


def test_extremely_tiny_budget_still_joins():
    tables = _star_estate(n_customers=20, n_orders=100)
    # only 8 rows total across two tables
    _, report = plan_subset(tables, budget=8)
    assert report.within_budget
    assert report.total_kept <= 8
    # at least one matched key kept on both sides so the join is not fully severed
    o = _real_fk_overlap(report, "orders", "customer_id", "customers", "customer_id")
    assert o.kept_overlap >= 1


# --------------------------------------------------------------------------
# Hypothesis bootstrap
# --------------------------------------------------------------------------


def test_hypothesis_does_not_break_budget_or_joins():
    tables = _bridge_estate()
    hyp = OntologyHypothesis(
        join_keys=(
            ("wrote", "author_id"),
            ("wrote", "book_id"),
            ("authors", "author_id"),
            ("books", "book_id"),
        )
    )
    _, report = plan_subset(tables, budget=80, hypothesis=hyp)
    assert report.within_budget
    assert report.joinability_ok(), report.severed_joins()


def test_hypothesis_oversamples_join_key_coverage():
    """Bootstrapping off a hypothesis should not REDUCE join-key coverage vs. none —
    the hypothesized join keys are treated as first-class coverage targets."""
    tables = _bridge_estate()
    hyp = OntologyHypothesis(join_keys=(("wrote", "author_id"), ("authors", "author_id")))
    _, base = plan_subset(tables, budget=50)
    _, boosted = plan_subset(tables, budget=50, hypothesis=hyp)
    base_o = _real_fk_overlap(base, "wrote", "author_id", "authors", "author_id")
    boost_o = _real_fk_overlap(boosted, "wrote", "author_id", "authors", "author_id")
    assert boost_o.kept_overlap >= base_o.kept_overlap


# --------------------------------------------------------------------------
# Report fidelity + edge cases
# --------------------------------------------------------------------------


def test_report_explains_each_table():
    tables = _star_estate()
    _, report = plan_subset(tables, budget=300)
    for tp in report.tables:
        # the report says WHY rows were kept and what key coverage was retained
        assert sum(tp.reasons.values()) >= tp.kept_rows or tp.reasons
        assert isinstance(tp.key_coverage, dict)
        assert tp.budget >= tp.kept_rows
        assert tp.total_rows > 0


def test_kept_indices_are_valid_and_in_order():
    tables = _star_estate()
    _, report = plan_subset(tables, budget=200)
    for tp in report.tables:
        assert list(tp.kept_indices) == sorted(tp.kept_indices)
        assert all(0 <= i < tp.total_rows for i in tp.kept_indices)
        assert len(set(tp.kept_indices)) == len(tp.kept_indices)


def test_zero_budget_keeps_nothing():
    tables = _star_estate()
    subset, report = plan_subset(tables, budget=0)
    assert report.total_kept == 0
    assert all(len(rows) == 0 for rows in subset.values())
    # vacuously joinable (no join can be severed if no full overlap exists in subset)
    assert report.within_budget


def test_single_table_no_joins():
    table = {"x": list(range(100)), "y": [i % 7 for i in range(100)]}
    subset, report = plan_subset({"solo": table}, budget=20)
    assert report.within_budget
    assert report.total_kept <= 20
    assert report.joinability_ok()  # no INDs -> trivially ok
    assert len(report.overlaps) == 0 or all(o.survives for o in report.overlaps)


def test_empty_table_handled():
    tables = {
        "filled": {"id": [1, 2, 3], "v": ["a", "b", "c"]},
        "empty": {"id": [], "v": []},
    }
    subset, report = plan_subset(tables, budget=10)
    assert report.within_budget
    empty_plan = next(t for t in report.tables if t.table == "empty")
    assert empty_plan.kept_rows == 0
    assert subset["empty"] == []


@pytest.mark.parametrize("budget", [4, 16, 64, 256, 1024])
def test_budget_invariant_holds_across_scales(budget):
    tables = _star_estate()
    _, report = plan_subset(tables, budget=budget)
    assert report.total_kept <= budget
    assert report.within_budget


@pytest.mark.parametrize("budget", [50, 120, 400])
def test_joinability_holds_across_scales(budget):
    tables = _star_estate()
    _, report = plan_subset(tables, budget=budget)
    assert report.joinability_ok(), (budget, report.severed_joins())
