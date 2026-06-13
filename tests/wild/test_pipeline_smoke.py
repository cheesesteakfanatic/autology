"""Fast generic-pipeline smoke over a FIXED 12-dataset wild subset.

The subset mixes the corpus's deliberate structure: 4 OpenFlights (the
joinable aviation cluster), 4 datasets-org (the ISO-coded world-data cluster),
2 FiveThirtyEight + 2 seaborn (silos). The full corpus runs in `ontoforge
demo wild`; this asserts the showcase claims cheaply (<120s, zero network):

* >= 6 induced classes;
* the airports<->routes join surface is DISCOVERED (M3 INDs via IATA codes
  and via OpenFlights airport ids — routes is the textbook keyless fact
  table, so the evidence lives in the IND layer);
* >= 1 CROSS-DATASET LINK in the induced ontology itself: the world-data
  cluster's ISO-3 thread (a World-Bank indicator table linking into the
  country-codes table) and/or the aviation cluster's airports->countries
  link, expressed as a link property whose subject and range classes are
  backed by different datasets.
"""

from __future__ import annotations

import shutil

import pytest

from ontoforge.pipeline import build_plans, discover_sources, induce_estate

from conftest import FIXTURES

SMOKE_SLUGS = [
    # openflights — the genuinely joinable cluster
    "of_airports", "of_airlines", "of_routes", "of_countries",
    # datasets-org — the ISO world-data cluster
    "ds_country_codes", "ds_gini_index", "ds_gdp", "ds_population",
    # fivethirtyeight + seaborn — wonderful randoms (silos)
    "fte_bad_drivers", "fte_alcohol_consumption_drinks",
    "sb_iris", "sb_penguins",
]


@pytest.fixture(scope="module")
def artifacts(tmp_path_factory):
    src = tmp_path_factory.mktemp("wild-smoke")
    for slug in SMOKE_SLUGS:
        shutil.copy(FIXTURES / f"{slug}.csv", src / f"{slug}.csv")
    estate = discover_sources(src)
    return induce_estate(estate, None)


def test_at_least_six_classes_induced(artifacts):
    assert len(artifacts.ontology.classes) >= 6


def test_airports_routes_join_surface_discovered(artifacts):
    """airports<->routes via IATA (and via OpenFlights ids): the M3 IND layer
    must surface the cluster's join evidence from the raw snapshot."""
    inds = {
        (i.lhs_table, i.lhs_column, i.rhs_table, i.rhs_column): i.coverage
        for i in artifacts.inds
    }
    assert inds.get(("of_routes", "Source airport", "of_airports", "IATA"), 0) >= 0.95
    assert inds.get(("of_routes", "Destination airport", "of_airports", "IATA"), 0) >= 0.95
    assert inds.get(("of_routes", "Airline ID", "of_airlines", "Airline ID"), 0) >= 0.95


def test_cross_dataset_link_in_induced_ontology(artifacts):
    """>= 1 link property connecting classes backed by DIFFERENT datasets."""
    onto = artifacts.ontology
    tables_of: dict[str, frozenset[str]] = {}
    for plan in build_plans(artifacts.strata, onto):
        backing = {plan.table} if plan.table else {t for t, _ in plan.member_columns}
        tables_of[plan.class_uri] = frozenset(backing)

    cross = []
    for c in onto.iter_classes():
        for p in c.properties:
            if not p.is_link or p.range_class not in onto.classes:
                continue
            subj = tables_of.get(c.uri, frozenset())
            rng = tables_of.get(p.range_class, frozenset())
            if subj and rng and len(subj | rng) >= 2 and subj != rng:
                cross.append((sorted(subj), p.name, sorted(rng)))
    assert cross, "no cross-dataset link property induced"

    # the named showcase threads: the world-data ISO cluster (country-codes /
    # gdp / population / gini-index — country identity threading datasets
    # together) and/or the openflights cluster's internal links
    world = {"ds_country_codes", "ds_gdp", "ds_population", "ds_gini_index"}
    named = [
        (s, p, r) for s, p, r in cross
        if (set(s) | set(r)) & world or sum(t.startswith("of_") for t in (*s, *r)) >= 2
    ]
    assert named, f"cross-dataset links exist but none on the showcase clusters: {cross}"
