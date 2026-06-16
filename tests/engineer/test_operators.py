"""Operator-application tests: preview impact math, the confidently-wrong join
guard, exact TEMPER apply+undo round-trips, spine-gated deferral."""

from __future__ import annotations

from ontoforge.engineer.commands import ProposedCommand, parse_command
from ontoforge.engineer.operators import JOIN_LIKELY_FLOOR


def _parse(cmd, schema) -> ProposedCommand:
    out = parse_command(cmd, schema)
    assert isinstance(out, ProposedCommand), out
    return out


def test_link_preview_reports_real_coverage(make_service, schema) -> None:
    svc = make_service()
    prev = svc.preview(_parse("link salelines to catalog on sku", schema))
    # salelines.sku ⊆ catalog.sku -> full coverage
    assert prev.coverage == 1.0
    assert prev.tier == "confirmed"
    assert not prev.blocked
    assert prev.op_dict is not None


def test_low_coverage_join_is_blocked(make_service, schema) -> None:
    """A join on columns with essentially no value overlap is REFUSED as a
    confidently-wrong join — no op token, blocked flag set."""
    svc = make_service()
    # quantity (1,2,3) vs catalog.sku (s1,s2,s3): zero overlap
    prev = svc.preview(_parse("link salelines to catalog on quantity", schema))
    assert prev.coverage < JOIN_LIKELY_FLOOR
    assert prev.blocked
    assert prev.op_dict is None
    assert "floor" in prev.block_reason


def test_link_apply_then_undo_round_trips(make_service, schema) -> None:
    """apply adds the link property; undo (TEMPER inverse) removes it exactly —
    link count and version progress as expected, final link count restored."""
    svc = make_service()
    before_links = svc.stats()["links"]
    prev = svc.preview(_parse("link salelines to catalog on sku", schema))
    applied = svc.apply(prev.op_dict)
    assert applied["ok"]
    assert applied["undo_token"] is not None
    assert applied["atlas_delta"]["added_links"]
    assert svc.stats()["links"] == before_links + 1

    undone = svc.undo(applied["undo_token"])
    assert undone["ok"]
    assert svc.stats()["links"] == before_links


def test_rename_apply_then_undo_restores_name(make_service, schema) -> None:
    svc = make_service()
    prev = svc.preview(_parse("rename country to nation", schema))
    assert prev.valid
    applied = svc.apply(prev.op_dict)
    assert applied["ok"]
    onto = svc.engine.ontology
    names = {p.name for c in onto.iter_classes() for p in c.properties}
    assert "nation" in names and "country" not in names

    svc.undo(applied["undo_token"])
    names2 = {p.name for c in svc.engine.ontology.iter_classes() for p in c.properties}
    assert "country" in names2 and "nation" not in names2


def test_merge_is_spine_gated_in_preview(make_service, schema) -> None:
    svc = make_service()
    prev = svc.preview(_parse("merge duplicate salelines", schema))
    assert prev.spine_gated
    assert "review" in prev.reason.lower()


def test_split_preview_reports_routed_rows(make_service, schema) -> None:
    svc = make_service()
    prev = svc.preview(_parse("split pname into a and b on space", schema))
    assert prev.spine_gated
    assert prev.affected_count >= 0  # no spaces in the synthetic names -> 0 routed


def test_retype_string_to_number_is_not_invertibly_applicable(make_service, schema) -> None:
    """qty is materialized as a numeric type already OR remains string; either
    way the preview reports a parse-rate and never silently retypes a column
    that only partly parses. The TEMPER op only exists when the source datatype
    admits an invertible conversion."""
    svc = make_service()
    prev = svc.preview(_parse("treat sku as number", schema))
    # sku values are 's1','s2','s3' -> 0% parse as numbers
    assert prev.coverage == 0.0
    assert prev.op_dict is None  # nothing applicable; honest refusal
    assert not prev.valid


def test_apply_unknown_property_rejects_cleanly(make_service) -> None:
    """A bogus op token is rejected by the precondition, not crash."""
    svc = make_service()
    bad = {
        "op_type": "RenameProperty",
        "class_uri": "onto://class/nope",
        "prop_name": "x",
        "new_name": "y",
    }
    out = svc.apply(bad)
    assert out["ok"] is False
    assert not out["deferred"]


def test_apply_refuses_hand_crafted_sub_floor_join(tmp_path_factory) -> None:
    """Defence-in-depth: a confidently-wrong link op that SKIPPED interpret
    (hand-crafted directly) is still refused on the apply path — apply
    re-measures coverage from the live HEARTH and blocks below the floor,
    never asserting the join. The link count must not move.

    Built over a dedicated world whose two classes share NO join key, so the
    only way the link could land is if apply blindly trusted the client op."""
    import pandas as pd

    from ontoforge.contracts import SpineProfile
    from ontoforge.engineer import EngineerService
    from ontoforge.hearth import Hearth
    from ontoforge.ledger import SqliteLedger
    from ontoforge.pipeline.playground import PlaygroundJob
    from ontoforge.spine import DecisionSpine
    from ontoforge.vista._pipeline import load_ontology

    tmp = tmp_path_factory.mktemp("disjoint-world")
    src = tmp / "src"
    src.mkdir()
    # animals(name,legs) and cities(city,pop) — no value overlap on any column
    pd.DataFrame({"name": ["cat", "dog", "owl"], "legs": ["4", "4", "2"]}).to_csv(
        src / "animals.csv", index=False
    )
    pd.DataFrame({"city": ["paris", "tokyo", "lima"], "pop": ["9", "37", "10"]}).to_csv(
        src / "cities.csv", index=False
    )
    play = tmp / "play"
    PlaygroundJob(
        job_id="dj",
        selections=[("a", "animals", src / "animals.csv"), ("c", "cities", src / "cities.csv")],
        project_dir=play,
    ).run_sync()
    ledger = SqliteLedger(str(play / "ledger.sqlite"))
    try:
        hearth = Hearth(play / "hearth", ledger)
        onto = load_ontology(play / "ontology.materialized.json")
        spine = DecisionSpine(SpineProfile(), model_client=None, ledger=ledger)
        svc = EngineerService(onto, hearth=hearth, ledger=ledger, spine=spine)

        before_links = svc.stats()["links"]
        classes = list(svc.ontology.iter_classes())
        animal = next(c for c in classes if "animal" in c.name.lower())
        city = next(c for c in classes if "citi" in c.name.lower())
        hand_crafted = {
            "op_type": "AddProperty",
            "class_uri": animal.uri,
            "name": "city_link",
            "range_class": city.uri,
            "cardinality": "one",
        }
        out = svc.apply(hand_crafted)
        assert out["ok"] is False
        assert out.get("blocked") is True
        assert "floor" in out["human_summary"]
        assert out["undo_token"] is None
        assert svc.stats()["links"] == before_links  # the join was never asserted
    finally:
        ledger.close()


# --------------------------- typed-relationship evidence (v2.1 §1.2/§1.3/§1.4)


def test_committed_link_records_typed_relationship_evidence(make_service, schema) -> None:
    """A committed link carries execution-grounded typed-relationship
    provenance: the distribution-aware confidence PROXY + the EvidenceArtifacts
    (which signals fired) + the SQL synthesize-and-EXECUTE JoinValidation
    (match/orphan/fan-out over real cells) + the reasoning-path RelationshipVerdict.
    salelines.sku ⊆ catalog.sku is a clean FK, so the executed validation
    matches and the verdict is a join type."""
    svc = make_service()
    prev = svc.preview(_parse("link salelines to catalog on sku", schema))
    applied = svc.apply(prev.op_dict)
    assert applied["ok"]
    tr = applied.get("typed_relationship")
    assert tr is not None, "a committed link must record typed-relationship provenance"
    # the EvidenceArtifact trail is present (which signals fired/conflicted)
    assert tr["evidence"] and any(ev["fired"] for ev in tr["evidence"])
    assert 0.0 <= tr["proxy_confidence"] <= 1.0
    # the SQL backward validation actually executed over the real join
    assert tr["validation"] is not None
    assert tr["validation"]["match_rate"] >= 0.9          # clean FK matches
    assert tr["validation"]["verdict"] in {"fk_join", "lookup_dimension"}
    # the reasoning-path verdict typed it (a join type) and did NOT contradict
    assert tr["rel_type"] in {"fk_join", "lookup_dimension"}
    assert tr["contradicted"] is False
    assert tr["votes"], "the reasoning-path votes are recorded for provenance"


def test_typed_relationship_evidence_is_deterministic(make_service, schema) -> None:
    """Keyless + deterministic: applying the same link over a fresh service
    yields byte-identical typed-relationship provenance (proxy, evidence,
    validation metrics, verdict) — a fixed world is a fixed verdict."""
    a = make_service()
    out_a = a.apply(a.preview(_parse("link salelines to catalog on sku", schema)).op_dict)
    b = make_service()
    out_b = b.apply(b.preview(_parse("link salelines to catalog on sku", schema)).op_dict)
    assert out_a["typed_relationship"] == out_b["typed_relationship"]
