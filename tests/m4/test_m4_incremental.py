"""AddIntent-style incremental maintenance (§3.4.2 item 4, §11.2 M4).

- inserting candidates one-by-one (in shuffled order) yields EXACTLY the
  admitted concept set and class URIs of all-at-once induction;
- the per-insert touched set is the order filter of the inserted object
  (every pre-existing concept whose intent the new object carries — and
  nothing else — grows its extent);
- flipped admissions surface as ChangeProposals, never silently: every diff
  between consecutive admission states is covered by a proposal, and with a
  ledger wired the proposals are recorded as artifacts.
"""

from __future__ import annotations

import random

import pytest

from ontoforge.ledger import SqliteLedger
from ontoforge.strata import Strata
from ontoforge.strata.candidates import generate_candidates


def test_incremental_equals_batch(profiles, inds, induction):
    _, batch = induction
    batch_admitted = set(batch.admission.admitted)
    batch_uris = set(batch.ontology.classes)

    cands = generate_candidates(profiles, inds)
    rng = random.Random(11)
    rng.shuffle(cands)

    strata = Strata()
    strata.induce(profiles, inds, candidates=cands[:1])
    for cand in cands[1:]:
        strata.insert_delta([cand])

    assert set(strata.admission.admitted) == batch_admitted
    assert set(strata.emit_ontology().classes) == batch_uris
    assert len(strata.lattice.concepts) == len(batch.lattice.concepts)


def test_insert_touches_exactly_the_order_filter(profiles, inds):
    cands = generate_candidates(profiles, inds)
    strata = Strata()
    strata.induce(profiles, inds, candidates=cands[:-1])
    before = {
        ih: (set(c.extent), c.intent)
        for ih, c in strata.lattice.concepts.items()
    }

    from ontoforge.strata.context import candidate_attributes
    from ontoforge.strata.incremental import insert_object

    cand = cands[-1]
    by_table = {tp.table: tp for tp in profiles}
    attrs = candidate_attributes(cand, strata.context.clusters, by_table)
    strata.context.add_object(cand.cid, attrs, cand)
    touched, created = insert_object(
        strata.lattice, strata.context, cand.cid, bypass=cand.bypass_sigma
    )

    expected_touched = {ih for ih, (_, intent) in before.items() if intent <= attrs}
    assert touched == expected_touched
    for ih, (extent, intent) in before.items():
        now = strata.lattice.concepts[ih]
        assert now.intent == intent, "pre-existing intents (and URIs) must never change"
        if ih in touched:
            assert now.extent == extent | {cand.cid}
        else:
            assert now.extent == extent
    for ih in created:
        assert ih not in before


def test_flips_become_change_proposals_never_silent(profiles, inds):
    ledger = SqliteLedger()
    cands = generate_candidates(profiles, inds)
    strata = Strata(ledger=ledger)
    strata.induce(profiles, inds, candidates=cands[:2])

    seen_states = [
        {ih: strata.admission.outcome_of(ih) for ih in strata.lattice.concepts}
    ]
    all_proposals = []
    for cand in cands[2:]:
        prev = seen_states[-1]
        proposals, _ = strata.insert_delta([cand])
        all_proposals.extend(proposals)
        cur = {ih: strata.admission.outcome_of(ih) for ih in strata.lattice.concepts}
        seen_states.append(cur)
        # every outcome flip between the states is covered by a proposal
        proposed = {p.intent_hash: (p.previous or "unknown", p.proposed or "unknown") for p in proposals}
        for ih in set(prev) | set(cur):
            p_out = prev.get(ih, "unknown")
            c_out = cur.get(ih, "unknown")
            if p_out != c_out:
                assert ih in proposed, f"silent admission flip on {ih}: {p_out} -> {c_out}"
                assert proposed[ih] == (p_out, c_out)

    assert all_proposals, "growing the lattice candidate-by-candidate must flip something"
    kinds = {p.kind for p in all_proposals}
    assert kinds <= {"add-class", "retract-class", "merge-class", "discard-class"}
    n_rows = ledger.connection.execute(
        "SELECT COUNT(*) FROM artifact WHERE kind = 'strata.change_proposal'"
    ).fetchone()[0]
    assert n_rows > 0, "proposals must be recorded in the ledger"


def test_insert_delta_requires_induce_first(profiles, inds):
    strata = Strata()
    with pytest.raises(RuntimeError):
        strata.insert_delta([])


def test_duplicate_candidate_rejected(profiles, inds):
    cands = generate_candidates(profiles, inds)
    strata = Strata()
    strata.induce(profiles, inds, candidates=cands)
    with pytest.raises(ValueError):
        strata.insert_delta([cands[0]])
