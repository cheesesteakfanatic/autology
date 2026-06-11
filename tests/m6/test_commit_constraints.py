"""Commit-path constraint enforcement (§1.3 constraint H, §4.3, rank reservation).

A rejected commit must be ATOMIC: nothing written, indexes untouched.
"""

from __future__ import annotations

import pytest

from m6_helpers import lc, mint_prov, vc

from ontoforge.contracts import ZERO, Interval, Layer, ValueCell
from ontoforge.hearth import CommitRejected, canonical_state
from ontoforge.ledger import SqliteLedger

CLASS = "onto://test/Thing"


def test_rejects_empty_prov_ref(hearth, ledger) -> None:
    cell = vc("e://1", "p", 1, prov="")
    with pytest.raises(CommitRejected, match="constraint H"):
        hearth.commit(Layer.ENTITY, CLASS, [cell], now=1000)
    assert hearth.read("e://1") == {}


def test_rejects_unknown_prov_ref(hearth) -> None:
    cell = vc("e://1", "p", 1, prov="deadbeefdeadbeef")
    with pytest.raises(CommitRejected, match="constraint H"):
        hearth.commit(Layer.ENTITY, CLASS, [cell], now=1000)


def test_rejects_zero_provenance_term(hearth, ledger) -> None:
    """Interned ZERO resolves but is NOT a derivation — constraint H refuses it."""
    zero_ref = ledger.intern(ZERO)
    cell = vc("e://1", "p", 1, prov=zero_ref)
    with pytest.raises(CommitRejected, match="ZERO|constraint H"):
        hearth.commit(Layer.ENTITY, CLASS, [cell], now=1000)


def test_rejects_closed_system_interval(hearth, ledger) -> None:
    prov = mint_prov(ledger, "closed-sys")
    cell = ValueCell("e://1", "p", 1, valid=Interval(0), system=Interval(0, 10), prov_ref=prov)
    with pytest.raises(CommitRejected, match="OPEN system interval"):
        hearth.commit(Layer.ENTITY, CLASS, [cell], now=1000)


def test_rank_zero_reserved_for_actions(hearth, ledger) -> None:
    prov = mint_prov(ledger, "rank0")
    cell = vc("e://1", "p", 1, prov=prov, rank=0)
    with pytest.raises(CommitRejected, match="reserved for human Actions"):
        hearth.commit(Layer.ENTITY, CLASS, [cell], now=1000)


def test_rejects_negative_rank_and_bad_confidence(hearth, ledger) -> None:
    prov = mint_prov(ledger, "bad")
    with pytest.raises(CommitRejected, match="src_rank"):
        hearth.commit(Layer.ENTITY, CLASS, [vc("e://1", "p", 1, prov=prov, rank=-1)], now=1000)
    with pytest.raises(CommitRejected, match="confidence"):
        hearth.commit(Layer.ENTITY, CLASS, [vc("e://1", "p", 1, prov=prov, conf=1.5)], now=1000)


def test_rejects_non_json_value(hearth, ledger) -> None:
    prov = mint_prov(ledger, "nonjson")
    with pytest.raises(CommitRejected, match="JSON"):
        hearth.commit(Layer.ENTITY, CLASS, [vc("e://1", "p", object(), prov=prov)], now=1000)
    with pytest.raises(CommitRejected, match="JSON"):  # NaN is not canonical JSON
        hearth.commit(Layer.ENTITY, CLASS, [vc("e://1", "p", float("nan"), prov=prov)], now=1000)


def test_rejects_non_monotone_system_time(hearth, ledger) -> None:
    prov = mint_prov(ledger, "mono")
    hearth.commit(Layer.ENTITY, CLASS, [vc("e://1", "p", 1, prov=prov)], now=2000)
    with pytest.raises(CommitRejected, match="monotone"):
        hearth.commit(Layer.ENTITY, CLASS, [vc("e://1", "p", 2, prov=prov)], now=1000)
    assert hearth.read("e://1") == {"p": 1}


def test_batch_is_all_or_nothing(hearth, ledger) -> None:
    """One bad cell poisons the whole batch — validated BEFORE any apply."""
    prov = mint_prov(ledger, "batch")
    good = vc("e://1", "a", 1, prov=prov)
    bad = vc("e://1", "b", 2, prov="")
    before = canonical_state(hearth)
    with pytest.raises(CommitRejected):
        hearth.commit(Layer.ENTITY, CLASS, [good, bad], now=1000)
    assert canonical_state(hearth) == before
    assert hearth.read("e://1") == {}


def test_link_constraint_h_and_predicate_mismatch(hearth, ledger) -> None:
    with pytest.raises(CommitRejected, match="constraint H"):
        hearth.commit_links(CLASS, "knows", [lc("e://1", "knows", "e://2", prov="")], now=1000)
    prov = mint_prov(ledger, "link")
    with pytest.raises(CommitRejected, match="does not match shard predicate"):
        hearth.commit_links(CLASS, "owns", [lc("e://1", "knows", "e://2", prov=prov)], now=1000)


def test_layers_are_separate_shards(tmp_path) -> None:
    """RAW / CONFORMED / ENTITY are distinct shard namespaces (§4.2)."""
    from ontoforge.hearth import Hearth

    ledger = SqliteLedger()
    h = Hearth(tmp_path / "h", ledger)
    prov = mint_prov(ledger, "layers")
    h.commit(Layer.RAW, "raw.faa_master", [vc("row://1", "N_NUMBER", "N123", prov)], now=1000)
    h.commit(Layer.ENTITY, CLASS, [vc("e://1", "tail", "N123", prov)], now=2000)
    assert h.read("row://1") == {}  # ENTITY-layer read does not see RAW
    assert h.read("row://1", class_uri="raw.faa_master", layer=Layer.RAW) == {"N_NUMBER": "N123"}
    assert h.classes(Layer.RAW) == ["raw.faa_master"]
    assert h.classes(Layer.ENTITY) == [CLASS]
