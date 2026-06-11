"""Intent-hash URI stability under permuted input order (§3.4.4, §11.2 M4).

Re-induction with shuffled profiles / INDs / candidates must yield the exact
same class URI set AND the same names: concept identity is anchored on intent
hashes, never on discovery order.
"""

from __future__ import annotations

import random

from ontoforge.strata import Strata
from ontoforge.strata.candidates import generate_candidates


def _name_by_hash(onto):
    return {c.intent_hash: c.name for c in onto.classes.values()}


def test_shuffled_inputs_yield_identical_uris_and_names(profiles, inds, induction):
    _, baseline = induction
    base_uris = set(baseline.ontology.classes)
    base_names = _name_by_hash(baseline.ontology)
    cands = generate_candidates(profiles, inds)

    for seed in (3, 17):
        rng = random.Random(seed)
        p = list(profiles)
        i = list(inds)
        c = list(cands)
        rng.shuffle(p)
        rng.shuffle(i)
        rng.shuffle(c)
        result = Strata().induce(p, i, candidates=c)
        assert set(result.ontology.classes) == base_uris, f"URI drift at seed {seed}"
        assert _name_by_hash(result.ontology) == base_names, f"name drift at seed {seed}"


def test_uris_derive_from_intent_hashes(induction):
    _, result = induction
    for uri, c in result.ontology.classes.items():
        assert c.intent_hash
        assert uri == f"onto://class/{c.intent_hash}"
        concept = result.lattice.concepts[c.intent_hash]
        # the hash is a pure function of the concept's full original intent
        from ontoforge.strata import intent_hash_of

        assert intent_hash_of(concept.intent) == c.intent_hash


def test_repeated_emission_is_idempotent(induction):
    strata, result = induction
    again = strata.emit_ontology()
    assert set(again.classes) == set(result.ontology.classes)
    assert {c.name for c in again.classes.values()} == {
        c.name for c in result.ontology.classes.values()
    }
