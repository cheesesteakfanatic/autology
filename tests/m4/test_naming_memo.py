"""Naming memoization keyed on intent hash (§3.4 failure-mode (c)).

Names are assigned by the T2 task ``strata.name_concept`` ONCE per intent
hash and memoized through the ledger artifact table (kind
``strata.name_memo``): re-induction — even by a fresh Strata instance sharing
only the ledger — reuses the recorded names instead of re-deriving them.
"""

from __future__ import annotations

from ontoforge.ledger import SqliteLedger
from ontoforge.strata import Strata
from ontoforge.strata.admission import NameMemo


def _names(result) -> dict[str, str]:
    return {c.intent_hash: c.name for c in result.ontology.classes.values()}


def test_reinduction_reuses_memoized_names(profiles, inds):
    ledger = SqliteLedger()
    first = Strata(ledger=ledger).induce(profiles, inds)
    names1 = _names(first)
    assert names1

    rows = ledger.connection.execute(
        "SELECT artifact_id FROM artifact WHERE kind = 'strata.name_memo'"
    ).fetchall()
    memo_ids = {r[0] for r in rows}
    assert memo_ids == {f"strata:name:{ih}" for ih in names1}

    # a FRESH instance sharing only the ledger reuses every recorded name
    second = Strata(ledger=ledger).induce(profiles, inds)
    assert _names(second) == names1
    n_after = ledger.connection.execute(
        "SELECT COUNT(*) FROM artifact WHERE kind = 'strata.name_memo'"
    ).fetchone()[0]
    assert n_after == len(rows), "second induction must not re-derive any name"


def test_memo_overrides_the_naming_handler(profiles, inds):
    """The memo is authoritative: a recorded name survives re-induction even
    when the heuristic namer would say otherwise (renames only via TEMPER)."""
    baseline = Strata().induce(profiles, inds)
    target = sorted(_names(baseline))[0]

    strata = Strata()
    strata.memo.put(target, "FrozenLegacyName", "memoized definition", [])
    result = strata.induce(profiles, inds)
    by_hash = {c.intent_hash: c for c in result.ontology.classes.values()}
    assert by_hash[target].name == "FrozenLegacyName"
    assert by_hash[target].definition == "memoized definition"


def test_memo_roundtrip_through_ledger():
    ledger = SqliteLedger()
    atom_ids = []
    memo = NameMemo(ledger)
    assert memo.get("deadbeef00000000") is None
    # without provenance atoms the write stays in-memory only
    memo.put("deadbeef00000000", "InMemOnly", "d", atom_ids)
    assert memo.get("deadbeef00000000") == ("InMemOnly", "d")
    fresh = NameMemo(ledger)
    assert fresh.get("deadbeef00000000") is None
