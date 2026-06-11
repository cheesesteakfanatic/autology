"""Wave 2 cross-module seam test (whitepaper §11.3 critical path, §18.1 gate).

One pass over the REAL aviation hero estate exercising every Wave-2 boundary
with real implementations on both sides:

    estates -> profiling (all 5 tables, FDs + INDs)            (estate x M3)
    profiles + INDs -> STRATA.induce -> contracts.Ontology     (M3 x M4)
    induced ontology vs gold harness (tests/m4 comparator)     (M4 gold gates)
    estate -> ER cascade -> clusters, held-out F1 gate         (M5 gold gates)
    ER aircraft clusters -> HEARTH canonical entities, with    (M5 x M6 x M0)
      prov_refs interned over atoms registered from the actual
      source cells in one real SqliteLedger; bitemporal reads,
      a link traversal, and constraint H verified end to end.

No fakes, no network: the spine tiers behind STRATA and ER are the
deterministic HeuristicAdapter handlers, and every prov_ref committed to
HEARTH resolves in the shared ledger to a term whose leaves are atoms minted
from real fixture cells.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "m4"))
from m4_helpers import compare_to_gold  # noqa: E402  (M4's own gold harness)

from ontoforge.contracts import FOREVER, Interval, Layer, LinkCell, Stance, ValueCell, leaf, make_cell_atom
from ontoforge.er import ERCascade, extract_mentions, load_gold, pairwise_prf
from ontoforge.estates import load_estate, load_gold_ontology
from ontoforge.hearth import Hearth
from ontoforge.ledger import SqliteLedger
from ontoforge.profiling import discover_inds, profile_table
from ontoforge.strata import Strata

ER_KINDS = ("aircraft", "operator")
CLASS_PRECISION_GATE = 0.70   # same gates as tests/m4/test_gold_harness.py
CLASS_RECALL_GATE = 0.60
ER_F1_GATE = 0.85             # same gate as tests/m5/test_cascade.py
MIN_ADMITTED_CLASSES = 8
N_HEARTH_CLUSTERS = 40        # subsample committed to HEARTH (gates above run on the full estate)

_EPOCH_ORDINAL = date(1970, 1, 1).toordinal()
_US_PER_DAY = 86_400 * 1_000_000


def ordinal_to_instant(ordinal: int) -> int:
    """Proleptic-Gregorian day ordinal (er.records.parse_date_ordinal) -> µs instant."""
    return (ordinal - _EPOCH_ORDINAL) * _US_PER_DAY


# --------------------------------------------------------------------------
# shared fixtures: one estate, one ledger, one induction, one ER run
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def estate():
    return load_estate()


@pytest.fixture(scope="module")
def profiles(estate):
    return [
        profile_table(df, estate["metadata"]["tables"][name]["source_id"], name)
        for name, df in estate["tables"].items()
    ]


@pytest.fixture(scope="module")
def inds(estate):
    return discover_inds(estate["tables"])


@pytest.fixture(scope="module")
def induction(profiles, inds):
    return Strata().induce(profiles, inds)


@pytest.fixture(scope="module")
def gold_comparison(induction, profiles):
    return compare_to_gold(induction, load_gold_ontology(), profiles)


@pytest.fixture(scope="module")
def er_gold():
    return load_gold()


@pytest.fixture(scope="module")
def er_run(estate, er_gold):
    """Full-estate batch resolution; calibration sees the TRAIN split only."""
    mentions = extract_mentions(estate)
    train = {k: er_gold.split_labels(k, "train") for k in ER_KINDS}
    cascade = ERCascade()
    result = cascade.run(mentions, train)
    return mentions, result


@pytest.fixture(scope="module")
def ledger():
    led = SqliteLedger(":memory:")
    yield led
    led.close()


# --------------------------------------------------------------------------
# (a) profiling the whole estate: 5 tables, FDs, INDs
# --------------------------------------------------------------------------


def test_all_five_tables_profile_with_dependencies(estate, profiles, inds):
    assert len(profiles) == 5
    assert {tp.table for tp in profiles} == set(estate["tables"])
    for tp in profiles:
        assert tp.row_count == len(estate["tables"][tp.table])
        assert tp.columns
        assert tp.candidate_keys, f"{tp.table}: no candidate keys discovered"
    by_table = {tp.table: tp for tp in profiles}
    assert by_table["faa_master"].fds, "no FDs discovered on faa_master"
    assert inds, "no inclusion dependencies discovered across the estate"
    # the estate's hero join paths must surface as INDs (tail/model references)
    tables_in_inds = {t for ind in inds for t in (ind.lhs_table, ind.rhs_table)}
    assert "faa_master" in tables_in_inds


# --------------------------------------------------------------------------
# (b) STRATA induction on the live profiles, scored by M4's gold harness
# --------------------------------------------------------------------------


def test_strata_admits_enough_classes(induction):
    n = len(induction.ontology.classes)
    assert n >= MIN_ADMITTED_CLASSES, f"only {n} classes admitted"


def test_strata_meets_gold_class_gates(gold_comparison):
    cmpr = gold_comparison
    print("\n" + cmpr.report)
    assert cmpr.precision >= CLASS_PRECISION_GATE, (
        f"class precision {cmpr.precision:.3f} < {CLASS_PRECISION_GATE}\n{cmpr.report}"
    )
    assert cmpr.recall >= CLASS_RECALL_GATE, (
        f"class recall {cmpr.recall:.3f} < {CLASS_RECALL_GATE}\n{cmpr.report}"
    )


def test_strata_recovers_aircraft_class(gold_comparison):
    """The HEARTH leg below stores entities under the induced Aircraft-like
    class, so its existence is itself a seam requirement."""
    assert "Aircraft" in gold_comparison.gold_matches, gold_comparison.report


# --------------------------------------------------------------------------
# (c) ER cascade over the estate: held-out F1 hard gate
# --------------------------------------------------------------------------


def test_er_holdout_f1_gate(er_run, er_gold):
    _, result = er_run
    combined: dict[str, str] = {}
    for kind in ER_KINDS:
        labels = er_gold.split_labels(kind, "test")
        assert labels, f"empty held-out split for {kind}"
        prf = pairwise_prf(result.mention_to_uri, labels)
        assert prf["f1"] >= ER_F1_GATE, f"{kind} held-out PRF: {prf}"
        combined.update(labels)
    prf = pairwise_prf(result.mention_to_uri, combined)
    print(f"\ncombined held-out ER PRF: {prf}")
    assert prf["f1"] >= ER_F1_GATE, f"combined held-out PRF: {prf}"


def test_er_produces_aircraft_clusters_with_registry_anchors(er_run):
    mentions, result = er_run
    by_id = {m.mention_id: m for m in mentions}
    registry_backed = [
        c
        for c in result.clusters["aircraft"].values()
        if any(by_id[mid].table == "faa_master" for mid in c.mention_ids)
    ]
    assert len(registry_backed) >= N_HEARTH_CLUSTERS


# --------------------------------------------------------------------------
# (d) HEARTH: canonical aircraft entities from the ER clusters, with REAL
#     provenance (atoms registered from the source cells of faa_master)
# --------------------------------------------------------------------------

# induced-Aircraft property <- faa_master source column
AIRCRAFT_PROPS = {
    "tail": "N-NUMBER",
    "serial": "SERIAL NUMBER",
    "model": "MFR MDL CODE",
}
REGISTRANT_PROP = "registrant"
REGISTRANT_COL = "REGISTRANT NAME"
LINK_PRED = "has_model"


@pytest.fixture(scope="module")
def hearth_world(tmp_path_factory, estate, er_run, induction, gold_comparison, ledger):
    """Commit the subsampled ER aircraft clusters into a real Hearth.

    Every committed cell's prov_ref is an interned Leaf over an atom minted
    with make_cell_atom from the ACTUAL faa_master source cell (same
    source_id / table / row_key / column / raw value the CDC path would
    register), in the shared SqliteLedger.
    """
    mentions, result = er_run
    by_id = {m.mention_id: m for m in mentions}

    # the induced Aircraft-like class URI, via M4's own gold matching
    induced_name = gold_comparison.gold_matches["Aircraft"][1]
    class_uri = next(
        uri for uri, c in induction.ontology.classes.items() if c.name == induced_name
    )

    # raw faa_master rows keyed exactly as er.records keys them
    raw_rows = {
        f"{str(r['N-NUMBER']).strip()}|{str(r['SERIAL NUMBER']).strip()}": r
        for r in estate["tables"]["faa_master"].to_dict("records")
    }
    faa_source_id = estate["metadata"]["tables"]["faa_master"]["source_id"]

    hearth = Hearth(tmp_path_factory.mktemp("hearth") / "store", ledger)

    selected: list[tuple[str, object]] = []  # (cluster_uri, registry mention)
    for uri in sorted(result.clusters["aircraft"]):
        cluster = result.clusters["aircraft"][uri]
        reg = sorted(
            mid for mid in cluster.mention_ids if by_id[mid].table == "faa_master"
        )
        if reg:
            selected.append((uri, by_id[reg[0]]))
        if len(selected) >= N_HEARTH_CLUSTERS:
            break
    assert len(selected) == N_HEARTH_CLUSTERS

    cells: list[ValueCell] = []
    entity_meta: dict[str, dict] = {}
    for uri, m in selected:
        raw = raw_rows[m.row_key]
        meta: dict = {"mention": m, "props": {}, "prov": {}}
        cols = dict(AIRCRAFT_PROPS)
        cols[REGISTRANT_PROP] = REGISTRANT_COL
        for prop, col in cols.items():
            value = str(raw[col]).strip()
            atom = make_cell_atom(faa_source_id, m.table, m.row_key, col, raw[col])
            ledger.register_atoms([atom])
            prov = ledger.intern(leaf(atom.atom_id))
            if prop == REGISTRANT_PROP:
                lo, hi = m.fields.get("date_lo"), m.fields.get("date_hi")
                valid = (
                    Interval(ordinal_to_instant(lo), ordinal_to_instant(hi))
                    if lo is not None and hi is not None and lo < hi
                    else Interval(0)
                )
            else:
                valid = Interval(0)  # open: identity facts are current
            cells.append(
                ValueCell(
                    entity_uri=uri,
                    prop=prop,
                    value=value,
                    valid=valid,
                    system=Interval(0),  # store-stamped on commit
                    prov_ref=prov,
                    confidence=1.0,
                    src_rank=1,
                )
            )
            meta["props"][prop] = value
            meta["prov"][prop] = prov
            if prop == REGISTRANT_PROP:
                meta["registrant_valid"] = valid
        entity_meta[uri] = meta

    n = hearth.commit(Layer.ENTITY, class_uri, cells)
    assert n == len(cells)

    # the "known aircraft": first selected cluster with a real closed
    # registration window and a non-empty registrant
    known_uri = next(
        uri
        for uri, _ in selected
        if entity_meta[uri]["registrant_valid"].end < FOREVER
        and entity_meta[uri]["props"][REGISTRANT_PROP]
    )

    # one link: aircraft -> its model designator, prov = the model source cell
    model_obj = f"model://{entity_meta[known_uri]['props']['model']}"
    link = LinkCell(
        subject_uri=known_uri,
        predicate=LINK_PRED,
        object_uri=model_obj,
        valid=Interval(0),
        system=Interval(0),
        prov_ref=entity_meta[known_uri]["prov"]["model"],
    )
    assert hearth.commit_links(class_uri, LINK_PRED, [link]) == 1

    return {
        "hearth": hearth,
        "class_uri": class_uri,
        "entity_meta": entity_meta,
        "known_uri": known_uri,
        "model_obj": model_obj,
        "n_cells": len(cells),
    }


def test_hearth_current_read_of_known_aircraft(hearth_world):
    w = hearth_world
    meta = w["entity_meta"][w["known_uri"]]
    got = w["hearth"].read(w["known_uri"])  # current stance
    for prop in AIRCRAFT_PROPS:
        assert got[prop] == meta["props"][prop], f"{prop}: {got!r}"


def test_hearth_as_of_read_sees_registration_window(hearth_world):
    w = hearth_world
    meta = w["entity_meta"][w["known_uri"]]
    valid: Interval = meta["registrant_valid"]
    mid = (valid.start + valid.end) // 2

    as_of = w["hearth"].read(w["known_uri"], Stance("as_of", valid_at=mid))
    assert as_of[REGISTRANT_PROP] == meta["props"][REGISTRANT_PROP]
    assert as_of["tail"] == meta["props"]["tail"]  # open cells visible too

    # outside the registration window the registrant assertion disappears,
    # while the identity facts stay (proper bitemporal slicing, §4.4)
    before = w["hearth"].read(w["known_uri"], Stance("as_of", valid_at=valid.start - 1))
    assert REGISTRANT_PROP not in before
    assert before["tail"] == meta["props"]["tail"]


def test_hearth_link_commit_and_traverse(hearth_world):
    w = hearth_world
    assert w["hearth"].traverse(w["known_uri"], LINK_PRED) == [w["model_obj"]]
    # reverse traversal closes the loop: model -> the aircraft that carries it
    assert w["known_uri"] in w["hearth"].traverse(w["model_obj"], LINK_PRED, reverse=True)


# --------------------------------------------------------------------------
# (e) constraint H end to end: every committed cell's prov_ref resolves to a
#     non-ZERO term whose leaves are genuinely registered atom ids
# --------------------------------------------------------------------------


def test_constraint_h_every_committed_cell_resolves_to_real_atoms(hearth_world, ledger):
    w = hearth_world
    hearth: Hearth = w["hearth"]

    all_cells = [c for shard in hearth.value_shard_items() for c in shard.cells]
    all_links = [c for shard in hearth.links.link_shard_items() for c in shard.cells]
    assert len(all_cells) == w["n_cells"]
    assert len(all_links) == 1

    for cell in all_cells + all_links:
        assert cell.prov_ref, "empty prov_ref escaped commit validation"
        assert ledger.valuate_ref(cell.prov_ref, "derivable") is True
        citations = ledger.valuate_ref(cell.prov_ref, "citations")
        assert citations, f"ZERO/empty provenance for {cell!r}"
        for atom_id in citations:
            atom = ledger.get_atom(atom_id)
            assert atom is not None, f"prov leaf {atom_id} not registered in the ledger"
            # minted from real source cells of faa_master
            assert atom.uri.startswith("atom://faa_registry/faa_master/"), atom
