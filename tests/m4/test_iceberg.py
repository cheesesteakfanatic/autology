"""Iceberg threshold at the ADMISSION level (§3.4.2 item 1 + failure-mode (b)).

No admitted concept may sit below sigma support — except G-join hub bypass
candidates, which skip the iceberg cut but pass through explicit spine review
instead. Exercised end-to-end on a synthetic estate whose shared country
domain is materialized by NO table (so its object concept genuinely sits
below sigma), and on the aviation estate at sigma = 2.
"""

from __future__ import annotations

import pandas as pd
import pytest

from ontoforge.profiling import discover_inds, profile_table
from ontoforge.strata import Strata

#: two composite-key tables sharing a country-code domain that neither
#: materializes: the §3.3 "Airport with no backing table" situation.
ORDERS = {
    "order_ref": ["O1", "O1", "O2", "O2", "O3", "O3", "O4", "O4"],
    "line": ["1", "2", "1", "2", "1", "2", "1", "2"],
    "country": ["DE", "FR", "DE", "US", "FR", "US", "DE", "FR"],
    "qty": ["5", "5", "7", "7", "5", "7", "5", "7"],
}
SHIPMENTS = {
    "ship_ref": ["S1", "S1", "S2", "S2", "S3", "S3"],
    "leg": ["a", "b", "a", "b", "a", "b"],
    "country_code": ["DE", "FR", "US", "DE", "FR", "US"],
    "weight": ["10", "20", "10", "20", "10", "20"],
}


@pytest.fixture(scope="module")
def hub_estate_result():
    tables = {"orders": pd.DataFrame(ORDERS), "shipments": pd.DataFrame(SHIPMENTS)}
    profiles = [profile_table(df, "synth", name) for name, df in tables.items()]
    inds = discover_inds(tables)
    strata = Strata(sigma=2)
    return strata.induce(profiles, inds)


def test_no_admitted_concept_below_sigma_except_bypass(hub_estate_result):
    result = hub_estate_result
    below = [
        ac for ac in result.admission.admitted.values() if ac.concept.support < 2
    ]
    assert below, "the unmaterialized hub domain must be admitted below sigma"
    for ac in below:
        assert ac.concept.bypass is True
        extent_kinds = {
            result.context.candidates[g].kind
            for g in ac.concept.extent
            if g in result.context.candidates
        }
        assert extent_kinds == {"g-join"}  # only hub candidates ride the bypass
    for ac in result.admission.admitted.values():
        assert ac.concept.support >= 2 or ac.concept.bypass


def test_bypassed_hub_received_explicit_spine_review(hub_estate_result):
    result = hub_estate_result
    assert result.hub_reviews["g-join:orders.country"].outcome == "admit"
    names = {c.name for c in result.ontology.classes.values()}
    assert "Country" in names


def test_lattice_itself_respects_sigma(hub_estate_result):
    for c in hub_estate_result.lattice.concepts.values():
        assert c.support >= 2 or c.bypass


def test_aviation_sigma2_respects_threshold(profiles, inds):
    result = Strata(sigma=2).induce(profiles, inds)
    for ac in result.admission.admitted.values():
        assert ac.concept.support >= 2 or ac.concept.bypass
    for c in result.lattice.concepts.values():
        assert c.support >= 2 or c.bypass
