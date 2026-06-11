"""Incremental ER tests (M5 step 6): staged two-cycle load vs batch —
F1(incremental)/F1(batch) >= 0.97, URI churn <= 1 per affected entity per
cycle, zero churn off the affected set, and delta-scoped re-adjudication."""

from __future__ import annotations

import pytest
import xxhash

from ontoforge.er import ERCascade, IncrementalER, pairwise_prf

KINDS = ("aircraft", "operator")


def _two_cycle_split(mentions):
    """Deterministic staged load: every registry row plus the hash-even half
    of the event mentions arrive in cycle 1; the rest arrive as the delta."""
    c1, c2 = [], []
    for m in mentions:
        if m.table == "faa_master" or xxhash.xxh3_64_intdigest(m.mention_id.encode()) % 2 == 0:
            c1.append(m)
        else:
            c2.append(m)
    return c1, c2


@pytest.fixture(scope="module")
def staged(mentions, train_labels):
    """Cycle-1 batch resolve, then the cycle-2 delta through add_mentions."""
    c1, c2 = _two_cycle_split(mentions)
    cascade = ERCascade()
    res1 = cascade.run(c1, train_labels)
    inc = IncrementalER(cascade, res1)
    snapshot_before = inc.snapshot_mention_to_uri()
    report = inc.add_mentions(c2)
    return c1, c2, res1, inc, report, snapshot_before


class TestTwoCycleQuality:
    def test_incremental_vs_batch_f1_ratio(self, staged, batch, test_labels):
        """GATE: F1(incremental final state) / F1(batch) >= 0.97 on held-out gold."""
        _, _, _, inc, _, _ = staged
        _, batch_res = batch
        combined: dict[str, str] = {}
        for kind in KINDS:
            combined.update(test_labels[kind])
        f_batch = pairwise_prf(batch_res.mention_to_uri, combined)["f1"]
        f_inc = pairwise_prf(inc.snapshot_mention_to_uri(), combined)["f1"]
        assert f_batch > 0
        ratio = f_inc / f_batch
        assert ratio >= 0.97, f"incremental/batch F1 ratio {ratio:.4f} (inc={f_inc:.4f}, batch={f_batch:.4f})"

    def test_delta_actually_processed(self, staged):
        c1, c2, _, inc, report, _ = staged
        assert report.new_mentions == len(c2) > 0
        assert report.new_pairs_scored > 0
        # every delta mention is resolved to some entity URI
        for m in c2:
            if m.entity_kind in KINDS:
                assert m.mention_id in inc.mention_to_uri


class TestUriChurn:
    def test_churn_gate_per_affected_entity(self, staged):
        """GATE: <= 1 URI change per affected entity per cycle."""
        _, _, _, _, report, _ = staged
        assert report.max_churn_per_affected_entity <= 1
        for kind, churn in report.churn_by_entity.items():
            for uri, n in churn.items():
                assert n <= 1, f"{kind} entity {uri} absorbed {n} prior URIs in one cycle"

    def test_mention_level_at_most_one_change_per_cycle(self, staged):
        _, _, _, _, report, _ = staged
        assert all(n <= 1 for n in report.mention_uri_changes.values())

    def test_unaffected_entities_zero_churn(self, staged):
        """Hysteresis: any mention whose entity the delta never touched keeps
        its URI bit-identically."""
        _, _, res1, inc, report, before = staged
        affected: set[str] = set()
        for s in report.affected_entities.values():
            affected |= s
        for s in report.retired_uris.values():
            affected |= s
        changed = [
            m for m, uri in before.items()
            if uri not in affected and inc.mention_to_uri.get(m) != uri
        ]
        assert changed == [], f"URIs changed outside the affected set: {changed[:5]}"

    def test_retired_uris_not_in_final_state(self, staged):
        _, _, _, inc, report, _ = staged
        live = {uri for kind in KINDS for uri in inc.clusters.get(kind, {})}
        for kind, retired in report.retired_uris.items():
            assert live.isdisjoint(retired)


class TestDeltaScoping:
    def test_only_delta_touched_edges_adjudicated(self, staged):
        """Re-adjudication is delta-scoped: every newly decided pair touches a
        node introduced or grown by the delta."""
        c1, c2, res1, inc, report, _ = staged
        delta_mentions = {m.mention_id for m in c2}
        for kind, kd in report.decisions.items():
            blocker = inc.cascade.blockers[kind]
            for (a, b) in kd:
                ms = set(blocker.nodes[a].mention_ids) | set(blocker.nodes[b].mention_ids)
                assert ms & delta_mentions, f"pair ({a},{b}) does not touch the delta"
            # nothing decided in cycle 1 was re-decided
            assert set(kd).isdisjoint(set(res1.decisions[kind].keys()))

    def test_incremental_is_deterministic(self, staged, mentions, train_labels):
        """A second staged run reproduces the final state bit-identically."""
        _, _, _, inc, _, _ = staged
        c1, c2 = _two_cycle_split(mentions)
        cascade2 = ERCascade()
        res1b = cascade2.run(c1, train_labels)
        inc2 = IncrementalER(cascade2, res1b)
        inc2.add_mentions(c2)
        assert inc2.snapshot_mention_to_uri() == inc.snapshot_mention_to_uri()
        assert inc2.state_summary() == inc.state_summary()
