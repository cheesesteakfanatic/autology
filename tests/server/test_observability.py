"""OBSERVATORY endpoints — the observability surface (R0 P1).

Four read-only views that SURFACE the existing append-only substrate
(atoms/decisions/artifacts/cost) over the real built aviation world from
``conftest.py``; nothing here recomputes. The tests assert each endpoint
returns real data, that value-LEVEL lineage resolves a known HEARTH cell all
the way to its RAW source row+column, that the compute ledger reconciles with
the COST table (they can never diverge — ``LedgerCostMeter`` writes both), and
that the append-only audit log + run history populate once the engine has
adjudicated. Zero network: the TestClient drives the ASGI app in-process.
"""

from __future__ import annotations

from urllib.parse import quote

import pytest

from ontoforge.contracts import Layer


# ----------------------------------------------------------------- helpers


@pytest.fixture(scope="module")
def known_cell(client, world) -> tuple[str, str, str]:
    """(entity_uri, prop, prov_ref) for a real current ENTITY cell.

    Mirrors the proven ``aircraft_uri`` discovery in test_entities.py: take
    ``client`` FIRST so the ledger sqlite handle is opened on the app's event-
    loop thread, then read the HEARTH shards (shard iteration is disk-side, not
    a sqlite query). The cell + its interned prov_ref are what the lineage trail
    resolves back to RAW source rows."""
    client.get("/api/status")  # open the world on the app thread
    with world.lock:
        for s in world.hearth.value_shard_items():
            if s.layer is not Layer.ENTITY:
                continue
            for entity, seqs in sorted(s.by_entity.items()):
                for seq in seqs:
                    cell = s.cells[seq]
                    if cell.prov_ref:
                        return entity, cell.prop, cell.prov_ref
    pytest.skip("no entity cells in the built world")


@pytest.fixture(scope="module")
def asked(client):
    """Drive a few asks so the spine records QI decisions + question artifacts
    + per-task cost — the substrate the audit/runs/compute views surface. Module
    scope so it happens once; the ledger is append-only so this only adds."""
    for q in (
        "how many aircraft are there?",
        "what is the average number of seats?",
        "list the accident events",
    ):
        client.post("/api/ask", json={"question": q})
    return client


# ----------------------------------------------------------------- lineage
# The differentiator: an answer value traced to the RAW source row + column.


def test_lineage_by_cell_resolves_to_raw_source_rows(client, known_cell):
    entity, prop, _ref = known_cell
    r = client.get(f"/api/lineage?cell={quote(entity, safe='')}&prop={quote(prop)}")
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["cell"] == entity and out["prop"] == prop
    assert out["prov_ref"], "the cell carries an interned provenance ref"
    assert out["n_atoms"] >= 1 and len(out["atoms"]) == out["n_atoms"]
    # value-LEVEL lineage: every leaf is a RAW source record, and at least one
    # parses to source/table/row/column — the trail incumbents cannot show.
    parsed = [a for a in out["atoms"] if a["source"] and a["table"] and a["column"]]
    assert parsed, f"no atom parsed to a raw row+column: {out['atoms']}"
    leaf = parsed[0]
    assert leaf["uri"].startswith("atom://"), leaf["uri"]
    assert leaf["atom_id"]
    assert out["sources"], "the distinct source systems are surfaced"
    assert leaf["source"] in out["sources"]
    # the resolved polynomial tree is the same shape /api/provenance returns
    assert out["resolved"]["kind"] in {"atom", "sum", "product", "one", "zero"}


def test_lineage_by_atom_matches_a_real_atom(client, known_cell):
    _entity, _prop, ref = known_cell
    # /api/lineage by prov_ref gives us a real backing atom id (HTTP only)
    seed = client.get(f"/api/lineage?prov_ref={ref}").json()
    atom_id = seed["atoms"][0]["atom_id"]
    out = client.get(f"/api/lineage?atom={atom_id}").json()
    ids = {a["atom_id"] for a in out["atoms"]}
    assert atom_id in ids
    assert client.get(f"/api/provenance/{out['prov_ref']}").status_code == 200


def test_lineage_by_prov_ref_round_trips(client, known_cell):
    _entity, _prop, ref = known_cell
    out = client.get(f"/api/lineage?prov_ref={ref}").json()
    assert out["prov_ref"] == ref and out["n_atoms"] >= 1


def test_lineage_error_paths_are_clean(client):
    assert client.get("/api/lineage").status_code == 422              # no selector
    assert client.get("/api/lineage?cell=ent://x").status_code == 422  # cell needs prop
    assert client.get("/api/lineage?atom=does-not-exist").status_code == 404
    assert client.get("/api/lineage?cell=ent://nope&prop=foo").status_code == 404


# ------------------------------------------------------------------- audit


def test_audit_is_the_append_only_decision_log(asked):
    out = asked.get("/api/audit").json()
    assert out["total"] >= 1
    assert out["entries"], "real entries over the adjudicated world"
    cats = {e["category"] for e in out["entries"]}
    assert "decision" in cats, "spine decisions are surfaced"
    # decisions carry tier + outcome; the by_* roll-ups are consistent
    dec = next(e for e in out["entries"] if e["category"] == "decision")
    assert dec["tier"] is not None and dec["outcome"]
    assert dec["kind"] in out["by_kind"]
    assert str(dec["tier"]) in out["by_tier"]
    assert out["by_category"]["decision"] >= 1
    # newest-first, append-only ordering by seq
    seqs = [e["seq"] for e in out["entries"]]
    assert seqs == sorted(seqs, reverse=True)


def test_audit_surfaces_question_commits(asked):
    out = asked.get("/api/audit").json()
    kinds = {e["kind"] for e in out["entries"]}
    assert "question" in kinds, "recorded asks appear as append-only commits"


def test_audit_respects_limit(asked):
    out = asked.get("/api/audit?limit=2").json()
    assert len(out["entries"]) <= 2


# -------------------------------------------------------------------- runs


def test_runs_history_lists_stages_and_lanes(asked):
    out = asked.get("/api/runs").json()
    # the pipeline stages this estate cleared (from state.json)
    assert "materialize" in out["stages"] and "ingest" in out["stages"]
    assert out["total_decisions"] >= 1
    lanes = {r["kind"] for r in out["runs"]}
    assert "decision" in lanes, "the spine's adjudication lane is present"
    dec_lane = next(r for r in out["runs"] if r["kind"] == "decision")
    assert dec_lane["decisions"] == out["total_decisions"]


# --------------------------------------------------------- compute-ledger


def test_compute_ledger_reconciles_with_the_cost_table(asked, ledger_db):
    out = asked.get("/api/compute-ledger").json()
    status = asked.get("/api/status").json()
    # the headline reconciliation: compute-ledger total == the COST table sum
    # == /api/status.cost_tokens. Equal even at zero (deterministic tiers are
    # free) — the point is they NEVER diverge.
    (cost_sum,) = ledger_db.execute("SELECT COALESCE(SUM(tokens), 0) FROM cost").fetchone()
    assert out["total_tokens"] == cost_sum == status["cost_tokens"]
    # by_task tokens sum back to the total
    assert sum(r["tokens"] for r in out["by_task"]) == out["total_tokens"]


def test_compute_ledger_rolls_up_by_task_and_tier(asked, ledger_db):
    out = asked.get("/api/compute-ledger").json()
    assert out["estate"] == "aviation"
    # metered calls == the COUNT(*) of the COST table
    (n_cost,) = ledger_db.execute("SELECT COUNT(*) FROM cost").fetchone()
    assert out["total_calls"] == n_cost
    # the by-tier roll-up reconciles with the decision table's cost column
    (dec_tokens,) = ledger_db.execute(
        "SELECT COALESCE(SUM(cost_tokens), 0) FROM decision"
    ).fetchone()
    assert out["decision_tokens"] == dec_tokens
    assert sum(r["tokens"] for r in out["by_tier"]) == dec_tokens
    # at least one task lane exists once the spine has decided
    assert out["by_task"], "the spine's metered tasks are rolled up"
    assert any(r["calls"] >= 1 for r in out["by_task"])


# ---------------------------------------------------- contracts don't break


def test_existing_status_contract_is_unchanged(client):
    """The additive endpoints must not perturb the existing /api/status shape."""
    s = client.get("/api/status").json()
    for key in ("project", "estate", "atoms", "decisions_by_tier", "cost_tokens"):
        assert key in s
