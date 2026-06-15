"""GET /api/review surfaces the GENUINE human-in-the-loop queue.

The flywheel must be visibly alive on a built world: ER/QI decisions that
carry real residual uncertainty are queued, honestly classified by
``review_reason`` — and a clean auto-decision is NEVER queued (no faked doubt).

These cases mirror what the Meridian demo's ER cascade actually lands in the
ledger: deferred pairs, plus low-margin pairs that escalated past the
deterministic Fellegi-Sunter bands into the spine's calibrated tier with an
unresolved conformal set (the supplier-name / facility-code variant warts).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ontoforge.contracts import Atom, DecisionResult, Tier, leaf
from ontoforge.ledger import SqliteLedger
from ontoforge.server.app import (
    REVIEW_CONFIDENCE_FLOOR,
    REVIEW_LOW_MARGIN_CEILING,
    create_app,
)


def _decision(did, outcome, conf, *, tier, cset, deferred=False, quarantined=False):
    return DecisionResult(
        decision_id=did,
        outcome=outcome,
        confidence=conf,
        conformal_set=tuple(cset),
        tier=tier,
        cost_tokens=0,
        deferred_to_human=deferred,
        quarantined=quarantined,
        rationale="synthetic ER decision for the review queue test",
    )


@pytest.fixture()
def review_project(tmp_path) -> Path:
    """A minimal project whose ledger holds ER decisions spanning every queue
    reason plus a clean auto-decision that must NOT appear."""
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "config.json").write_text(
        json.dumps(
            {"estate": "generic", "source_dir": str(tmp_path),
             "ledger": "ledger.sqlite", "hearth_root": "hearth"}
        ),
        encoding="utf-8",
    )
    ledger = SqliteLedger(str(proj / "ledger.sqlite"))
    atom = Atom(uri="atom://test/er/seed", value="seed")
    ledger.register_atoms([atom])
    prov = (atom.atom_id,)

    # 1) deferred (tiers exhausted) -> reason 'deferred'
    ledger.append_decision(
        _decision("er:operator:A||B", "no", 0.55, tier=Tier.HUMAN,
                  cset=("no", "yes"), deferred=True),
        prov,
    )
    # 2) low-confidence auto-decision below the floor -> reason 'low-confidence'
    ledger.append_decision(
        _decision("er:operator:C||D", "yes", REVIEW_CONFIDENCE_FLOOR - 0.05,
                  tier=Tier.T1, cset=("no", "yes")),
        prov,
    )
    # 3) genuine low-margin: escalated to T1, unresolved conformal set, below
    #    the ceiling but above the floor -> reason 'low-margin'
    ledger.append_decision(
        _decision("er:operator:E||F", "no", 0.80, tier=Tier.T1,
                  cset=("no", "yes")),
        prov,
    )
    ledger.append_decision(
        _decision("er:operator:G||H", "yes", 0.85, tier=Tier.T2,
                  cset=("no", "yes")),
        prov,
    )
    # 4) CLEAN auto-decision: high confidence, escalated but conformal SINGLETON
    #    -> must NOT be queued (no faked uncertainty)
    ledger.append_decision(
        _decision("er:operator:I||J", "yes", 0.99, tier=Tier.T1, cset=("yes",)),
        prov,
    )
    # 5) clean deterministic T0 auto-decision well above the ceiling -> not queued
    ledger.append_decision(
        _decision("er:operator:K||L", "yes", REVIEW_LOW_MARGIN_CEILING + 0.02,
                  tier=Tier.T0, cset=("no", "yes")),
        prov,
    )
    ledger.close()
    return proj


def test_review_returns_at_least_three_genuine_items(review_project):
    with TestClient(create_app(review_project)) as c:
        data = c.get("/api/review").json()

    items = data["items"]
    assert len(items) >= 3, "the human-in-the-loop queue is visibly alive"
    reasons = {it["decision_id"]: it["review_reason"] for it in items}
    assert reasons["er:operator:A||B"] == "deferred"
    assert reasons["er:operator:C||D"] == "low-confidence"
    assert reasons["er:operator:E||F"] == "low-margin"
    assert reasons["er:operator:G||H"] == "low-margin"

    # every surfaced item carries an honest, non-empty reason
    assert all(it["review_reason"] for it in items)


def test_clean_auto_decisions_are_never_queued(review_project):
    with TestClient(create_app(review_project)) as c:
        items = c.get("/api/review").json()["items"]
    queued = {it["decision_id"] for it in items}
    # conformal singleton and clean-deterministic high-confidence: not faked
    assert "er:operator:I||J" not in queued
    assert "er:operator:K||L" not in queued


def test_low_margin_items_keep_their_real_decision_fields(review_project):
    with TestClient(create_app(review_project)) as c:
        items = c.get("/api/review").json()["items"]
    lm = next(it for it in items if it["decision_id"] == "er:operator:E||F")
    assert lm["kind"] == "er"
    assert lm["outcome"] in lm["conformal_set"]
    assert lm["tier"] >= 1
    assert len(lm["conformal_set"]) > 1
    assert REVIEW_CONFIDENCE_FLOOR <= lm["confidence"] < REVIEW_LOW_MARGIN_CEILING


def test_accepting_a_low_margin_item_removes_it_from_the_queue(review_project):
    with TestClient(create_app(review_project)) as c:
        out = c.post(
            "/api/review/er:operator:E||F",
            json={"verdict": "accept", "note": "confirmed by reviewer"},
        )
        assert out.status_code == 200
        assert out.json()["verdict"] == "accept"
        remaining = {it["decision_id"] for it in c.get("/api/review").json()["items"]}
    assert "er:operator:E||F" not in remaining
