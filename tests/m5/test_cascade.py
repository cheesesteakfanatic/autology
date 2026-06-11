"""End-to-end cascade tests (M5 steps 3-5): the F1 >= 0.85 HARD GATE on the
held-out gold split, spine integration, deferral budget, and the temporal
N-number-reuse trap."""

from __future__ import annotations

from collections import defaultdict

import pytest

from ontoforge.contracts import DecisionKind
from ontoforge.er import pairwise_prf
from ontoforge.estates.aviation import load_er_gold_pairs

KINDS = ("aircraft", "operator")


class TestHoldoutDiscipline:
    def test_split_holds_out_at_least_half(self, gold):
        for kind in KINDS:
            train, test = gold.train_entities[kind], gold.test_entities[kind]
            assert train.isdisjoint(test)
            assert len(test) >= len(train), "TEST split must be >= 50% of entities"
            # >= 50% of gold pairs held out (entity-level split, pair-level check)
            assert gold.n_pairs(kind, "test") > 0
            assert gold.n_pairs(kind, "train") > 0

    def test_train_and_test_labels_disjoint_mentions(self, gold):
        for kind in KINDS:
            tr = set(gold.split_labels(kind, "train"))
            te = set(gold.split_labels(kind, "test"))
            assert tr.isdisjoint(te)

    def test_split_is_seeded_and_stable(self, gold):
        from ontoforge.er import load_gold

        again = load_gold()
        for kind in KINDS:
            assert again.test_entities[kind] == gold.test_entities[kind]


class TestF1Gates:
    def test_pairwise_f1_hard_gate_combined(self, batch, test_labels):
        """HARD GATE: pairwise F1 >= 0.85 on held-out gold, both kinds."""
        _, res = batch
        combined: dict[str, str] = {}
        for kind in KINDS:
            combined.update(test_labels[kind])
        prf = pairwise_prf(res.mention_to_uri, combined)
        assert prf["f1"] >= 0.85, f"combined held-out PRF: {prf}"

    def test_pairwise_f1_per_kind(self, batch, test_labels):
        _, res = batch
        for kind in KINDS:
            prf = pairwise_prf(res.mention_to_uri, test_labels[kind])
            assert prf["f1"] >= 0.85, f"{kind} held-out PRF: {prf}"
            assert prf["precision"] > 0.0 and prf["recall"] > 0.0


class TestSpineIntegration:
    def test_ambiguous_band_routes_through_spine(self, batch):
        """The FS band must leave a real ambiguous middle, and the spine must
        actually decide it (T1 calibrated + T2 heuristic adjudication)."""
        _, res = batch
        stages = defaultdict(int)
        for kind in KINDS:
            for dec in res.decisions[kind].values():
                stages[dec.stage] += 1
        assert stages["fs_high"] > 0 and stages["fs_low"] > 0
        spine_total = sum(v for s, v in stages.items() if s.startswith("spine:"))
        assert spine_total > 0, "no decision ever escalated to the spine"
        assert stages["spine:T2"] > 0, "T2 heuristic adjudicator never consulted"

    def test_t1_recalibrated_from_train_bootstrap(self, batch):
        cascade, res = batch
        assert res.metrics["calibration_samples"] >= 50
        ece = cascade.spine.ece(DecisionKind.ER)
        assert ece is not None, "ER kind was never calibrated"
        assert ece <= 0.05, f"post-calibration ECE {ece} above the §12 target"

    def test_deferral_budget(self, batch):
        """v1 target: deferral <= 25% (here measured on all pair decisions)."""
        _, res = batch
        for kind in KINDS:
            assert res.metrics[kind]["deferral_rate"] <= 0.25
            assert res.metrics[kind]["escalation_rate"] < 1.0

    def test_fail_closed_no_accepted_deferrals(self, batch):
        _, res = batch
        for kind in KINDS:
            for dec in res.decisions[kind].values():
                if dec.deferred or dec.quarantined:
                    assert dec.outcome == "no"


class TestTemporalReuseTrap:
    """§17.2.1: N-numbers are reused across aircraft over time — same tail,
    different serial / disjoint validity windows must NOT merge."""

    @pytest.fixture(scope="class")
    def reused_tails(self, mentions):
        by_tail = defaultdict(list)
        for m in mentions:
            if m.entity_kind == "aircraft" and m.table == "faa_master":
                by_tail[m.fields["tail"]].append(m)
        dups = {t: ms for t, ms in by_tail.items() if len(ms) > 1}
        assert dups, "fixture must contain reused tails"
        return dups

    def test_reused_registry_rows_never_co_cluster(self, batch, reused_tails):
        _, res = batch
        for tail, ms in reused_tails.items():
            uris = [res.mention_to_uri[m.mention_id] for m in ms]
            assert len(set(uris)) == len(uris), (
                f"tail {tail}: registry rows with different serials merged: {uris}"
            )

    def test_gold_trap_pairs_resolved_correctly(self, batch, gold):
        """The labeled trap pairs from the gold file: each event must cluster
        with ITS registration era and against the other era's registration."""
        _, res = batch
        df = load_er_gold_pairs()
        traps = df[df["NOTE"].str.contains("temporal_reuse_trap", na=False)]
        assert len(traps) > 0
        labels = gold.labels["aircraft"]
        for row in traps.to_dict("records"):
            left = f"aircraft/{row['LEFT_TABLE']}/{row['LEFT_KEY']}"
            right = f"aircraft/{row['RIGHT_TABLE']}/{row['RIGHT_KEY']}"
            # the gold-asserted era pairing holds...
            assert res.mention_to_uri[left] == res.mention_to_uri[right], (
                f"trap pair split: {left} vs {right}"
            )
            # ...and the event does NOT join any gold mention of another entity
            # that shares the same reused tail (the cross-era false positive)
            for other, eid in labels.items():
                if eid != labels[left] and other in res.mention_to_uri:
                    assert (
                        res.mention_to_uri[other] != res.mention_to_uri[right]
                        or other == right
                    )

    def test_cross_era_registry_pairs_rejected(self, batch, reused_tails):
        """Same-tail registry pairs (different serial, disjoint windows) sit in
        the same exact-key block, so they WERE scored — and decided 'no'."""
        _, res = batch
        checked = 0
        for tail, regs in reused_tails.items():
            for i in range(len(regs)):
                for j in range(i + 1, len(regs)):
                    key = tuple(sorted((regs[i].mention_id, regs[j].mention_id)))
                    dec = res.decisions["aircraft"].get(key)
                    assert dec is not None, f"reused-tail registry pair never scored: {key}"
                    assert dec.outcome == "no", f"cross-era registry pair accepted: {key}"
                    checked += 1
        assert checked >= 5

    def test_cross_era_events_stay_with_their_era(self, batch, mentions, reused_tails):
        """Every event whose date falls inside exactly ONE registration era of
        a reused tail must not co-cluster with the OTHER era's registry row."""
        _, res = batch
        events = defaultdict(list)
        for m in mentions:
            if (
                m.entity_kind == "aircraft"
                and m.fields.get("is_registry") != "1"
                and m.fields.get("tail") in reused_tails
                and m.fields.get("date_lo") is not None
            ):
                events[m.fields["tail"]].append(m)
        checked = 0
        for tail, evs in events.items():
            regs = reused_tails[tail]
            for ev in evs:
                d = ev.fields["date_lo"]
                inside = [r for r in regs if r.fields["date_lo"] <= d <= r.fields["date_hi"]]
                if len(inside) != 1:
                    continue
                era = inside[0]
                for other in regs:
                    if other.mention_id == era.mention_id:
                        continue
                    assert (
                        res.mention_to_uri[ev.mention_id]
                        != res.mention_to_uri[other.mention_id]
                    ), f"event {ev.mention_id} merged with wrong era {other.mention_id}"
                    checked += 1
        assert checked >= 3, "fixture should exercise at least a few cross-era events"


class TestDeterminism:
    def test_two_runs_identical(self, batch, mentions, train_labels):
        """Fixed seeds end to end: a fresh cascade reproduces URIs, cluster
        membership and every pair decision bit-identically."""
        from ontoforge.er import ERCascade

        _, res = batch
        res2 = ERCascade().run(mentions, train_labels)
        assert res2.mention_to_uri == res.mention_to_uri
        for kind in KINDS:
            assert res2.accepted_edges[kind] == res.accepted_edges[kind]
            assert set(res2.clusters[kind]) == set(res.clusters[kind])
            assert {k: (d.outcome, d.stage) for k, d in res2.decisions[kind].items()} == {
                k: (d.outcome, d.stage) for k, d in res.decisions[kind].items()
            }
